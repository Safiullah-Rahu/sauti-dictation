package com.sauti.core.api

import java.io.InterruptedIOException
import java.net.ConnectException
import java.net.NoRouteToHostException
import java.net.SocketTimeoutException
import java.net.UnknownHostException
import java.net.UnknownServiceException
import java.security.cert.CertificateException
import javax.net.ssl.SSLException
import javax.net.ssl.SSLHandshakeException
import javax.net.ssl.SSLPeerUnverifiedException

/** Coarse classification of transport failures, used for user-facing messages. */
enum class NetworkProblem {
    /** DNS lookup failed: the server name is wrong or there is no network. */
    HOST_NOT_FOUND,

    /** Connection refused / no route: the server is down or not reachable from here. */
    UNREACHABLE,

    /** Connect or read timed out. */
    TIMEOUT,

    /** TLS failed, typically because the server certificate is not trusted on this device. */
    CERTIFICATE,

    /** Plain http:// was blocked by the Android network security policy. */
    CLEARTEXT_BLOCKED,

    /** Anything else (connection reset, protocol error, ...). */
    OTHER,
}

object NetworkErrors {

    fun classify(t: Throwable): NetworkProblem {
        var cur: Throwable? = t
        var depth = 0
        while (cur != null && depth < 8) {
            when (cur) {
                is UnknownHostException -> return NetworkProblem.HOST_NOT_FOUND
                is SSLHandshakeException, is SSLPeerUnverifiedException, is CertificateException ->
                    return NetworkProblem.CERTIFICATE
                is SSLException -> return NetworkProblem.CERTIFICATE
                is UnknownServiceException ->
                    if (cur.message?.contains("CLEARTEXT", ignoreCase = true) == true) {
                        return NetworkProblem.CLEARTEXT_BLOCKED
                    }
                is ConnectException, is NoRouteToHostException -> return NetworkProblem.UNREACHABLE
                is SocketTimeoutException -> return NetworkProblem.TIMEOUT
                is InterruptedIOException ->
                    if (cur.message?.contains("timeout", ignoreCase = true) == true) {
                        return NetworkProblem.TIMEOUT
                    }
            }
            cur = cur.cause
            depth++
        }
        return NetworkProblem.OTHER
    }
}
