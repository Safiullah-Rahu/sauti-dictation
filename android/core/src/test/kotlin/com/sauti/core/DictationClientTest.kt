package com.sauti.core

import com.sauti.core.dictation.ConnectionState
import com.sauti.core.dictation.DictationClient
import com.sauti.core.dictation.DictationConfig
import com.sauti.core.dictation.OfflineReason
import com.sauti.core.dictation.UtteranceOptions
import com.sauti.core.dictation.UtteranceState
import com.sauti.core.ledger.FileLedgerStorage
import com.sauti.core.ledger.InMemoryLedgerStorage
import com.sauti.core.ledger.InsertionLedger
import com.sauti.core.protocol.ErrorCodes
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.MockResponse
import org.junit.After
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.rules.Timeout
import java.util.concurrent.TimeUnit

class DictationClientTest {

    @get:Rule
    val timeout: Timeout = Timeout(60, TimeUnit.SECONDS)

    @get:Rule
    val tmp = TemporaryFolder()

    private lateinit var server: FakeSautiServer
    private lateinit var scheduler: VirtualScheduler
    private lateinit var listener: RecordingListener
    private val http = OkHttpClient.Builder().connectTimeout(5, TimeUnit.SECONDS).build()
    private val clients = ArrayList<DictationClient>()

    @Before
    fun setUp() {
        server = FakeSautiServer()
        scheduler = VirtualScheduler()
        listener = RecordingListener()
    }

    @After
    fun tearDown() {
        clients.forEach { it.close() }
        try {
            scheduler.drain()
        } catch (_: Exception) {
        }
        scheduler.shutdown()
        server.close()
    }

    private fun newClient(
        ledger: InsertionLedger = InsertionLedger(InMemoryLedgerStorage()),
        config: DictationConfig = DictationConfig(transportPingMs = 0),
        listener: RecordingListener = this.listener,
        ids: Iterator<String>? = null,
    ): DictationClient {
        val client = DictationClient(
            serverUrl = server.baseUrl,
            token = TEST_TOKEN,
            httpClient = http,
            ledger = ledger,
            listener = listener,
            config = config,
            scheduler = scheduler,
            idGenerator = if (ids != null) ({ ids.next() }) else ({ java.util.UUID.randomUUID().toString() }),
        )
        clients.add(client)
        return client
    }

    private fun pcm(size: Int, seed: Int): ByteArray = ByteArray(size) { ((it * 31 + seed) and 0xff).toByte() }

    /** Connects, completes the handshake and waits until the client reports Ready. */
    private fun connected(client: DictationClient): FakeConnection {
        client.connect()
        val c = server.awaitConnection()
        c.handshake()
        listener.awaitConnection<ConnectionState.Ready>()
        return c
    }

    /** Starts an utterance, streams [audio] in 100 ms frames, ends it; returns the id and what the server got. */
    private fun dictate(client: DictationClient, c: FakeConnection, audio: ByteArray, options: UtteranceOptions = UtteranceOptions(mode = "clean", lang = "auto")): String {
        val id = client.startUtterance(options)
        val start = c.expect("start")
        assertEquals(id, start.str("utterance_id"))
        audio.toList().chunked(3200).forEach { client.sendAudio(id, it.toByteArray()) }
        client.endUtterance(id)
        val (received, end) = c.collectAudioUntilEnd()
        assertEquals(id, end.str("utterance_id"))
        assertArrayEquals(audio, received)
        return id
    }

    // ------------------------------------------------------------------------------------------

    @Test
    fun handshake_sendsHelloWithTokenInFirstMessage_andReportsReady() {
        val client = newClient()
        client.connect()
        listener.awaitConnection<ConnectionState.Connecting>()
        val c = server.awaitConnection()
        assertEquals("token must not be in the URL", "/v1/dictate", c.path)
        val hello = c.expect("hello")
        assertEquals(TEST_TOKEN, hello.str("token"))
        assertEquals("android/0.1.0", hello.str("client"))
        assertEquals("1", hello.str("protocol"))
        c.sendReady(sessionId = "c0ffee")
        val ready = listener.awaitConnection<ConnectionState.Ready>()
        assertEquals("c0ffee", ready.sessionId)
        assertEquals("Amina Yusuf", ready.user?.displayName)
        assertEquals(7L, ready.profileVersion)
        assertEquals(120, ready.limits.maxUtteranceSeconds)
    }

