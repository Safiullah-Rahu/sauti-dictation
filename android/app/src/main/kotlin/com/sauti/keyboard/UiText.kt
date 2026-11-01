package com.sauti.keyboard

import android.content.Context
import com.sauti.core.api.ApiException
import com.sauti.core.api.NetworkProblem
import com.sauti.core.api.ServerUrl
import com.sauti.core.dictation.DictationError
import com.sauti.core.dictation.OfflineReason
import com.sauti.core.protocol.ErrorCodes

/** Maps protocol codes and client states to user-facing strings. */
object UiText {

    fun languageName(context: Context, code: String): String = when (code) {
        "auto" -> context.getString(R.string.lang_auto)
        "en" -> context.getString(R.string.lang_en)
        "so" -> context.getString(R.string.lang_so)
        "ar" -> context.getString(R.string.lang_ar)
        "sw" -> context.getString(R.string.lang_sw)
        else -> code
    }

    fun languageChip(context: Context, code: String): String =
        if (code == "auto") context.getString(R.string.chip_lang_auto) else code.uppercase()

    fun modeLabel(context: Context, mode: String, targetLang: String): String = when (mode) {
        "verbatim" -> context.getString(R.string.mode_verbatim)
        "translate" -> context.getString(R.string.mode_translate_to, targetLang.uppercase())
        else -> context.getString(R.string.mode_clean)
    }

    fun modeName(context: Context, mode: String): String = when (mode) {
        "verbatim" -> context.getString(R.string.mode_verbatim)
        "translate" -> context.getString(R.string.mode_translate)
        else -> context.getString(R.string.mode_clean)
    }

    fun styleName(context: Context, style: String): String = when (style) {
        "formal" -> context.getString(R.string.style_formal)
        "casual" -> context.getString(R.string.style_casual)
        "message" -> context.getString(R.string.style_message)
        else -> context.getString(R.string.style_neutral)
    }

    fun offlineReason(context: Context, reason: OfflineReason): String = context.getString(
        when (reason) {
            OfflineReason.HOST_NOT_FOUND -> R.string.offline_host_not_found
            OfflineReason.SERVER_UNAVAILABLE -> R.string.offline_server_unavailable
            OfflineReason.TIMEOUT -> R.string.offline_timeout
            OfflineReason.CERTIFICATE -> R.string.offline_certificate
            OfflineReason.CLEARTEXT_BLOCKED -> R.string.offline_cleartext
            OfflineReason.CONNECTION_LOST -> R.string.offline_connection_lost
            OfflineReason.NO_RESPONSE -> R.string.offline_no_response
            OfflineReason.HANDSHAKE_FAILED -> R.string.offline_handshake
            OfflineReason.SERVER_CLOSED -> R.string.offline_server_closed
            OfflineReason.HTTP_ERROR -> R.string.offline_http_error
        },
    )

    fun dictationError(context: Context, error: DictationError): String = when (error.code) {
        ErrorCodes.GAVE_UP -> context.getString(R.string.err_gave_up)
        ErrorCodes.QUOTA_EXCEEDED -> error.message ?: context.getString(R.string.err_quota)
        ErrorCodes.TOO_LONG -> error.message ?: context.getString(R.string.err_too_long)
        ErrorCodes.BAD_REQUEST -> context.getString(R.string.err_bad_request)
        ErrorCodes.UNAUTHORIZED -> context.getString(R.string.err_unauthorized)
        ErrorCodes.TIMEOUT -> context.getString(R.string.err_timeout)
        ErrorCodes.UNAVAILABLE -> error.message ?: context.getString(R.string.err_unavailable)
        ErrorCodes.NO_AUDIO -> context.getString(R.string.kb_msg_nothing_recorded)
        else -> error.message ?: context.getString(R.string.err_generic, error.code)
    }

    fun apiError(context: Context, e: ApiException): String {
        e.network?.let { problem ->
            return when (problem) {
                NetworkProblem.HOST_NOT_FOUND -> context.getString(R.string.err_net_host)
                NetworkProblem.UNREACHABLE -> context.getString(R.string.err_net_unreachable)
                NetworkProblem.TIMEOUT -> context.getString(R.string.err_net_timeout)
                NetworkProblem.CERTIFICATE -> context.getString(R.string.err_net_certificate)
                NetworkProblem.CLEARTEXT_BLOCKED -> context.getString(R.string.err_net_cleartext)
                NetworkProblem.OTHER -> context.getString(R.string.err_net_other, e.message ?: "")
            }
        }
        return when {
            e.code == "invalid_code" -> context.getString(R.string.err_invalid_code)
            e.code == "rate_limited" || e.httpStatus == 429 -> context.getString(R.string.err_rate_limited)
            e.isUnauthorized -> context.getString(R.string.err_unauthorized_api)
            e.code == "bad_response" -> context.getString(R.string.err_bad_response)
            else -> context.getString(R.string.err_http, e.httpStatus ?: 0, e.message ?: "")
        }
    }

    fun serverUrlError(context: Context, reason: ServerUrl.Reason): String = context.getString(
        when (reason) {
            ServerUrl.Reason.EMPTY -> R.string.err_server_url_empty
            ServerUrl.Reason.NOT_A_URL -> R.string.err_server_url_invalid
            ServerUrl.Reason.UNSUPPORTED_SCHEME -> R.string.err_server_url_scheme
            ServerUrl.Reason.CLEARTEXT_NOT_ALLOWED -> R.string.err_server_url_cleartext
        },
    )
}
