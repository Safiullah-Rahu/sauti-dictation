package com.sauti.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okio.ByteString
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import java.io.ByteArrayOutputStream
import java.io.Closeable
import java.util.concurrent.CountDownLatch
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger

/**
 * A scriptable stand-in for the Sauti server built on MockWebServer's WebSocket support.
 * Each upgrade creates a [FakeConnection] that the test drives frame by frame.
 */
class FakeSautiServer : Closeable {

    val mock = MockWebServer()
    private lateinit var proxy: DropProxy
    private val connections = LinkedBlockingQueue<FakeConnection>()
    private val counter = AtomicInteger()

    /** When non-null, upgrades are answered with this instead (e.g. 503 = server down). */
    @Volatile
    var rejectUpgradesWith: (() -> MockResponse)? = null

    init {
        mock.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                rejectUpgradesWith?.let { return it() }
                val conn = FakeConnection(counter.incrementAndGet(), request.path ?: "") { proxy.dropAll() }
                connections.add(conn)
                return MockResponse().withWebSocketUpgrade(conn)
            }
        }
        mock.start()
        proxy = DropProxy(java.net.InetAddress.getByName(mock.hostName), mock.port)
    }

    /** Clients connect through the proxy so that tests can cut connections abruptly. */
    val baseUrl get() = "http://127.0.0.1:${proxy.port}/".toHttpUrl()

    fun awaitConnection(timeoutMs: Long = 5_000): FakeConnection {
        val c = connections.poll(timeoutMs, TimeUnit.MILLISECONDS)
        assertNotNull("expected a WebSocket connection", c)
        c!!.awaitOpen()
        return c
    }

    fun assertNoNewConnection(waitMs: Long = 300) {
        assertNull("did not expect another connection", connections.poll(waitMs, TimeUnit.MILLISECONDS))
    }

    val upgradeAttempts: Int get() = mock.requestCount

    override fun close() {
        proxy.close()
        try {
            mock.shutdown()
        } catch (_: Exception) {
        }
    }
}

sealed class Frame {
    data class Text(val obj: JsonObject) : Frame() {
        val type: String get() = (obj["type"] as? JsonPrimitive)?.contentOrNull ?: ""
        fun str(key: String): String? = (obj[key] as? JsonPrimitive)?.contentOrNull
    }

    class Binary(val bytes: ByteArray) : Frame()
    data class Closed(val code: Int) : Frame()
}

class FakeConnection(val index: Int, val path: String, private val cut: () -> Unit) : WebSocketListener() {

    private val opened = CountDownLatch(1)

    @Volatile
    private var ws: WebSocket? = null
    val inbox = LinkedBlockingQueue<Frame>()

    /** Answer JSON pings automatically (they are then not put in [inbox]). */
    @Volatile
    var autoPong = true

    override fun onOpen(webSocket: WebSocket, response: Response) {
        ws = webSocket
        opened.countDown()
    }

    override fun onMessage(webSocket: WebSocket, text: String) {
        val obj = Json.parseToJsonElement(text).jsonObject
        val frame = Frame.Text(obj)
        if (frame.type == "ping" && autoPong) {
            webSocket.send("""{"type":"pong","t":${frame.str("t")}}""")
            return
        }
        inbox.add(frame)
    }

