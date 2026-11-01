package com.sauti.core

import com.sauti.core.dictation.Cancellable
import com.sauti.core.dictation.Scheduler
import java.util.PriorityQueue
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * Serial executor with a virtual clock. [execute] runs tasks on one real thread (network
 * callbacks still arrive in real time); [schedule]d tasks only run when the test calls
 * [advanceBy], in due-time order, each to completion, so timer behaviour is deterministic.
 */
class VirtualScheduler : Scheduler {

    private class Entry(val due: Long, val seq: Long, val task: Runnable)

    private val executor = Executors.newSingleThreadExecutor { r -> Thread(r, "virtual-scheduler").apply { isDaemon = true } }
    private val lock = Any()
    private val queue = PriorityQueue<Entry>(compareBy<Entry>({ it.due }, { it.seq }))
    private var seq = 0L

    @Volatile
    private var now = 0L

    override fun nowMs(): Long = now

    override fun execute(task: Runnable) {
        executor.execute(task)
    }

    override fun schedule(delayMs: Long, task: Runnable): Cancellable {
        val entry = synchronized(lock) {
            Entry(now + delayMs.coerceAtLeast(0), seq++, task).also { queue.add(it) }
        }
        return Cancellable { synchronized(lock) { queue.remove(entry) } }
    }

    /** Moves the clock forward, running every timer that falls due on the way. */
    fun advanceBy(ms: Long) {
        drain()
        val target = now + ms
        while (true) {
            val next = synchronized(lock) {
                val head = queue.peek()
                if (head != null && head.due <= target) queue.poll() else null
            } ?: break
            now = next.due
            executor.submit(next.task).get(10, TimeUnit.SECONDS)
            drain()
        }
        now = target
        drain()
    }

    /** Waits until every task already queued on the executor has run. */
    fun drain() {
        executor.submit { }.get(10, TimeUnit.SECONDS)
    }

    fun pendingTimers(): Int = synchronized(lock) { queue.size }

    override fun shutdown() {
        executor.shutdownNow()
    }
}
