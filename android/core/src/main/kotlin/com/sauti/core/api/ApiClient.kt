package com.sauti.core.api

import com.sauti.core.protocol.ApiErrorBody
import com.sauti.core.protocol.DeviceRegistration
import com.sauti.core.protocol.Health
import com.sauti.core.protocol.PairingClaimRequest
import com.sauti.core.protocol.Profile
import com.sauti.core.protocol.ProtocolJson
import com.sauti.core.protocol.SettingsPatch
import kotlinx.serialization.KSerializer
import kotlinx.serialization.SerializationException
import okhttp3.HttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import java.io.IOException

/**
 * Failure of a REST call.
 *
 * [httpStatus] and [code] are set for HTTP errors (`code` from the JSON error body, e.g.
 * `invalid_code`, `rate_limited`, `unauthorized`); [network] is set for transport failures.
 */
class ApiException(
    message: String,
    val httpStatus: Int? = null,
    val code: String? = null,
    val network: NetworkProblem? = null,
    cause: Throwable? = null,
) : IOException(message, cause) {
    val isUnauthorized: Boolean get() = httpStatus == 401 || code == "unauthorized"
}

/**
 * Blocking REST client for the endpoints the Android app needs (docs/protocol.md, section 2).
 * Call it from a background thread.
 *
 * @param baseUrl server root, e.g. `https://dictate.example.lan` (see [ServerUrl.parse]).
 * @param token device token (`sdt_…`); null for the unauthenticated endpoints.
 */
class ApiClient(
    val baseUrl: HttpUrl,
    private val http: OkHttpClient,
    private val token: String? = null,
) {

    fun withToken(token: String?): ApiClient = ApiClient(baseUrl, http, token)

    /** `GET /v1/health` (no auth). */
    fun health(): Health = call("GET", "v1/health", null, Health.serializer(), auth = false)

    /** `POST /v1/pairing/claim` (no auth). Errors: 400 `invalid_code`, 429 `rate_limited`. */
    fun claimPairing(code: String, deviceName: String, platform: String = "android"): DeviceRegistration {
        val body = ProtocolJson.encode(
            PairingClaimRequest.serializer(),
            PairingClaimRequest(code = code, deviceName = deviceName, platform = platform),
        )
        return call("POST", "v1/pairing/claim", body, DeviceRegistration.serializer(), auth = false)
    }

    /** `GET /v1/me/profile`. */
    fun getProfile(): Profile = call("GET", "v1/me/profile", null, Profile.serializer(), auth = true)

    /** `PATCH /v1/me/settings` with only the non-null fields of [patch]; returns the full profile. */
    fun patchSettings(patch: SettingsPatch): Profile {
        val body = ProtocolJson.encode(SettingsPatch.serializer(), patch)
        return call("PATCH", "v1/me/settings", body, Profile.serializer(), auth = true)
    }

    /** `DELETE /v1/me/devices/{id}`: revokes a device token (used by "Unpair"). */
    fun revokeDevice(deviceId: Long) {
        call<Unit>("DELETE", "v1/me/devices/$deviceId", null, null, auth = true)
    }

    private fun <T> call(
        method: String,
        path: String,
        jsonBody: String?,
        serializer: KSerializer<T>?,
        auth: Boolean,
    ): T {
        val url = ServerUrl.resolve(baseUrl, path)
        val body: RequestBody? = jsonBody?.toRequestBody(JSON)
        val builder = Request.Builder().url(url).header("Accept", "application/json")
        when (method) {
            "GET" -> builder.get()
            "DELETE" -> builder.delete()
            else -> builder.method(method, body ?: ByteArray(0).toRequestBody(JSON))
        }
        if (auth) {
            val t = token ?: throw ApiException("Not paired", httpStatus = 401, code = "unauthorized")
            builder.header("Authorization", "Bearer $t")
        }
        val response = try {
            http.newCall(builder.build()).execute()
        } catch (e: IOException) {
            throw ApiException(e.message ?: e.javaClass.simpleName, network = NetworkErrors.classify(e), cause = e)
        }
        response.use { resp ->
            val text = try {
                resp.body?.string().orEmpty()
            } catch (e: IOException) {
                throw ApiException(e.message ?: "read failed", network = NetworkErrors.classify(e), cause = e)
            }
            if (!resp.isSuccessful) {
                val detail = try {
                    ProtocolJson.decode(ApiErrorBody.serializer(), text).error
                } catch (e: SerializationException) {
                    null
                } catch (e: IllegalArgumentException) {
                    null
                }
                throw ApiException(
                    message = detail?.message ?: "HTTP ${resp.code}",
                    httpStatus = resp.code,
                    code = detail?.code?.takeIf { it.isNotEmpty() },
                )
            }
            @Suppress("UNCHECKED_CAST")
            if (serializer == null) return Unit as T
            return try {
                ProtocolJson.decode(serializer, text)
            } catch (e: SerializationException) {
                throw ApiException("Unexpected response from server", httpStatus = resp.code, code = "bad_response", cause = e)
            } catch (e: IllegalArgumentException) {
                throw ApiException("Unexpected response from server", httpStatus = resp.code, code = "bad_response", cause = e)
            }
        }
    }

    private companion object {
        val JSON = "application/json; charset=utf-8".toMediaType()
    }
}
