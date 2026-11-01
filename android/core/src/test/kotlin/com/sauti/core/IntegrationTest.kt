package com.sauti.core

import com.sauti.core.api.ApiClient
import com.sauti.core.api.ServerUrl
import com.sauti.core.dictation.ConnectionState
import com.sauti.core.dictation.DictationClient
import com.sauti.core.dictation.DictationConfig
import com.sauti.core.dictation.UtteranceOptions
import com.sauti.core.ledger.InMemoryLedgerStorage
import com.sauti.core.ledger.InsertionLedger
import okhttp3.OkHttpClient
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File
import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.TimeUnit

/**
 * End-to-end check against a real Sauti server. Skipped unless these are set:
 *
 *   SAUTI_TEST_SERVER   e.g. http://127.0.0.1:8700
 *   SAUTI_TEST_TOKEN    a device token (sdt_…)
 *   SAUTI_TEST_WAV      path to a 16 kHz mono 16-bit PCM WAV file with speech
 *   SAUTI_TEST_REALTIME optional; "1" streams the audio at real-time pace (like a microphone)
 *
 * Run: gradlew :core:test --tests com.sauti.core.IntegrationTest
 */
class IntegrationTest {

    @Test
    fun streamsAWav_andReceivesExactlyOneNonEmptyFinal() {
        val serverEnv = System.getenv("SAUTI_TEST_SERVER")
        val token = System.getenv("SAUTI_TEST_TOKEN")
        val wavPath = System.getenv("SAUTI_TEST_WAV")
        assumeTrue("SAUTI_TEST_SERVER / SAUTI_TEST_TOKEN / SAUTI_TEST_WAV not set", !serverEnv.isNullOrBlank() && !token.isNullOrBlank() && !wavPath.isNullOrBlank())
        val realtime = System.getenv("SAUTI_TEST_REALTIME") == "1"

        val base = (ServerUrl.parse(serverEnv!!, allowCleartext = true) as? ServerUrl.Result.Ok)?.url
            ?: error("SAUTI_TEST_SERVER is not a valid URL: $serverEnv")
        val pcm = readPcm16Mono16k(File(wavPath!!))
        assertTrue("WAV has no audio", pcm.isNotEmpty())

        val http = OkHttpClient.Builder().connectTimeout(10, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS).build()
        val health = ApiClient(base, http).health()
        println("server health: ${health.status} ${health.version ?: ""}")

        val listener = RecordingListener()
        val client = DictationClient(
            serverUrl = base,
            token = token!!,
            httpClient = http,
            ledger = InsertionLedger(InMemoryLedgerStorage()),
            listener = listener,
            config = DictationConfig(clientName = "android-core-test/0.1.0"),
            logger = { println("client: $it") },
        )
        try {
            client.connect()
            val ready = listener.awaitConnection<ConnectionState.Ready>(timeoutMs = 20_000)
            println("ready: session ${ready.sessionId}, max ${ready.limits.maxUtteranceSeconds} s")

            val id = client.startUtterance(UtteranceOptions())
            val frame = 3200 // 100 ms
            var offset = 0
            while (offset < pcm.size) {
                val n = minOf(frame, pcm.size - offset)
                client.sendAudio(id, pcm, offset, n)
                offset += n
                if (realtime) Thread.sleep(100)
            }
            client.endUtterance(id)

            val final = listener.awaitFinal(timeoutMs = 150_000)
            println("final (${final.lang}, ${final.mode}, notices=${final.notices}): ${final.text}")
            assertEquals(id, final.utteranceId)
            assertTrue("final text should not be empty", final.text.isNotBlank())
            listener.assertNoFinal(waitMs = 3_000)
            assertEquals("exactly one final", 1, listener.allFinals.size)
        } finally {
            client.close()
        }
    }

    /** Minimal RIFF/WAVE reader: requires PCM, 1 channel, 16 kHz, 16-bit. */
    private fun readPcm16Mono16k(file: File): ByteArray {
        val bytes = file.readBytes()
        val bb = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
        require(bytes.size >= 12 && String(bytes, 0, 4, Charsets.US_ASCII) == "RIFF" && String(bytes, 8, 4, Charsets.US_ASCII) == "WAVE") {
            "${file.name} is not a RIFF/WAVE file"
        }
        var pos = 12
        var formatOk = false
        while (pos + 8 <= bytes.size) {
            val id = String(bytes, pos, 4, Charsets.US_ASCII)
            val size = bb.getInt(pos + 4)
            val body = pos + 8
            when (id) {
                "fmt " -> {
                    val format = bb.getShort(body).toInt() and 0xffff
                    val channels = bb.getShort(body + 2).toInt()
                    val rate = bb.getInt(body + 4)
                    val bits = bb.getShort(body + 14).toInt()
                    require((format == 1 || format == 0xFFFE) && channels == 1 && rate == 16_000 && bits == 16) {
                        "WAV must be 16 kHz mono 16-bit PCM (got format=$format channels=$channels rate=$rate bits=$bits)"
                    }
                    formatOk = true
                }
                "data" -> {
                    require(formatOk) { "WAV 'data' chunk before 'fmt '" }
                    val end = minOf(bytes.size, body + size)
                    return bytes.copyOfRange(body, end)
                }
            }
            pos = body + size + (size and 1)
        }
        error("no 'data' chunk in ${file.name}")
    }
}
