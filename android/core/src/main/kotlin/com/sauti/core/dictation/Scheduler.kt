package com.sauti.core.dictation

import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ScheduledThreadPoolExecutor
import java.util.concurrent.TimeUnit

fun interface Cancellable {
    fun cancel()
}

/**
 * The serial context and clock the [DictationClient] runs on.
 *
 * All client state is touched only from tasks run by [execute] / [schedule], which must run
 * one at a time, in order. Tests replace this with a virtual-time implementation so that
 * backoff, retry_after_ms and the two-minute give-up can be checked without waiting.
 */
interface Scheduler {
    /** Monotonic milliseconds. */
    fun nowMs(): Long

    fun execute(task: Runnable)

    fun schedule(delayMs: Long, task: Runnable): Cancellable

    fun shutdown() {}
}

/** Production scheduler: one daemon thread, monotonic clock. */
class ExecutorScheduler(threadName: String = "sauti-dictation") : Scheduler {

    private val executor = ScheduledThreadPoolExecutor(1) { r ->
        Thread(r, threadName).apply { isDaemon = true }
    }.apply {
        removeOnCancelPolicy = true
        executeExistingDelayedTasksAfterShutdownPolicy = false
    }

    override fun nowMs(): Long = TimeUnit.NANOSECONDS.toMillis(System.nanoTime())

    override fun execute(task: Runnable) {
        try {
            executor.execute(task)
        } catch (e: RejectedExecutionException) {
            // shut down: drop
        }
    }

    override fun schedule(delayMs: Long, task: Runnable): Cancellable {
        return try {
            val future = executor.schedule(task, delayMs.coerceAtLeast(0), TimeUnit.MILLISECONDS)
            Cancellable { future.cancel(false) }
        } catch (e: RejectedExecutionException) {
            Cancellable { }
        }
    }

    override fun shutdown() {
        executor.shutdown()
    }
}
