package com.sauti.keyboard

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey
import com.sauti.core.ledger.LedgerStorage
import com.sauti.core.protocol.Settings

/** The pairing: server address and device token, kept in EncryptedSharedPreferences. */
class TokenStore private constructor(private val prefs: SharedPreferences) {

    data class Pairing(
        val serverUrl: String,
        val token: String,
        val deviceId: Long,
        val userName: String,
    )

    fun load(): Pairing? {
        val url = prefs.getString(KEY_URL, null) ?: return null
        val token = prefs.getString(KEY_TOKEN, null) ?: return null
        return Pairing(
            serverUrl = url,
            token = token,
            deviceId = prefs.getLong(KEY_DEVICE_ID, -1L),
            userName = prefs.getString(KEY_USER, null) ?: "",
        )
    }

    fun save(pairing: Pairing) {
        prefs.edit()
            .putString(KEY_URL, pairing.serverUrl)
            .putString(KEY_TOKEN, pairing.token)
            .putLong(KEY_DEVICE_ID, pairing.deviceId)
            .putString(KEY_USER, pairing.userName)
            .commit()
    }

    fun updateUserName(name: String) {
        prefs.edit().putString(KEY_USER, name).apply()
    }

    fun clear() {
        prefs.edit().clear().commit()
    }

    companion object {
        private const val FILE = "sauti_secure"
        private const val KEY_URL = "server_url"
        private const val KEY_TOKEN = "device_token"
        private const val KEY_DEVICE_ID = "device_id"
        private const val KEY_USER = "user_name"

        fun open(context: Context): TokenStore {
            val app = context.applicationContext
            return try {
                TokenStore(create(app))
            } catch (e: Exception) {
                // The keystore key can be lost (for example after a device restore). The old
                // token cannot be decrypted any more, so start over; the user pairs again.
                app.deleteSharedPreferences(FILE)
                TokenStore(create(app))
            }
        }

        private fun create(context: Context): SharedPreferences {
            val masterKey = MasterKey.Builder(context)
                .setKeyScheme(MasterKey.KeyScheme.AES256_GCM)
                .build()
            return EncryptedSharedPreferences.create(
                context,
                FILE,
                masterKey,
                EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
                EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
            )
        }
    }
}

/** Dictation defaults (not secret): language, mode, translation target, style. */
class SettingsStore(context: Context) {

    private val prefs: SharedPreferences =
        context.applicationContext.getSharedPreferences("sauti_settings", Context.MODE_PRIVATE)

    var inputLang: String
        get() = prefs.getString(KEY_INPUT_LANG, null)?.takeIf { it in INPUT_LANGS } ?: "auto"
        set(value) = prefs.edit().putString(KEY_INPUT_LANG, value).apply()

    var mode: String
        get() = prefs.getString(KEY_MODE, null)?.takeIf { it in MODES } ?: "clean"
        set(value) = prefs.edit().putString(KEY_MODE, value).apply()

    var targetLang: String
        get() = prefs.getString(KEY_TARGET_LANG, null)?.takeIf { it in TARGET_LANGS } ?: "en"
        set(value) = prefs.edit().putString(KEY_TARGET_LANG, value).apply()

    var style: String
        get() = prefs.getString(KEY_STYLE, null)?.takeIf { it in STYLES } ?: "neutral"
        set(value) = prefs.edit().putString(KEY_STYLE, value).apply()

    var profileVersion: Long
        get() = prefs.getLong(KEY_PROFILE_VERSION, -1L)
        set(value) = prefs.edit().putLong(KEY_PROFILE_VERSION, value).apply()

    /** Adopt the server's settings (after pairing, saving, or a profile_changed push). */
    fun applyServerSettings(settings: Settings, version: Long) {
        prefs.edit()
            .putString(KEY_INPUT_LANG, settings.inputLang.takeIf { it in INPUT_LANGS } ?: "auto")
            .putString(KEY_MODE, settings.mode.takeIf { it in MODES } ?: "clean")
            .putString(KEY_TARGET_LANG, settings.outputLang.takeIf { it in TARGET_LANGS } ?: "en")
            .putString(KEY_STYLE, settings.style.takeIf { it in STYLES } ?: "neutral")
            .putLong(KEY_PROFILE_VERSION, version)
            .apply()
    }

    companion object {
        val INPUT_LANGS = listOf("auto", "en", "so", "ar", "sw")
        val TARGET_LANGS = listOf("en", "so", "ar", "sw")
        val MODES = listOf("clean", "verbatim", "translate")
        val STYLES = listOf("neutral", "formal", "casual", "message")

        private const val KEY_INPUT_LANG = "input_lang"
        private const val KEY_MODE = "mode"
        private const val KEY_TARGET_LANG = "target_lang"
        private const val KEY_STYLE = "style"
        private const val KEY_PROFILE_VERSION = "profile_version"
    }
}

/** Ledger ids as one newline-separated string; written synchronously (called off the main thread). */
class SharedPrefsLedgerStorage(private val prefs: SharedPreferences) : LedgerStorage {

    override fun load(): List<String> =
        prefs.getString(KEY, null)?.split('\n')?.map { it.trim() }?.filter { it.isNotEmpty() } ?: emptyList()

    override fun save(ids: List<String>) {
        prefs.edit().putString(KEY, ids.joinToString("\n")).commit()
    }

    private companion object {
        const val KEY = "inserted_ids"
    }
}
