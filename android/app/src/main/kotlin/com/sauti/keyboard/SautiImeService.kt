package com.sauti.keyboard

import android.Manifest
import android.animation.ObjectAnimator
import android.animation.PropertyValuesHolder
import android.animation.ValueAnimator
import android.annotation.SuppressLint
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.drawable.GradientDrawable
import android.inputmethodservice.InputMethodService
import android.net.ConnectivityManager
import android.net.Network
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.text.InputType
import android.util.Log
import android.view.HapticFeedbackConstants
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.ViewConfiguration
import android.view.inputmethod.EditorInfo
import android.view.inputmethod.InputMethodManager
import android.widget.Button
import android.widget.ImageButton
import android.widget.TextView
import android.widget.Toast
import androidx.annotation.ColorRes
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import com.sauti.core.api.ApiClient
import com.sauti.core.api.ServerUrl
import com.sauti.core.dictation.ConnectionState
import com.sauti.core.dictation.DictationClient
import com.sauti.core.dictation.DictationConfig
import com.sauti.core.dictation.DictationError
import com.sauti.core.dictation.DictationListener
import com.sauti.core.dictation.FinalResult
import com.sauti.core.dictation.UtteranceOptions
import com.sauti.core.dictation.UtteranceState
import com.sauti.core.protocol.ErrorCodes
import com.sauti.core.protocol.Notices
import com.sauti.core.protocol.StatusStates
import kotlin.math.ceil

/**
 * The Sauti voice keyboard: a compact IME with a status line, a live preview, mode and
 * language chips, a large microphone button and a minimal bottom row (globe, space,
 * backspace, enter). Speech is streamed to the paired server through [DictationClient];
 * the final text is committed to the focused field at most once.
 */
class SautiImeService : InputMethodService() {

    private enum class SetupProblem { NONE, NOT_PAIRED, STORAGE_ERROR }

    private class KeyboardUi(val root: View) {
        val statusRow: View = root.findViewById(R.id.kb_status_row)
        val dot: View = root.findViewById(R.id.kb_status_dot)
        val status: TextView = root.findViewById(R.id.kb_status)
        val settings: ImageButton = root.findViewById(R.id.kb_settings)
        val partial: TextView = root.findViewById(R.id.kb_partial)
        val modeChip: TextView = root.findViewById(R.id.kb_chip_mode)
        val langChip: TextView = root.findViewById(R.id.kb_chip_lang)
        val pulse: View = root.findViewById(R.id.kb_pulse)
        val mic: ImageButton = root.findViewById(R.id.kb_mic)
        val action: Button = root.findViewById(R.id.kb_action_button)
        val globe: ImageButton = root.findViewById(R.id.kb_globe)
        val space: TextView = root.findViewById(R.id.kb_space)
        val backspace: ImageButton = root.findViewById(R.id.kb_backspace)
        val enter: ImageButton = root.findViewById(R.id.kb_enter)
        val dotDrawable = GradientDrawable().apply { shape = GradientDrawable.OVAL }
        val basePaddingBottom: Int = root.paddingBottom

        init {
            dot.background = dotDrawable
        }
    }

    private val main = Handler(Looper.getMainLooper())
    private lateinit var app: SautiApp

    // Dictation client (one per pairing)
    private var client: DictationClient? = null
    private var clientKey: String? = null
    private var setupProblem = SetupProblem.NONE
    private var connection: ConnectionState = ConnectionState.Idle
    private var offlineRetryAt = 0L
    private var maxUtteranceSeconds = 120

    // Recording
    private var recorder: AudioRecorder? = null
    private var recordingId: String? = null
    private var micDownAt = 0L
    private var startedByThisPress = false
    private var stopOnRelease = false

    // The newest utterance that is still in flight, for the status line
    private var activeId: String? = null
    private var activeState: UtteranceState? = null
    private var retryAt = 0L
    private var partialText = ""

    // Short-lived message (errors, "no speech", ...)
    private var message: String? = null
    private var messageUntil = 0L

    // Editor
    private var inputActive = false
    private var passwordField = false

    private var ui: KeyboardUi? = null
    private var pulseAnimator: ValueAnimator? = null
    private var networkCallback: ConnectivityManager.NetworkCallback? = null
    private var ticking = false