    override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
        inbox.add(Frame.Binary(bytes.toByteArray()))
    }

    override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
        inbox.add(Frame.Closed(code))
        webSocket.close(1000, null)
    }

    override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) {
        inbox.add(Frame.Closed(-1))
    }

    fun awaitOpen() {
        if (!opened.await(5, TimeUnit.SECONDS)) fail("server side of WebSocket never opened")
    }

    fun next(timeoutMs: Long = 5_000): Frame {
        return inbox.poll(timeoutMs, TimeUnit.MILLISECONDS) ?: run {
            fail("timed out waiting for a frame from the client on connection #$index")
            throw IllegalStateException()
        }
    }

    /** Next frame must be a JSON message of [type]. */
    fun expect(type: String, timeoutMs: Long = 5_000): Frame.Text {
        val f = next(timeoutMs)
        if (f !is Frame.Text) fail("expected '$type' but got $f")
        f as Frame.Text
        assertEquals("message type on connection #$index", type, f.type)
        return f
    }

    /** Reads binary frames until a text frame of type `end`; returns the audio and the end. */
    fun collectAudioUntilEnd(timeoutMs: Long = 5_000): Pair<ByteArray, Frame.Text> {
        val out = ByteArrayOutputStream()
        while (true) {
            when (val f = next(timeoutMs)) {
                is Frame.Binary -> out.write(f.bytes)
                is Frame.Text -> {
                    assertEquals("expected audio then 'end'", "end", f.type)
                    return out.toByteArray() to f
                }
                is Frame.Closed -> fail("connection closed while collecting audio")
            }
        }
    }

    /** Reads exactly [count] bytes of binary audio (no text frames allowed in between). */
    fun collectAudio(count: Int, timeoutMs: Long = 5_000): ByteArray {
        val out = ByteArrayOutputStream()
        while (out.size() < count) {
            when (val f = next(timeoutMs)) {
                is Frame.Binary -> out.write(f.bytes)
                else -> fail("expected audio, got $f")
            }
        }
        return out.toByteArray()
    }

    fun assertSilent(waitMs: Long = 300) {
        val f = inbox.poll(waitMs, TimeUnit.MILLISECONDS)
        assertNull("expected nothing from the client, got $f", f)
    }

    fun send(json: String) {
        awaitOpen()
        ws!!.send(json)
    }

    fun sendReady(sessionId: String = "sess-$index", maxSeconds: Int = 120) {
        send(
            """{"type":"ready","session_id":"$sessionId","user":{"id":3,"username":"amina","display_name":"Amina Yusuf","role":"user"},"profile_version":7,"limits":{"max_utterance_seconds":$maxSeconds,"sample_rate":16000}}""",
        )
    }

    /** Waits for `hello` (asserting the token) and answers `ready`. */
    fun handshake(expectedToken: String = TEST_TOKEN): Frame.Text {
        val hello = expect("hello")
        assertEquals(expectedToken, hello.str("token"))
        sendReady()
        return hello
    }

    fun sendFinal(id: String, text: String, replayed: Boolean = false, notices: List<String> = emptyList()) {
        val obj = buildJsonObject {
            put("type", "final")
            put("utterance_id", id)
            put("text", text)
            put("raw", text.lowercase())
            put("lang", "en")
            put("lang_prob", 0.98)
            put("mode", "clean")
            put("target_lang", null as String?)
            put("replayed", replayed)
            put("timings_ms", buildJsonObject { put("queue", 12); put("asr", 820); put("total", 1490) })
            put("notices", kotlinx.serialization.json.JsonArray(notices.map { JsonPrimitive(it) }))
            put("some_future_field", "ignored")
        }
        send(obj.toString())
    }

    fun sendError(code: String, id: String? = null, retryable: Boolean, retryAfterMs: Long? = null, message: String? = null) {
        val obj = buildJsonObject {
            put("type", "error")
            if (id != null) put("utterance_id", id)
            put("code", code)
            if (message != null) put("message", message)
            put("retryable", retryable)
            if (retryAfterMs != null) put("retry_after_ms", retryAfterMs)
        }
        send(obj.toString())
    }

    fun sendStatus(id: String, state: String, position: Int? = null) {
        val obj = buildJsonObject {
            put("type", "status")
            put("utterance_id", id)
            put("state", state)
            if (position != null) put("position", position)
        }
        send(obj.toString())
    }

    fun sendResumeResult(id: String, state: String) {
        send("""{"type":"resume_result","utterance_id":"$id","state":"$state"}""")
    }

    /** Abrupt drop (TCP reset, no close handshake), like a network cut or a server crash. */
    fun drop() {
        awaitOpen()
        cut()
    }

    fun close(code: Int, reason: String) {
        awaitOpen()
        ws!!.close(code, reason)
    }
}

const val TEST_TOKEN = "sdt_test_token"
