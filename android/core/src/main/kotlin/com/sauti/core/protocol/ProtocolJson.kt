package com.sauti.core.protocol

import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/** JSON settings shared by the REST and WebSocket code. */
object ProtocolJson {

    val json: Json = Json {
        ignoreUnknownKeys = true   // the server may add fields at any time
        explicitNulls = false      // omit null optionals (start.mode, settings patch, ...)
        encodeDefaults = true      // always send protocol, sample_rate, format, platform
        coerceInputValues = true   // `"text": null` -> default instead of an exception
        classDiscriminator = "type"
    }

    fun encodeClient(message: ClientMessage): String =
        json.encodeToString(ClientMessage.serializer(), message)

    fun <T> encode(serializer: KSerializer<T>, value: T): String = json.encodeToString(serializer, value)

    fun <T> decode(serializer: KSerializer<T>, text: String): T = json.decodeFromString(serializer, text)

    /** Decodes one server text frame. Never throws. */
    fun decodeServer(text: String): ServerMessage {
        val obj: JsonObject = try {
            json.parseToJsonElement(text) as? JsonObject
                ?: return ServerMessage.Malformed("not a JSON object")
        } catch (e: SerializationException) {
            return ServerMessage.Malformed("invalid JSON")
        } catch (e: IllegalArgumentException) {
            return ServerMessage.Malformed("invalid JSON")
        }
        val type = (obj["type"] as? JsonPrimitive)?.contentOrNull
            ?: return ServerMessage.Malformed("missing type")
        return try {
            when (type) {
                "ready" -> json.decodeFromJsonElement(ServerMessage.Ready.serializer(), obj)
                "partial" -> json.decodeFromJsonElement(ServerMessage.Partial.serializer(), obj)
                "status" -> json.decodeFromJsonElement(ServerMessage.Status.serializer(), obj)
                "final" -> json.decodeFromJsonElement(ServerMessage.Final.serializer(), obj)
                "error" -> json.decodeFromJsonElement(ServerMessage.Error.serializer(), obj)
                "resume_result" -> json.decodeFromJsonElement(ServerMessage.ResumeResult.serializer(), obj)
                "profile_changed" -> json.decodeFromJsonElement(ServerMessage.ProfileChanged.serializer(), obj)
                "pong" -> json.decodeFromJsonElement(ServerMessage.Pong.serializer(), obj)
                else -> ServerMessage.Unknown(type)
            }
        } catch (e: SerializationException) {
            ServerMessage.Malformed("bad '$type' message: ${e.message?.take(120)}")
        } catch (e: IllegalArgumentException) {
            ServerMessage.Malformed("bad '$type' message: ${e.message?.take(120)}")
        }
    }
}
