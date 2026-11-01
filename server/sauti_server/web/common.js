// Shared helpers for the user portal and the admin dashboard. No external libraries:
// the pages work on an offline network and send nothing to third parties.
"use strict";

const Sauti = (() => {
  const TOKEN_KEY = "sauti.token";
  const THEME_KEY = "sauti.theme";
  const LANGS = { auto: "Auto-detect", en: "English", so: "Somali", ar: "Arabic", sw: "Swahili" };
  const MODES = { clean: "Clean", verbatim: "Verbatim", translate: "Translate" };
  const STYLES = { neutral: "Neutral", formal: "Formal", casual: "Casual", message: "Chat message" };
  const SVG = "http://www.w3.org/2000/svg";

  // 24x24 line icons (stroke = currentColor).
  const ICONS = {
    mic: "M12 15a3 3 0 0 0 3-3V6a3 3 0 1 0-6 0v6a3 3 0 0 0 3 3Z|M19 11a7 7 0 0 1-14 0|M12 18v3",
    grid: "M4 4h7v7H4z|M13 4h7v7h-7z|M4 13h7v7H4z|M13 13h7v7h-7z",
    users: "M16 19v-1a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v1|M9 10a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7Z|M22 19v-1a4 4 0 0 0-3-3.87|M16 3.13a3.5 3.5 0 0 1 0 6.75",
    laptop: "M4 5h16v10H4z|M2 19h20",
    phone: "M7 2h10v20H7z|M11 18h2",
    activity: "M3 12h4l3 8 4-16 3 8h4",
    chart: "M4 20V10|M10 20V4|M16 20v-7|M22 20H2",
    book: "M4 4.5A2.5 2.5 0 0 1 6.5 2H20v17H6.5A2.5 2.5 0 0 0 4 21.5Z|M4 19.5V4.5",
    alert: "M12 3 2 20h20L12 3Z|M12 10v4|M12 17h.01",
    settings: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z|M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1.1-1.55 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09A1.7 1.7 0 0 0 4.6 9a1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 9a1.7 1.7 0 0 0 1.55 1H21a2 2 0 1 1 0 4h-.09a1.7 1.7 0 0 0-1.51 1Z",
    history: "M3 12a9 9 0 1 0 3-6.7L3 8|M3 3v5h5|M12 7v5l3 2",
    sun: "M12 17a5 5 0 1 0 0-10 5 5 0 0 0 0 10Z|M12 1v2|M12 21v2|M4.2 4.2l1.4 1.4|M18.4 18.4l1.4 1.4|M1 12h2|M21 12h2|M4.2 19.8l1.4-1.4|M18.4 5.6l1.4-1.4",
    moon: "M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z",
    logout: "M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4|M16 17l5-5-5-5|M21 12H9",
    plus: "M12 5v14|M5 12h14",
    trash: "M3 6h18|M8 6V4h8v2|M19 6l-1 14H6L5 6",
    more: "M12 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z|M19 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z|M5 13a1 1 0 1 0 0-2 1 1 0 0 0 0 2Z",
    search: "M11 19a8 8 0 1 0 0-16 8 8 0 0 0 0 16Z|M21 21l-4.3-4.3",
    copy: "M9 9h11v11H9z|M5 15H4V4h11v1",
    check: "M20 6 9 17l-5-5",
    globe: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z|M2 12h20|M12 2a15 15 0 0 1 0 20|M12 2a15 15 0 0 0 0 20",
    cpu: "M6 6h12v12H6z|M9 9h6v6H9z|M9 2v4|M15 2v4|M9 18v4|M15 18v4|M2 9h4|M2 15h4|M18 9h4|M18 15h4",
    gpu: "M2 7h18v10H2z|M20 10h2v4h-2|M6 17v3|M10 17v3|M7 12a2 2 0 1 0 4 0 2 2 0 0 0-4 0|M14 10h3|M14 14h3",
    server: "M3 4h18v6H3z|M3 14h18v6H3z|M7 7h.01|M7 17h.01",
    shield: "M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z|M9 12l2 2 4-4",
    lock: "M5 11h14v10H5z|M8 11V7a4 4 0 0 1 8 0v4",
    clock: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20Z|M12 6v6l4 2",
    zap: "M13 2 3 14h9l-1 8 10-12h-9l1-8Z",
    x: "M18 6 6 18|M6 6l12 12",
    menu: "M3 6h18|M3 12h18|M3 18h18",
    link: "M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7|M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7",
    key: "M21 2l-2 2|M15.5 7.5l3 3L22 7l-3-3|M11.4 12.6a5.5 5.5 0 1 1-7.8 7.8 5.5 5.5 0 0 1 7.8-7.8Z|M11.4 12.6 19 5",
    windows: "M3 5.5 10 4.5v7H3z|M12 4.2 21 3v8.5h-9z|M3 12.5h7v7l-7-1z|M12 12.5h9V21l-9-1.2z",
    apple: "M16 3c0 2-1.5 3.5-3.4 3.5C12.4 4.6 14 3 16 3Z|M18.5 16.5c-.8 1.9-2.1 4.5-3.8 4.5-1.3 0-1.7-.8-3.2-.8s-2 .8-3.2.8C6.3 21 3.5 16.4 3.5 12.5 3.5 9 5.7 7.3 7.7 7.3c1.3 0 2.4.9 3.3.9.9 0 2.2-1 3.7-1 1.1 0 2.8.5 3.8 2.1-3.2 1.8-2.6 6.2 0 7.2Z",
    android: "M5 10h14v8a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1z|M5 9a7 7 0 0 1 14 0|M8 5 6.5 2.5|M16 5l1.5-2.5|M9 19v3|M15 19v3",
    browser: "M3 4h18v16H3z|M3 9h18|M6 6.5h.01|M9 6.5h.01",
  };

  function icon(name, cls = "") {
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "icon " + cls);
    svg.setAttribute("aria-hidden", "true");
    for (const d of (ICONS[name] || "").split("|")) {
      const p = document.createElementNS(SVG, "path");
      p.setAttribute("d", d);
      svg.append(p);
    }
    return svg;
  }

  function platformIcon(platform) {
    return icon({ windows: "windows", macos: "apple", android: "android", web: "browser", linux: "laptop" }[platform] || "laptop");
  }

  function token() { try { return sessionStorage.getItem(TOKEN_KEY); } catch { return null; } }
  function setToken(t) { try { t ? sessionStorage.setItem(TOKEN_KEY, t) : sessionStorage.removeItem(TOKEN_KEY); } catch {} }

  async function api(method, path, body) {
    const headers = { "Content-Type": "application/json" };
    const t = token();
    if (t) headers.Authorization = "Bearer " + t;
    const res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    if (res.status === 204) return null;
    let data = null;
    try { data = await res.json(); } catch {}
    if (!res.ok) {
      const err = new Error((data && data.error && data.error.message) || (data && data.detail && JSON.stringify(data.detail)) || res.statusText);
      err.status = res.status;
      err.code = data && data.error && data.error.code;
      throw err;
    }
    return data;
  }

  // Tiny DOM builder: h("td", {class: "num"}, "42")
  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === undefined || v === null || v === false) continue;
      if (k === "class") el.className = v;
      else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "text") el.textContent = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c === undefined || c === null || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }

  const $ = (sel, root) => (root || document).querySelector(sel);

  function toast(msg, ms = 2600) {
    const el = h("div", { class: "toast", role: "status" }, icon("check", "sm"), msg);
    document.body.append(el);
    setTimeout(() => el.remove(), ms);
  }

  function when(ts) {
    if (!ts) return "—";
    const diff = Date.now() / 1000 - ts;
    if (diff < 60) return "just now";
    if (diff < 3600) return Math.round(diff / 60) + " min ago";
    if (diff < 86400) return Math.round(diff / 3600) + " h ago";
    return new Date(ts * 1000).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
  }

  function duration(seconds) {
    const s = Math.max(0, Math.round(seconds));
    if (s < 60) return s + " s";
    if (s < 3600) return Math.floor(s / 60) + " min";
    return Math.floor(s / 3600) + " h " + Math.floor((s % 3600) / 60) + " min";
  }

  function initials(name) {
    return (name || "?").split(/[\s._@-]+/).filter(Boolean).slice(0, 2).map(p => p[0].toUpperCase()).join("");
  }

  function options(select, map, value) {
    select.replaceChildren(...Object.entries(map).map(([k, v]) => h("option", { value: k, selected: k === value }, v)));
  }

  function bar(pct, cls = "") {
    const fill = h("span");
    fill.style.width = Math.max(0, Math.min(100, pct)) + "%";
    return h("div", { class: "bar " + cls }, fill);
  }

  function sparkline(values, max) {
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("class", "spark");
    svg.setAttribute("viewBox", "0 0 100 40");
    svg.setAttribute("preserveAspectRatio", "none");
    if (values.length > 1) {
      const top = Math.max(max || 0, ...values, 1);
      const pts = values.map((v, i) => `${(i / (values.length - 1)) * 100},${38 - (v / top) * 34}`);
      const area = document.createElementNS(SVG, "polygon");
      area.setAttribute("points", `0,40 ${pts.join(" ")} 100,40`);
      const line = document.createElementNS(SVG, "polyline");
      line.setAttribute("points", pts.join(" "));
      svg.append(area, line);
    }
    return svg;
  }

  // Modal dialogs that replace window.confirm / window.prompt.
  function dialog({ title, body, confirm = "Confirm", danger = false, input = null }) {
    return new Promise(resolve => {
      const field = input ? h("input", { type: input.type || "text", value: input.value || "", placeholder: input.placeholder || "", minlength: input.minlength }) : null;
      const close = value => { scrim.remove(); resolve(value); };
      const ok = h("button", { class: "btn " + (danger ? "danger solid" : "primary"), onclick: () => close(field ? field.value : true) }, confirm);
      const scrim = h("div", { class: "scrim", onclick: e => { if (e.target === scrim) close(null); } },
        h("div", { class: "dialog", role: "dialog", "aria-modal": "true" },
          h("h2", {}, title),
          body ? h("p", { class: "muted small" }, body) : null,
          field ? h("label", { class: "field" }, input.label || "", field) : null,
          h("div", { class: "actions" }, h("button", { class: "btn", onclick: () => close(null) }, "Cancel"), ok)));
      scrim.addEventListener("keydown", e => { if (e.key === "Escape") close(null); if (e.key === "Enter" && field) close(field.value); });
      document.body.append(scrim);
      (field || ok).focus();
    });
  }

  function menu(anchor, items) {
    document.querySelectorAll(".menu").forEach(m => m.remove());
    const m = h("div", { class: "menu", role: "menu" }, items.map(it => it === "-" ? h("hr") :
      h("button", { class: it.danger ? "danger" : "", role: "menuitem", onclick: () => { m.remove(); it.run(); } }, icon(it.icon, "sm"), it.label)));
    document.body.append(m);
    const r = anchor.getBoundingClientRect();
    m.style.top = (window.scrollY + r.bottom + 6) + "px";
    m.style.left = Math.max(8, window.scrollX + r.right - m.offsetWidth) + "px";
    setTimeout(() => document.addEventListener("click", function off(e) { if (!m.contains(e.target)) { m.remove(); document.removeEventListener("click", off); } }), 0);
  }

  function drawer(title, content, onClose) {
    const scrim = h("div", { class: "scrim" });
    const close = () => { scrim.remove(); d.remove(); onClose && onClose(); };
    const d = h("aside", { class: "drawer", role: "dialog", "aria-modal": "true" },
      h("div", { class: "row" }, h("h2", {}, title), h("span", { class: "spacer" }), h("button", { class: "btn ghost icon-only", "aria-label": "Close", onclick: close }, icon("x"))),
      content);
    const onKey = e => { if (e.key === "Escape") { document.removeEventListener("keydown", onKey); close(); } };
    scrim.addEventListener("click", () => { document.removeEventListener("keydown", onKey); close(); });
    document.addEventListener("keydown", onKey);
    document.body.append(scrim, d);
    return () => { document.removeEventListener("keydown", onKey); close(); };
  }

  // Theme: follows the system until the person picks one; the choice is remembered in this browser only.
  function applyTheme(choice) {
    const dark = choice ? choice === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
    document.querySelectorAll("[data-theme-toggle]").forEach(b => b.replaceChildren(icon(dark ? "sun" : "moon"), dark ? "Light theme" : "Dark theme"));
  }
  function savedTheme() { try { return localStorage.getItem(THEME_KEY); } catch { return null; } }
  function toggleTheme() {
    const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
    try { localStorage.setItem(THEME_KEY, next); } catch {}
    applyTheme(next);
  }
  applyTheme(savedTheme());
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => { if (!savedTheme()) applyTheme(null); });

  function tabs(nav, onShow) {
    const buttons = [...nav.querySelectorAll("button[data-tab]")];
    function show(name) {
      for (const b of buttons) {
        const on = b.dataset.tab === name;
        b.setAttribute("aria-selected", on ? "true" : "false");
        document.getElementById("tab-" + b.dataset.tab).classList.toggle("hidden", !on);
      }
      const active = buttons.find(b => b.dataset.tab === name);
      const title = document.getElementById("page-title");
      if (title && active) {
        title.textContent = active.dataset.title || active.textContent.trim();
        const sub = document.getElementById("page-sub");
        if (sub) sub.textContent = active.dataset.sub || "";
      }
      document.querySelector(".sidebar")?.classList.remove("open");
      try { sessionStorage.setItem("sauti.tab." + location.pathname, name); } catch {}
      onShow && onShow(name);
    }
    buttons.forEach(b => b.addEventListener("click", () => show(b.dataset.tab)));
    let first = buttons[0].dataset.tab;
    try { first = sessionStorage.getItem("sauti.tab." + location.pathname) || first; } catch {}
    if (!buttons.some(b => b.dataset.tab === first)) first = buttons[0].dataset.tab;
    show(first);
    return show;
  }

  function decorate(root = document) {
    root.querySelectorAll("[data-icon]").forEach(el => { if (!el.querySelector("svg.icon")) el.prepend(icon(el.dataset.icon)); });
    root.querySelectorAll("[data-theme-toggle]").forEach(b => b.addEventListener("click", toggleTheme));
    root.querySelectorAll("[data-menu-toggle]").forEach(b => b.addEventListener("click", () => document.querySelector(".sidebar").classList.toggle("open")));
    applyTheme(savedTheme());
  }

  return { api, h, $, icon, platformIcon, toast, when, duration, initials, options, bar, sparkline, dialog, menu, drawer,
    tabs, decorate, token, setToken, LANGS, MODES, STYLES };
})();