    // =============================================================================================
    // Lifecycle
    // =============================================================================================

    override fun onCreate() {
        super.onCreate()
        app = SautiApp.from(this)
        registerNetworkCallback()
    }

    override fun onCreateInputView(): View {
        stopPulse()
        val root = layoutInflater.inflate(R.layout.keyboard_view, null)
        val keyboard = KeyboardUi(root)
        ui = keyboard
        bind(keyboard)
        render()
        return root
    }

    override fun onEvaluateFullscreenMode(): Boolean = false

    override fun onStartInput(attribute: EditorInfo?, restarting: Boolean) {
        super.onStartInput(attribute, restarting)
        inputActive = true
    }

    override fun onFinishInput() {
        super.onFinishInput()
        inputActive = false
    }

    override fun onStartInputView(info: EditorInfo?, restarting: Boolean) {
        super.onStartInputView(info, restarting)
        inputActive = true
        passwordField = info != null && isPasswordField(info)
        ensureClient()?.connect()
        startTicker()
        render()
    }

    override fun onFinishInputView(finishingInput: Boolean) {
        super.onFinishInputView(finishingInput)
        // The microphone never stays open while the keyboard is hidden.
        if (isRecording()) stopRecording()
        main.removeCallbacks(repeatDelete)
        client?.disconnect()
        stopTicker()
        stopPulse()
    }

    override fun onDestroy() {
        recorder?.stop()
        recorder = null
        closeClient()
        unregisterNetworkCallback()
        main.removeCallbacksAndMessages(null)
        stopPulse()
        super.onDestroy()
    }

    // =============================================================================================
    // View wiring
    // =============================================================================================

    @SuppressLint("ClickableViewAccessibility")
    private fun bind(k: KeyboardUi) {
        k.settings.setOnClickListener { openSetup() }
        k.action.setOnClickListener { openSetup() }
        k.statusRow.setOnClickListener {
            when {
                setupProblem != SetupProblem.NONE || connection is ConnectionState.Unauthorized -> openSetup()
                connection is ConnectionState.Offline -> client?.reconnectNow()
            }
        }

        k.modeChip.setOnClickListener { haptic(it); cycleMode() }
        k.modeChip.setOnLongClickListener { haptic(it); cycleTargetLanguage(); true }
        k.langChip.setOnClickListener { haptic(it); cycleInputLanguage() }

        // Microphone: tap toggles, press-and-hold records until release.
        k.mic.setOnTouchListener { v, event -> onMicTouch(v, event) }
        // Accessibility services (TalkBack) send clicks rather than touches.
        k.mic.setOnClickListener { if (isRecording()) stopRecording() else startRecording() }

        k.globe.setOnClickListener { haptic(it); switchKeyboard() }
        k.globe.setOnLongClickListener {
            imm()?.showInputMethodPicker()
            true
        }
        k.space.setOnClickListener { haptic(it); currentInputConnection?.commitText(" ", 1) }

        k.backspace.setOnTouchListener { v, event ->
            when (event.actionMasked) {
                MotionEvent.ACTION_DOWN -> {
                    v.isPressed = true
                    haptic(v)
                    deleteOne()
                    main.postDelayed(repeatDelete, REPEAT_START_MS)
                }
                MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                    v.isPressed = false
                    main.removeCallbacks(repeatDelete)
                }
            }
            true
        }
        k.backspace.setOnClickListener { deleteOne() }
        k.enter.setOnClickListener { haptic(it); handleEnter() }