    @Test
    fun start_carriesOptionsAndFormat() {
        val client = newClient()
        val c = connected(client)
        val id = client.startUtterance(UtteranceOptions(mode = "translate", lang = "so", targetLang = "en"))
        val start = c.expect("start")
        assertEquals(id, start.str("utterance_id"))
        assertEquals("translate", start.str("mode"))
        assertEquals("so", start.str("lang"))
        assertEquals("en", start.str("target_lang"))
        assertEquals("16000", start.str("sample_rate"))
        assertEquals("pcm_s16le", start.str("format"))
    }

    @Test
    fun final_isDeliveredOnce_acked_andAudioReleased() {
        val client = newClient()
        val c = connected(client)
        val id = dictate(client, c, pcm(9600, 1))
        assertEquals(mapOf(id to 9600L), client.pendingSnapshot())
        c.sendStatus(id, "transcribing")
        listener.awaitUtterance(id) { it is UtteranceState.Processing && it.stage == "transcribing" }
        c.send("""{"type":"partial","utterance_id":"$id","text":"hello wor","seq":1}""")
        assertEquals(id to "hello wor", listener.awaitIn(listener.partials, 5_000, "partial") { true })
        c.sendFinal(id, "Hello world.")
        val f = listener.awaitFinal()
        assertEquals(id, f.utteranceId)
        assertEquals("Hello world.", f.text)
        assertFalse(f.replayed)
        assertEquals(id, c.expect("ack").str("utterance_id"))
        listener.awaitUtterance(id) { it == UtteranceState.Done(inserted = true) }
        assertTrue("audio must be discarded after the final", client.pendingSnapshot().isEmpty())
        listener.assertNoFinal()
    }

    @Test
    fun duplicatedAndReplayedFinals_areNeverInsertedTwice_butAlwaysAcked() {
        val ledger = InsertionLedger(InMemoryLedgerStorage())
        val client = newClient(ledger = ledger)
        val c = connected(client)
        val id = dictate(client, c, pcm(3200, 2))
        c.sendFinal(id, "Once.")
        c.sendFinal(id, "Once.")
        c.sendFinal(id, "Once.", replayed = true)
        assertEquals("Once.", listener.awaitFinal().text)
        repeat(3) { assertEquals(id, c.expect("ack").str("utterance_id")) }
        listener.assertNoFinal()
        assertEquals(1, listener.allFinals.size)
        assertTrue(ledger.contains(id))
    }

    @Test
    fun ledger_persists_soAReplayToANewClientInstanceIsIgnored() {
        val file = tmp.newFile("ledger.txt")
        val fixedId = "6f1c4c1e-0000-4000-8000-000000000001"
        run {
            val client = newClient(ledger = InsertionLedger(FileLedgerStorage(file)), ids = listOf(fixedId).iterator())
            val c = connected(client)
            dictate(client, c, pcm(3200, 3))
            c.sendFinal(fixedId, "First time.")
            assertEquals("First time.", listener.awaitFinal().text)
            c.expect("ack")
            client.close()
        }
        // A new process (same ledger file) somehow gets the same id replayed: must not insert.
        val listener2 = RecordingListener()
        val client2 = newClient(ledger = InsertionLedger(FileLedgerStorage(file)), listener = listener2, ids = listOf(fixedId).iterator())
        client2.connect()
        val c2 = server.awaitConnection()
        c2.handshake()
        listener2.awaitConnection<ConnectionState.Ready>()
        val id = client2.startUtterance()
        assertEquals(fixedId, id)
        c2.expect("start")
        client2.sendAudio(id, pcm(3200, 3))
        client2.endUtterance(id)
        c2.collectAudioUntilEnd()
        c2.sendFinal(id, "First time.", replayed = true)
        assertEquals(id, c2.expect("ack").str("utterance_id"))
        listener2.awaitUtterance(id) { it == UtteranceState.Done(inserted = false) }
        listener2.assertNoFinal()
    }

