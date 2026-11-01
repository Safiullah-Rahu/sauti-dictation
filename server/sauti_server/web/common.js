// Shared helpers for the user portal and the admin dashboard. No external libraries:
// the pages work on an offline network and send nothing to third parties.
"use strict";

const Sauti = (() => {
  const TOKEN_KEY = "sauti.token";
  const LANGS = { auto: "Auto-detect", en: "English", so: "Somali", ar: "Arabic", sw: "Swahili" };
  const MODES = { clean: "Clean dictation", verbatim: "Verbatim", translate: "Translate" };
  const STYLES = { neutral: "Neutral", formal: "Formal", casual: "Casual", message: "Chat message" };

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

  function $(sel, root) { return (root || document).querySelector(sel); }

  function toast(msg, ms = 2600) {
    const el = h("div", { class: "toast", role: "status" }, msg);
    document.body.append(el);
    setTimeout(() => el.remove(), ms);
  }

  function when(ts) {
    if (!ts) return "—";
    const d = new Date(ts * 1000);
    const diff = (Date.now() - d.getTime()) / 1000;
    if (diff < 60) return "just now";
    if (diff < 3600) return Math.round(diff / 60) + " min ago";
    if (diff < 86400) return Math.round(diff / 3600) + " h ago";
    return d.toLocaleString();
  }

  function options(select, map, value) {
    select.replaceChildren(...Object.entries(map).map(([k, v]) => h("option", { value: k, selected: k === value }, v)));
  }

  function tabs(nav, onShow) {
    const buttons = [...nav.querySelectorAll("button[data-tab]")];
    function show(name) {
      for (const b of buttons) {
        const on = b.dataset.tab === name;
        b.setAttribute("aria-selected", on ? "true" : "false");
        document.getElementById("tab-" + b.dataset.tab).classList.toggle("hidden", !on);
      }
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

  return { api, h, $, toast, when, options, tabs, token, setToken, LANGS, MODES, STYLES };
})();
