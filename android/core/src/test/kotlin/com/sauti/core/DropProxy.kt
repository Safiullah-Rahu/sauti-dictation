package com.sauti.core

import java.io.Closeable
import java.io.IOException
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CopyOnWriteArrayList
import kotlin.concurrent.thread

/**
 * A transparent TCP proxy in front of MockWebServer whose connections the test can cut
 * abruptly (TCP reset), which is what a network change or a crashed server looks like to the
 * client. MockWebServer cannot do this itself for upgraded WebSocket connections.
 */
class DropProxy(private val targetHost: InetAddress, private val targetPort: Int) : Closeable {

    private val server = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
    private val sockets = CopyOnWriteArrayList<Socket>()

    @Volatile
    private var closed = false

    val port: Int get() = server.localPort

    init {
        thread(isDaemon = true, name = "drop-proxy-accept") { acceptLoop() }
    }

    private fun acceptLoop() {
        while (!closed) {
            val client = try {
                server.accept()
            } catch (e: IOException) {
                return
            }
            val upstream = try {
                Socket(targetHost, targetPort)
            } catch (e: IOException) {
                closeQuietly(client)
                continue
            }
            client.tcpNoDelay = true
            upstream.tcpNoDelay = true
            sockets += client
            sockets += upstream
            pipe(client, upstream)
            pipe(upstream, client)
        }
    }

    private fun pipe(from: Socket, to: Socket) = thread(isDaemon = true, name = "drop-proxy-pipe") {
        val buffer = ByteArray(16 * 1024)
        try {
            val input = from.getInputStream()
            val output = to.getOutputStream()
            while (true) {
                val n = input.read(buffer)
                if (n < 0) break
                output.write(buffer, 0, n)
                output.flush()
            }
        } catch (_: IOException) {
        } finally {
            closeQuietly(from)
            closeQuietly(to)
        }
    }

    /** Resets every open connection. */
    fun dropAll() {
        val open = sockets.toList()
        sockets.clear()
        for (s in open) {
            try {
                s.setSoLinger(true, 0)
            } catch (_: Exception) {
            }
            closeQuietly(s)
        }
    }

    override fun close() {
        closed = true
        closeQuietly(server)
        dropAll()
    }

    private fun closeQuietly(c: Closeable) {
        try {
            c.close()
        } catch (_: Exception) {
        }
    }
}
