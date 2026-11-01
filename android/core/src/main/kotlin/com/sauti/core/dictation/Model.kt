package com.sauti.core.dictation

import com.sauti.core.protocol.Limits
import com.sauti.core.protocol.UserInfo

/** Per-utterance options sent in `start`. Null fields fall back to the server profile. */
data class UtteranceOptions(
    val mode: String? = null,
    val lang: String? = null,
    val targetLang: String? = null,
)

/** Why the client is not connected. Used to pick a user-facing message. */
enum class OfflineReason {
    /** DNS failed: wrong server name or no network. */
    HOST_NOT_FOUND,

    /** Connection refused / unreachable / HTTP 5xx on the upgrade: server down. */
    SERVER_UNAVAILABLE,

    /** Connect timed out. */
    TIMEOUT,

    /** TLS failed; the server certificate is probably not trusted on this device. */
    CERTIFICATE,

    /** http:// blocked by the network security policy (release builds). */
    CLEARTEXT_BLOCKED,

    /** An open connection broke (network change, server restart, ...). */
    CONNECTION_LOST,

    /** Nothing heard from the server for the keep-alive window; treated as dead. */
    NO_RESPONSE,

    /** The server did not answer `hello` with `ready` in time, or rejected it. */
    HANDSHAKE_FAILED,

    /** The server closed the connection. */
    SERVER_CLOSED,

    /** The WebSocket upgrade got an unexpected HTTP status (e.g. 404 behind a proxy). */
    HTTP_ERROR,
}

sealed class ConnectionState {
    /** Not connected and not trying to (keyboard hidden, nothing pending). */
    object Idle : ConnectionState() {
        override fun toString() = "Idle"
    }

    data class Connecting(val attempt: Int) : ConnectionState()

    data class Ready(
        val sessionId: String,
        val user: UserInfo?,
        val profileVersion: Long?,
        val limits: Limits,
    ) : ConnectionState()

    /** Disconnected; the next attempt starts in [retryInMs]. */
    data class Offline(
        val reason: OfflineReason,
        val detail: String?,
        val retryInMs: Long,
    ) : ConnectionState()

    /** The device token was rejected. No reconnect until the app is paired again. */
    data class Unauthorized(val message: String?) : ConnectionState()
}

sealed class UtteranceState {
    /** Microphone open, audio streaming (or buffering while offline). */
    data class Recording(val online: Boolean) : UtteranceState()

    /** Recording finished; waiting for a connection to send or resume it. */
    object WaitingForServer : UtteranceState() {
        override fun toString() = "WaitingForServer"
    }

    /** Sent; waiting for the server (no status yet, or resumed and `processing`). */
    object Sent : UtteranceState() {
        override fun toString() = "Sent"
    }

    data class Queued(val position: Int?) : UtteranceState()

    /** `transcribing`, `cleaning` or `translating` (see [com.sauti.core.protocol.StatusStates]). */
    data class Processing(val stage: String) : UtteranceState()

    /** Server said busy/timeout/unavailable/internal; will resend with the same id. */
    data class Retrying(val code: String, val inMs: Long) : UtteranceState()

    data class Done(val inserted: Boolean) : UtteranceState()

    data class Failed(val error: DictationError) : UtteranceState()

    object Cancelled : UtteranceState() {
        override fun toString() = "Cancelled"
    }
}

/**
 * A user-visible problem. [code] is a protocol error code ([com.sauti.core.protocol.ErrorCodes])
 * or a client-side one (`gave_up`, `no_audio`). [message] is the server's text when it sent one.
 */
data class DictationError(
    val code: String,
    val message: String?,
    val utteranceId: String?,
    val retryable: Boolean,
)

/** Text to insert, delivered at most once per utterance id. `text` may be empty (no speech). */
data class FinalResult(
    val utteranceId: String,
    val text: String,
    val raw: String?,
    val lang: String?,
    val mode: String?,
    val targetLang: String?,
    val replayed: Boolean,
    val notices: List<String>,
)

/**
 * Callbacks from [DictationClient], invoked on the client's callback executor (the app passes
 * the main thread). Implementations must not block.
 */
interface DictationListener {
    fun onConnectionState(state: ConnectionState) {}

    fun onUtteranceState(utteranceId: String, state: UtteranceState) {}

    /** Live preview. Never insert it. */
    fun onPartial(utteranceId: String, text: String) {}

    /** The text to insert. Called at most once per utterance id, ever (ledger-backed). */
    fun onFinal(result: FinalResult)

    fun onError(error: DictationError) {}

    fun onProfileChanged(version: Long) {}
}

data class DictationConfig(
    /** Sent in `hello`, e.g. `android/0.1.0`. */
    val clientName: String = "android/0.1.0",
    /** Reconnect delays; the last one repeats (docs/protocol.md 3.4 rule 2). */
    val backoffMs: List<Long> = listOf(500, 1_000, 2_000, 4_000, 8_000),
    /** A connection that stays ready this long resets the backoff. */
    val stableConnectionMs: Long = 10_000,
    /** Give up on an utterance this long after the user finished it (rule 5). */
    val giveUpAfterMs: Long = 120_000,
    /** Wait this long for `ready` after `hello`. */
    val readyTimeoutMs: Long = 10_000,
    /** Wait this long for an answer to `resume` before resending anyway (duplicate starts are safe). */
    val resumeTimeoutMs: Long = 10_000,
    /** JSON `ping` interval; also how often silence is checked. 0 disables. */
    val keepAlivePingMs: Long = 15_000,
    /** Silence after which the connection is treated as dead (section 3.5). */
    val deadAfterSilenceMs: Long = 45_000,
    /** OkHttp transport-level ping interval. 0 disables. */
    val transportPingMs: Long = 20_000,
    /** Used when a retryable error has no retry_after_ms. */
    val defaultRetryAfterMs: Long = 1_000,
    /** How many times `timeout` is retried (protocol: once). */
    val timeoutRetries: Int = 1,
    /** Extra audio accepted beyond limits.max_utterance_seconds before frames are dropped. */
    val audioSlackSeconds: Int = 5,
) {
    init {
        require(backoffMs.isNotEmpty()) { "backoffMs must not be empty" }
    }
}

/** Optional diagnostics hook. Never receives audio, transcript text or tokens. */
fun interface DictationLogger {
    fun log(message: String)
}
