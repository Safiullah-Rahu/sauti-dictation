package com.sauti.core.protocol

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/** Body of `POST /v1/pairing/claim` (docs/protocol.md, section 2.3). */
@Serializable
data class PairingClaimRequest(
    val code: String,
    @SerialName("device_name") val deviceName: String,
    val platform: String = "android",
)

/** Response of `/v1/pairing/claim` and `/v1/devices/register`. */
@Serializable
data class DeviceRegistration(
    @SerialName("device_id") val deviceId: Long,
    @SerialName("device_token") val deviceToken: String,
    val user: UserInfo = UserInfo(),
)

@Serializable
data class Settings(
    @SerialName("input_lang") val inputLang: String = "auto",
    @SerialName("output_lang") val outputLang: String = "en",
    val mode: String = "clean",
    val style: String = "neutral",
    @SerialName("history_enabled") val historyEnabled: Boolean = false,
    @SerialName("retention_days") val retentionDays: Int = 30,
)

/**
 * Body of `PATCH /v1/me/settings`: any subset of [Settings]. Null fields are omitted from the
 * JSON (ProtocolJson uses explicitNulls = false), so only the fields that are set are changed.
 */
@Serializable
data class SettingsPatch(
    @SerialName("input_lang") val inputLang: String? = null,
    @SerialName("output_lang") val outputLang: String? = null,
    val mode: String? = null,
    val style: String? = null,
    @SerialName("history_enabled") val historyEnabled: Boolean? = null,
    @SerialName("retention_days") val retentionDays: Int? = null,
)

@Serializable
data class DictionaryEntry(
    val id: Long = 0,
    val term: String = "",
    val aliases: List<String> = emptyList(),
)

/** Response of `GET /v1/me/profile` and `PATCH /v1/me/settings`. */
@Serializable
data class Profile(
    val version: Long = 0,
    val user: UserInfo = UserInfo(),
    val settings: Settings = Settings(),
    val dictionary: List<DictionaryEntry> = emptyList(),
    @SerialName("org_dictionary") val orgDictionary: List<DictionaryEntry> = emptyList(),
    val languages: List<String> = listOf("en", "so", "ar", "sw"),
    val modes: List<String> = listOf("verbatim", "clean", "translate"),
    val styles: List<String> = listOf("neutral", "formal", "casual", "message"),
)

/** Response of `GET /v1/health`. `status` is `ok` or `degraded`. */
@Serializable
data class Health(
    val status: String = "unknown",
    val version: String? = null,
)

/** REST error body: `{"error": {"code": "...", "message": "..."}}`. */
@Serializable
data class ApiErrorBody(val error: ApiErrorDetail? = null)

@Serializable
data class ApiErrorDetail(
    val code: String = "",
    val message: String? = null,
)
