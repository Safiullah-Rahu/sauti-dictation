package com.sauti.keyboard

import android.app.Application
import android.content.Context
import android.util.Log
import com.sauti.core.ledger.InsertionLedger
import okhttp3.OkHttpClient
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/** Process-wide singletons shared by the keyboard service and the setup screen. */
class SautiApp : Application() {

    val http: OkHttpClient by lazy {
        val userAgent = "Sauti-Android/${BuildConfig.VERSION_NAME}"
        OkHttpClient.Builder()
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(30, TimeUnit.SECONDS)
            .writeTimeout(30, TimeUnit.SECONDS)
            .addInterceptor { chain ->
                chain.proceed(chain.request().newBuilder().header("User-Agent", userAgent).build())
            }
            .build()
    }

    val settings: SettingsStore by lazy { SettingsStore(this) }

    /** Ids of finals already inserted (protocol 3.4 rule 3); survives process restarts. */
    val ledger: InsertionLedger by lazy {
        InsertionLedger(
            storage = SharedPrefsLedgerStorage(getSharedPreferences(LEDGER_PREFS, Context.MODE_PRIVATE)),
            onStorageError = { Log.w(TAG, "insertion ledger could not be saved", it) },
        )
    }

    /** Background work for REST calls (pairing, profile, settings). */
    val background: ExecutorService by lazy { Executors.newFixedThreadPool(2) }

    @Volatile
    private var tokenStoreInstance: TokenStore? = null

    /**
     * The encrypted token store. Throws if the Android keystore is unusable; callers show
     * [R.string.err_secure_storage] in that case.
     */
    fun tokenStore(): TokenStore {
        tokenStoreInstance?.let { return it }
        synchronized(this) {
            tokenStoreInstance?.let { return it }
            return TokenStore.open(this).also { tokenStoreInstance = it }
        }
    }

    companion object {
        const val TAG = "Sauti"
        private const val LEDGER_PREFS = "sauti_ledger"

        fun from(context: Context): SautiApp = context.applicationContext as SautiApp
    }
}