        ViewCompat.setOnApplyWindowInsetsListener(k.root) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.navigationBars())
            v.setPadding(v.paddingLeft, v.paddingTop, v.paddingRight, k.basePaddingBottom + bars.bottom)
            insets
        }
    }

    private fun onMicTouch(v: View, event: MotionEvent): Boolean {
        when (event.actionMasked) {
            MotionEvent.ACTION_DOWN -> {
                v.isPressed = true
                micDownAt = SystemClock.uptimeMillis()
                if (isRecording()) {
                    stopOnRelease = true
                    startedByThisPress = false
                } else {
                    stopOnRelease = false
                    startedByThisPress = startRecording()
                }
                if (startedByThisPress || stopOnRelease) haptic(v)
            }
            MotionEvent.ACTION_UP, MotionEvent.ACTION_CANCEL -> {
                v.isPressed = false
                val held = SystemClock.uptimeMillis() - micDownAt
                if (stopOnRelease) {
                    stopRecording()
                } else if (startedByThisPress && held >= holdToTalkMs()) {
                    stopRecording() // hold-to-talk: released
                }
                startedByThisPress = false
                stopOnRelease = false
            }
        }
        return true
    }

    private fun holdToTalkMs(): Long = maxOf(ViewConfiguration.getLongPressTimeout().toLong(), 450L)

    private val repeatDelete = object : Runnable {
        override fun run() {
            deleteOne()
            main.postDelayed(this, REPEAT_INTERVAL_MS)
        }
    }

    private val ticker = object : Runnable {
        override fun run() {
            render()
            if (ticking) main.postDelayed(this, 1_000)
        }
    }

    private val autoStop = Runnable { if (isRecording()) stopRecording() }

    private fun startTicker() {
        if (ticking) return
        ticking = true
        main.postDelayed(ticker, 1_000)
    }

    private fun stopTicker() {
        ticking = false
        main.removeCallbacks(ticker)
    }

    // =============================================================================================
    // Dictation
    // =============================================================================================

    private fun isRecording(): Boolean = recorder?.isRecording == true

    private fun hasMicPermission(): Boolean =
        ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED

    /** Returns true if the microphone is now recording. */
    private fun startRecording(): Boolean {
        if (isRecording()) return true
        if (passwordField) {
            showMessage(getString(R.string.kb_status_password))
            return false
        }
        if (!hasMicPermission()) {
            render()
            return false
        }
        val c = ensureClient()
        if (c == null || connection is ConnectionState.Unauthorized) {
            render()
            return false
        }
        val settings = app.settings
        val id = c.startUtterance(
            UtteranceOptions(mode = settings.mode, lang = settings.inputLang, targetLang = settings.targetLang),
        )
        val rec = AudioRecorder(
            onFrame = { bytes, length -> c.sendAudio(id, bytes, 0, length) },
            onStopped = { bytes, error ->
                // Called on the capture thread after the last frame, so `end` follows all audio.
                if (bytes == 0L) c.cancelUtterance(id) else c.endUtterance(id)
                main.post { onRecorderStopped(id, bytes, error) }
            },
        )
        if (!rec.start()) {
            c.cancelUtterance(id)
            showMessage(getString(R.string.kb_msg_mic_failed))
            return false
        }
        recorder = rec
        recordingId = id
        activeId = id
        activeState = UtteranceState.Recording(online = connection is ConnectionState.Ready)
        partialText = ""
        message = null
        main.removeCallbacks(autoStop)
        main.postDelayed(autoStop, maxUtteranceSeconds * 1_000L)
        render()
        return true
    }

    private fun stopRecording() {
        main.removeCallbacks(autoStop)
        recorder?.stop()
        render()
    }

    private fun onRecorderStopped(id: String, bytes: Long, error: String?) {
        if (recordingId == id) {
            recordingId = null
            recorder = null
        }
        if (error != null) Log.w(SautiApp.TAG, "microphone stopped with an error: $error")
        if (bytes == 0L) {
            showMessage(getString(if (error != null) R.string.kb_msg_mic_failed else R.string.kb_msg_nothing_recorded))
        } else if (error != null) {
            showMessage(getString(R.string.kb_msg_mic_failed))
        }
        render()
    }

    private fun insertFinal(result: FinalResult) {
        partialText = ""
        if (result.text.isEmpty()) {
            showMessage(getString(R.string.kb_msg_no_speech))
            return
        }
        val ic = currentInputConnection
        var inserted = false
        if (inputActive && ic != null) {
            val before = ic.getTextBeforeCursor(1, 0)
            val needsSpace = !before.isNullOrEmpty() && !before[before.length - 1].isWhitespace()
            val text = if (needsSpace) " " + result.text else result.text
            ic.beginBatchEdit()
            inserted = ic.commitText(text, 1)
            ic.endBatchEdit()
        }
        if (!inserted) {
            copyToClipboard(result.text)
            val msg = getString(R.string.kb_msg_copied)
            showMessage(msg)
            if (ui?.root?.isShown != true) Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
            return
        }
        when {
            Notices.TRUNCATED in result.notices -> showMessage(getString(R.string.kb_msg_truncated))
            Notices.CLEANUP_FALLBACK in result.notices || Notices.CLEANUP_UNAVAILABLE in result.notices ->
                showMessage(getString(R.string.kb_msg_cleanup_fallback))
        }
    }

    private fun copyToClipboard(text: String) {
        val cm = getSystemService(ClipboardManager::class.java) ?: return
        cm.setPrimaryClip(ClipData.newPlainText(getString(R.string.clip_label), text))
    }

    /** Creates (or reuses) the client for the current pairing; null if not paired. */
    private fun ensureClient(): DictationClient? {
        val pairing = try {
            app.tokenStore().load()
        } catch (e: Exception) {
            Log.e(SautiApp.TAG, "secure storage unavailable", e)
            setupProblem = SetupProblem.STORAGE_ERROR
            closeClient()
            return null
        }
        if (pairing == null) {
            setupProblem = SetupProblem.NOT_PAIRED
            closeClient()
            return null
        }
        val key = pairing.serverUrl + "\n" + pairing.token
        client?.let { if (clientKey == key) { setupProblem = SetupProblem.NONE; return it } }
        closeClient()
        val base = (ServerUrl.parse(pairing.serverUrl, allowCleartext = BuildConfig.DEBUG) as? ServerUrl.Result.Ok)?.url
        if (base == null) {
            setupProblem = SetupProblem.NOT_PAIRED
            return null
        }
        setupProblem = SetupProblem.NONE
        val callbacks = Callbacks()
        val c = DictationClient(
            serverUrl = base,
            token = pairing.token,
            httpClient = app.http,
            ledger = app.ledger,
            listener = callbacks,
            config = DictationConfig(clientName = "android/${BuildConfig.VERSION_NAME}"),
            callbackExecutor = ContextCompat.getMainExecutor(this),
            logger = { Log.d(SautiApp.TAG, it) },
        )
        callbacks.owner = c
        client = c
        clientKey = key
        connection = ConnectionState.Idle
        return c
    }

    private fun closeClient() {
        if (isRecording()) recorder?.stop()
        client?.close()
        client = null
        clientKey = null
        connection = ConnectionState.Idle
        activeId = null
        activeState = null
    }

    /** Listener bound to one client instance; callbacks from a replaced client are ignored. */
    private inner class Callbacks : DictationListener {
        var owner: DictationClient? = null
        private fun current(): Boolean = owner != null && owner === client

        override fun onConnectionState(state: ConnectionState) {
            if (!current()) return
            connection = state
            when (state) {
                is ConnectionState.Offline -> offlineRetryAt = SystemClock.elapsedRealtime() + state.retryInMs
                is ConnectionState.Ready -> {
                    maxUtteranceSeconds = state.limits.maxUtteranceSeconds.coerceIn(5, 600)
                    val version = state.profileVersion
                    if (version != null && version != app.settings.profileVersion) refreshProfile()
                }
                else -> Unit
            }
            render()
        }

        override fun onUtteranceState(utteranceId: String, state: UtteranceState) {
            if (!current()) return
            when (state) {
                is UtteranceState.Done, is UtteranceState.Cancelled, is UtteranceState.Failed -> {
                    if (recordingId == utteranceId && isRecording()) stopRecording()
                    if (activeId == utteranceId) {
                        activeId = null
                        activeState = null
                        partialText = ""
                    }
                }
                else -> {
                    activeId = utteranceId
                    activeState = state
                    if (state is UtteranceState.Retrying) retryAt = SystemClock.elapsedRealtime() + state.inMs
                }
            }
            render()
        }

        override fun onPartial(utteranceId: String, text: String) {
            if (!current() || utteranceId != activeId) return
            partialText = text
            render()
        }

        override fun onFinal(result: FinalResult) {
            if (!current()) return
            insertFinal(result)
            render()
        }

        override fun onError(error: DictationError) {
            if (!current()) return
            // Unauthorized without an utterance is shown by the status line instead.
            if (error.code == ErrorCodes.UNAUTHORIZED && error.utteranceId == null) return
            showMessage(UiText.dictationError(this@SautiImeService, error), long = true)
        }

        override fun onProfileChanged(version: Long) {
            if (!current()) return
            refreshProfile()
        }
    }

    /** Re-fetch the profile (settings changed on another device) and adopt its settings. */
    private fun refreshProfile() {
        val pairing = try {
            app.tokenStore().load()
        } catch (e: Exception) {
            null
        } ?: return
        val base = (ServerUrl.parse(pairing.serverUrl, allowCleartext = BuildConfig.DEBUG) as? ServerUrl.Result.Ok)?.url ?: return
        app.background.execute {
            try {
                val profile = ApiClient(base, app.http, pairing.token).getProfile()
                main.post {
                    app.settings.applyServerSettings(profile.settings, profile.version)
                    render()
                }
            } catch (e: Exception) {
                Log.w(SautiApp.TAG, "profile refresh failed: ${e.javaClass.simpleName}")
            }
        }
    }

    // =============================================================================================
    // Keys
    // =============================================================================================

    private fun deleteOne() {
        val ic = currentInputConnection ?: return
        val selected = ic.getSelectedText(0)
        if (!selected.isNullOrEmpty()) {
            ic.commitText("", 1)
        } else {
            sendDownUpKeyEvents(KeyEvent.KEYCODE_DEL)
        }
    }

    private fun handleEnter() {
        val ic = currentInputConnection ?: return
        val info = currentInputEditorInfo
        val action = (info?.imeOptions ?: 0) and EditorInfo.IME_MASK_ACTION
        val noEnterAction = info != null && (info.imeOptions and EditorInfo.IME_FLAG_NO_ENTER_ACTION) != 0
        if (info != null && !noEnterAction &&
            action != EditorInfo.IME_ACTION_NONE && action != EditorInfo.IME_ACTION_UNSPECIFIED
        ) {
            ic.performEditorAction(action)
            return
        }
        val multiLine = info != null && (info.inputType and InputType.TYPE_TEXT_FLAG_MULTI_LINE) != 0
        if (multiLine) ic.commitText("\n", 1) else sendDownUpKeyEvents(KeyEvent.KEYCODE_ENTER)
    }

    @Suppress("DEPRECATION")
    private fun switchKeyboard() {
        val switched = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            switchToNextInputMethod(false)
        } else {
            val token = window?.window?.attributes?.token
            val manager = imm()
            token != null && manager != null && manager.switchToNextInputMethod(token, false)
        }
        if (!switched) imm()?.showInputMethodPicker()
    }

    private fun imm(): InputMethodManager? = getSystemService(InputMethodManager::class.java)

    private fun cycleMode() {
        val s = app.settings
        val modes = SettingsStore.MODES
        s.mode = modes[(modes.indexOf(s.mode) + 1) % modes.size]
        if (s.mode == "translate") showMessage(getString(R.string.kb_hint_translate))
        render()
    }

    private fun cycleTargetLanguage() {
        val s = app.settings
        val targets = SettingsStore.TARGET_LANGS
        s.targetLang = targets[(targets.indexOf(s.targetLang) + 1) % targets.size]
        s.mode = "translate"
        render()
    }

    private fun cycleInputLanguage() {
        val s = app.settings
        val langs = SettingsStore.INPUT_LANGS
        s.inputLang = langs[(langs.indexOf(s.inputLang) + 1) % langs.size]
        render()
    }

    private fun openSetup() {
        val intent = Intent(this, MainActivity::class.java)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP)
        try {
            startActivity(intent)
            requestHideSelf(0)
        } catch (e: Exception) {
            Log.w(SautiApp.TAG, "could not open setup", e)
        }
    }

    private fun haptic(v: View) {
        v.performHapticFeedback(HapticFeedbackConstants.KEYBOARD_TAP)
    }

    private fun isPasswordField(info: EditorInfo): Boolean {
        val cls = info.inputType and InputType.TYPE_MASK_CLASS
        val variation = info.inputType and InputType.TYPE_MASK_VARIATION
        return when (cls) {
            InputType.TYPE_CLASS_TEXT ->
                variation == InputType.TYPE_TEXT_VARIATION_PASSWORD ||
                    variation == InputType.TYPE_TEXT_VARIATION_VISIBLE_PASSWORD ||
                    variation == InputType.TYPE_TEXT_VARIATION_WEB_PASSWORD
            InputType.TYPE_CLASS_NUMBER -> variation == InputType.TYPE_NUMBER_VARIATION_PASSWORD
            else -> false
        }
    }

    private fun registerNetworkCallback() {
        val cm = getSystemService(ConnectivityManager::class.java) ?: return
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                main.post { if (connection is ConnectionState.Offline) client?.reconnectNow() }
            }
        }
        try {
            cm.registerDefaultNetworkCallback(callback)
            networkCallback = callback
        } catch (e: RuntimeException) {
            Log.w(SautiApp.TAG, "network callback unavailable", e)
        }
    }

    private fun unregisterNetworkCallback() {
        val callback = networkCallback ?: return
        networkCallback = null
        try {
            getSystemService(ConnectivityManager::class.java)?.unregisterNetworkCallback(callback)
        } catch (_: RuntimeException) {
        }
    }

    // =============================================================================================
    // Rendering
    // =============================================================================================

    private fun showMessage(text: String, long: Boolean = false) {
        message = text
        messageUntil = SystemClock.elapsedRealtime() + if (long) 7_000 else 3_500
        render()
    }

    private fun color(@ColorRes id: Int): Int = ContextCompat.getColor(this, id)

    private fun secondsUntil(at: Long, now: Long): Int = ceil((at - now).coerceAtLeast(0) / 1000.0).toInt()

    private fun render() {
        val k = ui ?: return
        val now = SystemClock.elapsedRealtime()
        if (message != null && now >= messageUntil) message = null
        val recording = isRecording()
        val micAllowed = hasMicPermission()
        val settings = app.settings

        // Setup button replaces the microphone when dictation cannot work yet.
        val setupLabel = when {
            recording -> null
            setupProblem != SetupProblem.NONE -> getString(R.string.kb_open_setup)
            connection is ConnectionState.Unauthorized -> getString(R.string.kb_open_setup)
            !micAllowed -> getString(R.string.kb_allow_mic)
            else -> null
        }
        if (setupLabel != null) {
            k.mic.visibility = View.GONE
            k.action.visibility = View.VISIBLE
            k.action.text = setupLabel
        } else {
            k.mic.visibility = View.VISIBLE
            k.action.visibility = View.GONE
        }

        when {
            recording -> {
                k.mic.setBackgroundResource(R.drawable.bg_mic_recording)
                k.mic.setImageResource(R.drawable.ic_stop)
                k.mic.contentDescription = getString(R.string.cd_mic_stop)
                k.mic.isEnabled = true
                startPulse(k)
            }
            passwordField -> {
                k.mic.setBackgroundResource(R.drawable.bg_mic_disabled)
                k.mic.setImageResource(R.drawable.ic_mic)
                k.mic.contentDescription = getString(R.string.kb_status_password)
                k.mic.isEnabled = false
                stopPulse()
            }
            else -> {
                k.mic.setBackgroundResource(R.drawable.bg_mic_idle)
                k.mic.setImageResource(R.drawable.ic_mic)
                k.mic.contentDescription = getString(R.string.cd_mic_start)
                k.mic.isEnabled = true
                stopPulse()
            }
        }

        val modeLabel = UiText.modeLabel(this, settings.mode, settings.targetLang)
        k.modeChip.text = modeLabel
        k.modeChip.contentDescription = getString(R.string.cd_mode, modeLabel)
        k.langChip.text = UiText.languageChip(this, settings.inputLang)
        k.langChip.contentDescription = getString(R.string.cd_lang, UiText.languageName(this, settings.inputLang))

        val (statusText, dotColor) = statusLine(now, recording, micAllowed, modeLabel)
        k.status.text = statusText
        k.dotDrawable.setColor(color(dotColor))

        k.partial.text = when {
            partialText.isNotEmpty() -> tail(partialText)
            recording || activeState != null || setupLabel != null || passwordField -> ""
            else -> getString(R.string.kb_hint_idle)
        }
    }

    private fun statusLine(now: Long, recording: Boolean, micAllowed: Boolean, modeLabel: String): Pair<String, Int> {
        val conn = connection
        when (setupProblem) {
            SetupProblem.NOT_PAIRED -> return getString(R.string.kb_status_not_paired) to R.color.dot_idle
            SetupProblem.STORAGE_ERROR -> return getString(R.string.kb_status_storage_error) to R.color.dot_offline
            SetupProblem.NONE -> Unit
        }
        if (conn is ConnectionState.Unauthorized) return getString(R.string.kb_status_unauthorized) to R.color.dot_offline
        if (!micAllowed) return getString(R.string.kb_status_no_mic) to R.color.dot_idle
        if (recording) {
            val online = conn is ConnectionState.Ready
            return getString(if (online) R.string.kb_status_listening else R.string.kb_status_listening_offline) to
                R.color.kb_mic_recording
        }
        message?.let { return it to connectionDot(conn) }
        when (val s = activeState) {
            is UtteranceState.Retrying -> {
                val secs = secondsUntil(retryAt, now)
                val text = if (s.code == ErrorCodes.BUSY) {
                    getString(R.string.kb_status_retrying_busy, secs)
                } else {
                    getString(R.string.kb_status_retrying, secs)
                }
                return text to R.color.dot_connecting
            }
            is UtteranceState.Queued -> {
                val text = s.position?.let { getString(R.string.kb_status_queued_position, it) }
                    ?: getString(R.string.kb_status_queued)
                return text to R.color.dot_busy
            }
            is UtteranceState.Processing -> {
                val text = when (s.stage) {
                    StatusStates.CLEANING -> getString(R.string.kb_status_cleaning)
                    StatusStates.TRANSLATING -> getString(R.string.kb_status_translating)
                    else -> getString(R.string.kb_status_transcribing)
                }
                return text to R.color.dot_busy
            }
            is UtteranceState.Sent -> return getString(R.string.kb_status_sending) to R.color.dot_busy
            is UtteranceState.WaitingForServer, is UtteranceState.Recording ->
                if (conn !is ConnectionState.Offline) return getString(R.string.kb_status_waiting) to R.color.dot_connecting
            else -> Unit
        }
        if (passwordField) return getString(R.string.kb_status_password) to connectionDot(conn)
        return when (conn) {
            is ConnectionState.Ready -> getString(
                R.string.kb_status_ready,
                UiText.languageName(this, app.settings.inputLang),
                modeLabel,
            ) to R.color.dot_ready
            is ConnectionState.Offline -> {
                val reason = UiText.offlineReason(this, conn.reason)
                val secs = secondsUntil(offlineRetryAt, now)
                val text = if (secs > 0) {
                    getString(R.string.kb_status_offline_retry, reason, secs)
                } else {
                    getString(R.string.kb_status_offline_now, reason)
                }
                text to R.color.dot_offline
            }
            else -> getString(R.string.kb_status_connecting) to R.color.dot_connecting
        }
    }

    private fun connectionDot(conn: ConnectionState): Int = when (conn) {
        is ConnectionState.Ready -> R.color.dot_ready
        is ConnectionState.Offline, is ConnectionState.Unauthorized -> R.color.dot_offline
        else -> R.color.dot_connecting
    }

    private fun tail(text: String): String = if (text.length <= PREVIEW_CHARS) text else "…" + text.takeLast(PREVIEW_CHARS)

    private fun startPulse(k: KeyboardUi) {
        if (pulseAnimator != null) return
        k.pulse.visibility = View.VISIBLE
        if (!ValueAnimator.areAnimatorsEnabled()) {
            // System animations are off: keep a static, clearly visible ring instead.
            k.pulse.alpha = 0.7f
            k.pulse.scaleX = 1f
            k.pulse.scaleY = 1f
            return
        }
        val animator = ObjectAnimator.ofPropertyValuesHolder(
            k.pulse,
            PropertyValuesHolder.ofFloat(View.SCALE_X, 0.75f, 1.1f),
            PropertyValuesHolder.ofFloat(View.SCALE_Y, 0.75f, 1.1f),
            PropertyValuesHolder.ofFloat(View.ALPHA, 0.9f, 0f),
        )
        animator.duration = 1_000
        animator.repeatCount = ValueAnimator.INFINITE
        animator.repeatMode = ValueAnimator.RESTART
        animator.start()
        pulseAnimator = animator
    }

    private fun stopPulse() {
        pulseAnimator?.cancel()
        pulseAnimator = null
        ui?.pulse?.visibility = View.INVISIBLE
    }

    private companion object {
        const val REPEAT_START_MS = 400L
        const val REPEAT_INTERVAL_MS = 50L
        const val PREVIEW_CHARS = 140
    }
}
