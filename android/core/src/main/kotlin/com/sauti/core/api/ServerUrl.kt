package com.sauti.core.api

import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/** Parsing and normalising the server address the user types during pairing. */
object ServerUrl {

    sealed class Result {
        data class Ok(val url: HttpUrl) : Result()
        data class Invalid(val reason: Reason) : Result()
    }

    enum class Reason { EMPTY, NOT_A_URL, UNSUPPORTED_SCHEME, CLEARTEXT_NOT_ALLOWED }

    /**
     * Accepts `dictate.example.lan`, `https://dictate.example.lan/`, `http://10.0.2.2:8700` or a
     * URL with a path prefix (`https://example.org/sauti`). A missing scheme means https.
     * Query and fragment are dropped; the result has no trailing slash in its path.
     *
     * @param allowCleartext whether `http://` is accepted (debug builds only).
     */
    fun parse(input: String, allowCleartext: Boolean): Result {
        val trimmed = input.trim()
        if (trimmed.isEmpty()) return Result.Invalid(Reason.EMPTY)
        val schemeMatch = Regex("^([a-zA-Z][a-zA-Z0-9+.-]*)://").find(trimmed)
        val withScheme = if (schemeMatch == null) "https://$trimmed" else trimmed
        val scheme = schemeMatch?.groupValues?.get(1)?.lowercase() ?: "https"
        if (scheme != "http" && scheme != "https") return Result.Invalid(Reason.UNSUPPORTED_SCHEME)
        if (scheme == "http" && !allowCleartext) return Result.Invalid(Reason.CLEARTEXT_NOT_ALLOWED)
        val parsed = withScheme.toHttpUrlOrNull() ?: return Result.Invalid(Reason.NOT_A_URL)
        if (parsed.host.isEmpty()) return Result.Invalid(Reason.NOT_A_URL)
        val segments = parsed.pathSegments.filter { it.isNotEmpty() }
        val builder = parsed.newBuilder().query(null).fragment(null).encodedPath("/")
        segments.forEach { builder.addPathSegment(it) }
        return Result.Ok(builder.build())
    }

    /** `base` + relative path segments, e.g. resolve(base, "v1/dictate"). */
    fun resolve(base: HttpUrl, path: String): HttpUrl {
        val builder = base.newBuilder()
        path.trim('/').split('/').filter { it.isNotEmpty() }.forEach { builder.addPathSegment(it) }
        return builder.build()
    }

    /** Display form without a trailing slash: `https://dictate.example.lan`. */
    fun display(url: HttpUrl): String = url.toString().trimEnd('/')
}
