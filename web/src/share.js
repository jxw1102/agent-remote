// Read-only transcript viewer for a daemon-hosted share link.
//
// Reuses the web client's markdown renderer (md.js) so a shared session
// reads like the Agent Remote transcript pane — no composer, no list, no
// daemon token. The share token in the URL is the only credential.

import { renderMarkdown, inlineInto } from "./md.js";

const $ = (id) => document.getElementById(id);

function el(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
}

function toast(message) {
  const box = $("toast");
  if (!box) return;
  box.textContent = message;
  box.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => box.classList.add("hidden"), 2600);
}

async function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand && document.execCommand("copy");
  ta.remove();
  if (!ok) throw new Error("clipboard unavailable");
}

function tokenFromPath() {
  const parts = location.pathname.replace(/\/+$/, "").split("/");
  return parts[parts.length - 1] || "";
}

function expandMessages(messages, offset) {
  const out = [];
  messages.forEach((m, i) => {
    const id = `${offset + i}:${m.uuid || ""}`;
    const text = m.text || "";
    const ts = m.ts || m.timestamp || "";
    if (m.role === "user" && text.startsWith("[shell] ! ") && text.includes("\n[output]\n")) {
      const command = text.split("\n[output]\n")[0].replace(/^\[shell\] /, "").trim();
      const body = text.split("\n[output]\n")[1].split("\n[silent]")[0].trim();
      out.push({ id, role: "user", text: command, ts });
      if (body) out.push({ id: id + ":out", role: "assistant", text: body, ts });
      return;
    }
    out.push({ id, role: m.role, text, metaKind: m.metaKind || "", ts });
  });
  return out;
}

function copyButton(text) {
  const b = el("button", null, "Copy");
  b.type = "button";
  b.addEventListener("click", async () => {
    try {
      await copyText(text);
      toast("Copied");
    } catch {
      toast("Clipboard blocked by the browser");
    }
  });
  return b;
}

function renderMessage(item) {
  const wrap = el("div", `msg ${item.role}`);
  if (item.role === "user") {
    wrap.appendChild(inlineInto(el("span", "body"), item.text));
    const tools = el("div", "msg-tools");
    tools.appendChild(copyButton(item.text));
    wrap.appendChild(tools);
    return wrap;
  }
  if (item.role === "status" || item.role === "notice") {
    wrap.textContent = item.text;
    return wrap;
  }
  wrap.appendChild(renderMarkdown(item.text));
  const tools = el("div", "msg-tools");
  tools.appendChild(copyButton(item.text));
  wrap.appendChild(tools);
  return wrap;
}

function empty(title, detail) {
  const box = el("div", "empty");
  box.appendChild(el("h2", null, title));
  if (detail) box.appendChild(el("p", null, detail));
  return box;
}

function applyAccent(provider) {
  const p = String(provider || "").toLowerCase();
  const accent = p === "grok" ? "#00d4ff"
    : p === "claude" ? "#d97757"
    : p === "codex" ? "#9aa4b2"
    : "#9aa4b2";
  document.documentElement.style.setProperty("--accent", accent);
}

function expiresLabel(expiresIn) {
  const s = Number(expiresIn) || 0;
  if (s <= 0) return "expired";
  const days = Math.max(1, Math.round(s / 86400));
  return days === 1 ? "expires in 1 day" : `expires in ${days} days`;
}

async function load() {
  const token = tokenFromPath();
  const view = $("transcript");
  if (!token) {
    $("share-title").textContent = "Link not found";
    view.textContent = "";
    view.appendChild(empty("Link not found",
      "This address is not a share link."));
    return;
  }
  let data;
  try {
    const res = await fetch("/api/share/" + encodeURIComponent(token), {
      headers: { Accept: "application/json" },
    });
    data = await res.json().catch(() => ({}));
    if (!res.ok) {
      $("share-title").textContent = "Link not found";
      view.textContent = "";
      view.appendChild(empty(
        res.status === 404 ? "Link not found" : "Could not open this session",
        data.error || "This share link is invalid or has expired.",
      ));
      return;
    }
  } catch {
    $("share-title").textContent = "Could not open this session";
    view.textContent = "";
    view.appendChild(empty("Could not open this session",
      "The daemon did not respond."));
    return;
  }

  applyAccent(data.provider);
  $("share-title").textContent = data.title || "Shared session";
  document.title = (data.title || "Shared session") + " · Agent Remote";
  const bits = [];
  if (data.provider) bits.push(data.provider);
  bits.push("read only");
  bits.push(expiresLabel(data.expires_in));
  $("share-sub").textContent = bits.join(" · ");

  const items = expandMessages(data.messages || [], data.offset || 0);
  view.textContent = "";
  const thread = el("div", "thread");
  if ((data.offset || 0) > 0) {
    const btn = el("button", "load-older", "Load earlier messages");
    btn.type = "button";
    btn.addEventListener("click", () => loadOlder(token, data.offset, items, btn));
    thread.appendChild(btn);
  }
  items.forEach((item) => thread.appendChild(renderMessage(item)));
  if (!items.length)
    thread.appendChild(el("p", "empty", "This session has no messages yet."));
  view.appendChild(thread);
  view.scrollTop = view.scrollHeight;
}

async function loadOlder(token, earliest, items, btn) {
  if (earliest <= 0) return;
  const from = Math.max(0, earliest - 200);
  const count = earliest - from;
  btn.disabled = true;
  btn.textContent = "Loading…";
  try {
    const res = await fetch(
      "/api/share/" + encodeURIComponent(token)
      + "/messages?offset=" + from + "&limit=" + count,
      { headers: { Accept: "application/json" } },
    );
    const page = await res.json();
    if (!res.ok) throw new Error(page.error || "Could not load");
    const older = expandMessages(page.messages || [], page.offset || 0);
    const view = $("transcript");
    const before = view.scrollHeight - view.scrollTop;
    const thread = el("div", "thread");
    if ((page.offset || 0) > 0) {
      const next = el("button", "load-older", "Load earlier messages");
      next.type = "button";
      next.addEventListener("click", () => loadOlder(token, page.offset, older.concat(items), next));
      thread.appendChild(next);
    }
    older.concat(items).forEach((item) => thread.appendChild(renderMessage(item)));
    view.textContent = "";
    view.appendChild(thread);
    view.scrollTop = view.scrollHeight - before;
  } catch (e) {
    toast(e.message || "Could not load earlier messages");
    btn.disabled = false;
    btn.textContent = "Load earlier messages";
  }
}

load();