    @Test
    fun dropAfterEnd_reconnectsAfterHalfSecond_resumes_andReplayedFinalIsInsertedOnce() {
        val client = newClient()
        val c1 = connected(client)
        val id = dictate(client, c1, pcm(6400, 4))
        c1.sendStatus(id, "transcribing")
        listener.awaitUtterance(id) { it is UtteranceState.Processing }
        c1.drop()
        val offline = listener.awaitConnection<ConnectionState.Offline>()
        assertEquals(500L, offline.retryInMs)
        assertEquals(OfflineReason.CONNECTION_LOST, offline.reason)
        listener.awaitUtterance(id) { it == UtteranceState.WaitingForServer }
        server.assertNoNewConnection(200)

        scheduler.advanceBy(499)
        server.assertNoNewConnection(200)
        scheduler.advanceBy(1)
        val c2 = server.awaitConnection()
        c2.handshake()
        assertEquals(id, c2.expect("resume").str("utterance_id"))
        c2.sendFinal(id, "Replayed text.", replayed = true)
        val f = listener.awaitFinal()
        assertEquals("Replayed text.", f.text)
        assertTrue(f.replayed)
        assertEquals(id, c2.expect("ack").str("utterance_id"))
        // A second replay (e.g. after yet another reconnect) is still ignored.
        c2.sendFinal(id, "Replayed text.", replayed = true)
        c2.expect("ack")
        listener.assertNoFinal()
        assertEquals(1, listener.allFinals.size)
    }

    @Test
    fun resumeProcessing_waitsForTheFinal() {
        val client = newClient()
        val c1 = connected(client)
        val id = dictate(client, c1, pcm(3200, 5))
        c1.drop()
        listener.awaitConnection<ConnectionState.Offline>()
        scheduler.advanceBy(500)
        val c2 = server.awaitConnection()
        c2.handshake()
        c2.expect("resume")
        c2.sendResumeResult(id, "processing")
        c2.assertSilent()
        c2.sendFinal(id, "Later.")
        assertEquals("Later.", listener.awaitFinal().text)
        c2.expect("ack")
    }

    @Test
    fun resumeUnknown_resendsStartAllAudioAndEnd_withTheSameId() {
        val client = newClient()
        val c1 = connected(client)
        val audio = pcm(16000, 6)
        val id = dictate(client, c1, audio)
        c1.drop()
        listener.awaitConnection<ConnectionState.Offline>()
        scheduler.advanceBy(500)
        val c2 = server.awaitConnection()
        c2.handshake()
        assertEquals(id, c2.expect("resume").str("utterance_id"))
        c2.sendResumeResult(id, "unknown")
        val start = c2.expect("start")
        assertEquals("same utterance_id on resend", id, start.str("utterance_id"))
        assertEquals("clean", start.str("mode"))
        val (resent, end) = c2.collectAudioUntilEnd()
        assertEquals(id, end.str("utterance_id"))
        assertArrayEquals("all buffered audio is resent", audio, resent)
        c2.sendFinal(id, "Resent and transcribed.")
        assertEquals("Resent and transcribed.", listener.awaitFinal().text)
        c2.expect("ack")
        listener.assertNoFinal()
    }

    @Test
    fun dropWhileRecording_keepsBuffering_thenResendsEverythingAndKeepsStreaming() {
        val client = newClient()
        val c1 = connected(client)
        val part1 = pcm(3200, 7)
        val part2 = pcm(3200, 8)
        val part3 = pcm(3200, 9)
        val id = client.startUtterance()
        c1.expect("start")
        client.sendAudio(id, part1)
        assertArrayEquals(part1, c1.collectAudio(part1.size))
        c1.drop()
        listener.awaitConnection<ConnectionState.Offline>()
        listener.awaitUtterance(id) { it == UtteranceState.Recording(online = false) }
        client.sendAudio(id, part2) // user is still talking while offline
        scheduler.advanceBy(500)
        val c2 = server.awaitConnection()
        c2.handshake()
        assertEquals(id, c2.expect("resume").str("utterance_id"))
        c2.sendResumeResult(id, "unknown")
        assertEquals(id, c2.expect("start").str("utterance_id"))
        assertArrayEquals(part1 + part2, c2.collectAudio(part1.size + part2.size))
        listener.awaitUtterance(id) { it == UtteranceState.Recording(online = true) }
        client.sendAudio(id, part3)
        client.endUtterance(id)
        val (rest, _) = c2.collectAudioUntilEnd()
        assertArrayEquals(part3, rest)
        c2.sendFinal(id, "Whole sentence.")
        assertEquals("Whole sentence.", listener.awaitFinal().text)
        c2.expect("ack")
    }

