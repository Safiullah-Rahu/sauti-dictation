"use strict";
(() => {
  const { api, h, $, icon, platformIcon, toast, when, duration, initials, bar, sparkline, dialog, menu, drawer, tabs, setToken } = Sauti;
  let current = "overview";
  let timer = null;
  let users = [];
  let eventFilter = "all";
  const history = { cpu: [], queued: [], wait: [], sessions: [] };
  const push = (k, v) => { history[k].push(v); if (history[k].length > 60) history[k].shift(); };

  const ms = v => (v == null ? "—" : v >= 10000 ? (v / 1000).toFixed(0) + " s" : v >= 1000 ? (v / 1000).toFixed(1) + " s" : Math.round(v) + " ms");
  const pair = s => (s && s.n ? `${ms(s.p50_ms)} / ${ms(s.p95_ms)}` : "—");
  const base = p => (p || "").split(/[\\/]/).filter(Boolean).pop() || p;

  function head(table, cols) {
    table.replaceChildren(h("thead", {}, h("tr", {}, cols.map(c => h("th", { class: c.endsWith("#") ? "num" : "" }, c.replace(/#$/, ""))))));
    const body = h("tbody");
    table.append(body);
    return body;
  }

  function kpi(iconName, label, value, sub, spark) {
    return h("div", { class: "card kpi" },
      h("div", { class: "label" }, icon(iconName, "sm"), label),
      h("div", { class: "value" }, value),
      sub ? h("div", { class: "sub" }, sub) : null,
      spark || null);
  }

  function meter(label, pct, detail, cls) {
    return h("div", { class: "meter" }, h("div", { class: "row" }, h("span", {}, label), h("b", {}, detail)), bar(pct, cls));
  }

  // ---------------------------------------------------------------- overview
  async function overview() {
    const o = await api("GET", "/v1/admin/overview");
    const asr = o.stages.find(s => s.name === "asr");
    const sys = o.system;
    push("cpu", sys.cpu_percent);
    push("queued", o.stages.reduce((a, s) => a + s.queued.final + s.queued.batch, 0));
    push("wait", asr && asr.wait.final.n ? asr.wait.final.p95_ms : 0);
    push("sessions", o.sessions.active);
    const healthy = o.status === "ok";
    $("#status-pill").replaceChildren(h("span", { class: "pill " + (healthy ? "ok" : "warn") }, h("span", { class: "dot " + (healthy ? "ok" : "warn") }), healthy ? "All systems healthy" : "Degraded"));
    $("#count-sessions").textContent = o.sessions.active;
    const errs = Object.values(o.events_24h || {}).reduce((a, b) => a + b, 0);
    $("#kpis").replaceChildren(
      kpi("activity", "Live sessions", String(o.sessions.active),
        `${o.sessions.recording} recording · ${o.sessions.processing} processing`, sparkline(history.sessions)),
      kpi("mic", "Dictated today", h("span", {}, String(o.today.audio_minutes), h("small", {}, "min")),
        `${o.today.utterances} dictations · ${o.today.errors} errors`),
      kpi("clock", "Final-text wait (p95)", asr && asr.wait.final.n ? ms(asr.wait.final.p95_ms) : "—",
        `${history.queued.at(-1) || 0} waiting now`, sparkline(history.wait)),
      kpi("cpu", "Server CPU", h("span", {}, String(Math.round(sys.cpu_percent)), h("small", {}, "%")),
        `${sys.cpu_count} threads · ${o.users} users · ${o.devices} devices`, sparkline(history.cpu, 100)),
      kpi("alert", "Events (24 h)", String(errs),
        Object.entries(o.events_24h || {}).map(([k, v]) => `${k.replace(/_/g, " ")} ${v}`).join(" · ") || "nothing has failed"),
    );

    const stageIcon = { asr: "mic", llm: "zap", translate: "globe" };
    const stageName = { asr: "Recognition", llm: "Cleanup LLM", translate: "Translation" };
    $("#stages").replaceChildren(...o.stages.map(s => {
      const busy = s.workers ? (s.active / s.workers) * 100 : 0;
      const c = s.counters;
      return h("div", { class: "stage" },
        h("div", {}, h("div", { class: "name" }, h("span", { class: "tile-icon accent" }, icon(stageIcon[s.name] || "cpu", "sm")), stageName[s.name] || s.name),
          h("div", { class: "tiny muted" }, `${s.active} of ${s.workers} busy`), bar(busy, busy > 99 ? "warn" : "")),
        h("div", { class: "stats" },
          h("div", { class: "stat", title: "jobs waiting: final text · live preview · long recording" },
            h("b", {}, `${s.queued.final} · ${s.queued.partial} · ${s.queued.batch}`), h("span", {}, "waiting")),
          h("div", { class: "stat" }, h("b", {}, pair(s.wait.final)), h("span", {}, "wait p50 / p95")),
          h("div", { class: "stat" }, h("b", {}, pair(s.service.final)), h("span", {}, "run p50 / p95")),
          h("div", { class: "stat", title: `${c.timeouts} timeouts · ${c.rejected} busy · ${c.dropped} previews dropped` },
            h("b", {}, String(c.completed)), h("span", {}, `done · ${c.timeouts + c.rejected} late/busy`))));
    }));

    const parts = [
      meter("CPU", sys.cpu_percent, `${Math.round(sys.cpu_percent)}% of ${sys.cpu_count} threads`, sys.cpu_percent > 90 ? "warn" : ""),
      meter("Memory", sys.memory.percent, `${sys.memory.used_gb} / ${sys.memory.total_gb} GB`),
    ];
    if (sys.disk && sys.disk.total_gb) {
      const used = 100 - (sys.disk.free_gb / sys.disk.total_gb) * 100;
      parts.push(meter("Data disk", used, `${sys.disk.free_gb} GB free`, used > 90 ? "bad" : ""));
    }
    if (sys.gpus && sys.gpus.length) {
      for (const g of sys.gpus) {
        parts.push(h("div", { class: "row small" }, icon("gpu", "sm"), h("b", {}, g.name), h("span", { class: "spacer" }), g.temperature_c != null ? h("span", { class: "muted" }, `${g.temperature_c} °C`) : null));
        parts.push(meter("GPU load", g.util_percent || 0, g.util_percent == null ? "n/a" : `${g.util_percent}%`));
        parts.push(meter("GPU memory", g.memory_total_mb ? (g.memory_used_mb / g.memory_total_mb) * 100 : 0,
          `${Math.round(g.memory_used_mb / 1024 * 10) / 10} / ${Math.round(g.memory_total_mb / 1024)} GB`));
      }
    } else {
      parts.push(h("div", { class: "notice" }, icon("gpu"), h("span", {}, "No GPU detected — speech models run on the CPU. Pass an NVIDIA card through to this VM and the models switch automatically.")));
    }
    parts.push(h("div", { class: "list" }, sys.processes.map(p => h("div", { class: "list-item" },
      h("span", { class: "tile-icon" }, icon(p.name.startsWith("llama") ? "zap" : "server", "sm")),
      h("div", { class: "grow" }, h("div", { class: "title small" }, p.name.startsWith("llama") ? "LLM server" : "Sauti server"), h("div", { class: "meta" }, `pid ${p.pid} · ${p.threads} threads`)),
      h("b", { class: "small" }, `${(p.rss_mb / 1024).toFixed(1)} GB`)))));
    $("#system").replaceChildren(...parts);
    $("#uptime").textContent = "up " + duration(sys.uptime_s);

    const m = o.models;
    const a = m.asr, l = m.llm, t = m.translate;
    const chip = (txt, cls = "grey") => h("span", { class: "pill " + cls }, txt);
    const gpu = a.device && String(a.device).startsWith("cuda");
    $("#models").replaceChildren(
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("span", { class: "tile-icon accent" }, icon("mic", "sm")), h("h2", {}, "Speech recognition")),
        h("p", { class: "title" }, base(a.model) || a.backend), h("div", { class: "chips" },
          a.device ? chip(gpu ? "GPU · " + a.device : "CPU", gpu ? "ok" : "grey") : null,
          a.compute_type ? chip(a.compute_type) : null, a.workers ? chip(`${a.workers} parallel`) : null,
          a.partial_model ? chip("previews: " + base(a.partial_model)) : null)),
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("span", { class: "tile-icon accent" }, icon("zap", "sm")), h("h2", {}, "Cleanup")),
        h("p", { class: "title" }, l ? l.model : "Rule-based only"), h("div", { class: "chips" },
          l ? chip(m.llm_healthy ? "reachable" : "unreachable", m.llm_healthy ? "ok" : "bad") : null,
          l && l.base_url ? chip(l.base_url.replace(/^https?:\/\//, "")) : null)),
      h("div", { class: "card" }, h("div", { class: "card-head" }, h("span", { class: "tile-icon accent" }, icon("globe", "sm")), h("h2", {}, "Translation")),
        h("p", { class: "title" }, typeof t === "string" ? "Same model as cleanup" : t ? (t.family || base(t.model_path)) : "Off"),
        h("div", { class: "chips" }, typeof t === "object" && t ? [chip(t.device || "cpu"), chip(t.compute_type || "")] : chip("low-confidence speech is not translated", "info"))),
    );
  }

  // ---------------------------------------------------------------- users
  function renderUsers() {
    const q = ($("#user-search").value || "").toLowerCase();
    const b = head($("#users"), ["User", "Role", "Status", "Devices#", "Today", "History", ""]);
    const rows = users.filter(u => !q || u.username.toLowerCase().includes(q) || (u.display_name || "").toLowerCase().includes(q));
    for (const u of rows) {
      const limit = u.daily_limit_minutes;
      const usage = h("div", { class: "meter" }, h("div", { class: "row tiny" }, h("span", {}, `${u.used_today_minutes} min`), h("span", { class: "muted" }, limit ? `of ${limit}` : "default limit")),
        limit ? bar((u.used_today_minutes / limit) * 100, u.used_today_minutes >= limit ? "bad" : "") : null);
      const more = h("button", { class: "btn ghost icon-only", "aria-label": `Actions for ${u.username}` }, icon("more"));
      more.addEventListener("click", () => menu(more, [
        { icon: "clock", label: "Set daily limit", run: () => setLimit(u) },
        { icon: u.disabled ? "check" : "x", label: u.disabled ? "Enable account" : "Disable account", run: () => patch(u, { disabled: !u.disabled }, u.disabled ? "Account enabled" : "Account disabled") },
        { icon: "shield", label: u.role === "admin" ? "Make regular user" : "Make administrator", run: () => patch(u, { role: u.role === "admin" ? "user" : "admin" }, "Role changed") },
        { icon: "key", label: "Reset password", run: () => resetPassword(u) },
        "-",
        { icon: "trash", label: "Delete account", danger: true, run: () => deleteUser(u) },
      ]));
      b.append(h("tr", {},
        h("td", {}, h("div", { class: "cell-user" }, h("span", { class: "avatar" }, initials(u.display_name || u.username)), h("div", {}, h("b", {}, u.display_name || u.username), h("span", {}, "@" + u.username)))),
        h("td", {}, u.role === "admin" ? h("span", { class: "pill" }, icon("shield", "sm"), "admin") : h("span", { class: "muted small" }, "user")),
        h("td", {}, u.disabled ? h("span", { class: "pill bad" }, "disabled") : h("span", { class: "pill ok" }, "active")),
        h("td", { class: "num" }, u.devices),
        h("td", {}, usage),
        h("td", { class: "small" }, u.history_enabled ? h("span", {}, h("span", { class: "pill info" }, "on"), ` ${u.history_items} items · ${u.retention_days} d`) : h("span", { class: "muted" }, "off")),
        h("td", { class: "num" }, more)));
    }
    if (!rows.length) b.append(h("tr", {}, h("td", { colspan: 7 }, h("div", { class: "empty" }, icon("users"), h("div", {}, q ? "No user matches that search." : "No users yet.")))));
  }

  async function loadUsers() { users = (await api("GET", "/v1/admin/users")).items; renderUsers(); }

  async function patch(u, body, done) {
    try { await api("PATCH", `/v1/admin/users/${u.id}`, body); toast(done); loadUsers(); } catch (e) { toast(e.message); }
  }

  async function setLimit(u) {
    const v = await dialog({ title: `Daily limit for ${u.display_name || u.username}`, body: "Minutes of audio per day (UTC). Leave empty to use the server default.",
      input: { type: "number", value: u.daily_limit_minutes ?? "", placeholder: "default", label: "Minutes" }, confirm: "Save" });
    if (v === null) return;
    patch(u, v.trim() ? { daily_limit_minutes: Number(v) } : { clear_limit: true }, "Limit saved");
  }

  async function resetPassword(u) {
    const pw = await dialog({ title: `Reset password for ${u.username}`, danger: true, confirm: "Reset password",
      body: "Their saved history and personal dictionary will be deleted and every device signed out — administrators cannot unlock personal data.",
      input: { type: "password", label: "New password (8+ characters)", minlength: 8 } });
    if (!pw) return;
    patch(u, { new_password: pw }, "Password reset");
  }

  async function deleteUser(u) {
    if (!(await dialog({ title: `Delete ${u.username}?`, body: "The account and all of its data are removed. This cannot be undone.", confirm: "Delete", danger: true }))) return;
    try { await api("DELETE", `/v1/admin/users/${u.id}`); toast("User deleted"); loadUsers(); } catch (e) { toast(e.message); }
  }

  function addUser() {
    const form = h("form", { class: "stack" },
      h("label", { class: "field" }, "Username", h("input", { name: "username", required: true, pattern: "[A-Za-z0-9._@-]+", minlength: 2 })),
      h("label", { class: "field" }, "Display name", h("input", { name: "display_name" })),
      h("label", { class: "field" }, "Initial password", h("input", { name: "password", type: "password", minlength: 8, required: true })),
      h("label", { class: "field" }, "Role", h("select", { name: "role" }, h("option", { value: "user" }, "User"), h("option", { value: "admin" }, "Administrator"))),
      h("label", { class: "field" }, "Daily limit (minutes)", h("input", { name: "daily_limit_minutes", type: "number", min: 1, placeholder: "server default" })),
      h("p", { class: "muted small" }, "Share the initial password privately; the user can change it in the portal."),
      h("button", { class: "btn primary", type: "submit" }, icon("plus", "sm"), "Create user"));
    const close = drawer("Add a user", form);
    form.addEventListener("submit", async ev => {
      ev.preventDefault();
      const f = new FormData(form);
      const body = { username: f.get("username"), display_name: f.get("display_name"), password: f.get("password"), role: f.get("role") };
      if (f.get("daily_limit_minutes")) body.daily_limit_minutes = Number(f.get("daily_limit_minutes"));
      try { await api("POST", "/v1/admin/users", body); close(); toast("User created"); loadUsers(); } catch (e) { toast(e.message); }
    });
    form.querySelector("input").focus();
  }

  // ---------------------------------------------------------------- devices & sessions
  async function devices() {
    const { items } = await api("GET", "/v1/admin/devices");
    $("#devices").replaceChildren(...items.map(d => h("div", { class: "list-item" },
      h("span", { class: "tile-icon" }, platformIcon(d.platform)),
      h("div", { class: "grow" }, h("div", { class: "title" }, d.name, " ", h("span", { class: "muted small" }, "· " + d.username)),
        h("div", { class: "meta" }, `${d.platform} · added ${when(d.created_at)}`)),
      d.online ? h("span", { class: "pill ok" }, h("span", { class: "dot ok" }), "online") : h("span", { class: "muted small" }, "seen " + when(d.last_seen)),
      h("button", { class: "btn sm danger", onclick: async () => {
        if (!(await dialog({ title: `Revoke ${d.name}?`, body: "It is signed out immediately and must sign in or pair again.", confirm: "Revoke", danger: true }))) return;
        await api("DELETE", `/v1/admin/devices/${d.id}`); toast("Device revoked"); devices();
      } }, "Revoke"))));
    if (!items.length) $("#devices").append(h("div", { class: "empty" }, icon("laptop"), h("div", {}, "No devices yet.")));
  }

  async function sessions() {
    const { items } = await api("GET", "/v1/admin/sessions");
    $("#count-sessions").textContent = items.length;
    const state = s => s.state === "recording" ? h("span", { class: "pill bad" }, h("span", { class: "dot rec" }), "recording")
      : s.state === "processing" ? h("span", { class: "pill warn" }, h("span", { class: "dot warn" }), "processing") : h("span", { class: "pill grey" }, "idle");
    $("#sessions").replaceChildren(...items.map(s => h("div", { class: "list-item" },
      h("span", { class: "avatar" }, initials(s.username)),
      h("div", { class: "grow" }, h("div", { class: "title" }, s.username, " ", h("span", { class: "muted small" }, `· ${s.device}`)),
        h("div", { class: "meta" }, `${s.platform} · ${s.remote} · connected ${duration(Date.now() / 1000 - s.connected_at)} · ${s.utterances} dictations`)),
      state(s),
      h("button", { class: "btn sm", onclick: async () => { await api("DELETE", `/v1/admin/sessions/${s.session_id}`); sessions(); } }, "End"))));
    if (!items.length) $("#sessions").append(h("div", { class: "empty" }, icon("activity"), h("div", {}, "Nobody is connected right now.")));
  }

  // ---------------------------------------------------------------- usage
  async function usage() {
    const { items } = await api("GET", "/v1/admin/usage");
    const byDay = {};
    for (const r of items) byDay[r.day] = (byDay[r.day] || 0) + r.audio_minutes;
    const days = Object.keys(byDay).sort().slice(-14);
    const top = Math.max(1, ...days.map(d => byDay[d]));
    $("#usage-bars").replaceChildren(...(days.length ? days.map(d => {
      const col = h("span");
      col.style.height = Math.max(2, (byDay[d] / top) * 96) + "px";
      return h("div", { class: "b", title: `${d}: ${byDay[d].toFixed(1)} min` }, h("b", { class: "tiny" }, byDay[d].toFixed(0)), col, d.slice(5));
    }) : [h("div", { class: "empty" }, "No usage yet.")]));
    const total = items.reduce((a, r) => a + r.audio_minutes, 0);
    const utt = items.reduce((a, r) => a + r.utterances, 0);
    const errs = items.reduce((a, r) => a + r.errors, 0);
    $("#usage-kpis").replaceChildren(
      kpi("mic", "Audio, 14 days", h("span", {}, total.toFixed(0), h("small", {}, "min"))),
      kpi("activity", "Dictations", String(utt)),
      kpi("users", "Active users", String(new Set(items.map(r => r.username)).size)),
      kpi("alert", "Errors", String(errs), utt ? `${((errs / Math.max(1, utt + errs)) * 100).toFixed(1)}% of attempts` : ""));
    const b = head($("#usage"), ["Day", "User", "Audio min#", "Dictations#", "Errors#"]);
    for (const r of items) b.append(h("tr", {}, h("td", {}, r.day), h("td", {}, h("div", { class: "cell-user" }, h("span", { class: "avatar" }, initials(r.username)), r.username)),
      h("td", { class: "num" }, r.audio_minutes), h("td", { class: "num" }, r.utterances), h("td", { class: "num" }, r.errors)));
  }

  // ---------------------------------------------------------------- events
  async function events() {
    const ev = await api("GET", "/v1/admin/events");
    const codes = ["all", ...new Set(ev.items.map(e => e.code))];
    $("#event-filter").replaceChildren(...codes.map(c => h("button", { class: "btn sm " + (c === eventFilter ? "primary" : "ghost"), onclick: () => { eventFilter = c; events(); } }, c.replace(/_/g, " "))));
    const shown = ev.items.filter(e => eventFilter === "all" || e.code === eventFilter);
    $("#events").replaceChildren(...shown.map(e => h("div", { class: "list-item" },
      h("span", { class: "tile-icon" }, icon(e.kind === "guard" ? "shield" : e.kind === "auth" ? "key" : "alert", "sm")),
      h("div", { class: "grow" }, h("div", { class: "title small" }, h("span", { class: "pill " + (e.kind === "guard" ? "info" : "warn") }, e.code.replace(/_/g, " ")), " ", e.stage ? h("span", { class: "muted" }, "in " + e.stage) : null),
        h("div", { class: "meta" }, [e.username, e.detail].filter(Boolean).join(" · ") || "—")),
      h("span", { class: "muted small" }, when(e.ts)))));
    if (!shown.length) $("#events").append(h("div", { class: "empty" }, icon("check"), h("div", {}, "Nothing has failed.")));
    const au = await api("GET", "/v1/admin/audit");
    const a = head($("#audit"), ["When", "Administrator", "Action", "Target"]);
    for (const e of au.items) a.append(h("tr", {}, h("td", { class: "small" }, when(e.ts)), h("td", {}, e.actor || "system"), h("td", {}, h("span", { class: "pill grey" }, e.action)), h("td", { class: "small muted" }, e.target)));
  }

  // ---------------------------------------------------------------- dictionary
  async function dictionary() {
    const { items } = await api("GET", "/v1/admin/org-dictionary");
    $("#terms").replaceChildren(...items.map(t => h("span", { class: "chip" }, h("b", {}, t.term), t.aliases.length ? h("span", { class: "alias" }, "· " + t.aliases.join(", ")) : null,
      h("button", { "aria-label": `Remove ${t.term}`, onclick: async () => { await api("DELETE", `/v1/admin/org-dictionary/${t.id}`); dictionary(); } }, icon("x", "sm")))));
    if (!items.length) $("#terms").append(h("div", { class: "empty" }, icon("book"), h("div", {}, "No shared terms yet.")));
  }

  // ---------------------------------------------------------------- shell
  const loaders = { overview, users: loadUsers, devices, sessions, usage, dictionary, events };

  async function refresh() {
    try { await loaders[current](); }
    catch (e) {
      if (e.status === 401 || e.status === 403) { setToken(null); start(); return; }
      toast(e.message);
    }
  }

  function start() {
    clearInterval(timer);
    if (!Sauti.token()) { $("#shell").classList.add("hidden"); $("#login").classList.remove("hidden"); return; }
    $("#login").classList.add("hidden");
    $("#shell").classList.remove("hidden");
    api("GET", "/v1/me").then(m => {
      if (m.user.role !== "admin") { $("#login-error").textContent = "This account is not an administrator."; setToken(null); start(); return; }
      $("#whoami").textContent = m.user.display_name;
      $("#me-avatar").textContent = initials(m.user.display_name);
    }).catch(() => { setToken(null); start(); });
    tabs($("#tabs"), name => { current = name; refresh(); });
    timer = setInterval(() => { if (["overview", "sessions"].includes(current) && !document.hidden) refresh(); }, 3000);
  }

  Sauti.decorate();
  $("#login-form").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    try {
      const r = await api("POST", "/v1/auth/login", { username: f.get("username"), password: f.get("password") });
      if (r.user.role !== "admin") { $("#login-error").textContent = "This account is not an administrator."; return; }
      setToken(r.token);
      start();
    } catch (e) { $("#login-error").textContent = e.message; }
  });
  $("#logout").addEventListener("click", async () => { try { await api("POST", "/v1/auth/logout"); } catch {} setToken(null); start(); });
  $("#add-user").addEventListener("click", addUser);
  $("#user-search").addEventListener("input", renderUsers);
  $("#new-term").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    const aliases = String(f.get("aliases") || "").split(",").map(s => s.trim()).filter(Boolean);
    try { await api("POST", "/v1/admin/org-dictionary", { term: f.get("term"), aliases }); ev.target.reset(); dictionary(); } catch (e) { toast(e.message); }
  });
  start();
})();
