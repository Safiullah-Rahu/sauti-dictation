"use strict";
(() => {
  const { api, h, $, toast, when, options, tabs, setToken, LANGS, MODES, STYLES } = Sauti;
  const TARGETS = { en: "English", so: "Somali", ar: "Arabic", sw: "Swahili" };
  let profile = null;

  function head(table, cols) {
    table.replaceChildren(h("thead", {}, h("tr", {}, cols.map(c => h("th", {}, c)))));
    const body = h("tbody");
    table.append(body);
    return body;
  }

  async function loadProfile() {
    profile = await api("GET", "/v1/me/profile");
    const s = profile.settings;
    $("#whoami").textContent = profile.user.display_name;
    $("#admin-link").classList.toggle("hidden", profile.user.role !== "admin");
    const f = $("#settings");
    options(f.mode, MODES, s.mode);
    options(f.input_lang, LANGS, s.input_lang);
    options(f.output_lang, TARGETS, s.output_lang);
    options(f.style, STYLES, s.style);
    const hs = $("#history-settings");
    hs.history_enabled.checked = s.history_enabled;
    hs.retention_days.value = s.retention_days;
    options($("#d-mode"), MODES, s.mode);
    options($("#d-lang"), LANGS, s.input_lang);
    options($("#d-target"), TARGETS, s.output_lang);
    $("#d-target-wrap").classList.toggle("hidden", $("#d-mode").value !== "translate");
    renderTerms();
  }

  function renderTerms() {
    const b = head($("#terms"), ["Term", "Sounds like", ""]);
    for (const t of profile.dictionary) b.append(h("tr", {}, h("td", {}, h("strong", {}, t.term)), h("td", { class: "small" }, t.aliases.join(", ") || "—"),
      h("td", {}, h("button", { class: "btn small danger", onclick: async () => { await api("DELETE", `/v1/me/dictionary/${t.id}`); await loadProfile(); } }, "Remove"))));
    if (!profile.dictionary.length) b.append(h("tr", {}, h("td", { colspan: 3, class: "muted" }, "No personal terms yet.")));
    const o = head($("#org-terms"), ["Term", "Sounds like"]);
    for (const t of profile.org_dictionary) o.append(h("tr", {}, h("td", {}, t.term), h("td", { class: "small" }, t.aliases.join(", ") || "—")));
    if (!profile.org_dictionary.length) o.append(h("tr", {}, h("td", { colspan: 2, class: "muted" }, "None.")));
  }

  async function loadHistory() {
    $("#history-off").classList.toggle("hidden", profile.settings.history_enabled);
    const { items } = await api("GET", "/v1/me/history?limit=100");
    const b = head($("#history"), ["When", "Mode", "Language", "Text", ""]);
    for (const it of items) b.append(h("tr", {}, h("td", { class: "small" }, when(it.created_at)), h("td", {}, it.mode),
      h("td", {}, it.target_lang ? `${it.lang} → ${it.target_lang}` : it.lang || "—"), h("td", { dir: "auto" }, it.text),
      h("td", {}, h("button", { class: "btn small danger", onclick: async () => { await api("DELETE", `/v1/me/history/${it.id}`); loadHistory(); } }, "Delete"))));
    if (!items.length) b.append(h("tr", {}, h("td", { colspan: 5, class: "muted" }, "Nothing saved.")));
  }

  async function loadDevices() {
    const { items } = await api("GET", "/v1/me/devices");
    const b = head($("#devices"), ["Device", "Platform", "Added", "Last seen", ""]);
    for (const d of items) b.append(h("tr", {}, h("td", {}, d.name), h("td", {}, d.platform), h("td", {}, when(d.created_at)), h("td", {}, when(d.last_seen)),
      h("td", {}, h("button", { class: "btn small danger", onclick: async () => {
        if (!confirm(`Remove ${d.name}? It will be signed out.`)) return;
        await api("DELETE", `/v1/me/devices/${d.id}`); loadDevices();
      } }, "Remove"))));
    if (!items.length) b.append(h("tr", {}, h("td", { colspan: 5, class: "muted" }, "No devices yet. Install the desktop app or pair a phone.")));
  }

  // -- dictation test ------------------------------------------------------
  const out = $("#d-out");
  let partialEl = null;
  let recording = false;
  const status = t => { $("#d-status").textContent = t; };
  const STATE_TEXT = {
    recording: "Listening… press again to finish.",
    queued: "Waiting for the server…",
    transcribing: "Transcribing…",
    cleaning: "Cleaning up…",
    translating: "Translating…",
    processing: "Processing…",
    offline: "Disconnected.",
  };
  const NOTICE_TEXT = {
    cleanup_fallback: "The cleanup model's version changed the meaning, so the safer rule-based cleanup was used.",
    cleanup_unavailable: "Cleanup model unavailable: basic cleanup applied.",
    translation_unverified: "Check this translation: a number or length did not match the original.",
    no_speech: "No speech detected.",
    truncated: "The recording hit the length limit and was cut.",
  };
  const rec = new SautiRecorder({
    onState: (s, pos) => { if (!recording || s === "recording") status(s === "queued" && pos ? `In queue (position ${pos})…` : STATE_TEXT[s] || s); },
    onPartial: t => {
      if (!partialEl) { partialEl = h("span", { class: "partial" }); out.append(partialEl); }
      partialEl.textContent = t;
    },
    onFinal: m => {
      partialEl && partialEl.remove();
      partialEl = null;
      if (m.text) out.append(h("p", { dir: "auto" }, m.text));
      const notes = (m.notices || []).map(n => NOTICE_TEXT[n]).filter(Boolean);
      status(notes.join(" ") || `Done (${m.lang || "?"}${m.target_lang ? " → " + m.target_lang : ""}).`);
      const t = m.timings_ms;
      $("#d-timing").textContent = `server: ${t.total} ms (recognition ${t.asr}, cleanup ${t.cleanup}${t.translate ? ", translation " + t.translate : ""})`;
    },
    onError: m => { partialEl && partialEl.remove(); partialEl = null; status(m.message || m.code); },
  });

  $("#mic").addEventListener("click", async () => {
    const mic = $("#mic");
    if (!recording) {
      try {
        const mode = $("#d-mode").value;
        await rec.start({ mode, lang: $("#d-lang").value, target_lang: $("#d-target").value });
        recording = true;
        mic.classList.add("recording");
        mic.setAttribute("aria-label", "Stop dictation");
      } catch (e) { status(e.message || "Microphone unavailable."); }
    } else {
      recording = false;
      mic.classList.remove("recording");
      mic.setAttribute("aria-label", "Start dictation");
      await rec.stop();
    }
  });
  $("#d-mode").addEventListener("change", () => $("#d-target-wrap").classList.toggle("hidden", $("#d-mode").value !== "translate"));
  $("#d-copy").addEventListener("click", async () => {
    const text = [...out.querySelectorAll("p")].map(p => p.textContent).join("\n");
    try { await navigator.clipboard.writeText(text); toast("Copied"); } catch { toast("Copy failed"); }
  });
  $("#d-clear").addEventListener("click", () => out.replaceChildren());

  // -- forms -------------------------------------------------------------------
  $("#settings").addEventListener("change", async ev => {
    try { await api("PATCH", "/v1/me/settings", { [ev.target.name]: ev.target.value }); toast("Saved"); await loadProfile(); } catch (e) { toast(e.message); }
  });
  $("#history-settings").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = ev.target;
    if (!f.history_enabled.checked && profile.settings.history_enabled && !confirm("Turn history off and delete everything already saved?")) return;
    try {
      await api("PATCH", "/v1/me/settings", { history_enabled: f.history_enabled.checked, retention_days: Number(f.retention_days.value) });
      toast("Saved"); await loadProfile();
    } catch (e) { toast(e.message); }
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
    try { await api("POST", "/v1/me/dictionary", { term: f.get("term"), aliases }); ev.target.reset(); await loadProfile(); } catch (e) { toast(e.message); }
  });
  $("#clear-history").addEventListener("click", async () => {
    if (!confirm("Delete your entire history?")) return;
    await api("DELETE", "/v1/me/history"); loadHistory();
  });
  let pairTimer = null;
  $("#pair").addEventListener("click", async () => {
    const r = await api("POST", "/v1/pairing/codes");
    $("#pair-code").textContent = r.code.slice(0, 3) + " " + r.code.slice(3);
    $("#pair-server").textContent = r.server_url;
    $("#pair-out").classList.remove("hidden");
    clearInterval(pairTimer);
    const tick = () => {
      const left = Math.max(0, Math.round(r.expires_at - Date.now() / 1000));
      $("#pair-expiry").textContent = left ? `expires in ${Math.floor(left / 60)}:${String(left % 60).padStart(2, "0")}` : "expired";
      if (!left) clearInterval(pairTimer);
    };
    tick();
    pairTimer = setInterval(tick, 1000);
  });

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

  $("#login-form").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    try { const r = await api("POST", "/v1/auth/login", { username: f.get("username"), password: f.get("password") }); setToken(r.token); start(); }
    catch (e) { $("#login-error").textContent = e.message; }
  });
  $("#logout").addEventListener("click", async () => { try { await api("POST", "/v1/auth/logout"); } catch {} setToken(null); location.reload(); });

  start();
})();
