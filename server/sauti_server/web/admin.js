"use strict";
(() => {
  const { api, h, $, toast, when, tabs, setToken, LANGS } = Sauti;
  let current = "overview";
  let timer = null;

  function head(table, cols) {
    table.replaceChildren(h("thead", {}, h("tr", {}, cols.map(c => h("th", { class: c.endsWith("#") ? "num" : "" }, c.replace(/#$/, ""))))));
    const body = h("tbody");
    table.append(body);
    return body;
  }

  function stat(label, value, sub) {
    return h("div", { class: "card stat" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value), sub ? h("div", { class: "sub" }, sub) : null);
  }

  const ms = s => (s && s.n ? `${Math.round(s.p50_ms)} / ${Math.round(s.p95_ms)}` : "—");

  function bar(pct) {
    const fill = h("span");
    fill.style.width = Math.max(0, Math.min(100, pct)) + "%";
    return h("div", { class: "bar" }, fill);
  }

  async function overview() {
    const o = await api("GET", "/v1/admin/overview");
    const errs = Object.values(o.events_24h || {}).reduce((a, b) => a + b, 0);
    $("#stats").replaceChildren(
      stat("Status", h("span", {}, h("span", { class: "dot " + (o.status === "ok" ? "ok" : "bad") }), o.status === "ok" ? "Healthy" : "Degraded"),
        o.models.llm_healthy === false ? "Cleanup model unreachable — rule-based cleanup in use" : `${o.users} users · ${o.devices} devices`),
      stat("Live sessions", o.sessions.active, `${o.sessions.recording} recording · ${o.sessions.processing} processing`),
      stat("Today", `${o.today.audio_minutes} min`, `${o.today.utterances} dictations · ${o.today.errors} errors`),
      stat("Events (24 h)", errs, Object.entries(o.events_24h || {}).map(([k, v]) => `${k} ${v}`).join(" · ") || "none"),
    );
    const q = head($("#queues"), ["Stage", "Workers", "Queued (final / preview / long)", "Wait p50/p95 ms#", "Run p50/p95 ms#", "Done / failed / timeouts / busy#"]);
    for (const s of o.stages) {
      q.append(h("tr", {},
        h("td", {}, s.name), h("td", {}, `${s.active} of ${s.workers} busy`),
        h("td", {}, `${s.queued.final} / ${s.queued.partial} / ${s.queued.batch}`),
        h("td", { class: "num" }, ms(s.wait.final)), h("td", { class: "num" }, ms(s.service.final)),
        h("td", { class: "num" }, `${s.counters.completed} / ${s.counters.failed} / ${s.counters.timeouts} / ${s.counters.rejected}`)));
    }
    const sys = o.system;
    const memPct = sys.memory.percent;
    $("#system").replaceChildren(
      h("p", {}, `CPU ${sys.cpu_percent}% of ${sys.cpu_count} threads` + (sys.load_avg ? ` · load ${sys.load_avg.join(" ")}` : "")),
      bar(sys.cpu_percent),
      h("p", {}, `Memory ${sys.memory.used_gb} of ${sys.memory.total_gb} GB (${memPct}%)`),
      bar(memPct),
      h("p", { class: "small muted" }, `Data disk: ${sys.disk.free_gb ?? "?"} GB free · up ${Math.round(sys.uptime_s / 3600 * 10) / 10} h`),
      h("table", {}, h("tbody", {}, sys.processes.map(p => h("tr", {}, h("td", {}, p.name), h("td", { class: "num" }, `${p.rss_mb} MB`), h("td", { class: "num" }, `${p.threads} threads`))))),
    );
    const m = head($("#models"), ["Role", "Backend", "Model", "Details"]);
    const asr = o.models.asr, llm = o.models.llm, tr = o.models.translate;
    m.append(h("tr", {}, h("td", {}, "Speech recognition"), h("td", {}, asr.backend), h("td", {}, asr.model || "—"),
      h("td", { class: "small muted" }, asr.workers ? `${asr.workers} parallel × ${asr.cpu_threads} threads, ${asr.compute_type}${asr.partial_model ? `, previews: ${asr.partial_model}` : ""}` : "")));
    m.append(h("tr", {}, h("td", {}, "Cleanup"), h("td", {}, llm ? llm.backend : "rules only"), h("td", {}, llm ? llm.model : "—"),
      h("td", { class: "small muted" }, llm ? h("span", {}, h("span", { class: "dot " + (o.models.llm_healthy ? "ok" : "bad") }), o.models.llm_healthy ? "reachable" : "unreachable", llm.base_url ? ` · ${llm.base_url}` : "") : "")));
    m.append(h("tr", {}, h("td", {}, "Translation"), h("td", {}, typeof tr === "string" ? tr : tr ? tr.backend : "—"),
      h("td", {}, typeof tr === "object" && tr ? (tr.family || tr.model_path) : typeof tr === "string" ? "same as cleanup" : "—"), h("td", {}, "")));
  }

  async function users() {
    const { items } = await api("GET", "/v1/admin/users");
    const b = head($("#users"), ["User", "Role", "Status", "Devices#", "Today min#", "Daily limit", "History", ""]);
    for (const u of items) {
      const limit = h("input", { type: "number", min: "1", value: u.daily_limit_minutes ?? "", placeholder: "default", class: "small", "aria-label": "daily limit minutes" });
      limit.style.width = "6.5rem";
      b.append(h("tr", {},
        h("td", {}, h("strong", {}, u.username), h("div", { class: "small muted" }, u.display_name)),
        h("td", {}, u.role === "admin" ? h("span", { class: "pill" }, "admin") : "user"),
        h("td", {}, u.disabled ? h("span", { class: "pill bad" }, "disabled") : h("span", { class: "pill grey" }, "active")),
        h("td", { class: "num" }, u.devices), h("td", { class: "num" }, u.used_today_minutes),
        h("td", {}, h("div", { class: "row" }, limit, h("button", { class: "btn small", onclick: async () => {
          const v = limit.value.trim();
          await api("PATCH", `/v1/admin/users/${u.id}`, v ? { daily_limit_minutes: Number(v) } : { clear_limit: true });
          toast("Limit saved"); users();
        } }, "Save"))),
        h("td", { class: "small" }, u.history_enabled ? `on · ${u.history_items} items · ${u.retention_days} d` : "off"),
        h("td", {}, h("div", { class: "row" },
          h("button", { class: "btn small", onclick: async () => {
            try { await api("PATCH", `/v1/admin/users/${u.id}`, { disabled: !u.disabled }); users(); } catch (e) { toast(e.message); }
          } }, u.disabled ? "Enable" : "Disable"),
          h("button", { class: "btn small", onclick: async () => {
            const pw = prompt(`New password for ${u.username} (min 8 characters).\n\nTheir saved history and personal dictionary will be deleted and all devices signed out.`);
            if (!pw) return;
            try { await api("PATCH", `/v1/admin/users/${u.id}`, { new_password: pw }); toast("Password reset"); users(); } catch (e) { toast(e.message); }
          } }, "Reset password"),
          h("button", { class: "btn small", onclick: async () => {
            try { await api("PATCH", `/v1/admin/users/${u.id}`, { role: u.role === "admin" ? "user" : "admin" }); users(); } catch (e) { toast(e.message); }
          } }, u.role === "admin" ? "Make user" : "Make admin"),
          h("button", { class: "btn small danger", onclick: async () => {
            if (!confirm(`Delete ${u.username} and all of their data? This cannot be undone.`)) return;
            try { await api("DELETE", `/v1/admin/users/${u.id}`); users(); } catch (e) { toast(e.message); }
          } }, "Delete"),
        ))));
    }
  }

  async function devices() {
    const { items } = await api("GET", "/v1/admin/devices");
    const b = head($("#devices"), ["Device", "User", "Platform", "Added", "Last seen", ""]);
    for (const d of items) {
      b.append(h("tr", {}, h("td", {}, h("span", { class: "dot " + (d.online ? "ok" : "") }), d.name), h("td", {}, d.username), h("td", {}, d.platform),
        h("td", {}, when(d.created_at)), h("td", {}, d.online ? "online" : when(d.last_seen)),
        h("td", {}, h("button", { class: "btn small danger", onclick: async () => {
          if (!confirm(`Revoke ${d.name}? It will be signed out immediately.`)) return;
          await api("DELETE", `/v1/admin/devices/${d.id}`); devices();
        } }, "Revoke"))));
    }
    if (!items.length) b.append(h("tr", {}, h("td", { colspan: 6, class: "muted" }, "No devices yet.")));
  }

  async function sessions() {
    const { items } = await api("GET", "/v1/admin/sessions");
    const b = head($("#sessions"), ["User", "Device", "State", "Connected", "Dictations#", "Address", ""]);
    for (const s of items) {
      const cls = s.state === "recording" ? "dot rec" : s.state === "processing" ? "dot ok" : "dot";
      b.append(h("tr", {}, h("td", {}, s.username), h("td", {}, `${s.device} (${s.platform})`), h("td", {}, h("span", { class: cls }), s.state),
        h("td", {}, when(s.connected_at)), h("td", { class: "num" }, s.utterances), h("td", { class: "small" }, s.remote),
        h("td", {}, h("button", { class: "btn small", onclick: async () => { await api("DELETE", `/v1/admin/sessions/${s.session_id}`); sessions(); } }, "End"))));
    }
    if (!items.length) b.append(h("tr", {}, h("td", { colspan: 7, class: "muted" }, "Nobody is connected.")));
  }

  async function usage() {
    const { items } = await api("GET", "/v1/admin/usage");
    const b = head($("#usage"), ["Day", "User", "Audio min#", "Dictations#", "Errors#"]);
    for (const r of items) b.append(h("tr", {}, h("td", {}, r.day), h("td", {}, r.username), h("td", { class: "num" }, r.audio_minutes),
      h("td", { class: "num" }, r.utterances), h("td", { class: "num" }, r.errors)));
    if (!items.length) b.append(h("tr", {}, h("td", { colspan: 5, class: "muted" }, "No usage recorded yet.")));
  }

  async function dictionary() {
    const { items } = await api("GET", "/v1/admin/org-dictionary");
    const b = head($("#terms"), ["Term", "Sounds like", ""]);
    for (const t of items) b.append(h("tr", {}, h("td", {}, h("strong", {}, t.term)), h("td", { class: "small" }, t.aliases.join(", ") || "—"),
      h("td", {}, h("button", { class: "btn small danger", onclick: async () => { await api("DELETE", `/v1/admin/org-dictionary/${t.id}`); dictionary(); } }, "Remove"))));
    if (!items.length) b.append(h("tr", {}, h("td", { colspan: 3, class: "muted" }, "No shared terms yet.")));
  }

  async function events() {
    const ev = await api("GET", "/v1/admin/events");
    const b = head($("#events"), ["When", "Kind", "Code", "Stage", "User", "Detail"]);
    for (const e of ev.items) b.append(h("tr", {}, h("td", {}, when(e.ts)), h("td", {}, e.kind), h("td", {}, h("span", { class: "pill warn" }, e.code)),
      h("td", {}, e.stage || "—"), h("td", {}, e.username || "—"), h("td", { class: "small muted" }, e.detail || "")));
    if (!ev.items.length) b.append(h("tr", {}, h("td", { colspan: 6, class: "muted" }, "Nothing has failed.")));
    const au = await api("GET", "/v1/admin/audit");
    const a = head($("#audit"), ["When", "Administrator", "Action", "Target"]);
    for (const e of au.items) a.append(h("tr", {}, h("td", {}, when(e.ts)), h("td", {}, e.actor || "system"), h("td", {}, e.action), h("td", { class: "small" }, e.target)));
  }

  const loaders = { overview, users, devices, sessions, usage, dictionary, events };

  async function refresh() {
    try { await loaders[current](); }
    catch (e) {
      if (e.status === 401 || e.status === 403) { setToken(null); start(); return; }
      toast(e.message);
    }
  }

  function start() {
    clearInterval(timer);
    if (!Sauti.token()) {
      $("#shell").classList.add("hidden");
      $("#login").classList.remove("hidden");
      return;
    }
    $("#login").classList.add("hidden");
    $("#shell").classList.remove("hidden");
    api("GET", "/v1/me").then(m => {
      if (m.user.role !== "admin") { $("#login-error").textContent = "This account is not an administrator."; setToken(null); start(); return; }
      $("#whoami").textContent = m.user.display_name;
    }).catch(() => { setToken(null); start(); });
    tabs($("#tabs"), name => { current = name; refresh(); });
    timer = setInterval(() => { if (["overview", "sessions"].includes(current)) refresh(); }, 3000);
  }

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

  $("#new-user").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    const body = { username: f.get("username"), display_name: f.get("display_name"), password: f.get("password"), role: f.get("role") };
    if (f.get("daily_limit_minutes")) body.daily_limit_minutes = Number(f.get("daily_limit_minutes"));
    try { await api("POST", "/v1/admin/users", body); ev.target.reset(); toast("User created"); users(); } catch (e) { toast(e.message); }
  });

  $("#new-term").addEventListener("submit", async ev => {
    ev.preventDefault();
    const f = new FormData(ev.target);
    const aliases = String(f.get("aliases") || "").split(",").map(s => s.trim()).filter(Boolean);
    try { await api("POST", "/v1/admin/org-dictionary", { term: f.get("term"), aliases }); ev.target.reset(); dictionary(); } catch (e) { toast(e.message); }
  });

  void LANGS;
  start();
})();
