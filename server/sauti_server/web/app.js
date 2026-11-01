"use strict";
(() => {
  const { api, h, $, icon, platformIcon, toast, when, initials, options, bar, dialog, tabs, setToken, LANGS, MODES, STYLES } = Sauti;
  const TARGETS = { en: "English", so: "Somali", ar: "Arabic", sw: "Swahili" };
  let profile = null;

  // Segmented control bound to a value; calls onPick(key) when the person chooses.
  function segmented(el, map, value, onPick) {
    el.replaceChildren(...Object.entries(map).map(([k, v]) => h("button", { type: "button", "aria-pressed": k === value ? "true" : "false",
      onclick: ev => { el.querySelectorAll("button").forEach(b => b.setAttribute("aria-pressed", b === ev.currentTarget ? "true" : "false")); onPick(k); } }, v)));
  }

  async function save(values, note = "Saved") {
    try { await api("PATCH", "/v1/me/settings", values); toast(note); await loadProfile(); } catch (e) { toast(e.message); }
  }

  async function loadProfile() {
    profile = await api("GET", "/v1/me/profile");
    const s = profile.settings;
    $("#whoami").textContent = profile.user.display_name;
    $("#me-avatar").textContent = initials(profile.user.display_name);
    $("#me-sub").textContent = "@" + profile.user.username;
    $("#admin-link").classList.toggle("hidden", profile.user.role !== "admin");
    segmented($("#s-mode"), MODES, s.mode, k => save({ mode: k }));
    segmented($("#s-style"), STYLES, s.style, k => save({ style: k }));
    options($("#s-input"), LANGS, s.input_lang);
    options($("#s-output"), TARGETS, s.output_lang);
    $("#s-history").checked = s.history_enabled;
    $("#s-retention").value = s.retention_days;
    const lim = profile.limits;
    $("#s-usage").replaceChildren(h("div", { class: "meter" },
      h("div", { class: "row" }, h("span", {}, "Audio dictated today"), h("b", {}, `${lim.used_today_minutes} of ${lim.daily_audio_minutes} min`)),
      bar((lim.used_today_minutes / Math.max(1, lim.daily_audio_minutes)) * 100)),
      h("p", { class: "muted small" }, "Limits are set by your administrator and reset at midnight UTC."));
    if (!dictMode) {
      dictMode = s.mode;
      segmented($("#d-mode"), MODES, dictMode, k => { dictMode = k; $("#d-target-wrap").classList.toggle("hidden", k !== "translate"); });
      options($("#d-lang"), LANGS, s.input_lang);
      options($("#d-target"), TARGETS, s.output_lang);
      $("#d-target-wrap").classList.toggle("hidden", dictMode !== "translate");
    }
    renderTerms();
  }

  function chip(t, onRemove) {
    return h("span", { class: "chip" }, h("b", {}, t.term), t.aliases.length ? h("span", { class: "alias" }, "· " + t.aliases.join(", ")) : null,
      onRemove ? h("button", { "aria-label": `Remove ${t.term}`, onclick: onRemove }, icon("x", "sm")) : h("span", {}, " "));
  }

  function renderTerms() {
    $("#terms").replaceChildren(...profile.dictionary.map(t => chip(t, async () => { await api("DELETE", `/v1/me/dictionary/${t.id}`); await loadProfile(); })));
    if (!profile.dictionary.length) $("#terms").append(h("div", { class: "empty" }, icon("book"), h("div", {}, "No personal terms yet.")));
    $("#org-terms").replaceChildren(...profile.org_dictionary.map(t => chip(t, null)));
    if (!profile.org_dictionary.length) $("#org-terms").append(h("span", { class: "muted small" }, "None."));
  }

  async function copy(text) {
    try { await navigator.clipboard.writeText(text); toast("Copied"); } catch { toast("Copy failed"); }
  }

  async function loadHistory() {
    $("#history-off").classList.toggle("hidden", profile.settings.history_enabled);
    const { items } = await api("GET", "/v1/me/history?limit=100");
    $("#history").replaceChildren(...items.map(it => h("div", { class: "list-item" },
      h("span", { class: "tile-icon accent" }, icon(it.mode === "translate" ? "globe" : "mic", "sm")),
      h("div", { class: "grow" }, h("div", { dir: "auto" }, it.text),
        h("div", { class: "meta" }, `${when(it.created_at)} · ${it.mode} · ${it.target_lang ? `${it.lang} → ${it.target_lang}` : it.lang || "—"}`)),
      h("button", { class: "btn ghost icon-only", "aria-label": "Copy", onclick: () => copy(it.text) }, icon("copy", "sm")),
      h("button", { class: "btn ghost icon-only", "aria-label": "Delete", onclick: async () => { await api("DELETE", `/v1/me/history/${it.id}`); loadHistory(); } }, icon("trash", "sm")))));
    if (!items.length) $("#history").append(h("div", { class: "empty" }, icon("history"), h("div", {}, "Nothing saved.")));
  }

  async function loadDevices() {
    const { items } = await api("GET", "/v1/me/devices");
    $("#devices").replaceChildren(...items.map(d => h("div", { class: "list-item" },
      h("span", { class: "tile-icon" }, platformIcon(d.platform)),
      h("div", { class: "grow" }, h("div", { class: "title" }, d.name, d.current ? h("span", { class: "pill ok" }, " this browser") : null),
        h("div", { class: "meta" }, `${d.platform} · added ${when(d.created_at)} · seen ${when(d.last_seen)}`)),
      h("button", { class: "btn sm danger", onclick: async () => {
        if (!(await dialog({ title: `Remove ${d.name}?`, body: "It will be signed out and must sign in or pair again.", confirm: "Remove", danger: true }))) return;
        await api("DELETE", `/v1/me/devices/${d.id}`); loadDevices();
      } }, "Remove"))));
    if (!items.length) $("#devices").append(h("div", { class: "empty" }, icon("laptop"), h("div", {}, "No devices yet. Install the desktop app or pair a phone.")));
  }

  // ---------------------------------------------------------------- dictation test
  let dictMode = null;
  let recording = false;
  let started = 0;
  let tick = null;
  let partialEl = null;
  const out = $("#d-out");
  const status = (t, cls) => $("#d-status").replaceChildren(cls ? h("span", { class: "dot " + cls }) : "", t);
  const STATE = { recording: ["Listening…", "rec"], queued: ["Waiting for the server…", "warn"], transcribing: ["Transcribing…", "warn"],
    cleaning: ["Cleaning up…", "warn"], translating: ["Translating…", "warn"], processing: ["Processing…", "warn"], offline: ["Disconnected", "bad"] };
  const NOTICE = {
    cleanup_fallback: "Safe cleanup used (the model changed a name, number or negation)",
    cleanup_unavailable: "Cleanup model offline — basic cleanup",
    translation_unverified: "Check this translation",
    not_translated: "Not translated: the recogniser was unsure",
    low_confidence: "Low confidence — please check",
    no_speech: "No speech detected",
    truncated: "Cut at the length limit",
  };
  const ring = $("#mic-ring");
  const rec = new SautiRecorder({
    onState: (s, pos) => {
      if (recording && s !== "recording") return;
      const [label, cls] = STATE[s] || [s, "warn"];
      status(s === "queued" && pos ? `In queue (position ${pos})…` : label, cls);
    },
    onLevel: lv => ring.style.setProperty("--level", String(0.62 + Math.min(1, lv * 3) * 0.38)),
    onPartial: t => {
      $("#d-empty")?.remove();
      if (!partialEl) { partialEl = h("div", { class: "result partial", dir: "auto" }); out.prepend(partialEl); }
      partialEl.textContent = t;
    },
    onFinal: m => {
      partialEl?.remove();
      partialEl = null;
      $("#d-empty")?.remove();
      const notes = (m.notices || []).map(n => NOTICE[n]).filter(Boolean);
      const t = m.timings_ms || {};
      if (m.text) {
        out.prepend(h("div", { class: "result" },
          h("div", { class: "text", dir: "auto" }, m.text),
          h("div", { class: "meta" },
            h("span", { class: "pill" }, m.target_lang ? `${m.lang} → ${m.target_lang}` : (m.lang || "?")),
            h("span", { class: "pill grey" }, m.mode),
            h("span", { class: "pill grey" }, `${((t.total || 0) / 1000).toFixed(1)} s on the server`),
            ...notes.map(n => h("span", { class: "pill warn" }, n)),
            h("span", { class: "spacer" }),
            h("button", { class: "btn sm ghost", onclick: () => copy(m.text) }, icon("copy", "sm"), "Copy"))));
      }
      status(m.text ? "Done" : notes[0] || "Done", m.text ? "ok" : "warn");
    },
    onError: m => { partialEl?.remove(); partialEl = null; status(m.message || m.code, "bad"); },
  });

  $("#mic").addEventListener("click", async () => {
    const mic = $("#mic");
    if (!recording) {
      try {
        await rec.start({ mode: dictMode, lang: $("#d-lang").value, target_lang: $("#d-target").value });
        recording = true;
        started = Date.now();
        mic.classList.add("recording"); ring.classList.add("recording");
        mic.setAttribute("aria-label", "Stop dictation");
        tick = setInterval(() => { $("#d-timer").textContent = ((Date.now() - started) / 1000).toFixed(0) + " s"; }, 250);
      } catch (e) { status(e.message || "Microphone unavailable", "bad"); }
    } else {
      recording = false;
      clearInterval(tick);
      $("#d-timer").textContent = "";
      mic.classList.remove("recording"); ring.classList.remove("recording");
      mic.setAttribute("aria-label", "Start dictation");
      await rec.stop();
    }
  });
  $("#d-clear").addEventListener("click", () => out.replaceChildren(h("div", { class: "empty", id: "d-empty" }, icon("mic"), h("div", {}, "Your dictations appear here."))));

  // ---------------------------------------------------------------- forms
  $("#s-input").addEventListener("change", e => save({ input_lang: e.target.value }));
  $("#s-output").addEventListener("change", e => save({ output_lang: e.target.value }));
  $("#s-history-save").addEventListener("click", async () => {
    const on = $("#s-history").checked;
    if (!on && profile.settings.history_enabled &&
        !(await dialog({ title: "Turn history off?", body: "Everything already saved will be deleted.", confirm: "Turn off and delete", danger: true }))) return;
    save({ history_enabled: on, retention_days: Number($("#s-retention").value) }, "History settings saved");
  });
  $("#password").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    try { await api("POST", "/v1/me/password", { current_password: f.get("current_password"), new_password: f.get("new_password") }); ev.target.reset(); toast("Password changed"); }
    catch (e) { toast(e.message); }
  });
  $("#new-term").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    const aliases = String(f.get("aliases") || "").split(",").map(s => s.trim()).filter(Boolean);
    try { await api("POST", "/v1/me/dictionary", { term: f.get("term"), aliases }); ev.target.reset(); await loadProfile(); toast("Term added"); } catch (e) { toast(e.message); }
  });
  $("#clear-history").addEventListener("click", async () => {
    if (!(await dialog({ title: "Delete your entire history?", body: "This cannot be undone.", confirm: "Delete all", danger: true }))) return;
    await api("DELETE", "/v1/me/history"); loadHistory();
  });

  let pairTimer = null;
  $("#pair").addEventListener("click", async () => {
    const r = await api("POST", "/v1/pairing/codes");
    $("#pair-code").textContent = r.code.slice(0, 3) + " " + r.code.slice(3);
    $("#pair-server").textContent = r.server_url;
    $("#pair-out").classList.remove("hidden");
    const circ = 2 * Math.PI * 23;
    const prog = $("#pair-ring");
    prog.style.strokeDasharray = String(circ);
    clearInterval(pairTimer);
    const total = Math.max(1, r.expires_at - Date.now() / 1000);
    const update = () => {
      const left = Math.max(0, Math.round(r.expires_at - Date.now() / 1000));
      prog.style.strokeDashoffset = String(circ * (1 - left / total));
      $("#pair-expiry").textContent = left ? `Expires in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : "Expired — create a new code";
      if (!left) clearInterval(pairTimer);
    };
    update();
    pairTimer = setInterval(update, 1000);
  });
  $("#pair-copy").addEventListener("click", () => copy($("#pair-server").textContent));

  async function start() {
    if (!Sauti.token()) { $("#shell").classList.add("hidden"); $("#login").classList.remove("hidden"); return; }
    try { await loadProfile(); } catch { setToken(null); return start(); }
    $("#login").classList.add("hidden");
    $("#shell").classList.remove("hidden");
    tabs($("#tabs"), name => {
      if (name === "history") loadHistory();
      if (name === "devices") loadDevices();
    });
  }

  Sauti.decorate();
  $("#login-form").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    try { const r = await api("POST", "/v1/auth/login", { username: f.get("username"), password: f.get("password") }); setToken(r.token); start(); }
    catch (e) { $("#login-error").textContent = e.message; }
  });
  $("#logout").addEventListener("click", async () => { try { await api("POST", "/v1/auth/logout"); } catch {} setToken(null); location.reload(); });
  start();
})();
