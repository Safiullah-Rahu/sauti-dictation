package com.sauti.keyboard

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.provider.Settings
import android.view.View
import android.view.inputmethod.InputMethodManager
import android.widget.AdapterView
import android.widget.ArrayAdapter
import android.widget.Button
import android.widget.EditText
import android.widget.Spinner
import android.widget.TextView
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import com.sauti.core.api.ApiClient
import com.sauti.core.api.ApiException
import com.sauti.core.api.ServerUrl
import com.sauti.core.protocol.Profile
import com.sauti.core.protocol.SettingsPatch

/**
 * Setup screen: (1) microphone permission, (2) enable and choose the keyboard,
 * (3) pair with the server using a 6-digit code, (4) default language/mode/style synced to the
 * server, plus "Test connection" and "Unpair".
 */
class MainActivity : AppCompatActivity() {

    private lateinit var app: SautiApp

    private lateinit var micStatus: TextView
    private lateinit var micButton: Button
    private lateinit var kbStatus: TextView
    private lateinit var kbEnableButton: Button
    private lateinit var kbChooseButton: Button
    private lateinit var pairForm: View
    private lateinit var serverUrl: EditText
    private lateinit var pairCode: EditText
    private lateinit var deviceName: EditText
    private lateinit var pairButton: Button
    private lateinit var pairStatus: TextView
    private lateinit var pairedActions: View
    private lateinit var testButton: Button
    private lateinit var unpairButton: Button
    private lateinit var testStatus: TextView
    private lateinit var spinnerInputLang: Spinner
    private lateinit var spinnerMode: Spinner
    private lateinit var spinnerTargetLang: Spinner
    private lateinit var spinnerStyle: Spinner
    private lateinit var saveDefaultsButton: Button
    private lateinit var defaultsStatus: TextView

    private var micDeniedOnce = false
    private var busy = false

