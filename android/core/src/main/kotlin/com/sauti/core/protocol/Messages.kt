package com.sauti.core.protocol

import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.descriptors.PrimitiveKind
import kotlinx.serialization.descriptors.PrimitiveSerialDescriptor
import kotlinx.serialization.descriptors.SerialDescriptor
import kotlinx.serialization.encoding.Decoder
import kotlinx.serialization.encoding.Encoder
import kotlinx.serialization.json.JsonDecoder
import kotlinx.serialization.json.double
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive

/** Protocol version sent in `hello` (docs/protocol.md, section 3.1). */
const val PROTOCOL_VERSION: Int = 1

/** The only audio format accepted in protocol v1. */
const val SAMPLE_RATE_HZ: Int = 16_000
const val AUDIO_FORMAT: String = "pcm_s16le"

/** 16 kHz * 2 bytes per sample, mono. */
const val BYTES_PER_SECOND: Int = SAMPLE_RATE_HZ * 2

@Serializable
data class UserInfo(
    val id: Long = 0,
    val username: String = "",
    @SerialName("display_name") val displayName: String? = null,
    val role: String? = null,
)

/** Reads 120 or 120.0: a server may serialise whole numbers as floats. */
object LenientIntSerializer : KSerializer<Int> {
    override val descriptor: SerialDescriptor = PrimitiveSerialDescriptor("LenientInt", PrimitiveKind.INT)

    override fun deserialize(decoder: Decoder): Int {
        val json = decoder as? JsonDecoder ?: return decoder.decodeInt()
        val primitive = json.decodeJsonElement().jsonPrimitive
        return primitive.intOrNull ?: primitive.double.toInt()
    }

    override fun serialize(encoder: Encoder, value: Int) = encoder.encodeInt(value)
}

@Serializable
data class Limits(
    @Serializable(with = LenientIntSerializer::class)
    @SerialName("max_utterance_seconds") val maxUtteranceSeconds: Int = 120,
    @Serializable(with = LenientIntSerializer::class)
    @SerialName("sample_rate") val sampleRate: Int = SAMPLE_RATE_HZ,
)

// ---------------------------------------------------------------------------------------------
// Client -> server (WebSocket text frames). Encoded with ProtocolJson.encodeClient, which adds
// the "type" discriminator and omits null optional fields.
// ---------------------------------------------------------------------------------------------

@Serializable
sealed class ClientMessage {

    @Serializable
    @SerialName("hello")
    data class Hello(
        val token: String,
        val client: String,
        val protocol: Int = PROTOCOL_VERSION,
    ) : ClientMessage()

    @Serializable
    @SerialName("start")
    data class Start(
        @SerialName("utterance_id") val utteranceId: String,
        val mode: String? = null,
        val lang: String? = null,
        @SerialName("target_lang") val targetLang: String? = null,
        @SerialName("sample_rate") val sampleRate: Int = SAMPLE_RATE_HZ,
        val format: String = AUDIO_FORMAT,
    ) : ClientMessage()

    @Serializable
    @SerialName("end")
    data class End(@SerialName("utterance_id") val utteranceId: String) : ClientMessage()

    @Serializable
    @SerialName("cancel")
    data class Cancel(@SerialName("utterance_id") val utteranceId: String) : ClientMessage()

    @Serializable
    @SerialName("ack")
    data class Ack(@SerialName("utterance_id") val utteranceId: String) : ClientMessage()

    @Serializable
    @SerialName("resume")
    data class Resume(@SerialName("utterance_id") val utteranceId: String) : ClientMessage()

    @Serializable
    @SerialName("ping")
    data class Ping(val t: Long) : ClientMessage()
}

// ---------------------------------------------------------------------------------------------
// Server -> client. Decoded by ProtocolJson.decodeServer, which dispatches on "type" and maps
// unknown types to [ServerMessage.Unknown] instead of failing (forward compatibility).
// Numeric fields that are durations are parsed as Double so that `1500` and `1500.0` both work.
// ---------------------------------------------------------------------------------------------

sealed interface ServerMessage {

    @Serializable
    data class Ready(
        @SerialName("session_id") val sessionId: String = "",
        val user: UserInfo? = null,
        @SerialName("profile_version") val profileVersion: Long? = null,
        val limits: Limits = Limits(),
    ) : ServerMessage

    @Serializable
    data class Partial(
        @SerialName("utterance_id") val utteranceId: String? = null,
        val text: String = "",
        val seq: Long = 0,
    ) : ServerMessage

    @Serializable
    data class Status(
        @SerialName("utterance_id") val utteranceId: String? = null,
        val state: String = "",
        val position: Int? = null,
    ) : ServerMessage

    @Serializable
    data class Final(
        @SerialName("utterance_id") val utteranceId: String,
        val text: String = "",
        val raw: String? = null,
        val lang: String? = null,
        @SerialName("lang_prob") val langProb: Double? = null,
        val mode: String? = null,
        @SerialName("target_lang") val targetLang: String? = null,
        val replayed: Boolean = false,
        @SerialName("timings_ms") val timingsMs: Map<String, Double> = emptyMap(),
        val notices: List<String> = emptyList(),
    ) : ServerMessage

    @Serializable
    data class Error(
        @SerialName("utterance_id") val utteranceId: String? = null,
        val code: String = ErrorCodes.INTERNAL,
        val message: String? = null,
        val retryable: Boolean = false,
        @SerialName("retry_after_ms") val retryAfterMs: Double? = null,
    ) : ServerMessage

    @Serializable
    data class ResumeResult(
        @SerialName("utterance_id") val utteranceId: String,
        val state: String,
    ) : ServerMessage

    @Serializable
    data class ProfileChanged(val version: Long = 0) : ServerMessage

    @Serializable
    data class Pong(val t: Double? = null) : ServerMessage

    /** A message type this client does not know. Ignored, but kept for logging. */
    data class Unknown(val type: String) : ServerMessage

    /** A frame that was not valid JSON or did not match the expected shape. */
    data class Malformed(val reason: String) : ServerMessage
}

object ErrorCodes {
    const val BUSY = "busy"
    const val TIMEOUT = "timeout"
    const val QUOTA_EXCEEDED = "quota_exceeded"
    const val TOO_LONG = "too_long"
    const val BAD_REQUEST = "bad_request"
    const val CANCELLED = "cancelled"
    const val UNAVAILABLE = "unavailable"
    const val INTERNAL = "internal"
    const val UNAUTHORIZED = "unauthorized"

    /** Client-side code: the utterance could not be delivered within the give-up window. */
    const val GAVE_UP = "gave_up"

    /** Client-side code: the microphone/recorder produced no usable audio. */
    const val NO_AUDIO = "no_audio"
}

object StatusStates {
    const val QUEUED = "queued"
    const val TRANSCRIBING = "transcribing"
    const val CLEANING = "cleaning"
    const val TRANSLATING = "translating"
}

object ResumeStates {
    const val PROCESSING = "processing"
    const val UNKNOWN = "unknown"
}

object Notices {
    const val NO_SPEECH = "no_speech"
    const val CLEANUP_FALLBACK = "cleanup_fallback"
    const val CLEANUP_UNAVAILABLE = "cleanup_unavailable"
    const val TRANSLATION_UNVERIFIED = "translation_unverified"
    const val TRUNCATED = "truncated"
    const val LANG_RESTRICTED = "lang_restricted"
}

/** WebSocket close code the server uses after `unauthorized`. */
const val CLOSE_UNAUTHORIZED: Int = 4401