    @Test
    fun recordedWhileOffline_isSentOnceConnected_withoutResume() {
        server.rejectUpgradesWith = { MockResponse().setResponseCode(503) }
        val client = newClient()
        client.connect()
        val offline = listener.awaitConnection<ConnectionState.Offline>()
        assertEquals(OfflineReason.SERVER_UNAVAILABLE, offline.reason)
        val audio = pcm(6400, 10)
        val id = client.startUtterance()
        client.sendAudio(id, audio)
        client.endUtterance(id)
        listener.awaitUtterance(id) { it == UtteranceState.WaitingForServer }
        server.rejectUpgradesWith = null
        scheduler.advanceBy(500)
        val c = server.awaitConnection()
        c.handshake()
        assertEquals(id, c.expect("start").str("utterance_id"))
        val (sent, _) = c.collectAudioUntilEnd()
        assertArrayEquals(audio, sent)
        c.sendFinal(id, "Sent late.")
        assertEquals("Sent late.", listener.awaitFinal().text)
        c.expect("ack")
    }

    @Test
    fun busy_isRetriedAfterRetryAfterMs_withTheSameIdAndAudio() {
        val client = newClient()
        val c = connected(client)
        val audio = pcm(9600, 11)
        val id = dictate(client, c, audio)
        c.sendError(ErrorCodes.BUSY, id, retryable = true, retryAfterMs = 1500, message = "Queue full")
        val retrying = listener.awaitUtterance(id) { it is UtteranceState.Retrying } as UtteranceState.Retrying
        assertEquals(1500L, retrying.inMs)
        assertEquals("busy", retrying.code)
        scheduler.advanceBy(1499)
        c.assertSilent()
        scheduler.advanceBy(1)
        assertEquals("retry uses the same utterance_id", id, c.expect("start").str("utterance_id"))
        val (again, _) = c.collectAudioUntilEnd()
        assertArrayEquals(audio, again)
        // busy again, without retry_after_ms: default delay
        c.sendError(ErrorCodes.BUSY, id, retryable = true)
        listener.awaitUtterance(id) { it is UtteranceState.Retrying && it.inMs == 1000L }
        scheduler.advanceBy(1000)
        assertEquals(id, c.expect("start").str("utterance_id"))
        c.collectAudioUntilEnd()
        c.sendFinal(id, "Eventually.")
        assertEquals("Eventually.", listener.awaitFinal().text)
        c.expect("ack")
        listener.assertNoError()
    }

    @Test
    fun timeout_isRetriedOnce_thenReported() {
        val client = newClient()
        val c = connected(client)
        val id = dictate(client, c, pcm(3200, 12))
        c.sendError(ErrorCodes.TIMEOUT, id, retryable = true, retryAfterMs = 0)
        assertEquals(id, c.expect("start").str("utterance_id"))
        c.collectAudioUntilEnd()
        c.sendError(ErrorCodes.TIMEOUT, id, retryable = true, retryAfterMs = 0)
        val err = listener.awaitError()
        assertEquals(ErrorCodes.TIMEOUT, err.code)
        assertEquals(id, err.utteranceId)
        c.assertSilent()
        assertTrue(client.pendingSnapshot().isEmpty())
    }

    @Test
    fun nonRetryableError_discardsAudio_andIsReported() {
        val client = newClient()
        val c = connected(client)
        val id = dictate(client, c, pcm(3200, 13))
        c.sendError(ErrorCodes.QUOTA_EXCEEDED, id, retryable = false, message = "Daily audio limit reached.")
        val err = listener.awaitError()
        assertEquals(ErrorCodes.QUOTA_EXCEEDED, err.code)
        assertEquals("Daily audio limit reached.", err.message)
        listener.awaitUtterance(id) { it is UtteranceState.Failed }
        assertTrue(client.pendingSnapshot().isEmpty())
        // a late final for it is not inserted
        c.sendFinal(id, "Too late.")
        c.expect("ack")
        listener.assertNoFinal()
    }

    @Test
    fun givesUpVisibly_twoMinutesAfterTheUserFinished_whenServerStaysDown() {
        val client = newClient()
        val c = connected(client)
        val id = dictate(client, c, pcm(6400, 14))
        server.rejectUpgradesWith = { MockResponse().setResponseCode(503) }
        c.drop()
        listener.awaitConnection<ConnectionState.Offline>()
        scheduler.advanceBy(119_999)
        listener.assertNoError()
        assertEquals(1, client.pendingSnapshot().size)
        scheduler.advanceBy(1)
        val err = listener.awaitError()
        assertEquals(ErrorCodes.GAVE_UP, err.code)
        assertEquals(id, err.utteranceId)
        listener.awaitUtterance(id) { it is UtteranceState.Failed }
        assertTrue("audio discarded after giving up", client.pendingSnapshot().isEmpty())
        listener.assertNoFinal()
    }

