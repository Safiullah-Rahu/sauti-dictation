package com.sauti.core.dictation

import com.sauti.core.api.NetworkErrors
import com.sauti.core.api.NetworkProblem
import com.sauti.core.api.ServerUrl
import com.sauti.core.ledger.InsertionLedger
import com.sauti.core.protocol.BYTES_PER_SECOND
import com.sauti.core.protocol.CLOSE_UNAUTHORIZED
import com.sauti.core.protocol.ClientMessage
import com.sauti.core.protocol.ErrorCodes
import com.sauti.core.protocol.Limits
import com.sauti.core.protocol.ProtocolJson
import com.sauti.core.protocol.ResumeStates
import com.sauti.core.protocol.ServerMessage
import com.sauti.core.protocol.StatusStates
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.net.SocketTimeoutException
import java.util.UUID
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executor
import java.util.concurrent.TimeUnit

/**
 * Client side of the dictation WebSocket (`/v1/dictate`, docs/protocol.md section 3).
 *
 * Responsibilities:
 * - handshake (`hello` -> `ready`), keep-alive pings, dead-connection detection (45 s silence);
 * - one utterance at a time: `start`, binary PCM frames, `end`, `cancel`;
 * - reliability (section 3.4): every utterance's audio is kept in memory until its `final` or a
 *   non-retryable error; on reconnect (backoff 0.5/1/2/4 then 8 s) each pending utterance that
 *   reached the server is `resume`d, and `resume_result: unknown` resends `start` + all audio +
 *   `end` with the same id; `busy`/`timeout`/`unavailable`/`internal` are retried with the same
 *   id after `retry_after_ms`; an utterance not delivered within two minutes of the user
 *   finishing it is given up visibly;
 * - at-most-once insertion through the [InsertionLedger], and an `ack` for every final.
 *
 * Thread-safety: public methods may be called from any thread. All state lives on
 * [scheduler]'s serial context; [listener] callbacks go through [callbackExecutor].
 */
