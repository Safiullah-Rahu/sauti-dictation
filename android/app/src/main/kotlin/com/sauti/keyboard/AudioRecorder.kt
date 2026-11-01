package com.sauti.keyboard

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import android.os.Process

/**
 * Microphone capture: 16 kHz, mono, PCM16 little-endian, delivered in ~100 ms frames on a
 * dedicated thread. The microphone is open only between [start] and [stop]; nothing is
 * written to disk.
 *
 * [onFrame] receives a fresh array each time (safe to keep). [onStopped] is called exactly once,
 * on the capture thread, after the last frame and after the microphone has been released.
 */
class AudioRecorder(
    private val onFrame: (ByteArray, Int) -> Unit,
    private val onStopped: (bytesRecorded: Long, error: String?) -> Unit,
) {
    @Volatile
    private var running = false
    private var thread: Thread? = null

    val isRecording: Boolean get() = running

    /** Returns false if the microphone could not be opened. Caller must hold RECORD_AUDIO. */
    @SuppressLint("MissingPermission")
    fun start(): Boolean {
        if (running) return true
        val minBuffer = AudioRecord.getMinBufferSize(SAMPLE_RATE, CHANNEL, ENCODING)
        if (minBuffer <= 0) return false
        val bufferSize = maxOf(minBuffer, FRAME_BYTES * 4)
        val record = open(MediaRecorder.AudioSource.VOICE_RECOGNITION, bufferSize)
            ?: open(MediaRecorder.AudioSource.MIC, bufferSize)
            ?: return false
        try {
            record.startRecording()
        } catch (e: IllegalStateException) {
            record.release()
            return false
        }
        if (record.recordingState != AudioRecord.RECORDSTATE_RECORDING) {
            record.release()
            return false
        }
        running = true
        thread = Thread({ captureLoop(record) }, "sauti-mic").also { it.start() }
        return true
    }

    /** Asks the capture thread to finish; [onStopped] follows within ~100 ms. */
    fun stop() {
        running = false
    }

    @SuppressLint("MissingPermission")
    private fun open(source: Int, bufferSize: Int): AudioRecord? {
        return try {
            val r = AudioRecord(source, SAMPLE_RATE, CHANNEL, ENCODING, bufferSize)
            if (r.state == AudioRecord.STATE_INITIALIZED) {
                r
            } else {
                r.release()
                null
            }
        } catch (e: IllegalArgumentException) {
            null
        } catch (e: SecurityException) {
            null
        }
    }

    private fun captureLoop(record: AudioRecord) {
        try {
            Process.setThreadPriority(Process.THREAD_PRIORITY_URGENT_AUDIO)
        } catch (_: Exception) {
        }
        var total = 0L
        var error: String? = null
        try {
            while (running) {
                val frame = ByteArray(FRAME_BYTES)
                var filled = 0
                while (running && filled < FRAME_BYTES) {
                    val n = record.read(frame, filled, FRAME_BYTES - filled)
                    if (n < 0) {
                        error = "AudioRecord.read returned $n"
                        running = false
                        break
                    }
                    if (n == 0) Thread.sleep(5)
                    filled += n
                }
                if (filled > 0) {
                    onFrame(frame, filled)
                    total += filled
                }
            }
        } catch (e: Exception) {
            error = e.javaClass.simpleName
        } finally {
            running = false
            try {
                record.stop()
            } catch (_: IllegalStateException) {
            }
            record.release()
            onStopped(total, error)
        }
    }

    companion object {
        const val SAMPLE_RATE = 16_000
        private const val CHANNEL = AudioFormat.CHANNEL_IN_MONO
        private const val ENCODING = AudioFormat.ENCODING_PCM_16BIT

        /** 100 ms of 16 kHz mono PCM16. */
        const val FRAME_BYTES = SAMPLE_RATE * 2 / 10
    }
}