    @Test
    fun reconnectBackoff_is_half_1_2_4_then_8_seconds() {
        server.rejectUpgradesWith = { MockResponse().setResponseCode(503) }
        val client = newClient()
        client.connect()
        val delays = ArrayList<Long>()
        repeat(7) {
            val off = listener.awaitConnection<ConnectionState.Offline>()
            delays.add(off.retryInMs)
            scheduler.advanceBy(off.retryInMs)
        }
        assertEquals(listOf(500L, 1_000L, 2_000L, 4_000L, 8_000L, 8_000L, 8_000L), delays)
    }

    @Test
    fun unauthorized_errorAndClose4401_stopsReconnecting() {
        val client = newClient()
        client.connect()
        val c = server.awaitConnection()
        c.expect("hello")
        c.sendError(ErrorCodes.UNAUTHORIZED, retryable = false)
        c.close(4401, "unauthorized")
        listener.awaitConnection<ConnectionState.Unauthorized>()
        scheduler.advanceBy(10 * 60_000)
        server.assertNoNewConnection(500)
        assertEquals(1, server.upgradeAttempts)
        // dictating now fails straight away instead of queueing forever
        val id = client.startUtterance()
        assertEquals(ErrorCodes.UNAUTHORIZED, listener.awaitError { it.utteranceId == id }.code)
        client.reconnectNow()
        server.assertNoNewConnection(300)
    }

    @Test
    fun closeCode4401Alone_isTreatedAsUnauthorized() {
        val client = newClient()
        val c = connected(client)
        val id = dictate(client, c, pcm(3200, 15))
        c.close(4401, "device revoked")
        listener.awaitConnection<ConnectionState.Unauthorized>()
        listener.awaitUtterance(id) { it is UtteranceState.Failed }
        scheduler.advanceBy(60_000)
        server.assertNoNewConnection(300)
    }

    @Test
    fun http401OnUpgrade_isTreatedAsUnauthorized() {
        server.rejectUpgradesWith = { MockResponse().setResponseCode(401) }
        val client = newClient()
        client.connect()
        listener.awaitConnection<ConnectionState.Unauthorized>()
        scheduler.advanceBy(60_000)
        assertEquals(1, server.upgradeAttempts)
    }

    @Test
    fun cancel_sendsCancel_andNothingIsInserted() {
        val client = newClient()
        val c = connected(client)
        val id = client.startUtterance()
        c.expect("start")
        client.sendAudio(id, pcm(3200, 16))
        c.collectAudio(3200)
        client.cancelUtterance(id)
        assertEquals(id, c.expect("cancel").str("utterance_id"))
        listener.awaitUtterance(id) { it == UtteranceState.Cancelled }
        c.sendError(ErrorCodes.CANCELLED, id, retryable = false)
        listener.assertNoError()
        listener.assertNoFinal()
        assertTrue(client.pendingSnapshot().isEmpty())
    }

    @Test
    fun silentServer_isTreatedAsDeadAfter45Seconds() {
        val client = newClient()
        val c1 = connected(client)
        c1.autoPong = false
        scheduler.advanceBy(15_000)
        c1.expect("ping")
        scheduler.advanceBy(15_000)
        c1.expect("ping")
        scheduler.advanceBy(15_000)
        val off = listener.awaitConnection<ConnectionState.Offline>()
        assertEquals(OfflineReason.NO_RESPONSE, off.reason)
        scheduler.advanceBy(off.retryInMs)
        server.awaitConnection().handshake()
        listener.awaitConnection<ConnectionState.Ready>()
    }

    @Test
    fun disconnect_closesWhenIdle_butFinishesPendingWorkFirst() {
        val client = newClient()
        val c = connected(client)
        val id = dictate(client, c, pcm(3200, 17))
        client.disconnect()
        c.assertSilent() // still open: a final is pending
        c.sendFinal(id, "Delivered before closing.")
        assertEquals("Delivered before closing.", listener.awaitFinal().text)
        c.expect("ack")
        listener.awaitConnection<ConnectionState.Idle>()
        assertTrue(c.next() is Frame.Closed)
    }
}