class DictationClient(
    serverUrl: HttpUrl,
    private val token: String,
    httpClient: OkHttpClient,
    private val ledger: InsertionLedger,
    private val listener: DictationListener,
    private val config: DictationConfig = DictationConfig(),
    scheduler: Scheduler? = null,
    private val callbackExecutor: Executor = Executor { it.run() },
    private val idGenerator: () -> String = { UUID.randomUUID().toString() },
    private val logger: DictationLogger = DictationLogger { },
) {
    private val ownsScheduler = scheduler == null
    private val scheduler: Scheduler = scheduler ?: ExecutorScheduler()

    private val dictateUrl: HttpUrl = ServerUrl.resolve(serverUrl, "v1/dictate")

    private val wsClient: OkHttpClient = httpClient.newBuilder()
        .readTimeout(0, TimeUnit.MILLISECONDS)
        .callTimeout(0, TimeUnit.MILLISECONDS)
        .pingInterval(config.transportPingMs.coerceAtLeast(0), TimeUnit.MILLISECONDS)
        .build()

    private enum class Wire { NONE, CONNECTING, HANDSHAKE, READY }

    private enum class Phase {
        /** Not on the current connection: offline, or waiting for one. */
        WAITING,

        /** Must (re)send start + audio (+ end) as soon as the wire is free. */
        NEEDS_SEND,

        /** `start` sent on this connection, live audio flowing, `end` not sent yet. */
        STREAMING,

        /** start..end sent on this connection; waiting for status/final. */
        SENT,

        /** `resume` sent; waiting for `resume_result` or a replayed `final`. */
        AWAITING_RESUME,

        /** Server said `processing`; the final will follow. */
        PROCESSING,

        /** Waiting for retry_after_ms after a retryable error. */
        RETRY_WAIT,
    }

    private class Utterance(val id: String, val options: UtteranceOptions) {
        val audio = ArrayList<ByteString>()
        var audioBytes = 0L
        var ended = false
        var everSent = false
        var phase = Phase.WAITING
        var lastSeq = -1L
        var timeoutRetries = 0
        var overLimit = false
        var deadline: Cancellable? = null
        var resumeTimeout: Cancellable? = null
        var retryTask: Cancellable? = null

        fun cancelTimers() {
            deadline?.cancel()
            resumeTimeout?.cancel()
            retryTask?.cancel()
        }
    }

    // ---- state: touched only on the scheduler's serial context ----
    private var wire = Wire.NONE
    private var socket: WebSocket? = null
    private var generation = 0
    private var wanted = false
    private var unauthorized = false
    private var shutDown = false
    private var failures = 0
    private var reconnectTask: Cancellable? = null
    private var readyTimeoutTask: Cancellable? = null
    private var keepAliveTask: Cancellable? = null
    private var stableTask: Cancellable? = null
    private var lastHeardMs = 0L
    private var limits = Limits()
    private var openOnWire: String? = null
    private var recordingId: String? = null
    private val pending = LinkedHashMap<String, Utterance>()

    @Volatile
    private var lastState: ConnectionState = ConnectionState.Idle

    /** The most recent connection state (also delivered through the listener). */
    val connectionState: ConnectionState get() = lastState

    // =============================================================================================
    // Public API
    // =============================================================================================

    /** Keep a connection open (and reconnect) until [disconnect]. Call when the keyboard shows. */
    fun connect() = post {
        wanted = true
        ensureConnected()
    }

    /**
     * Stop wanting a connection. It is closed once nothing is pending; utterances in flight are
     * still delivered (or given up) first.
     */
    fun disconnect() = post {
        wanted = false
        maybeIdle()
    }

    /** Skip the backoff wait and try now (e.g. the user tapped "retry", or the network came back). */
    fun reconnectNow() = post {
        if (socket == null && !unauthorized && !shutDown) {
            reconnectTask?.cancel()
            reconnectTask = null
            openSocket()
        }
    }

    /**
     * Begin a new utterance and return its id. Audio may be sent immediately with [sendAudio];
     * it is buffered while offline and streamed once connected. Starting a new utterance ends
     * the previous one.
     */
    fun startUtterance(options: UtteranceOptions = UtteranceOptions()): String {
        val id = idGenerator()
        post { doStart(id, options) }
        return id
    }

    /** 16 kHz mono PCM16 little-endian. The bytes are copied before this returns. */
    fun sendAudio(utteranceId: String, pcm: ByteArray, offset: Int = 0, length: Int = pcm.size - offset) {
        if (length <= 0) return
        val chunk = pcm.toByteString(offset, length)
        post { doAudio(utteranceId, chunk) }
    }

    /** The user finished speaking. */
    fun endUtterance(utteranceId: String) = post { doEnd(utteranceId) }

    /** Discard the utterance; nothing will be inserted for it. */
    fun cancelUtterance(utteranceId: String) = post { doCancel(utteranceId) }

    /** Close the connection and release resources. The client cannot be used afterwards. */
    fun close() = post {
        if (shutDown) return@post
        shutDown = true
        reconnectTask?.cancel()
        stopConnectionTimers()
        pending.values.forEach { it.cancelTimers() }
        pending.clear()
        recordingId = null
        val ws = socket
        socket = null
        generation++
        wire = Wire.NONE
        ws?.close(1000, "client closed")
        setState(ConnectionState.Idle)
        if (ownsScheduler) scheduler.shutdown()
    }

    /** Test/diagnostic hook: pending utterance ids mapped to buffered audio bytes. */
    internal fun pendingSnapshot(timeoutMs: Long = 5_000): Map<String, Long> {
        val latch = CountDownLatch(1)
        var result: Map<String, Long> = emptyMap()
        post {
            result = pending.mapValues { it.value.audioBytes }
            latch.countDown()
        }
        latch.await(timeoutMs, TimeUnit.MILLISECONDS)
        return result
    }

    // =============================================================================================
    // Utterance lifecycle
    // =============================================================================================

    private fun doStart(id: String, options: UtteranceOptions) {
        if (shutDown) return
        if (unauthorized) {
            val err = DictationError(ErrorCodes.UNAUTHORIZED, null, id, retryable = false)
            emitUtterance(id, UtteranceState.Failed(err))
            emitError(err)
            return
        }
        recordingId?.let { doEnd(it) }
        val u = Utterance(id, options)
        pending[id] = u
        recordingId = id
        if (wire == Wire.READY && openOnWire == null) {
            sendWhole(u)
        } else {
            u.phase = if (wire == Wire.READY) Phase.NEEDS_SEND else Phase.WAITING
            emitUtterance(id, UtteranceState.Recording(online = false))
            ensureConnected()
        }
    }

    private fun doAudio(id: String, chunk: ByteString) {
        val u = pending[id] ?: return
        if (u.ended) return
        val maxBytes = (limits.maxUtteranceSeconds.toLong() + config.audioSlackSeconds) * BYTES_PER_SECOND
        if (u.audioBytes + chunk.size > maxBytes) {
            if (!u.overLimit) {
                u.overLimit = true
                log("utterance over max length; dropping further audio")
            }
            return
        }
        u.audio.add(chunk)
        u.audioBytes += chunk.size
        if (openOnWire == id && wire == Wire.READY) socket?.send(chunk)
    }

    private fun doEnd(id: String) {
        val u = pending[id] ?: return
        if (u.ended) return
        if (u.audioBytes == 0L) {
            doCancel(id)
            return
        }
        u.ended = true
        if (recordingId == id) recordingId = null
        u.deadline = later(config.giveUpAfterMs) { giveUp(id) }
        when {
            openOnWire == id && wire == Wire.READY -> {
                send(ClientMessage.End(id))
                openOnWire = null
                u.phase = Phase.SENT
                emitUtterance(id, UtteranceState.Sent)
                flushSends()
            }
            u.phase == Phase.NEEDS_SEND -> {
                emitUtterance(id, UtteranceState.WaitingForServer)
                flushSends()
            }
            u.phase == Phase.RETRY_WAIT -> Unit // keeps showing "retrying"
            u.phase == Phase.PROCESSING -> emitUtterance(id, UtteranceState.Sent)
            else -> emitUtterance(id, UtteranceState.WaitingForServer)
        }
    }

    private fun doCancel(id: String) {
        val u = pending.remove(id) ?: return
        u.cancelTimers()
        if (recordingId == id) recordingId = null
        if (wire == Wire.READY && u.everSent) send(ClientMessage.Cancel(id))
        if (openOnWire == id) openOnWire = null
        emitUtterance(id, UtteranceState.Cancelled)
        flushSends()
        maybeIdle()
    }

    private fun giveUp(id: String) {
        val u = pending[id] ?: return
        log("giving up on utterance after ${config.giveUpAfterMs} ms")
        val err = DictationError(ErrorCodes.GAVE_UP, null, id, retryable = false)
        if (wire == Wire.READY && u.everSent) send(ClientMessage.Cancel(id))
        fail(u, err)
    }

    private fun fail(u: Utterance, err: DictationError) {
        pending.remove(u.id)
        u.cancelTimers()
        if (openOnWire == u.id) openOnWire = null
        if (recordingId == u.id) recordingId = null
        emitUtterance(u.id, UtteranceState.Failed(err))
        emitError(err)
        flushSends()
        maybeIdle()
    }

    /** start + every buffered frame (+ end if finished). Caller ensures the wire is free. */
    private fun sendWhole(u: Utterance) {
        if (socket == null || wire != Wire.READY) return
        u.everSent = true
        u.lastSeq = -1
        send(
            ClientMessage.Start(
                utteranceId = u.id,
                mode = u.options.mode,
                lang = u.options.lang,
                targetLang = u.options.targetLang,
            ),
        )
        for (chunk in u.audio) socket?.send(chunk)
        if (u.ended) {
            send(ClientMessage.End(u.id))
            u.phase = Phase.SENT
            emitUtterance(u.id, UtteranceState.Sent)
        } else {
            openOnWire = u.id
            u.phase = Phase.STREAMING
            emitUtterance(u.id, UtteranceState.Recording(online = true))
        }
    }

    /**
     * Sends whatever is due. Finished utterances go first, each as one atomic start..end, so
     * that a live recording (which keeps its `start` open) never gets implicitly ended by them.
     */
    private fun flushSends() {
        if (wire != Wire.READY) return
        for (u in pending.values.toList()) {
            if (openOnWire != null) return
            if (u.phase == Phase.NEEDS_SEND && u.ended) sendWhole(u)
        }
        if (openOnWire != null) return
        val rec = recordingId?.let { pending[it] }
        if (rec != null && rec.phase == Phase.NEEDS_SEND && !rec.ended) sendWhole(rec)
    }

    // =============================================================================================
    // Server messages
    // =============================================================================================

    private fun onServerText(gen: Int, text: String) {
        if (gen != generation) return
        lastHeardMs = scheduler.nowMs()
        when (val m = ProtocolJson.decodeServer(text)) {
            is ServerMessage.Ready -> onReady(m)
            is ServerMessage.Partial -> onPartial(m)
            is ServerMessage.Status -> onStatus(m)
            is ServerMessage.Final -> onFinal(m)
            is ServerMessage.Error -> onError(m)
            is ServerMessage.ResumeResult -> onResumeResult(m)
            is ServerMessage.ProfileChanged -> callback { listener.onProfileChanged(m.version) }
            is ServerMessage.Pong -> Unit
            is ServerMessage.Unknown -> log("ignoring unknown message type '${m.type}'")
            is ServerMessage.Malformed -> log("ignoring malformed message: ${m.reason}")
        }
    }

    private fun onReady(m: ServerMessage.Ready) {
        if (wire != Wire.HANDSHAKE) {
            log("unexpected ready")
            return
        }
        readyTimeoutTask?.cancel()
        readyTimeoutTask = null
        wire = Wire.READY
        limits = m.limits
        setState(ConnectionState.Ready(m.sessionId, m.user, m.profileVersion, m.limits))
        stableTask?.cancel()
        stableTask = later(config.stableConnectionMs) { failures = 0 }
        startKeepAlive()
        for (u in pending.values.toList()) {
            if (u.everSent) {
                send(ClientMessage.Resume(u.id))
                u.phase = Phase.AWAITING_RESUME
                u.resumeTimeout?.cancel()
                val id = u.id
                u.resumeTimeout = later(config.resumeTimeoutMs) { onResumeTimeout(id) }
            } else {
                u.phase = Phase.NEEDS_SEND
            }
        }
        flushSends()
    }

    private fun onResumeTimeout(id: String) {
        val u = pending[id] ?: return
        if (u.phase != Phase.AWAITING_RESUME) return
        log("no answer to resume; resending (duplicate starts are safe)")
        u.phase = Phase.NEEDS_SEND
        flushSends()
    }

    private fun onResumeResult(m: ServerMessage.ResumeResult) {
        val u = pending[m.utteranceId] ?: return
        if (u.phase != Phase.AWAITING_RESUME) return
        u.resumeTimeout?.cancel()
        u.resumeTimeout = null
        when (m.state) {
            ResumeStates.UNKNOWN -> {
                u.phase = Phase.NEEDS_SEND
                flushSends()
            }
            else -> {
                if (m.state != ResumeStates.PROCESSING) log("unknown resume state '${m.state}', waiting")
                u.phase = Phase.PROCESSING
                emitUtterance(u.id, if (u.ended) UtteranceState.Sent else UtteranceState.Recording(online = true))
            }
        }
    }

    private fun onPartial(m: ServerMessage.Partial) {
        val id = m.utteranceId ?: return
        val u = pending[id] ?: return
        if (m.seq > 0 && m.seq <= u.lastSeq) return
        u.lastSeq = maxOf(u.lastSeq, m.seq)
        val text = m.text
        callback { listener.onPartial(id, text) }
    }

    private fun onStatus(m: ServerMessage.Status) {
        val id = m.utteranceId ?: pending.values.lastOrNull { it.ended }?.id ?: return
        if (!pending.containsKey(id)) return
        val state = when (m.state) {
            StatusStates.QUEUED -> UtteranceState.Queued(m.position)
            else -> UtteranceState.Processing(m.state)
        }
        emitUtterance(id, state)
    }

    private fun onFinal(m: ServerMessage.Final) {
        val id = m.utteranceId
        val u = pending.remove(id)
        if (u == null) {
            // Already delivered (duplicate / replay), cancelled, given up, or not ours: never insert.
            send(ClientMessage.Ack(id))
            return
        }
        u.cancelTimers()
        if (openOnWire == id) openOnWire = null
        if (recordingId == id) recordingId = null
        val fresh = ledger.record(id)
        if (fresh) {
            val result = FinalResult(
                utteranceId = id,
                text = m.text,
                raw = m.raw,
                lang = m.lang,
                mode = m.mode,
                targetLang = m.targetLang,
                replayed = m.replayed,
                notices = m.notices,
            )
            callback { listener.onFinal(result) }
        } else {
            log("final for an id already in the ledger; not inserting again")
        }
        emitUtterance(id, UtteranceState.Done(inserted = fresh))
        send(ClientMessage.Ack(id))
        flushSends()
        maybeIdle()
    }

    private fun onError(m: ServerMessage.Error) {
        if (m.code == ErrorCodes.UNAUTHORIZED) {
            handleUnauthorized(m.message)
            return
        }
        val id = m.utteranceId
        if (id == null) {
            val err = DictationError(m.code, m.message, null, m.retryable)
            if (wire == Wire.HANDSHAKE) {
                emitError(err)
                dropSocket(OfflineReason.HANDSHAKE_FAILED, m.code)
            } else {
                emitError(err)
            }
            return
        }
        val u = pending[id] ?: return
        if (m.code == ErrorCodes.CANCELLED) {
            pending.remove(id)
            u.cancelTimers()
            if (openOnWire == id) openOnWire = null
            if (recordingId == id) recordingId = null
            emitUtterance(id, UtteranceState.Cancelled)
            flushSends()
            maybeIdle()
            return
        }
        val retryable = m.retryable || m.code in RETRYABLE_CODES
        val err = DictationError(m.code, m.message, id, retryable)
        if (!retryable) {
            fail(u, err)
            return
        }
        if (m.code == ErrorCodes.TIMEOUT) {
            if (u.timeoutRetries >= config.timeoutRetries) {
                fail(u, err.copy(retryable = false))
                return
            }
            u.timeoutRetries++
        }
        if (openOnWire == id) openOnWire = null
        val delay = m.retryAfterMs?.toLong()?.coerceAtLeast(0) ?: config.defaultRetryAfterMs
        u.phase = Phase.RETRY_WAIT
        u.resumeTimeout?.cancel()
        u.retryTask?.cancel()
        u.retryTask = later(delay) { onRetryDue(id) }
        emitUtterance(id, UtteranceState.Retrying(m.code, delay))
        flushSends()
    }

    private fun onRetryDue(id: String) {
        val u = pending[id] ?: return
        if (u.phase != Phase.RETRY_WAIT) return
        u.retryTask = null
        u.phase = if (wire == Wire.READY) Phase.NEEDS_SEND else Phase.WAITING
        flushSends()
    }

    private fun handleUnauthorized(message: String?) {
        if (unauthorized) return
        unauthorized = true
        log("device token rejected; not reconnecting")
        reconnectTask?.cancel()
        reconnectTask = null
        stopConnectionTimers()
        val ws = socket
        socket = null
        generation++
        wire = Wire.NONE
        openOnWire = null
        ws?.close(1000, null)
        val failed = pending.values.toList()
        pending.clear()
        recordingId = null
        for (u in failed) {
            u.cancelTimers()
            emitUtterance(u.id, UtteranceState.Failed(DictationError(ErrorCodes.UNAUTHORIZED, message, u.id, false)))
        }
        setState(ConnectionState.Unauthorized(message))
        emitError(DictationError(ErrorCodes.UNAUTHORIZED, message, null, false))
    }

    // =============================================================================================
    // Connection management
    // =============================================================================================

    private fun needed(): Boolean = wanted || pending.isNotEmpty()

    private fun ensureConnected() {
        if (shutDown || unauthorized) return
        if (socket == null && reconnectTask == null) openSocket()
    }

    private fun openSocket() {
        reconnectTask = null
        if (shutDown || unauthorized || socket != null) return
        val gen = ++generation
        wire = Wire.CONNECTING
        setState(ConnectionState.Connecting(failures + 1))
        val request = Request.Builder().url(dictateUrl).build()
        socket = wsClient.newWebSocket(request, SocketListener(gen))
    }

    private fun onOpened(gen: Int, ws: WebSocket) {
        if (gen != generation) {
            ws.close(1000, null)
            return
        }
        wire = Wire.HANDSHAKE
        lastHeardMs = scheduler.nowMs()
        ws.send(ProtocolJson.encodeClient(ClientMessage.Hello(token = token, client = config.clientName)))
        readyTimeoutTask?.cancel()
        readyTimeoutTask = later(config.readyTimeoutMs) {
            if (gen == generation && wire == Wire.HANDSHAKE) dropSocket(OfflineReason.HANDSHAKE_FAILED, "no ready")
        }
    }

    private fun onSocketClosed(gen: Int, code: Int, reason: String) {
        if (gen != generation) return
        if (code == CLOSE_UNAUTHORIZED) {
            handleUnauthorized(reason.ifEmpty { null })
            return
        }
        val why = if (wire == Wire.READY) OfflineReason.SERVER_CLOSED else OfflineReason.HANDSHAKE_FAILED
        connectionLost(why, "closed ($code)")
    }

    private fun onSocketFailed(gen: Int, t: Throwable, httpStatus: Int?) {
        if (gen != generation) return
        val status = httpStatus?.takeIf { it != 101 }
        if (status == 401 || status == 403) {
            handleUnauthorized("HTTP $status")
            return
        }
        val reason = when {
            status != null -> if (status >= 500) OfflineReason.SERVER_UNAVAILABLE else OfflineReason.HTTP_ERROR
            wire == Wire.READY || wire == Wire.HANDSHAKE ->
                if (t is SocketTimeoutException) OfflineReason.NO_RESPONSE else OfflineReason.CONNECTION_LOST
            else -> when (NetworkErrors.classify(t)) {
                NetworkProblem.HOST_NOT_FOUND -> OfflineReason.HOST_NOT_FOUND
                NetworkProblem.UNREACHABLE -> OfflineReason.SERVER_UNAVAILABLE
                NetworkProblem.TIMEOUT -> OfflineReason.TIMEOUT
                NetworkProblem.CERTIFICATE -> OfflineReason.CERTIFICATE
                NetworkProblem.CLEARTEXT_BLOCKED -> OfflineReason.CLEARTEXT_BLOCKED
                NetworkProblem.OTHER -> OfflineReason.SERVER_UNAVAILABLE
            }
        }
        val detail = if (status != null) "HTTP $status" else (t.message ?: t.javaClass.simpleName)
        connectionLost(reason, detail)
    }

    /** Client-initiated drop of a connection that is unusable; reconnects with backoff. */
    private fun dropSocket(reason: OfflineReason, detail: String?) {
        val ws = socket
        connectionLost(reason, detail)
        ws?.cancel()
    }

    private fun connectionLost(reason: OfflineReason, detail: String?) {
        log("connection lost: $reason")
        socket = null
        generation++
        wire = Wire.NONE
        openOnWire = null
        stopConnectionTimers()
        for (u in pending.values) {
            u.resumeTimeout?.cancel()
            u.resumeTimeout = null
            u.retryTask?.cancel()
            u.retryTask = null
            u.phase = Phase.WAITING
            emitUtterance(u.id, if (u.ended) UtteranceState.WaitingForServer else UtteranceState.Recording(online = false))
        }
        scheduleReconnectOrIdle(reason, detail)
    }

    private fun scheduleReconnectOrIdle(reason: OfflineReason, detail: String?) {
        if (shutDown || unauthorized) return
        if (!needed()) {
            setState(ConnectionState.Idle)
            return
        }
        failures++
        val delay = config.backoffMs[minOf(failures - 1, config.backoffMs.lastIndex)]
        setState(ConnectionState.Offline(reason, detail, delay))
        reconnectTask?.cancel()
        reconnectTask = later(delay) { if (reconnectTask != null) openSocket() }
    }

    private fun maybeIdle() {
        if (needed() || shutDown) return
        reconnectTask?.cancel()
        reconnectTask = null
        val ws = socket
        if (ws != null) {
            socket = null
            generation++
            wire = Wire.NONE
            openOnWire = null
            stopConnectionTimers()
            ws.close(1000, "idle")
        }
        if (!unauthorized) setState(ConnectionState.Idle)
    }

    private fun startKeepAlive() {
        keepAliveTask?.cancel()
        keepAliveTask = null
        if (config.keepAlivePingMs <= 0) return
        val gen = generation
        keepAliveTask = later(config.keepAlivePingMs) { keepAliveTick(gen) }
    }

    private fun keepAliveTick(gen: Int) {
        if (gen != generation || wire != Wire.READY) return
        val now = scheduler.nowMs()
        if (now - lastHeardMs >= config.deadAfterSilenceMs) {
            dropSocket(OfflineReason.NO_RESPONSE, "silent for ${now - lastHeardMs} ms")
            return
        }
        send(ClientMessage.Ping(now))
        keepAliveTask = later(config.keepAlivePingMs) { keepAliveTick(gen) }
    }

    private fun stopConnectionTimers() {
        readyTimeoutTask?.cancel()
        readyTimeoutTask = null
        keepAliveTask?.cancel()
        keepAliveTask = null
        stableTask?.cancel()
        stableTask = null
    }

    private inner class SocketListener(private val gen: Int) : WebSocketListener() {
        override fun onOpen(webSocket: WebSocket, response: Response) {
            post { onOpened(gen, webSocket) }
        }

        override fun onMessage(webSocket: WebSocket, text: String) {
            post { onServerText(gen, text) }
        }

        override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
            post { if (gen == generation) lastHeardMs = scheduler.nowMs() }
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(1000, null)
            post { onSocketClosed(gen, code, reason) }
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) {
            post { onSocketClosed(gen, code, reason) }
        }

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
            val status = response?.code
            post { onSocketFailed(gen, t, status) }
        }
    }

    // =============================================================================================
    // Helpers
    // =============================================================================================

    private fun send(message: ClientMessage) {
        socket?.send(ProtocolJson.encodeClient(message))
    }

    /** Runs [block] on the serial context after [delayMs] (immediately, but still async, if <= 0). */
    private fun later(delayMs: Long, block: () -> Unit): Cancellable {
        if (delayMs <= 0) {
            var cancelled = false
            post { if (!cancelled) block() }
            return Cancellable { cancelled = true }
        }
        return scheduler.schedule(delayMs) { post(block) }
    }

    private fun post(block: () -> Unit) {
        scheduler.execute {
            try {
                block()
            } catch (t: Throwable) {
                log("internal error: ${t.javaClass.simpleName}: ${t.message}")
            }
        }
    }

    private fun setState(state: ConnectionState) {
        if (state == lastState) return
        lastState = state
        callback { listener.onConnectionState(state) }
    }

    private fun emitUtterance(id: String, state: UtteranceState) {
        callback { listener.onUtteranceState(id, state) }
    }

    private fun emitError(error: DictationError) {
        callback { listener.onError(error) }
    }

    private fun callback(block: () -> Unit) {
        callbackExecutor.execute {
            try {
                block()
            } catch (t: Throwable) {
                log("listener threw ${t.javaClass.simpleName}: ${t.message}")
            }
        }
    }

    private fun log(message: String) {
        try {
            logger.log(message)
        } catch (_: Throwable) {
        }
    }

    private companion object {
        val RETRYABLE_CODES = setOf(ErrorCodes.BUSY, ErrorCodes.TIMEOUT, ErrorCodes.UNAVAILABLE, ErrorCodes.INTERNAL)
    }
}
