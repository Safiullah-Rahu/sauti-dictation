package com.sauti.core

import com.sauti.core.dictation.ConnectionState
import com.sauti.core.dictation.DictationError
import com.sauti.core.dictation.DictationListener
import com.sauti.core.dictation.FinalResult
import com.sauti.core.dictation.UtteranceState
import org.junit.Assert.assertNull
import org.junit.Assert.fail
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

/** Collects every callback into queues the tests can wait on. */
class RecordingListener : DictationListener {

    val connection = LinkedBlockingQueue<ConnectionState>()
    val utterance = LinkedBlockingQueue<Pair<String, UtteranceState>>()
    val finals = LinkedBlockingQueue<FinalResult>()
    val errors = LinkedBlockingQueue<DictationError>()
    val partials = LinkedBlockingQueue<Pair<String, String>>()
    val allFinals = CopyOnWriteArrayList<FinalResult>()
    val allConnectionStates = CopyOnWriteArrayList<ConnectionState>()

    override fun onConnectionState(state: ConnectionState) {
        allConnectionStates.add(state)
        connection.add(state)
    }

    override fun onUtteranceState(utteranceId: String, state: UtteranceState) {
        utterance.add(utteranceId to state)
    }

    override fun onPartial(utteranceId: String, text: String) {
        partials.add(utteranceId to text)
    }

    override fun onFinal(result: FinalResult) {
        allFinals.add(result)
        finals.add(result)
    }

    override fun onError(error: DictationError) {
        errors.add(error)
    }

    inline fun <reified T : ConnectionState> awaitConnection(timeoutMs: Long = 5_000, noinline predicate: (T) -> Boolean = { true }): T =
        awaitIn(connection, timeoutMs, "connection state ${T::class.simpleName}") { it is T && predicate(it) } as T

    fun awaitUtterance(id: String, timeoutMs: Long = 5_000, predicate: (UtteranceState) -> Boolean): UtteranceState =
        awaitIn(utterance, timeoutMs, "utterance state for $id") { it.first == id && predicate(it.second) }.second

    fun awaitFinal(timeoutMs: Long = 5_000): FinalResult = awaitIn(finals, timeoutMs, "final") { true }

    fun awaitError(timeoutMs: Long = 5_000, predicate: (DictationError) -> Boolean = { true }): DictationError =
        awaitIn(errors, timeoutMs, "error") { predicate(it) }

    fun assertNoFinal(waitMs: Long = 300) {
        assertNull("did not expect another final", finals.poll(waitMs, TimeUnit.MILLISECONDS))
    }

    fun assertNoError(waitMs: Long = 100) {
        assertNull("did not expect an error", errors.poll(waitMs, TimeUnit.MILLISECONDS))
    }

    fun <E> awaitIn(queue: LinkedBlockingQueue<E>, timeoutMs: Long, what: String, predicate: (E) -> Boolean): E {
        val deadline = System.currentTimeMillis() + timeoutMs
        val skipped = ArrayList<E>()
        while (true) {
            val left = deadline - System.currentTimeMillis()
            if (left <= 0) fail("timed out waiting for $what; saw $skipped")
            val e = queue.poll(left, TimeUnit.MILLISECONDS) ?: continue
            if (predicate(e)) return e
            skipped.add(e)
        }
    }
}