    private val requestMic = registerForActivityResult(ActivityResultContracts.RequestPermission()) { granted ->
        if (!granted) micDeniedOnce = true
        refresh()
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        app = SautiApp.from(this)
        setContentView(R.layout.activity_main)

        val scroll = findViewById<View>(R.id.main_scroll)
        ViewCompat.setOnApplyWindowInsetsListener(scroll) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime())
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            insets
        }

        micStatus = findViewById(R.id.mic_status)
        micButton = findViewById(R.id.mic_button)
        kbStatus = findViewById(R.id.kb_setup_status)
        kbEnableButton = findViewById(R.id.kb_enable_button)
        kbChooseButton = findViewById(R.id.kb_choose_button)
        pairForm = findViewById(R.id.pair_form)
        serverUrl = findViewById(R.id.server_url)
        pairCode = findViewById(R.id.pair_code)
        deviceName = findViewById(R.id.device_name)
        pairButton = findViewById(R.id.pair_button)
        pairStatus = findViewById(R.id.pair_status)
        pairedActions = findViewById(R.id.paired_actions)
        testButton = findViewById(R.id.test_button)
        unpairButton = findViewById(R.id.unpair_button)
        testStatus = findViewById(R.id.test_status)
        spinnerInputLang = findViewById(R.id.spinner_input_lang)
        spinnerMode = findViewById(R.id.spinner_mode)
        spinnerTargetLang = findViewById(R.id.spinner_target_lang)
        spinnerStyle = findViewById(R.id.spinner_style)
        saveDefaultsButton = findViewById(R.id.save_defaults_button)
        defaultsStatus = findViewById(R.id.defaults_status)

        findViewById<TextView>(R.id.version_text).text = getString(R.string.version_label, BuildConfig.VERSION_NAME)

        micButton.setOnClickListener { onMicButton() }
        kbEnableButton.setOnClickListener { startActivity(Intent(Settings.ACTION_INPUT_METHOD_SETTINGS)) }
        kbChooseButton.setOnClickListener { getSystemService(InputMethodManager::class.java)?.showInputMethodPicker() }
        pairButton.setOnClickListener { pair() }
        testButton.setOnClickListener { testConnection() }
        unpairButton.setOnClickListener { confirmUnpair() }
        saveDefaultsButton.setOnClickListener { saveDefaults() }

        if (deviceName.text.isNullOrEmpty()) deviceName.setText(defaultDeviceName())
        if (BuildConfig.DEBUG && serverUrl.text.isNullOrEmpty()) serverUrl.setText(DEBUG_SERVER_HINT)

        setUpSpinners()
        refresh()
        if (pairing() != null) loadProfile()
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    override fun onWindowFocusChanged(hasFocus: Boolean) {
        super.onWindowFocusChanged(hasFocus)
        // The keyboard picker is a dialog over this window; re-check when it closes.
        if (hasFocus) refresh()
    }

    // =============================================================================================
    // State
    // =============================================================================================

    private fun pairing(): TokenStore.Pairing? = try {
        app.tokenStore().load()
    } catch (e: Exception) {
        pairStatus.text = getString(R.string.err_secure_storage, e.javaClass.simpleName)
        null
    }

    private fun hasMic(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    private fun refresh() {
        // 1. Microphone
        if (hasMic()) {
            micStatus.text = getString(R.string.step_mic_granted)
            micButton.visibility = View.GONE
        } else {
            val permanentlyDenied = micDeniedOnce && !shouldShowRequestPermissionRationale(Manifest.permission.RECORD_AUDIO)
            micStatus.text = getString(if (micDeniedOnce) R.string.step_mic_denied else R.string.step_mic_needed)
            micButton.visibility = View.VISIBLE
            micButton.text = getString(if (permanentlyDenied) R.string.step_mic_settings_button else R.string.step_mic_button)
        }

        // 2. Keyboard
        val imm = getSystemService(InputMethodManager::class.java)
        val enabled = imm?.enabledInputMethodList?.any { it.packageName == packageName } == true
        val current = Settings.Secure.getString(contentResolver, Settings.Secure.DEFAULT_INPUT_METHOD) ?: ""
        val selected = current.startsWith("$packageName/")
        kbStatus.text = getString(
            when {
                selected -> R.string.step_kb_selected
                enabled -> R.string.step_kb_enabled
                else -> R.string.step_kb_disabled
            },
        )
        kbEnableButton.visibility = if (enabled) View.GONE else View.VISIBLE
        kbChooseButton.visibility = if (enabled && !selected) View.VISIBLE else View.GONE

        // 3. Pairing
        val p = pairing()
        if (p != null) {
            pairForm.visibility = View.GONE
            pairedActions.visibility = View.VISIBLE
            pairStatus.text = getString(R.string.step_pair_paired, p.serverUrl, p.userName.ifEmpty { "?" })
        } else {
            pairForm.visibility = View.VISIBLE
            pairedActions.visibility = View.GONE
            if (pairStatus.text.isNullOrEmpty()) pairStatus.text = getString(R.string.unpaired)
        }
        pairButton.isEnabled = !busy
        testButton.isEnabled = !busy
        unpairButton.isEnabled = !busy
        saveDefaultsButton.isEnabled = !busy
    }

    private fun onMicButton() {
        if (hasMic()) return
        val permanentlyDenied = micDeniedOnce && !shouldShowRequestPermissionRationale(Manifest.permission.RECORD_AUDIO)
        if (permanentlyDenied) {
            val intent = Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", packageName, null))
            startActivity(intent)
        } else {
            requestMic.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    // =============================================================================================
    // Pairing
    // =============================================================================================

    private fun pair() {
        val parsed = ServerUrl.parse(serverUrl.text?.toString().orEmpty(), allowCleartext = BuildConfig.DEBUG)
        if (parsed is ServerUrl.Result.Invalid) {
            pairStatus.text = UiText.serverUrlError(this, parsed.reason)
            return
        }
        val base = (parsed as ServerUrl.Result.Ok).url
        val code = pairCode.text?.toString().orEmpty().filter { it.isDigit() }
        if (code.length != 6) {
            pairStatus.text = getString(R.string.err_code_format)
            return
        }
        val name = deviceName.text?.toString()?.trim().orEmpty().ifEmpty { defaultDeviceName() }
        setBusy(true)
        pairStatus.text = getString(R.string.step_pair_working)
        runInBackground(
            work = {
                val api = ApiClient(base, app.http)
                val registration = api.claimPairing(code = code, deviceName = name, platform = "android")
                val display = registration.user.displayName?.takeIf { it.isNotBlank() } ?: registration.user.username
                app.tokenStore().save(
                    TokenStore.Pairing(
                        serverUrl = ServerUrl.display(base),
                        token = registration.deviceToken,
                        deviceId = registration.deviceId,
                        userName = display,
                    ),
                )
                // Best effort: adopt the account's defaults right away.
                try {
                    api.withToken(registration.deviceToken).getProfile()
                } catch (e: ApiException) {
                    null
                }
            },
            done = { profile ->
                pairCode.setText("")
                pairStatus.text = ""
                testStatus.text = ""
                profile?.let { applyProfile(it) }
                refresh()
            },
        )
    }

    private fun testConnection() {
        val p = pairing() ?: return
        val base = baseUrl(p) ?: return
        setBusy(true)
        testStatus.text = getString(R.string.step_pair_testing)
        runInBackground(
            work = {
                val api = ApiClient(base, app.http, p.token)
                val health = api.health()
                val profile = api.getProfile()
                health to profile
            },
            done = { (health, profile) ->
                val who = profile.user.displayName?.takeIf { it.isNotBlank() } ?: profile.user.username
                app.tokenStore().updateUserName(who)
                testStatus.text = getString(
                    if (health.status == "ok") R.string.test_ok else R.string.test_degraded,
                    health.version ?: "?",
                    who,
                )
                applyProfile(profile)
                refresh()
            },
            errorView = testStatus,
        )
    }

    private fun confirmUnpair() {
        AlertDialog.Builder(this)
            .setTitle(R.string.unpair_confirm_title)
            .setMessage(R.string.unpair_confirm_message)
            .setPositiveButton(R.string.step_pair_unpair) { _, _ -> unpair() }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun unpair() {
        val p = pairing() ?: return
        val base = baseUrl(p)
        setBusy(true)
        app.background.execute {
            // Revoke on the server if it is reachable; remove locally in every case.
            if (base != null && p.deviceId > 0) {
                try {
                    ApiClient(base, app.http, p.token).revokeDevice(p.deviceId)
                } catch (_: Exception) {
                }
            }
            try {
                app.tokenStore().clear()
            } catch (_: Exception) {
            }
            runOnUiThread {
                setBusy(false)
                testStatus.text = ""
                pairStatus.text = getString(R.string.unpaired)
                refresh()
            }
        }
    }

    // =============================================================================================
    // Defaults
    // =============================================================================================

    private class Choice(val code: String, val label: String) {
        override fun toString(): String = label
    }

    private lateinit var inputLangChoices: List<Choice>
    private lateinit var modeChoices: List<Choice>
    private lateinit var targetLangChoices: List<Choice>
    private lateinit var styleChoices: List<Choice>

    private fun setUpSpinners() {
        inputLangChoices = SettingsStore.INPUT_LANGS.map { Choice(it, UiText.languageName(this, it)) }
        modeChoices = SettingsStore.MODES.map { Choice(it, UiText.modeName(this, it)) }
        targetLangChoices = SettingsStore.TARGET_LANGS.map { Choice(it, UiText.languageName(this, it)) }
        styleChoices = SettingsStore.STYLES.map { Choice(it, UiText.styleName(this, it)) }
        bindSpinner(spinnerInputLang, inputLangChoices)
        bindSpinner(spinnerMode, modeChoices)
        bindSpinner(spinnerTargetLang, targetLangChoices)
        bindSpinner(spinnerStyle, styleChoices)
        showLocalDefaults()
    }

    private fun bindSpinner(spinner: Spinner, choices: List<Choice>) {
        val adapter = ArrayAdapter(this, android.R.layout.simple_spinner_item, choices)
        adapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item)
        spinner.adapter = adapter
        spinner.onItemSelectedListener = object : AdapterView.OnItemSelectedListener {
            override fun onItemSelected(parent: AdapterView<*>?, view: View?, position: Int, id: Long) {
                defaultsStatus.text = ""
            }

            override fun onNothingSelected(parent: AdapterView<*>?) = Unit
        }
    }

    private fun select(spinner: Spinner, choices: List<Choice>, code: String) {
        val index = choices.indexOfFirst { it.code == code }
        if (index >= 0) spinner.setSelection(index)
    }

    private fun selected(spinner: Spinner, choices: List<Choice>): String =
        choices.getOrNull(spinner.selectedItemPosition)?.code ?: choices.first().code

    private fun showLocalDefaults() {
        val s = app.settings
        select(spinnerInputLang, inputLangChoices, s.inputLang)
        select(spinnerMode, modeChoices, s.mode)
        select(spinnerTargetLang, targetLangChoices, s.targetLang)
        select(spinnerStyle, styleChoices, s.style)
    }

    private fun applyProfile(profile: Profile) {
        app.settings.applyServerSettings(profile.settings, profile.version)
        showLocalDefaults()
    }

    private fun loadProfile() {
        val p = pairing() ?: return
        val base = baseUrl(p) ?: return
        app.background.execute {
            val profile = try {
                ApiClient(base, app.http, p.token).getProfile()
            } catch (_: Exception) {
                null
            }
            if (profile != null) runOnUiThread { if (!isFinishing) applyProfile(profile) }
        }
    }

    private fun saveDefaults() {
        val patch = SettingsPatch(
            inputLang = selected(spinnerInputLang, inputLangChoices),
            mode = selected(spinnerMode, modeChoices),
            outputLang = selected(spinnerTargetLang, targetLangChoices),
            style = selected(spinnerStyle, styleChoices),
        )
        // Always keep them locally, so the keyboard uses them even before the server answers.
        val s = app.settings
        s.inputLang = patch.inputLang ?: s.inputLang
        s.mode = patch.mode ?: s.mode
        s.targetLang = patch.outputLang ?: s.targetLang
        s.style = patch.style ?: s.style

        val p = pairing()
        val base = p?.let { baseUrl(it) }
        if (p == null || base == null) {
            defaultsStatus.text = getString(R.string.defaults_need_pairing)
            return
        }
        setBusy(true)
        defaultsStatus.text = getString(R.string.defaults_saving)
        runInBackground(
            work = { ApiClient(base, app.http, p.token).patchSettings(patch) },
            done = { profile ->
                applyProfile(profile)
                defaultsStatus.text = getString(R.string.defaults_saved)
            },
            errorView = defaultsStatus,
        )
    }

    // =============================================================================================
    // Helpers
    // =============================================================================================

    private fun baseUrl(p: TokenStore.Pairing) =
        (ServerUrl.parse(p.serverUrl, allowCleartext = BuildConfig.DEBUG) as? ServerUrl.Result.Ok)?.url

    private fun setBusy(value: Boolean) {
        busy = value
        refresh()
    }

    /** Runs [work] off the main thread; shows ApiException messages in [errorView]. */
    private fun <T> runInBackground(work: () -> T, done: (T) -> Unit, errorView: TextView = pairStatus) {
        app.background.execute {
            val outcome: Result<T> = try {
                Result.success(work())
            } catch (e: Exception) {
                Result.failure(e)
            }
            runOnUiThread {
                if (isFinishing || isDestroyed) return@runOnUiThread
                setBusy(false)
                outcome.fold(
                    onSuccess = { done(it) },
                    onFailure = { e ->
                        errorView.text = when (e) {
                            is ApiException -> UiText.apiError(this, e)
                            else -> getString(R.string.err_net_other, e.message ?: e.javaClass.simpleName)
                        }
                    },
                )
            }
        }
    }

    private fun defaultDeviceName(): String {
        val fromSettings = try {
            Settings.Global.getString(contentResolver, Settings.Global.DEVICE_NAME)
        } catch (_: Exception) {
            null
        }
        if (!fromSettings.isNullOrBlank()) return fromSettings
        val manufacturer = Build.MANUFACTURER.replaceFirstChar { it.uppercase() }
        return if (Build.MODEL.startsWith(Build.MANUFACTURER, ignoreCase = true)) Build.MODEL else "$manufacturer ${Build.MODEL}"
    }

    private companion object {
        /** Emulator address of a server running on the development machine. */
        const val DEBUG_SERVER_HINT = "http://10.0.2.2:8700"
    }
}
