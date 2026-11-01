package com.sauti.core

import com.sauti.core.protocol.ClientMessage
import com.sauti.core.protocol.ProtocolJson
import com.sauti.core.protocol.ServerMessage
import com.sauti.core.protocol.SettingsPatch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ProtocolJsonTest {

    private fun obj(s: String): JsonObject = Json.parseToJsonElement(s).jsonObject

    @Test
    fun hello_hasTypeTokenClientAndProtocol() {
        val o = obj(ProtocolJson.encodeClient(ClientMessage.Hello(token = "sdt_x", client = "android/0.1.0")))
        assertEquals(obj("""{"type":"hello","token":"sdt_x","client":"android/0.1.0","protocol":1}"""), o)
    }

    @Test
    fun start_omitsMissingOptions_andAlwaysSendsFormat() {
        val o = obj(ProtocolJson.encodeClient(ClientMessage.Start(utteranceId = "u1", mode = "clean")))
        assertEquals(
            obj("""{"type":"start","utterance_id":"u1","mode":"clean","sample_rate":16000,"format":"pcm_s16le"}"""),
            o,
        )
    }

    @Test
    fun smallClientMessages() {
        assertEquals(obj("""{"type":"end","utterance_id":"u"}"""), obj(ProtocolJson.encodeClient(ClientMessage.End("u"))))
        assertEquals(obj("""{"type":"cancel","utterance_id":"u"}"""), obj(ProtocolJson.encodeClient(ClientMessage.Cancel("u"))))
        assertEquals(obj("""{"type":"ack","utterance_id":"u"}"""), obj(ProtocolJson.encodeClient(ClientMessage.Ack("u"))))
        assertEquals(obj("""{"type":"resume","utterance_id":"u"}"""), obj(ProtocolJson.encodeClient(ClientMessage.Resume("u"))))
        assertEquals(obj("""{"type":"ping","t":123}"""), obj(ProtocolJson.encodeClient(ClientMessage.Ping(123))))
    }

    @Test
    fun final_fromTheProtocolExample_decodes_ignoringUnknownFields() {
        val m = ProtocolJson.decodeServer(
            """{"type":"final","utterance_id":"U","text":"Hello.","raw":"hello","lang":"so","lang_prob":0.91,
               "mode":"clean","target_lang":null,"replayed":true,
               "timings_ms":{"queue":12,"asr":820,"cleanup":640,"translate":0,"total":1490},
               "notices":["cleanup_fallback"],"new_field":{"x":1}}""",
        )
        m as ServerMessage.Final
        assertEquals("U", m.utteranceId)
        assertEquals("Hello.", m.text)
        assertTrue(m.replayed)
        assertEquals(1490.0, m.timingsMs["total"])
        assertEquals(listOf("cleanup_fallback"), m.notices)
        assertEquals(null, m.targetLang)
    }

    @Test
    fun final_withNullTextAndNoOptionalFields_decodes() {
        val m = ProtocolJson.decodeServer("""{"type":"final","utterance_id":"U","text":null}""") as ServerMessage.Final
        assertEquals("", m.text)
        assertFalse(m.replayed)
        assertTrue(m.notices.isEmpty())
    }

    @Test
    fun error_acceptsIntegerOrFloatRetryAfter() {
        val a = ProtocolJson.decodeServer("""{"type":"error","utterance_id":"U","code":"busy","retryable":true,"retry_after_ms":1500}""") as ServerMessage.Error
        val b = ProtocolJson.decodeServer("""{"type":"error","code":"busy","retryable":true,"retry_after_ms":1500.0}""") as ServerMessage.Error
        assertEquals(1500.0, a.retryAfterMs)
        assertEquals(1500.0, b.retryAfterMs)
        assertEquals(null, b.utteranceId)
    }

    @Test
    fun readyStatusResumeProfilePong() {
        val ready = ProtocolJson.decodeServer(
            """{"type":"ready","session_id":"c0ffee","user":{"id":3,"username":"amina","display_name":"Amina Yusuf","role":"user"},"profile_version":7,"limits":{"max_utterance_seconds":120,"sample_rate":16000}}""",
        ) as ServerMessage.Ready
        assertEquals("c0ffee", ready.sessionId)
        assertEquals(3L, ready.user?.id)
        val status = ProtocolJson.decodeServer("""{"type":"status","utterance_id":"U","state":"queued","position":2}""") as ServerMessage.Status
        assertEquals(2, status.position)
        val rr = ProtocolJson.decodeServer("""{"type":"resume_result","utterance_id":"U","state":"unknown"}""") as ServerMessage.ResumeResult
        assertEquals("unknown", rr.state)
        assertEquals(8L, (ProtocolJson.decodeServer("""{"type":"profile_changed","version":8}""") as ServerMessage.ProfileChanged).version)
        assertEquals(123.0, (ProtocolJson.decodeServer("""{"type":"pong","t":123}""") as ServerMessage.Pong).t)
    }

    @Test
    fun ready_acceptsWholeNumbersSentAsFloats() {
        val ready = ProtocolJson.decodeServer(
            """{"type":"ready","session_id":"s","profile_version":1,"limits":{"max_utterance_seconds":120.0,"sample_rate":16000}}""",
        ) as ServerMessage.Ready
        assertEquals(120, ready.limits.maxUtteranceSeconds)
    }

    @Test
    fun unknownTypes_andGarbage_doNotThrow() {
        assertEquals(ServerMessage.Unknown("hint"), ProtocolJson.decodeServer("""{"type":"hint","x":1}"""))
        assertTrue(ProtocolJson.decodeServer("not json") is ServerMessage.Malformed)
        assertTrue(ProtocolJson.decodeServer("[1,2]") is ServerMessage.Malformed)
        assertTrue(ProtocolJson.decodeServer("""{"no":"type"}""") is ServerMessage.Malformed)
        assertTrue(ProtocolJson.decodeServer("""{"type":"final"}""") is ServerMessage.Malformed)
    }

    @Test
    fun settingsPatch_sendsOnlyTheFieldsThatAreSet() {
        val s = ProtocolJson.encode(SettingsPatch.serializer(), SettingsPatch(mode = "verbatim", inputLang = "so"))
        assertEquals(obj("""{"input_lang":"so","mode":"verbatim"}"""), obj(s))
    }
}
