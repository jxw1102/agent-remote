// Agent Remote — browser client for agentremoted.
//
// Same model as the Android and BlackBerry apps: a *profile* is one daemon,
// the provider comes from that daemon's /api/ping (never from a build flag),
// and every enabled profile's sessions merge into one list.
//
// It is served BY a daemon, so the host you opened is same-origin; the others
// are reached cross-origin, which is why the daemon sends CORS headers. Auth
// is the token header, kept in localStorage — never a cookie.

import { renderMarkdown, inlineInto } from "./md.js";

const PROVIDERS = {
  claude: { label: "Claude", accent: "#d97757", heading: "#e08a5c", inline: "#e0a183" },
  grok: { label: "Grok", accent: "#00d4ff", heading: "#b9a2f0", inline: "#67e8f9" },
  codex: { label: "Codex", accent: "#10a37f", heading: "#3dd68c", inline: "#6ee7b7" },
};
const NEUTRAL = { label: "Agent", accent: "#9aa4b2", heading: "#9aa4b2", inline: "#9aa4b2" };
// Multi-harness host chrome (one profile, Claude+Grok+Codex) — purple, not gray.
const MULTI = { label: "Multi", accent: "#a78bfa", heading: "#c4b5fd", inline: "#c4b5fd" };
const providerOf = (p) => PROVIDERS[String(p || "").toLowerCase()] || NEUTRAL;

/** Harnesses this host profile can run (multi root or single provider). */
const profileHarnesses = (p) => {
  if (p && p.multi && Array.isArray(p.providers) && p.providers.length)
    return p.providers.slice();
  if (p && Array.isArray(p.providers) && p.providers.length > 1)
    return p.providers.slice();
  return p && p.provider ? [p.provider] : [];
};
/** "Claude · Grok · Codex" for multi hosts; single label otherwise. */
const profileHarnessLabel = (p) => {
  const hs = profileHarnesses(p);
  if (!hs.length) return "";
  return hs.map((h) => providerOf(h).label).join(" · ");
};
/** Multi-host profiles use purple chrome; single-provider keeps brand accent. */
const profileHostAccent = (p) => {
  const hs = profileHarnesses(p);
  if (hs.length > 1) return MULTI.accent;
  return providerOf(hs[0] || "").accent;
};
/** Session harness wins over profile default (multi tags each row). */
const sessionProvider = (session, profile) =>
  (session && session.provider) || (profile && profile.provider) || "";

// ---- ANSI → HTML for Live TUI (tmux capture-pane -e) --------------------
// BB keeps plain text; web/Android render colours. 16-colour + 256 + truecolor.
const _ANSI_FG = [
  "#0c0c0c", "#cd3131", "#0dbc79", "#e5e510",
  "#2472c8", "#bc3fbc", "#11a8cd", "#e5e5e5",
];
const _ANSI_FG_BRIGHT = [
  "#666666", "#f14c4c", "#23d18b", "#f5f543",
  "#3b8eea", "#d670d6", "#29b8db", "#e5e5e5",
];
const _ANSI_BG = [
  "#0c0c0c", "#cd3131", "#0dbc79", "#e5e510",
  "#2472c8", "#bc3fbc", "#11a8cd", "#e5e5e5",
];
function _ansi256(n) {
  n = n | 0;
  if (n < 0) return null;
  if (n < 8) return _ANSI_FG[n];
  if (n < 16) return _ANSI_FG_BRIGHT[n - 8];
  if (n < 232) {
    n -= 16;
    const r = Math.floor(n / 36), g = Math.floor((n % 36) / 6), b = n % 6;
    const c = (v) => (v === 0 ? 0 : 55 + v * 40);
    return `rgb(${c(r)},${c(g)},${c(b)})`;
  }
  const v = 8 + (n - 232) * 10;
  return `rgb(${v},${v},${v})`;
}
function _escHtml(s) {
  return String(s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}
/** Convert SGR sequences into <span style=…>…</span> (safe HTML). */
function ansiToHtml(raw) {
  if (!raw) return "";
  // Normalise CSI
  const s = String(raw).replace(/\u001b\][^\u0007\u001b]*(?:\u0007|\u001b\\)/g, "");
  let i = 0;
  let html = "";
  let bold = false, dim = false, italic = false, underline = false;
  let fg = null, bg = null;
  const open = () => {
    const st = [];
    if (fg) st.push(`color:${fg}`);
    if (bg) st.push(`background-color:${bg}`);
    if (bold) st.push("font-weight:700");
    if (dim) st.push("opacity:0.7");
    if (italic) st.push("font-style:italic");
    if (underline) st.push("text-decoration:underline");
    return st.length ? `<span style="${st.join(";")}">` : "";
  };
  let openTag = open();
  if (openTag) html += openTag;
  const re = /\u001b\[([0-9;]*)m/g;
  let m;
  let last = 0;
  while ((m = re.exec(s)) !== null) {
    const chunk = s.slice(last, m.index);
    if (chunk) html += _escHtml(chunk);
    last = re.lastIndex;
    if (openTag) html += "</span>";
    const parts = (m[1] || "0").split(";").map((x) => (x === "" ? 0 : parseInt(x, 10)));
    for (let p = 0; p < parts.length; p++) {
      const code = parts[p];
      if (code === 0 || Number.isNaN(code)) {
        bold = dim = italic = underline = false;
        fg = bg = null;
      } else if (code === 1) bold = true;
      else if (code === 2) dim = true;
      else if (code === 3) italic = true;
      else if (code === 4) underline = true;
      else if (code === 22) { bold = false; dim = false; }
      else if (code === 23) italic = false;
      else if (code === 24) underline = false;
      else if (code === 39) fg = null;
      else if (code === 49) bg = null;
      else if (code >= 30 && code <= 37) fg = _ANSI_FG[code - 30];
      else if (code >= 90 && code <= 97) fg = _ANSI_FG_BRIGHT[code - 90];
      else if (code >= 40 && code <= 47) bg = _ANSI_BG[code - 40];
      else if (code >= 100 && code <= 107) bg = _ANSI_FG_BRIGHT[code - 100];
      else if (code === 38 || code === 48) {
        const isFg = code === 38;
        const mode = parts[p + 1];
        if (mode === 5 && parts[p + 2] != null) {
          const c = _ansi256(parts[p + 2]);
          if (isFg) fg = c; else bg = c;
          p += 2;
        } else if (mode === 2 && parts[p + 4] != null) {
          const c = `rgb(${parts[p + 2]|0},${parts[p + 3]|0},${parts[p + 4]|0})`;
          if (isFg) fg = c; else bg = c;
          p += 4;
        }
      }
    }
    openTag = open();
    if (openTag) html += openTag;
  }
  if (last < s.length) html += _escHtml(s.slice(last));
  if (openTag) html += "</span>";
  // Drop other CSI (cursor etc.) that capture sometimes leaves
  return html.replace(/\u001b\[[0-9;?]*[A-Za-z]/g, "");
}

const PAGE = 60;
const POLL_IDLE_MS = 6000;   // stream healthy: timer is only a safety net
const POLL_ACTIVE_MS = 1500; // no usable stream: this is the real rate
const MAX_UPLOAD_BYTES = 16 * 1024 * 1024; // matches daemon max_upload_mb default

/**
 * crypto.randomUUID exists only in a secure context (https, localhost,
 * file://) — hosting this file on a plain-http box would otherwise crash on
 * the first profile save. Same story for the clipboard below.
 */
const uuid = () => (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
  ? crypto.randomUUID()
  : "p-" + Date.now().toString(36) + "-" + Math.random().toString(36).slice(2, 10));

async function copyText(text) {
  if (navigator.clipboard && navigator.clipboard.writeText) {
    await navigator.clipboard.writeText(text);
    return;
  }
  // Non-secure context: the old selection trick still works.
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

/** Same affordance as the BB10 long-press / Android "Session id" menu item. */
async function copySessionId(id) {
  const sid = String(id || "").trim();
  if (!sid) {
    toast("No session id yet");
    return;
  }
  try {
    await copyText(sid);
    toast("Session id copied");
  } catch {
    toast("Could not copy session id");
  }
}

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text !== undefined) n.textContent = text;
  return n;
};

// ------------------------------------------------------------------ state

const store = {
  load() {
    try {
      const raw = JSON.parse(localStorage.getItem("agentremote.profiles") || "{}");
      return {
        profiles: Array.isArray(raw.profiles) ? raw.profiles : [],
        settings: raw.settings || {},
      };
    } catch {
      return { profiles: [], settings: {} };
    }
  },
  save() {
    localStorage.setItem("agentremote.profiles", JSON.stringify({
      profiles: state.profiles, settings: state.settings,
    }));
  },
};

const state = {
  profiles: [],          // [{id, name, baseUrl, token, enabled, provider, caps, execMode, model, effort}]
  settings: {},
  rows: [],              // merged session list
  feeds: {},             // profileId -> {error, count}
  loading: false,
  query: "",
  filter: null,          // profileId or null
  active: {},            // profileId -> [activeJobDto]
  streams: {},           // profileId -> EventSource
  open: null,            // {profileId, sessionId, session}
  items: [],             // transcript rows
  total: 0,
  earliest: 0,
  job: null,             // {id, status, queued, pendingPermission, pendingQuestion, toolLine, startedAt}
  jobTimer: null,
  jobSince: 0,
  jobFails: 0,
  liveTui: false,        // Live TUI mode open
  liveTuiTimer: null,
  liveTuiSeq: 0,
  liveTuiKeys: false,    // pane has keyboard focus
  liveTuiEscArmed: false,
  askedQuestion: null,   // request_id already auto-opened (reopen via banner)
  askedPermission: null, // request_id already auto-opened (reopen via banner)
  gen: 0,                // fan-out generation guard
};

const profileById = (id) => state.profiles.find((p) => p.id === id) || null;
const enabledProfiles = () => state.profiles.filter((p) => p.enabled !== false && p.baseUrl && p.token);

// ------------------------------------------------------------------- chime
// Port of blackberry/src/chime.cpp — pitches from flipper-claude-buddy
// notifications.c (seq_success / seq_error / seq_perm). Scheduled square
// oscillators so we don't depend on AudioBuffer sample-rate quirks, and we
// always await AudioContext.resume() (browsers mute until a user gesture).

const CHIME = {
  AMP: 0.28,
  STATUS_GAP_MS: 1200,
  SEQ: {
    status: [{ hz: 600, ms: 180 }],
    done: [
      { hz: 523.25, ms: 100 }, // C5
      { hz: 659.25, ms: 100 }, // E5
      { hz: 783.99, ms: 120 }, // G5
    ],
    error: [
      { hz: 350, ms: 110 },
      { hz: 0, ms: 80 },
      { hz: 350, ms: 110 },
    ],
    // Flipper SoundPerm: rising C5-E5 — needs permission / question / plan.
    attention: [
      { hz: 523.25, ms: 100 }, // C5
      { hz: 0, ms: 50 },
      { hz: 659.25, ms: 100 }, // E5
    ],
  },
};

let chimeCtx = null;
let chimeLastStatusMs = 0;
let chimePlayGen = 0; // drop stale schedules if a newer cue supersedes
// Per-job chime tracking across EVERY profile's SSE active list — not only
// the open session. Keyed by "profileId/jobId".
const chimeJobs = new Map(); // key -> { sig, nextSeq }
const chimeEnded = new Set(); // keys we already played done/error for (dedup)

const soundCuesOn = () => state.settings.soundCues !== false; // default on

function ensureChimeCtx() {
  const AC = window.AudioContext || window.webkitAudioContext;
  if (!AC) return null;
  if (!chimeCtx) chimeCtx = new AC();
  return chimeCtx;
}

/** Unlock audio after a gesture; safe to call often. */
async function unlockChime() {
  const ctx = ensureChimeCtx();
  if (!ctx) return null;
  if (ctx.state === "suspended") {
    try { await ctx.resume(); } catch { return null; }
  }
  return ctx.state === "running" ? ctx : null;
}

/**
 * Schedule a mono square-wave sequence (chime.cpp's PC-speaker voice).
 * @param {"status"|"done"|"error"|"attention"} kind
 */
async function playChime(kind) {
  if (!soundCuesOn()) return;
  if (kind === "status") {
    const now = Date.now();
    if (now - chimeLastStatusMs < CHIME.STATUS_GAP_MS) return;
    chimeLastStatusMs = now;
  }
  const notes = CHIME.SEQ[kind];
  if (!notes) return;
  const gen = ++chimePlayGen;
  const ctx = await unlockChime();
  if (!ctx || gen !== chimePlayGen) return;

  const master = ctx.createGain();
  master.gain.value = CHIME.AMP;
  master.connect(ctx.destination);

  let t = ctx.currentTime + 0.005;
  for (const n of notes) {
    const dur = Math.max(0.001, n.ms / 1000);
    if (n.hz > 0) {
      const osc = ctx.createOscillator();
      const env = ctx.createGain();
      osc.type = "square";
      osc.frequency.setValueAtTime(n.hz, t);
      // Tiny attack/release so the hard square edge doesn't click.
      env.gain.setValueAtTime(0, t);
      env.gain.linearRampToValueAtTime(1, t + 0.002);
      env.gain.setValueAtTime(1, t + Math.max(0.003, dur - 0.004));
      env.gain.linearRampToValueAtTime(0, t + dur);
      osc.connect(env);
      env.connect(master);
      osc.start(t);
      osc.stop(t + dur + 0.01);
    }
    t += dur;
  }
}

/** Kind of work for a status-stream job frame (stable across elapsed ticks). */
function jobChimeSig(frame) {
  if (!frame) return "";
  if (frame.pending_permission) return "permission";
  if (frame.pending_question) return "question";
  const phase = frame.phase || "";
  const tool = frame.tool || "";
  if (phase || tool) return `${phase}/${tool}`;
  return "working";
}

/**
 * Drive sound cues from one daemon's active-job list.
 *
 * Status / attention fire on signature changes for any session on any
 * profile — not only the transcript that is currently open. When a job
 * vanishes from the list we fetch its final snapshot and play done/error
 * (stop stays silent), same idea as Android's JobWatchService.
 */
function chimeFromActive(profileId, jobs) {
  const next = new Map();
  (jobs || []).forEach((j) => {
    const jobId = j && j.job_id;
    if (!jobId) return;
    const key = `${profileId}/${jobId}`;
    const sig = jobChimeSig(j);
    next.set(key, {
      sig,
      nextSeq: typeof j.next_seq === "number" ? j.next_seq : 0,
    });
    const prev = chimeJobs.get(key);
    if (!prev) {
      // First sight: seed only. Blipping every job already running when the
      // page opens would be noise. Already-blocked turns still need the
      // attention cue so a question waiting on another session is heard.
      if (sig === "permission" || sig === "question") playChime("attention");
    } else if (prev.sig !== sig) {
      if (sig === "permission" || sig === "question") playChime("attention");
      else playChime("status");
    }
  });

  // Jobs that were running on this profile and are gone now have finished.
  for (const [key, meta] of chimeJobs) {
    if (!key.startsWith(profileId + "/")) continue;
    if (next.has(key)) continue;
    const jobId = key.slice(profileId.length + 1);
    chimeJobEnded(profileId, jobId, meta);
  }

  // Replace this profile's entries; leave other profiles alone.
  for (const key of [...chimeJobs.keys()]) {
    if (key.startsWith(profileId + "/")) chimeJobs.delete(key);
  }
  next.forEach((v, k) => chimeJobs.set(k, v));
}

/** Drop tracking for a profile without end-chimes (stream tear-down / disable). */
function chimeForgetProfile(profileId) {
  for (const key of [...chimeJobs.keys()]) {
    if (key.startsWith(profileId + "/")) chimeJobs.delete(key);
  }
}

/**
 * Done / error for a finished job. Deduped so the open-session poll and the
 * global SSE watcher don't both play when the same turn ends.
 */
async function chimeJobEnded(profileId, jobId, meta = {}) {
  const key = `${profileId}/${jobId}`;
  if (chimeEnded.has(key)) return;
  chimeEnded.add(key);
  setTimeout(() => chimeEnded.delete(key), 8000);

  const profile = profileById(profileId);
  if (!profile) {
    playChime("done");
    return;
  }
  const since = typeof meta.nextSeq === "number" ? meta.nextSeq : 0;
  try {
    const snap = await call(profile, `/api/jobs/${jobId}?since=${since}`,
      { timeout: 15000 });
    // User-initiated stop is silent (Android parity).
    if (snap.status === "stopped") return;
    if (snap.status === "error") playChime("error");
    else playChime("done");
  } catch {
    playChime("done");
  }
}

function setSoundCues(on) {
  state.settings.soundCues = !!on;
  store.save();
  syncSoundButton();
  if (on) playChime("status");
}

function syncSoundButton() {
  const b = $("btn-sound");
  if (!b) return;
  const on = soundCuesOn();
  b.setAttribute("aria-pressed", String(on));
  b.title = on
    ? "Sound cues on — click to mute"
    : "Sound cues off — click to enable";
  b.textContent = on ? "♪" : "♪̸";
}

// ------------------------------------------------------------------- http

class DaemonError extends Error {
  constructor(status, message) { super(message); this.status = status; }
}

async function call(profile, path, { method = "GET", body, timeout = 30000, raw = false } = {}) {
  const url = profile.baseUrl.replace(/\/+$/, "") + path;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  try {
    const res = await fetch(url, {
      method,
      headers: {
        "X-Auth-Token": profile.token,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: ctrl.signal,
      // Token auth, no cookies — see the daemon's _cors_headers.
      credentials: "omit",
      cache: "no-store",
    });
    if (raw) {
      if (!res.ok) throw new DaemonError(res.status, `HTTP ${res.status}`);
      return await res.blob();
    }
    let data = null;
    try { data = await res.json(); } catch { /* empty or non-JSON body */ }
    if (!res.ok) {
      const msg = (data && data.error)
        || (res.status === 401 ? "Token rejected by the daemon" : `HTTP ${res.status}`);
      throw new DaemonError(res.status, msg);
    }
    return data;
  } catch (e) {
    if (e instanceof DaemonError) throw e;
    if (e.name === "AbortError") throw new DaemonError(0, "The daemon did not answer in time");
    // A CORS rejection, mixed content block, or a dead host all land here as
    // an opaque TypeError ("Failed to fetch") — disambiguate when we can.
    if (e.message === "Failed to fetch") {
      const base = String(profile.baseUrl || "");
      const pageHttps = typeof location !== "undefined"
        && location.protocol === "https:";
      const daemonHttp = /^http:\/\//i.test(base);
      if (pageHttps && daemonHttp) {
        throw new DaemonError(0,
          "Blocked: this page is HTTPS but the daemon URL is plain HTTP. "
          + "Use an https:// daemon URL (TLS or Cloudflare), or open the "
          + "web UI over http://localhost instead.");
      }
      throw new DaemonError(0,
        "Could not reach the daemon (offline, CORS, or private-network "
        + "block — needs agentremoted 2.0.0+)");
    }
    throw new DaemonError(0, e.message || "Request failed");
  } finally {
    clearTimeout(timer);
  }
}

// ------------------------------------------------------------------ time

function epochOf(iso) {
  if (!iso) return 0;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : 0;
}
function stamp(ms) {
  if (!ms) return "";
  const d = new Date(ms), now = new Date();
  const sameDay = d.toDateString() === now.toDateString();
  // Always 24h (HH:mm), independent of the browser locale's 12h default.
  if (sameDay) {
    return d.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      hour12: false,
      hourCycle: "h23",
    });
  }
  if (d.getFullYear() === now.getFullYear()) {
    return d.toLocaleDateString([], { day: "numeric", month: "short" });
  }
  return d.toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" });
}
function dayLabel(ms) {
  if (!ms) return "Older";
  const d = new Date(ms), now = new Date();
  const yday = new Date(now); yday.setDate(now.getDate() - 1);
  if (d.toDateString() === now.toDateString()) return "Today";
  if (d.toDateString() === yday.toDateString()) return "Yesterday";
  return stamp(ms);
}
function elapsed(secs) {
  if (secs < 60) return `${secs}s`;
  const m = Math.floor(secs / 60), s = secs % 60;
  if (m < 60) return `${m}m ${s}s`;
  return `${Math.floor(m / 60)}h ${m % 60}m`;
}

/** Middle-ellipsis — port of BB10 shortDetail (default 48). */
function shortDetail(s, maxLen = 48) {
  const t = String(s || "").replace(/\s+/g, " ").trim();
  if (t.length <= maxLen) return t;
  if (maxLen < 3) return t.slice(0, maxLen);
  const keep = maxLen - 1;
  const head = Math.floor(keep / 2);
  const tail = keep - head;
  return t.slice(0, head) + "…" + t.slice(-tail);
}

/**
 * Human phrase for what the agent is doing — port of ApiClient::phaseLine.
 * One string only (no secondary gray tool column); the banner wraps it to
 * two lines, same as TranscriptPage's multiline maxLineCount: 2.
 */
function phaseLine(frame, job, pal) {
  const agent = (pal && pal.label) || "Agent";
  if (!frame) return shortDetail(job && job.toolLine) || `${agent} is working…`;
  const phase = frame.phase || "";
  const detail = shortDetail(frame.phase_detail || "");
  if (phase === "thinking") return `${agent} is thinking…`;
  if (phase === "writing") return `${agent} is writing…`;
  if (phase === "editing") return detail ? `Editing ${detail}` : "Editing files…";
  if (phase === "reading") return detail ? `Reading ${detail}` : "Reading files…";
  if (phase === "searching") return "Searching the code…";
  if (phase === "running") return detail ? `Running: ${detail}` : "Running a command…";
  if (phase === "browsing") return detail ? `Browsing ${detail}` : "Browsing the web…";
  if (phase === "delegating") return "Delegating to a subagent…";
  if (phase && detail) return `${phase} · ${detail}`;
  if (phase) return phase;
  // Fallback: last tool line (⚙ name  detail), same as the phone.
  const tool = shortDetail(frame.tool || "", 32);
  const toolDetail = shortDetail(frame.tool_detail || "");
  if (tool && toolDetail) return `⚙ ${tool}  ${toolDetail}`;
  if (tool) return `⚙ ${tool}`;
  if (toolDetail) return toolDetail;
  return shortDetail(job && job.toolLine) || `${agent} is working…`;
}

/**
 * Full live-status string the phone puts in one Label: phaseLine + · Ns
 * (+ · N queued). CSS clamps the block to two lines.
 */
function liveStatusLine(frame, job, pal) {
  let line = phaseLine(frame, job, pal);
  const secs = frame && typeof frame.elapsed_s === "number"
    ? frame.elapsed_s
    : Math.max(0, Math.round((Date.now() - (job.startedAt || Date.now())) / 1000));
  line += `  ·  ${elapsed(secs)}`;
  const queued = (job.queued && job.queued.length)
    || (frame && frame.queued_count)
    || 0;
  if (queued) line += `  ·  ${queued} queued`;
  return line;
}
function humanSize(n) {
  if (!n) return "0 B";
  if (n < 1024) return `${n} B`;
  if (n < 1048576) return `${Math.round(n / 1024)} KB`;
  if (n < 1073741824) return `${(n / 1048576).toFixed(1)} MB`;
  return `${(n / 1073741824).toFixed(1)} GB`;
}

function toast(message) {
  const box = $("toast");
  box.textContent = message;
  box.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => box.classList.add("hidden"), 2600);
}

// --------------------------------------------------------------- profiles

async function pingProfile(profile) {
  try {
    const ping = await call(profile, "/api/ping", { timeout: 10000 });
    profile.provider = ping.provider || "";
    profile.caps = ping.caps || {};
    profile.slashCommands = ping.slash_commands || [];
    profile.models = ping.models || [];
    profile.efforts = ping.efforts || [];
    profile.host = ping.host || "";
    profile.version = ping.version || "";
    // Multi-harness daemon (agentremoted ≥ 2.1): one profile, many providers.
    profile.multi = !!ping.multi;
    profile.providers = Array.isArray(ping.providers) ? ping.providers : [];
    profile.providerDetails = ping.provider_details || {};
    store.save();
    return true;
  } catch {
    return false;
  }
}

const capOf = (profile, key, fallback = false, harness = null) => {
  if (harness && profile && profile.providerDetails && profile.providerDetails[harness]) {
    const caps = profile.providerDetails[harness].caps || {};
    if (key in caps) return !!caps[key];
  }
  const caps = profile && profile.caps;
  return caps && key in caps ? !!caps[key] : fallback;
};
/**
 * Execution is only Interactive (host TUI) or Headless (CLI -p / exec).
 * Both always auto-approve tools — no acceptEdits / ask / plan UI.
 * Stored as "interactive" | "headless"; older values map to headless.
 * Wire API still wants "bypassPermissions" for headless (daemon contract).
 */
const normalizeExecMode = (mode, canInteractive = true) => {
  const m = String(mode || "").trim();
  if (m === "interactive" && canInteractive) return "interactive";
  // Legacy: bypassPermissions | acceptEdits | default | plan | Auto | …
  return "headless";
};
const wireExecMode = (mode) =>
  (normalizeExecMode(mode) === "interactive" ? "interactive" : "bypassPermissions");
const execModeLabel = (mode) =>
  (normalizeExecMode(mode) === "interactive" ? "Interactive" : "Headless");
const execModeOf = (profile, harness = null) => {
  const canInteractive = capOf(profile, "interactive", true, harness);
  if (profile && profile.execMode)
    return normalizeExecMode(profile.execMode, canInteractive);
  return canInteractive ? "interactive" : "headless";
};
const requiresCwd = (profile, harness = null) => {
  const h = harness || profile.provider;
  return capOf(profile, "requires_cwd", h !== "grok", harness);
};
const harnessesOf = (profile) => {
  if (profile && profile.multi && profile.providers && profile.providers.length)
    return profile.providers.slice();
  return profile && profile.provider ? [profile.provider] : [];
};
const modelsOf = (profile, harness = null) => {
  if (harness && profile && profile.providerDetails && profile.providerDetails[harness])
    return profile.providerDetails[harness].models || [];
  return (profile && profile.models) || [];
};
const slashOf = (profile, harness = null) => {
  if (harness && profile && profile.providerDetails && profile.providerDetails[harness])
    return profile.providerDetails[harness].slash_commands || [];
  return (profile && profile.slashCommands) || [];
};
const effortsOf = (profile, harness = null) => {
  if (harness && profile && profile.providerDetails && profile.providerDetails[harness])
    return profile.providerDetails[harness].efforts || [];
  return (profile && profile.efforts) || [];
};

// ----------------------------------------------------------- session list

async function refreshSessions() {
  const targets = enabledProfiles();
  const gen = ++state.gen;
  state.loading = true;
  state.feeds = {};
  renderStatus();

  if (!targets.length) {
    state.rows = [];
    state.loading = false;
    renderSessions();
    renderStatus();
    return;
  }

  const query = state.query.trim();
  const all = state.settings.showAll ? "&all=1" : "";
  const collected = [];
  await Promise.all(targets.map(async (profile) => {
    const path = query
      ? `/api/sessions/search?q=${encodeURIComponent(query)}&limit=40${all}`
      : `/api/sessions?limit=40${all}`;
    try {
      const data = await call(profile, path, { timeout: 45000 });
      const list = query ? (data.results || []) : (data.sessions || []);
      list.forEach((s) => collected.push({
        profileId: profile.id,
        profileName: profile.name || profile.baseUrl,
        // Multi daemon tags each session; single uses the profile provider.
        provider: s.provider || profile.provider || "",
        session: s,
        sortKey: epochOf(s.last_active) || epochOf(s.started),
      }));
      state.feeds[profile.id] = { count: list.length };
    } catch (e) {
      state.feeds[profile.id] = { error: e.message };
    }
  }));

  if (gen !== state.gen) return; // a newer refresh already owns the list
  state.rows = collected.sort((a, b) => b.sortKey - a.sortKey);
  state.loading = false;
  renderSessions();
  renderStatus();
}

function visibleRows() {
  return state.rows.filter((r) => !state.filter || r.profileId === state.filter);
}

function workingKeys() {
  const keys = new Set();
  for (const [pid, jobs] of Object.entries(state.active)) {
    for (const job of jobs) {
      for (const sid of [job.session_id, job.new_session_id]) {
        if (sid) keys.add(`${pid}/${sid}`);
      }
    }
  }
  return keys;
}
function blockedKeys() {
  const keys = new Set();
  for (const [pid, jobs] of Object.entries(state.active)) {
    for (const job of jobs) {
      if (!job.pending_permission && !job.pending_question) continue;
      for (const sid of [job.session_id, job.new_session_id]) {
        if (sid) keys.add(`${pid}/${sid}`);
      }
    }
  }
  return keys;
}

function renderStatus() {
  const box = $("list-status");
  box.textContent = "";
  const problems = Object.entries(state.feeds).filter(([, f]) => f.error);
  if (state.loading) {
    box.appendChild(el("span", "spinner"));
    box.appendChild(document.createTextNode(" Loading…"));
  } else {
    const working = workingKeys().size;
    const bits = [`${visibleRows().length} sessions`];
    if (working) bits.push(`${working} working`);
    box.appendChild(document.createTextNode(bits.join(" · ")));
  }
  // A dead daemon must never read as "it has no sessions".
  problems.forEach(([pid, f]) => {
    const p = profileById(pid);
    const line = el("div", "err", `${(p && p.name) || "Daemon"}: ${f.error}`);
    box.appendChild(line);
  });
}

function renderFilters() {
  const box = $("filters");
  box.textContent = "";
  if (state.profiles.length < 2) return;
  const mk = (label, id, accent) => {
    const b = el("button", "chip", label);
    b.type = "button";
    b.setAttribute("aria-pressed", String(state.filter === id));
    if (accent) {
      b.style.setProperty("--chip", accent);
      b.style.setProperty("--chip-soft", accent + "24");
    }
    b.addEventListener("click", () => {
      state.filter = state.filter === id ? null : id;
      renderFilters();
      renderSessions();
      renderStatus();
    });
    return b;
  };
  box.appendChild(mk("All", null, null));
  state.profiles.forEach((p) => {
    box.appendChild(mk(p.name || p.baseUrl, p.id, profileHostAccent(p)));
  });
}

function renderSessions() {
  const host = $("sessions");
  host.textContent = "";
  const rows = visibleRows();
  const working = workingKeys();
  const blocked = blockedKeys();

  if (!state.profiles.length) {
    const empty = el("div", "empty");
    empty.appendChild(el("h2", null, "No daemons yet"));
    empty.appendChild(el("p", null,
      "Add a daemon and its token. Claude on your Mac, Grok on your server — both land in this one list."));
    const b = el("button", "primary", "Add a daemon");
    b.type = "button";
    b.addEventListener("click", openProfiles);
    empty.appendChild(b);
    host.appendChild(empty);
    return;
  }
  if (!rows.length && !state.loading) {
    const empty = el("div", "empty");
    empty.appendChild(el("h2", null, state.query ? "No matches" : "Nothing here yet"));
    empty.appendChild(el("p", null, state.query
      ? `No session on any daemon mentions “${state.query}”.`
      : "Start a session and it will show up here, whichever daemon runs it."));
    host.appendChild(empty);
    return;
  }

  let lastDay = "";
  rows.forEach((row) => {
    if (!state.query) {
      const day = dayLabel(row.sortKey);
      if (day !== lastDay) {
        lastDay = day;
        host.appendChild(el("div", "day", day));
      }
    }
    const key = `${row.profileId}/${row.session.id}`;
    const pal = providerOf(row.provider);
    const btn = el("button", "row");
    btn.type = "button";
    if (state.open && state.open.profileId === row.profileId
        && state.open.sessionId === row.session.id) {
      btn.setAttribute("aria-current", "true");
    }

    const top = el("div", "row-top");
    // Working = breathing dot; permission/question = blinking "?" in that spot.
    if (blocked.has(key)) {
      const mark = el("span", "pulse-ask", "?");
      mark.title = "Waiting for your answer";
      mark.setAttribute("aria-label", "Waiting for your answer");
      top.appendChild(mark);
    } else if (working.has(key)) {
      const dot = el("span", "pulse");
      dot.style.background = pal.accent;
      top.appendChild(dot);
    }
    top.appendChild(el("div", "row-title", row.session.title || "Untitled session"));
    top.appendChild(el("div", "row-when", stamp(row.sortKey)));
    btn.appendChild(top);

    const meta = el("div", "row-meta");
    const tag = el("span", "tag provider", row.profileName);
    tag.style.setProperty("--tag", pal.accent);
    meta.appendChild(tag);
    const folder = String(row.session.cwd || "").replace(/\/+$/, "").split("/").pop();
    if (folder) meta.appendChild(el("span", "tag", folder));
    if (row.session.git_branch) meta.appendChild(el("span", "tag", "⑂ " + row.session.git_branch));
    if (blocked.has(key)) meta.appendChild(el("span", "tag waiting", "waiting for you"));
    if (row.session.id) {
      // Full id when space allows (CSS ellipsis); click copies without opening.
      const idTag = el("button", "tag session-id-tag", row.session.id);
      idTag.type = "button";
      idTag.title = `Copy session id\n${row.session.id}`;
      idTag.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        copySessionId(row.session.id);
      });
      meta.appendChild(idTag);
    }
    btn.appendChild(meta);

    btn.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      if (row.session.id) copySessionId(row.session.id);
    });

    const preview = state.query ? (row.session.snippet || row.session.last_text)
                               : row.session.last_text;
    if (preview) {
      const box = el("div", "row-preview");
      highlightInto(box, String(preview).replace(/\s+/g, " ").trim(), state.query);
      btn.appendChild(box);
    }

    btn.addEventListener("click", () => openSession(row));
    host.appendChild(btn);
  });
}

/** Case-insensitive query highlight, built with DOM nodes (never innerHTML). */
function highlightInto(host, text, query) {
  const q = (query || "").trim();
  if (!q) { host.textContent = text; return; }
  const lower = text.toLowerCase(), needle = q.toLowerCase();
  let at = 0;
  for (;;) {
    const hit = lower.indexOf(needle, at);
    if (hit < 0) break;
    if (hit > at) host.appendChild(document.createTextNode(text.slice(at, hit)));
    host.appendChild(el("mark", null, text.slice(hit, hit + needle.length)));
    at = hit + needle.length;
  }
  if (at < text.length) host.appendChild(document.createTextNode(text.slice(at)));
}

// ------------------------------------------------------------ transcript

function applyAccent(provider) {
  const pal = providerOf(provider);
  const root = document.documentElement.style;
  root.setProperty("--accent", pal.accent);
  root.setProperty("--accent-soft", pal.accent + "24");
  root.setProperty("--heading", pal.heading);
  root.setProperty("--inline-code", pal.inline);
}

async function openSession(row) {
  stopJobWatch();
  closeLiveTui();
  state.open = {
    profileId: row.profileId,
    sessionId: row.session.id,
    session: row.session,
    profileName: row.profileName,
  };
  state.items = [];
  state.total = 0;
  state.earliest = 0;
  state.job = null;
  state.askedQuestion = null;
  state.askedPermission = null;
  applyAccent(row.provider);

  $("chat-head").classList.remove("hidden");
  $("composer").classList.remove("hidden");
  $("chat-title").textContent = row.session.title || "Session";
  renderChatSub();
  updateLiveTuiButton();
  renderSessions();

  $("transcript").innerHTML = "";
  const loading = el("div", "empty");
  loading.appendChild(el("span", "spinner"));
  $("transcript").appendChild(loading);

  await loadTail();
  // Adopt whatever is already running for this session (started here, from
  // the desktop TUI, or on another device).
  const running = (state.active[row.profileId] || []).find(
    (j) => j.session_id === row.session.id || j.new_session_id === row.session.id);
  if (running) attachJob(running.job_id);
  updateLiveTuiButton();
}

// ---------------------------------------------------------------- live TUI

function profileSupportsLiveTui(profile, harness) {
  if (!profile) return false;
  return !!capOf(profile, "live_tui", false, harness)
    || !!capOf(profile, "interactive", false, harness);
}

function updateLiveTuiButton() {
  const btn = $("btn-live-tui");
  if (!btn) return;
  const open = state.open;
  if (!open || !open.sessionId) {
    btn.classList.add("hidden");
    return;
  }
  const profile = profileById(open.profileId);
  const harness = sessionProvider(open.session, profile);
  if (!profileSupportsLiveTui(profile, harness)) {
    btn.classList.add("hidden");
    return;
  }
  btn.classList.remove("hidden");
  btn.setAttribute("aria-pressed", state.liveTui ? "true" : "false");
  btn.title = state.liveTui
    ? "Back to transcript"
    : "Live TUI — host terminal for this session";
}

function openLiveTui() {
  if (!state.open || !state.open.sessionId) return;
  state.liveTui = true;
  state.liveTuiSeq = 0;
  $("live-tui").classList.remove("hidden");
  $("transcript").classList.add("hidden");
  $("composer").classList.add("hidden");
  updateLiveTuiButton();
  const pane = $("live-tui-pane");
  pane.textContent = "Connecting to host TUI…";
  pane.classList.add("empty-tui");
  $("live-tui-status").textContent = "Host TUI";
  $("live-tui-status").classList.remove("live");
  pollLiveTui(true);
  clearInterval(state.liveTuiTimer);
  state.liveTuiTimer = setInterval(() => pollLiveTui(false), 400);
}

function closeLiveTui() {
  state.liveTui = false;
  state.liveTuiKeys = false;
  state.liveTuiEscArmed = false;
  clearInterval(state.liveTuiTimer);
  state.liveTuiTimer = null;
  const box = $("live-tui");
  if (box) box.classList.add("hidden");
  const tr = $("transcript");
  if (tr) tr.classList.remove("hidden");
  if (state.open) {
    $("composer")?.classList.remove("hidden");
  }
  updateLiveTuiButton();
}

async function pollLiveTui(force) {
  if (!state.liveTui || !state.open) return;
  const profile = profileById(state.open.profileId);
  if (!profile) return;
  try {
    // Colour clients opt in; default daemon payload is plain for BB.
    const frame = await call(
      profile,
      `/api/sessions/${encodeURIComponent(state.open.sessionId)}/tui?ansi=1`,
    );
    if (!state.liveTui) return;
    const status = $("live-tui-status");
    const pane = $("live-tui-pane");
    if (!frame || frame.attached === false) {
      status.textContent = frame?.error || "No host TUI attached";
      status.classList.remove("live");
      if (force || !pane.dataset.hadFrame) {
        pane.textContent = frame?.error
          || "No interactive TUI for this session. Start a turn in Interactive mode.";
        pane.classList.add("empty-tui");
        delete pane.dataset.hadFrame;
      }
      return;
    }
    if (!force && frame.seq === state.liveTuiSeq) return;
    state.liveTuiSeq = frame.seq;
    pane.dataset.hadFrame = "1";
    pane.classList.remove("empty-tui");
    const atBottom = pane.scrollHeight - pane.scrollTop - pane.clientHeight < 48;
    // Coloured SGR when ?ansi=1 (default plain has no escapes).
    const raw = frame.text || "(empty pane)";
    if (frame.ansi || raw.includes("\u001b[") || raw.includes("\x1b[")) {
      pane.innerHTML = ansiToHtml(raw);
    } else {
      pane.textContent = raw;
    }
    if (atBottom) pane.scrollTop = pane.scrollHeight;
    status.textContent = frame.job_id
      ? `Host TUI · job ${String(frame.job_id).slice(0, 8)}`
      : "Host TUI · live";
    status.classList.add("live");
  } catch (e) {
    if (!state.liveTui) return;
    $("live-tui-status").textContent = e.message || "Live TUI error";
    $("live-tui-status").classList.remove("live");
  }
}

async function sendLiveTuiKeys(keys, text) {
  if (!state.open || !state.open.sessionId) return;
  const profile = profileById(state.open.profileId);
  if (!profile) return;
  const body = {};
  if (keys && keys.length) body.keys = keys;
  if (text) body.text = text;
  if (!body.keys && !body.text) return;
  try {
    await call(
      profile,
      `/api/sessions/${encodeURIComponent(state.open.sessionId)}/tui/keys`,
      { method: "POST", body },
    );
    // Immediate refresh so typing feels snappy.
    setTimeout(() => pollLiveTui(true), 80);
  } catch (e) {
    toast(e.message || "TUI input failed");
  }
}

function liveTuiKeyName(e) {
  if (e.ctrlKey || e.metaKey) {
    const k = (e.key || "").toLowerCase();
    if (k.length === 1 && k >= "a" && k <= "z") return "Ctrl+" + k.toUpperCase();
    return null;
  }
  if (e.key === "Escape") return "Escape";
  if (e.key === "Enter") return "Enter";
  if (e.key === "Backspace") return "Backspace";
  if (e.key === "Tab") return "Tab";
  if (e.key === "ArrowUp") return "Up";
  if (e.key === "ArrowDown") return "Down";
  if (e.key === "ArrowLeft") return "Left";
  if (e.key === "ArrowRight") return "Right";
  if (e.key === "Home") return "Home";
  if (e.key === "End") return "End";
  if (e.key === "PageUp") return "PageUp";
  if (e.key === "PageDown") return "PageDown";
  if (e.key === "Delete") return "Delete";
  if (e.key.length === 1 && !e.altKey) return e.key; // printable
  return null;
}

/**
 * Profile badge, cwd, and a clickable session id (the web counterpart of
 * BB10's long-press "Copy session ID" / Android's overflow menu item).
 */
function renderChatSub() {
  const open = state.open;
  const sub = $("chat-sub");
  if (!open || !sub) return;
  sub.textContent = "";
  const profile = profileById(open.profileId);
  const harness = sessionProvider(open.session, profile);
  const pal = providerOf(harness);
  // Profile name + harness when the host is multi (one machine, several CLIs).
  let tagText = open.profileName || (profile && profile.name) || "Daemon";
  if (profileHarnesses(profile).length > 1 && harness)
    tagText = `${tagText} · ${pal.label}`;
  const tag = el("span", "tag provider", tagText);
  tag.style.setProperty("--tag", pal.accent);
  sub.appendChild(tag);
  const cwd = open.session && open.session.cwd;
  if (cwd) sub.appendChild(el("span", "chat-cwd", cwd));
  const sid = open.sessionId;
  if (sid) {
    // Full id when the header has room; CSS ellipsis only if cramped.
    // Click still copies (no "· copy" label — previous look was cleaner).
    const chip = el("button", "session-id", sid);
    chip.type = "button";
    chip.title = `Copy session id\n${sid}`;
    chip.addEventListener("click", (e) => {
      e.stopPropagation();
      copySessionId(sid);
    });
    sub.appendChild(chip);
  }
}

async function loadTail() {
  const open = state.open;
  const profile = profileById(open.profileId);
  if (!profile) return;
  try {
    const page = await call(profile,
      `/api/sessions/${encodeURIComponent(open.sessionId)}/messages?limit=${PAGE}`,
      { timeout: 60000 });
    state.total = page.total || 0;
    state.earliest = page.offset || 0;
    state.items = expandMessages(page.messages || [], page.offset || 0);
    renderTranscript(true);
  } catch (e) {
    $("transcript").innerHTML = "";
    const box = el("div", "empty");
    box.appendChild(el("h2", null, "Could not load this session"));
    box.appendChild(el("p", null, e.message));
    $("transcript").appendChild(box);
  }
}

async function loadOlder(btn) {
  const open = state.open;
  const profile = profileById(open.profileId);
  if (!profile || state.earliest <= 0) return;
  const from = Math.max(0, state.earliest - PAGE);
  const count = state.earliest - from;
  btn.disabled = true;
  btn.textContent = "Loading…";
  try {
    const page = await call(profile,
      `/api/sessions/${encodeURIComponent(open.sessionId)}/messages?offset=${from}&limit=${count}`,
      { timeout: 60000 });
    state.earliest = page.offset || 0;
    // Keep the reader where they were: measure, prepend, restore.
    const view = $("transcript");
    const before = view.scrollHeight - view.scrollTop;
    state.items = expandMessages(page.messages || [], page.offset || 0).concat(state.items);
    renderTranscript(false);
    view.scrollTop = view.scrollHeight - before;
  } catch (e) {
    toast(e.message);
    btn.disabled = false;
    btn.textContent = "Load earlier messages";
  }
}

/**
 * `!cmd` turns are stored as ONE user message carrying the command, its
 * output and a `[silent]` directive to the agent. Replay that verbatim and
 * the user reads their own plumbing, so split it back into the two rows it
 * looked like when it ran and drop the directive.
 */
function expandMessages(messages, offset) {
  const out = [];
  messages.forEach((m, i) => {
    const id = `${offset + i}:${m.uuid || ""}`;
    const text = m.text || "";
    if (m.role === "user" && text.startsWith("[shell] ! ") && text.includes("\n[output]\n")) {
      const command = text.split("\n[output]\n")[0].replace(/^\[shell\] /, "").trim();
      const body = text.split("\n[output]\n")[1].split("\n[silent]")[0].trim();
      out.push({ id, role: "user", text: command });
      if (body) out.push({ id: id + ":out", role: "assistant", text: body });
      return;
    }
    out.push({ id, role: m.role, text, metaKind: m.metaKind || "" });
  });
  return out;
}

function renderTranscript(toBottom) {
  const view = $("transcript");
  view.textContent = "";
  const thread = el("div", "thread");

  if (state.earliest > 0) {
    const btn = el("button", "load-older", "Load earlier messages");
    btn.type = "button";
    btn.addEventListener("click", () => loadOlder(btn));
    thread.appendChild(btn);
  }

  state.items.forEach((item) => thread.appendChild(renderMessage(item)));
  view.appendChild(thread);
  if (toBottom) view.scrollTop = view.scrollHeight;
}

function renderMessage(item) {
  const wrap = el("div", `msg ${item.role}${item.severity === "error" ? " error" : ""}`);
  if (item.role === "user") {
    wrap.appendChild(inlineInto(el("span", "body"), item.text));
    const tools = el("div", "msg-tools");
    tools.appendChild(copyButton(item.text));
    const profile = profileById(state.open.profileId);
    // Rewind exists only where the TUI keeps checkpoints; offering an action
    // that would fail is worse than not offering it.
    const rwHarness = sessionProvider(state.open && state.open.session, profile);
    if (profile && capOf(profile, "rewind", false, rwHarness)
        && execModeOf(profile) === "interactive") {
      const back = userStepsBack(item.id);
      if (back > 0) {
        const b = el("button", null, "Rewind to here");
        b.type = "button";
        b.addEventListener("click", () => confirmRewind(item, back));
        tools.appendChild(b);
      }
    }
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

function userStepsBack(itemId) {
  let back = 0;
  for (let i = state.items.length - 1; i >= 0; i--) {
    if (state.items[i].role !== "user") continue;
    back++;
    if (state.items[i].id === itemId) return back;
  }
  return 0;
}

function confirmRewind(item, back) {
  modal({
    title: "Rewind the session?",
    build(body) {
      body.appendChild(el("p", null, back === 1
        ? "The conversation goes back to just before this message, dropping your last message and the reply to it."
        : `The conversation goes back to just before this message, dropping the last ${back} of your messages and everything after them.`));
      const warn = el("p", null,
        "This cannot be undone. On Grok it also reverts file changes made since then, and anything uncommitted is lost.");
      warn.style.color = "var(--danger)";
      body.appendChild(warn);
      const quote = el("pre", null, item.text.split("\n")[0].slice(0, 160));
      quote.style.color = "var(--dim)";
      quote.style.whiteSpace = "pre-wrap";
      body.appendChild(quote);
    },
    actions: [
      { label: "Cancel", close: true },
      { label: "Rewind", danger: true, close: true, run: () => send(`/rewind ${back}`) },
    ],
  });
}

// ------------------------------------------------------------------- send

function composerNote(text, isError) {
  const box = $("composer-note");
  if (!text) { box.classList.add("hidden"); return; }
  box.textContent = text;
  box.className = "composer-note" + (isError ? " err" : "");
  box.classList.remove("hidden");
  clearTimeout(composerNote._t);
  if (!isError) composerNote._t = setTimeout(() => box.classList.add("hidden"), 2500);
}

/**
 * The composer's contract, same as the apps:
 *  - `!cmd` runs a shell command on the daemon in this session's folder;
 *  - a `/command` is refused unless the turn runs in the host TUI (headless
 *    answers "unknown skill" and burns the whole turn);
 *  - while a turn runs, interactive types into the TUI and headless queues on
 *    the daemon — never on this page, which can be closed at any moment.
 */
async function send(text) {
  const raw = String(text || "").trim();
  if (!raw || !state.open) return;
  const profile = profileById(state.open.profileId);
  if (!profile) return;

  if (raw.startsWith("!")) return runShell(raw.slice(1).trim());

  if (/^\/[A-Za-z][A-Za-z0-9_-]*$/.test(raw.split(" ")[0])) {
    const cmd = raw.split(" ")[0];
    const interactive = execModeOf(profile) === "interactive";
    if (!interactive) {
      composerNote(`${cmd} needs interactive execution — headless turns cannot run commands`, true);
      return;
    }
    // No hardcoded whitelist: the daemon advertises each harness's real
    // built-ins (claude/grok: /compact /exit /rewind — codex has no rewind),
    // so anything off the OPEN session's list is refused before it costs a
    // turn on a command that harness would not understand.
    const known = slashOf(profile, sessionProvider(state.open && state.open.session, profile));
    if (!known.includes(cmd)) {
      composerNote(known.length
        ? `${cmd} is not available here — try: ${known.slice(0, 6).join(" ")}`
        : "This daemon does not advertise any slash commands", true);
      return;
    }
  }

  const running = !!(state.job && state.job.id);
  appendLive({ role: "user", text: raw });

  if (running) {
    const interactive = execModeOf(profile) === "interactive";
    try {
      await call(profile, `/api/jobs/${state.job.id}/${interactive ? "input" : "queue"}`,
        { method: "POST", body: { prompt: raw } });
      composerNote(interactive ? "Typed into the session" : "Queued");
    } catch (e) {
      composerNote(e.message, true);
    }
    return;
  }

  try {
    const res = await call(profile,
      `/api/sessions/${encodeURIComponent(state.open.sessionId)}/continue`,
      {
        method: "POST",
        body: {
          prompt: raw,
          permission_mode: wireExecMode(execModeOf(profile)),
          model: profile.model || "",
          effort: profile.effort || "",
        },
      });
    if (res && res.job_id) attachJob(res.job_id);
  } catch (e) {
    appendLive({ role: "notice", text: e.message, severity: "error" });
  }
}

async function runShell(command) {
  if (!command) return;
  const profile = profileById(state.open.profileId);
  appendLive({ role: "user", text: "! " + command });
  composerNote("Running…");
  try {
    const res = await call(profile, "/api/shell", {
      method: "POST",
      timeout: 40000,
      body: {
        command,
        session_id: state.open.sessionId,
        cwd: (state.open.session && state.open.session.cwd) || "",
      },
    });
    const body = (res.output || "").replace(/\s+$/, "") || "(no output)";
    appendLive({ role: "assistant", text: "```\n" + body + "\n```" });
    composerNote("");
    // Hand it to the agent as context, with a directive not to reply.
    const prompt = `[shell] ! ${command}\n[output]\n\`\`\`\n${body.slice(0, 8000)}`
      + (res.exit_code ? `\n(exit code ${res.exit_code})` : "")
      + "\n```\n[silent] Shell result for context only. Do not reply or acknowledge"
      + " this message - wait for the next user instruction.";
    const started = await call(profile,
      `/api/sessions/${encodeURIComponent(state.open.sessionId)}/continue`,
      { method: "POST", body: { prompt, permission_mode: wireExecMode(execModeOf(profile)),
                                model: profile.model || "", effort: profile.effort || "" } });
    if (started && started.job_id) attachJob(started.job_id);
  } catch (e) {
    composerNote(e.message, true);
  }
}

function appendLive(item) {
  state.items.push({ id: `live-${state.items.length}-${Date.now()}`, live: true, ...item });
  const thread = $("transcript").querySelector(".thread");
  if (thread) {
    thread.appendChild(renderMessage(state.items[state.items.length - 1]));
    const view = $("transcript");
    view.scrollTop = view.scrollHeight;
  } else {
    renderTranscript(true);
  }
}

// -------------------------------------------------------------- job watch

function attachJob(jobId) {
  stopJobWatch();
  state.job = { id: jobId, status: "starting", queued: [], startedAt: Date.now(), toolLine: "" };
  state.jobSince = 0;
  state.jobFails = 0;
  // Allow an immediate status blip for the new turn (global gap timer).
  chimeLastStatusMs = 0;
  renderBanner();
  $("btn-stop").classList.remove("hidden");
  state.jobTimer = setInterval(pollJob, 250);
  pollJob();
}

function stopJobWatch() {
  if (state.jobTimer) clearInterval(state.jobTimer);
  state.jobTimer = null;
  state.job = null;
  state.jobLastFetch = 0;
  $("btn-stop").classList.add("hidden");
  renderBanner();
}

/**
 * Event-driven polling, same doorbell the apps use: the status stream already
 * pushes each job's next_seq about once a second, so the expensive job fetch
 * only fires when that cursor moves. The timer is the fallback for when the
 * stream is down.
 */
async function pollJob() {
  const job = state.job;
  if (!job || !state.open) return;
  renderBanner();

  const profile = profileById(state.open.profileId);
  if (!profile) return;
  const frame = (state.active[profile.id] || []).find((j) => j.job_id === job.id);
  const now = Date.now();
  let due = false;

  if (frame) {
    if (typeof frame.next_seq === "number" && frame.next_seq > state.jobSince) due = true;
    if (frame.queued_count !== job.lastQueued) { due = true; job.lastQueued = frame.queued_count; }
    const blocked = !!(frame.pending_permission || frame.pending_question);
    if (blocked !== job.lastBlocked) { due = true; job.lastBlocked = blocked; }
    job.sawFrame = true;
  } else if (job.sawFrame) {
    due = true; // dropped out of the active list: it ended
  }
  const streamOk = frame && typeof frame.next_seq === "number" && state.streams[profile.id];
  const interval = streamOk ? POLL_IDLE_MS : POLL_ACTIVE_MS;
  if (now - (state.jobLastFetch || 0) >= interval) due = true;
  if (!due || job.inFlight) return;

  job.inFlight = true;
  state.jobLastFetch = now;
  let snap;
  try {
    snap = await call(profile, `/api/jobs/${job.id}?since=${state.jobSince}`);
    state.jobFails = 0;
  } catch {
    if (++state.jobFails >= 5) {
      appendLive({ role: "notice", text: "Lost contact with the daemon", severity: "error" });
      stopJobWatch();
    }
    return;
  } finally {
    job.inFlight = false;
  }
  if (state.job !== job) return; // detached while the request was in flight

  state.jobSince = snap.next_seq || 0;
  (snap.events || []).forEach((ev) => {
    if (ev.kind === "text" && ev.text) appendLive({ role: "assistant", text: ev.text });
    else if (ev.kind === "tool") {
      job.toolLine = [ev.name, ev.detail].filter(Boolean).join("  ");
    }
  });
  job.status = snap.status || "";
  job.queued = snap.queued || [];
  job.pendingPermission = snap.pending_permission || null;
  job.pendingQuestion = snap.pending_question || null;

  // Auto-open once per request_id. Dismissing the modal does NOT cancel the
  // ask on the daemon — renderBanner keeps an "Answer" / "Respond" CTA so
  // the user can reopen (same idea as the phone's QuestionSheet banner).
  if (!job.pendingPermission) state.askedPermission = null;
  if (!job.pendingQuestion) state.askedQuestion = null;
  if (job.pendingPermission
      && job.pendingPermission.request_id !== state.askedPermission) {
    state.askedPermission = job.pendingPermission.request_id;
    showPermission(job.pendingPermission);
  }
  if (job.pendingQuestion
      && job.pendingQuestion.request_id !== state.askedQuestion) {
    state.askedQuestion = job.pendingQuestion.request_id;
    showQuestion(job.pendingQuestion);
  }

  // A headless resume forks the session; follow the fork.
  if (snap.new_session_id && snap.new_session_id !== state.open.sessionId) {
    state.open.sessionId = snap.new_session_id;
    if (state.open.session) state.open.session.id = snap.new_session_id;
    renderChatSub();
  }
  // The daemon chains queued prompts: follow the chain, don't tear down.
  if (snap.status === "done" && snap.next_job_id) {
    job.id = snap.next_job_id;
    state.jobSince = 0;
    job.sawFrame = false;
    job.toolLine = "";
    job.startedAt = Date.now();
    renderBanner();
    return;
  }
  if (["done", "error", "stopped"].includes(snap.status)) {
    const notes = [];
    if (snap.status === "error") notes.push(snap.error || "The turn failed");
    if (snap.status === "stopped") notes.push("Stopped");
    if (snap.dropped_queued) notes.push(`${snap.dropped_queued} queued prompt(s) dropped`);
    // End cue is shared with the global SSE watcher (deduped by key).
    if (snap.status === "done" || snap.status === "error") {
      chimeJobEnded(profile.id, job.id, { nextSeq: state.jobSince });
    }
    stopJobWatch();
    // Replace the live echoes with what the daemon actually persisted.
    await loadTail();
    if (notes.length) appendLive({ role: "notice", text: notes.join(" · "),
                                  severity: snap.status === "error" ? "error" : "" });
    refreshSessions();
  }
  renderBanner();
}

function renderBanner() {
  const box = $("banner");
  const job = state.job;
  if (!job) {
    box.classList.add("hidden");
    box.classList.remove("needs-answer");
    return;
  }
  const profile = profileById(state.open.profileId);
  const frame = (state.active[profile.id] || []).find((j) => j.job_id === job.id);
  const pal = providerOf(sessionProvider(state.open && state.open.session, profile));
  // Sound cues are driven by chimeFromActive (every profile's SSE list), not
  // only this open-session banner — so a question/done on another session
  // still beeps while you are reading this one.

  box.textContent = "";
  box.classList.remove("hidden");
  box.classList.toggle("needs-answer", !!(job.pendingQuestion || job.pendingPermission));
  box.appendChild(el("span", "pulse"));

  // Blocking human gates: keep a re-open control even after the modal is
  // dismissed (✕ / backdrop / Escape). Cancel and Send answer still go
  // through the modal itself.
  if (job.pendingQuestion) {
    box.appendChild(el("span", "banner-line", "A question is waiting for your answer"));
    const btn = el("button", "banner-action primary", "Answer");
    btn.type = "button";
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      showQuestion(job.pendingQuestion);
    });
    box.appendChild(btn);
  } else if (job.pendingPermission) {
    const tool = job.pendingPermission.tool_name || "a tool";
    box.appendChild(el("span", "banner-line", `Permission needed · ${tool}`));
    const btn = el("button", "banner-action primary", "Respond");
    btn.type = "button";
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      showPermission(job.pendingPermission, { force: true });
    });
    box.appendChild(btn);
  } else {
    // Mobile parity: one Label, multiline max 2 lines, elapsed/queued
    // baked into the string (not a separate gray column).
    box.appendChild(el("span", "banner-line", liveStatusLine(frame, job, pal)));
  }
}

// ---------------------------------------------------------------- streams

function syncStreams() {
  const wanted = new Map(enabledProfiles().map((p) => [p.id, p]));
  for (const [id, src] of Object.entries(state.streams)) {
    const p = wanted.get(id);
    if (!p || src._url !== streamUrl(p)) {
      src.close();
      delete state.streams[id];
      delete state.active[id];
      // Tear-down is not "job finished" — forget without end-chimes.
      chimeForgetProfile(id);
    }
  }
  wanted.forEach((profile, id) => {
    if (state.streams[id]) return;
    const url = streamUrl(profile);
    // EventSource cannot send headers, which is exactly why the daemon also
    // accepts the token as a query parameter on this endpoint.
    const src = new EventSource(url);
    src._url = url;
    src.onmessage = (ev) => {
      let jobs = [];
      try {
        const frame = JSON.parse(ev.data);
        jobs = frame.active || [];
        state.active[id] = jobs;
      } catch { return; }
      // Chimes for every session on this daemon, open or not.
      chimeFromActive(id, jobs);
      renderStatus();
      renderSessions();
      renderBanner();
    };
    src.onerror = () => {
      // EventSource reconnects on its own; drop the stale view meanwhile.
      // Do NOT chime ends here — a glitch would bi-bi every running job.
      state.active[id] = [];
    };
    state.streams[id] = src;
  });
}

function streamUrl(profile) {
  return profile.baseUrl.replace(/\/+$/, "")
    + "/sse/status?token=" + encodeURIComponent(profile.token);
}

// ----------------------------------------------------------------- modals

function modal({ title, build, actions = [], wide = false }) {
  const host = $("modal");
  $("modal-title").textContent = title;
  const body = $("modal-body");
  const foot = $("modal-foot");
  body.textContent = "";
  foot.textContent = "";
  if (build) build(body);
  actions.forEach((a) => {
    const b = el("button", a.primary ? "primary" : (a.danger ? "danger" : null), a.label);
    b.type = "button";
    if (a.id) b.id = a.id;
    if (a.disabled) b.disabled = true;
    b.addEventListener("click", async () => {
      if (a.run) await a.run();
      if (a.close !== false) closeModal();
    });
    foot.appendChild(b);
  });
  host.classList.remove("hidden");
  host.querySelector(".modal-card").style.width = wide ? "min(900px, 100%)" : "";
  return { body, foot };
}
function closeModal() {
  $("modal").classList.add("hidden");
  // New-session (and other) modals may recolor chrome via applyAccent(harness);
  // restore the open session's brand, or neutral multi chrome when idle.
  if (state.open) {
    const p = profileById(state.open.profileId);
    applyAccent(sessionProvider(state.open.session, p));
  }
}

// ---------------------------------------------------------------- daemons

function openProfiles() {
  // Re-ping so multi catalogues (providers[]) stay fresh after daemon upgrades.
  Promise.all(state.profiles.map((p) => pingProfile(p))).then(() => {
    store.save();
    renderFilters();
    // Rebuild the open sheet if it is still the Daemons list.
    const title = $("modal-title");
    if (title && title.textContent === "Daemons" && !$("modal").classList.contains("hidden"))
      paintProfilesModal();
  });
  paintProfilesModal();
}

function paintProfilesModal() {
  modal({
    title: "Daemons",
    build(body) {
      const list = el("div", "plist");
      state.profiles.forEach((p, i) => {
        const hs = profileHarnesses(p);
        const accent = profileHostAccent(p);
        const card = el("button", "pcard" + (hs.length > 1 ? " multi" : ""));
        card.type = "button";
        card.style.setProperty("--pc", accent);
        card.setAttribute("aria-pressed", String(p.enabled !== false));
        const dot = el("span", "pdot");
        // Multi-host: pie-slice dot of each harness colour instead of a single brand.
        if (hs.length > 1) {
          const slice = 100 / hs.length;
          const stops = hs.map((h, idx) => {
            const c = providerOf(h).accent;
            return `${c} ${idx * slice}% ${(idx + 1) * slice}%`;
          }).join(", ");
          dot.style.background = `conic-gradient(${stops})`;
        }
        card.appendChild(dot);
        const col = el("div");
        col.style.flex = "1";
        col.appendChild(el("div", "pname", p.name || p.baseUrl));
        const bits = [p.baseUrl.replace(/^https?:\/\//, "")];
        const harnessLabel = profileHarnessLabel(p);
        if (harnessLabel) bits.unshift(harnessLabel);
        if (p.version) bits.push("agentremoted " + p.version);
        col.appendChild(el("div", "phost", bits.join(" · ")));
        card.appendChild(col);
        card.appendChild(el("span", "tag", p.enabled === false ? "off" : "on"));
        card.title = "Click to include/exclude from the list";
        card.addEventListener("click", () => {
          p.enabled = p.enabled === false;
          store.save();
          paintProfilesModal();
          syncStreams();
          refreshSessions();
          renderFilters();
        });
        const edit = el("button", null, "Edit");
        edit.type = "button";
        edit.addEventListener("click", (e) => { e.stopPropagation(); editProfile(i); });
        card.appendChild(edit);
        list.appendChild(card);
      });
      body.appendChild(list);
      if (!state.profiles.length) {
        body.appendChild(el("p", null,
          "One agentremoted host can front Claude, Grok and Codex at once — add the host once, then pick the harness when you start a session. Add a second profile only for another machine."));
      }
    },
    actions: [
      { label: "Add daemon", primary: true, close: false, run: () => editProfile(-1) },
      { label: "Done", close: true },
    ],
  });
}

function editProfile(index) {
  const existing = index >= 0 ? state.profiles[index] : null;
  let testLine;
  modal({
    title: existing ? "Edit daemon" : "Add daemon",
    build(body) {
      const mk = (label, value, help, type = "text") => {
        const f = el("div", "field");
        f.appendChild(el("label", null, label));
        const input = el("input");
        input.type = type;
        input.value = value || "";
        f.appendChild(input);
        if (help) f.appendChild(el("div", "help", help));
        body.appendChild(f);
        return input;
      };
      const name = mk("Name", existing && existing.name, "e.g. Mac · Claude");
      const url = mk("Address", existing && existing.baseUrl,
        "http:// is assumed; add https:// for a TLS daemon");
      url.placeholder = "192.168.1.20:8473";
      const token = mk("Token", existing && existing.token,
        "The contents of ~/.agentremoted/token on that host", "password");
      testLine = el("div", "help");
      body.appendChild(testLine);
      body._fields = { name, url, token };
    },
    actions: [
      ...(existing ? [{
        label: "Delete", danger: true, close: true, run: () => {
          state.profiles.splice(index, 1);
          store.save();
          syncStreams();
          refreshSessions();
          renderFilters();
          openProfiles();
        },
      }] : []),
      {
        label: "Test", close: false, run: async () => {
          const f = $("modal-body")._fields;
          const probe = normalizeProfile({ baseUrl: f.url.value, token: f.token.value });
          testLine.textContent = "Testing…";
          // Ping alone is not proof: /api/ping is unauthenticated, so a wrong
          // token would still "succeed". Make one authenticated call too.
          try {
            const ping = await call(probe, "/api/ping", { timeout: 10000 });
            await call(probe, "/api/projects", { timeout: 15000 });
            const hs = (ping.multi && Array.isArray(ping.providers) && ping.providers.length)
              ? ping.providers
              : (ping.provider ? [ping.provider] : []);
            const labels = hs.map((h) => providerOf(h).label).join(" · ") || "Agent";
            testLine.textContent =
              `${labels} on ${ping.host || "the daemon"} · agentremoted ${ping.version}`;
            testLine.style.color = "var(--ok)";
          } catch (e) {
            testLine.textContent = e.status === 401
              ? "Reached the daemon, but the token was rejected" : e.message;
            testLine.style.color = "var(--danger)";
          }
        },
      },
      {
        // close:false — we reopen the Daemons list ourselves so "Add daemon"
        // is one click away (Save used to dismiss everything and stranded the
        // user on the main page wondering how to add a second profile).
        label: "Save", primary: true, close: false, run: async () => {
          const f = $("modal-body")._fields;
          const next = normalizeProfile({
            id: existing ? existing.id : uuid(),
            name: f.name.value.trim(),
            baseUrl: f.url.value,
            token: f.token.value,
            enabled: existing ? existing.enabled !== false : true,
            ...(existing || {}),
            // The form always wins over the carried-over copy.
            ...{ name: f.name.value.trim(), baseUrl: f.url.value.trim(), token: f.token.value.trim() },
          });
          if (existing) state.profiles[index] = next;
          else state.profiles.push(next);
          store.save();
          await pingProfile(next);
          store.save();
          syncStreams();
          renderFilters();
          refreshSessions();
          paintProfilesModal();
        },
      },
    ],
  });
}

function normalizeProfile(p) {
  let url = String(p.baseUrl || "").trim().replace(/\/+$/, "");
  if (url && !/^https?:\/\//i.test(url)) url = "http://" + url;
  // Strip accidental /claude|/grok|/codex suffixes — multi lives at the root.
  url = url.replace(/\/(claude|grok|codex)$/i, "");
  return {
    id: p.id || uuid(),
    name: p.name || url.replace(/^https?:\/\//, ""),
    baseUrl: url,
    token: String(p.token || "").trim(),
    enabled: p.enabled !== false,
    provider: p.provider || "",
    multi: !!p.multi,
    providers: Array.isArray(p.providers) ? p.providers : [],
    providerDetails: p.providerDetails || {},
    caps: p.caps || {},
    slashCommands: p.slashCommands || [],
    models: p.models || [],
    efforts: p.efforts || [],
    host: p.host || "",
    version: p.version || "",
    execMode: normalizeExecMode(p.execMode || ""),
    model: p.model || "",
    effort: p.effort || "",
  };
}

// ----------------------------------------------------------- new session

function openNewSession() {
  const candidates = enabledProfiles();
  if (!candidates.length) { openProfiles(); return; }
  let picked = candidates.find((p) => state.open && p.id === state.open.profileId) || candidates[0];
  let harness = (harnessesOf(picked)[0] || picked.provider || "claude");
  let projects = [];

  const render = () => {
    // Form chrome (Execution / Model / Effort / Start) follows the selected harness.
    applyAccent(harness);
    const harnessPal = providerOf(harness);
    const m = modal({
      title: "New session",
      wide: true,
      build(body) {
        // Multiple daemon hosts → pick host first. One multi-harness host →
        // only the harness picker (Claude / Grok / Codex).
        if (candidates.length > 1) {
          body.appendChild(el("div", "help", "Daemon"));
          const list = el("div", "plist");
          candidates.forEach((p) => {
            const pal = (p.multi || profileHarnesses(p).length > 1)
              ? MULTI : providerOf(p.provider);
            const card = el("button", "pcard");
            card.type = "button";
            card.style.setProperty("--pc", pal.accent);
            card.setAttribute("aria-pressed", String(p.id === picked.id));
            card.appendChild(el("span", "pdot"));
            const col = el("div");
            col.style.flex = "1";
            col.appendChild(el("div", "pname", p.name));
            const bits = [p.baseUrl.replace(/^https?:\/\//, "")];
            if (p.multi && p.providers && p.providers.length)
              bits.unshift(p.providers.map((h) => providerOf(h).label).join(" · "));
            else if (p.provider) bits.unshift(providerOf(p.provider).label);
            col.appendChild(el("div", "phost", bits.join(" · ")));
            card.appendChild(col);
            card.addEventListener("click", () => {
              picked = p;
              harness = harnessesOf(picked)[0] || picked.provider || "claude";
              projects = [];
              render();
              loadProjects();
            });
            list.appendChild(card);
          });
          body.appendChild(list);
        }

        const hs = harnessesOf(picked);
        if (hs.length > 1 || picked.multi) {
          body.appendChild(el("div", "help",
            candidates.length > 1 ? "Harness" : "Which harness?"));
          const bar = el("div", "pillbar");
          bar.style.marginBottom = "10px";
          (hs.length ? hs : ["claude", "grok"]).forEach((h) => {
            const pal = providerOf(h);
            const b = el("button", "pill pill-brand", pal.label);
            b.type = "button";
            // Own brand always — selected fill uses --pc, not session --accent.
            b.style.setProperty("--pc", pal.accent);
            b.setAttribute("aria-pressed", String(h === harness));
            b.addEventListener("click", () => {
              harness = h;
              projects = [];
              render();
              loadProjects();
            });
            bar.appendChild(b);
          });
          body.appendChild(bar);
        }

        const needCwd = requiresCwd(picked, harness);
        const f = el("div", "field");
        f.style.marginTop = "14px";
        f.appendChild(el("label", null, needCwd ? "Project folder (required)" : "Project folder (optional)"));
        const cwd = el("input");
        cwd.placeholder = needCwd ? "/Users/you/code/project" : "empty = the daemon's workspace";
        cwd.style.fontFamily = "var(--mono)";
        f.appendChild(cwd);
        body.appendChild(f);

        const plist = el("div");
        plist.style.maxHeight = "180px";
        plist.style.overflowY = "auto";
        projects.slice(0, 40).forEach((proj) => {
          const b = el("button", "pcard");
          b.type = "button";
          // Project dots / selection rim follow the active harness colour.
          b.style.setProperty("--pc", harnessPal.accent);
          b.appendChild(el("span", "pdot"));
          const col = el("div");
          col.style.flex = "1";
          col.appendChild(el("div", "pname", proj.name || proj.id));
          col.appendChild(el("div", "phost", proj.cwd));
          b.appendChild(col);
          b.appendChild(el("span", "tag", String(proj.session_count || 0)));
          b.addEventListener("click", () => { cwd.value = proj.cwd; });
          plist.appendChild(b);
        });
        body.appendChild(plist);

        const pf = el("div", "field");
        pf.style.marginTop = "14px";
        pf.appendChild(el("label", null, "First message"));
        const prompt = el("textarea");
        prompt.rows = 5;
        pf.appendChild(prompt);
        body.appendChild(pf);
        // Execution: Interactive | Headless (both always bypass permissions).
        const canInteractive = capOf(picked, "interactive", true, harness);
        const modes = canInteractive ? ["interactive", "headless"] : ["headless"];
        const modeBar = el("div", "field");
        modeBar.style.marginTop = "12px";
        modeBar.appendChild(el("label", null, "Execution"));
        const pills = el("div", "pillbar");
        modes.forEach((m) => {
          const b = el("button", "pill", execModeLabel(m));
          b.type = "button";
          b.setAttribute("aria-pressed",
            String(normalizeExecMode(execModeOf(picked, harness)) === m));
          b.addEventListener("click", () => {
            picked.execMode = m;
            store.save();
            render();
          });
          pills.appendChild(b);
        });
        modeBar.appendChild(pills);
        modeBar.appendChild(el("div", "help",
          normalizeExecMode(execModeOf(picked, harness)) === "interactive"
            ? "Host TUI in tmux — tools auto-run, connectors work."
            : "One-shot CLI turn — tools auto-run, no permission prompts."));
        body.appendChild(modeBar);

        // Model / effort for this harness (from multi provider_details).
        const models = modelsOf(picked, harness);
        if (capOf(picked, "can_set_model", true, harness) && models.length) {
          const mf = el("div", "field");
          mf.style.marginTop = "10px";
          mf.appendChild(el("label", null, "Model"));
          const mbar = el("div", "pillbar");
          const cur = picked.model && models.includes(picked.model)
            ? picked.model : models[0];
          models.slice(0, 12).forEach((v) => {
            const b = el("button", "pill", v);
            b.type = "button";
            b.setAttribute("aria-pressed", String(v === cur));
            b.addEventListener("click", () => {
              picked.model = v;
              store.save();
              render();
            });
            mbar.appendChild(b);
          });
          mf.appendChild(mbar);
          body.appendChild(mf);
        }
        const efforts = effortsOf(picked, harness);
        if (capOf(picked, "can_set_effort", false, harness) && efforts.length) {
          const ef = el("div", "field");
          ef.style.marginTop = "10px";
          ef.appendChild(el("label", null, "Reasoning effort"));
          const ebar = el("div", "pillbar");
          const curE = picked.effort && efforts.includes(picked.effort)
            ? picked.effort : efforts[0];
          efforts.forEach((v) => {
            const b = el("button", "pill", v);
            b.type = "button";
            b.setAttribute("aria-pressed", String(v === curE));
            b.addEventListener("click", () => {
              picked.effort = v;
              store.save();
              render();
            });
            ebar.appendChild(b);
          });
          ef.appendChild(ebar);
          body.appendChild(ef);
        }

        const hLabel = providerOf(harness).label;
        body.appendChild(el("div", "help",
          `Runs on ${picked.name} · ${hLabel} · ${execModeLabel(execModeOf(picked, harness))}`));
        body._new = { cwd, prompt, harness: () => harness, picked: () => picked };
      },
      actions: [
        { label: "Cancel", close: true },
        {
          label: "Start", primary: true, close: true, run: async () => {
            const f = $("modal-body")._new;
            const cwd = f.cwd.value.trim();
            const prompt = f.prompt.value.trim();
            const h = f.harness();
            const p = f.picked();
            if (!prompt || (requiresCwd(p, h) && !cwd)) {
              toast("A prompt" + (requiresCwd(p, h) ? " and a project folder are" : " is") + " required");
              return;
            }
            try {
              const body = {
                cwd, prompt,
                permission_mode: wireExecMode(execModeOf(p, h)),
                model: p.model || "",
                effort: p.effort || "",
              };
              // Multi-harness root requires provider so the daemon routes the turn.
              if (p.multi || harnessesOf(p).length > 1) body.provider = h;
              else if (h) body.provider = h;
              const res = await call(p, "/api/sessions/new", {
                method: "POST",
                body,
              });
              toast(`Started on ${providerOf(h).label} — it appears as the daemon names it`);
              // The session id only exists once the daemon reports it; the
              // status stream will surface the row within a second or two.
              setTimeout(refreshSessions, 1500);
              setTimeout(refreshSessions, 6000);
              if (res && res.job_id) pendingNewJob(p, res.job_id, prompt);
            } catch (e) {
              toast(e.message);
            }
          },
        },
      ],
    });
    return m;
  };

  const loadProjects = async () => {
    try {
      const data = await call(picked, "/api/projects", { timeout: 20000 });
      projects = data.projects || [];
      render();
    } catch { /* the cwd field still works */ }
  };

  render();
  loadProjects();
}

/** Follow a brand-new session until the daemon names it, then open it. */
async function pendingNewJob(profile, jobId, prompt) {
  for (let i = 0; i < 60; i++) {
    await new Promise((r) => setTimeout(r, 1500));
    let snap;
    try { snap = await call(profile, `/api/jobs/${jobId}?since=0`); } catch { continue; }
    const sid = snap.new_session_id || snap.session_id;
    if (sid) {
      await refreshSessions();
      const row = state.rows.find((r) => r.profileId === profile.id && r.session.id === sid);
      if (row) {
        await openSession(row);
        attachJob(jobId);
      }
      return;
    }
    if (["done", "error", "stopped"].includes(snap.status)) return;
  }
}

// ------------------------------------------------------ permission / ask

function showPermission(pending, { force = false } = {}) {
  if (!pending) return;
  // Suppress a re-paint when the same modal is already up (poll ticks);
  // force=true is the banner's "Respond" after a dismiss.
  if (!force && !$("modal").classList.contains("hidden")
      && $("modal-title").textContent.startsWith("Allow ")) {
    return;
  }
  const answer = async (allow) => {
    const profile = profileById(state.open.profileId);
    try {
      await call(profile, `/api/jobs/${state.job.id}/permission`,
        { method: "POST", body: { request_id: pending.request_id, allow } });
    } catch (e) { toast(e.message); }
  };
  modal({
    title: `Allow ${pending.tool_name || "this tool"}?`,
    build(body) {
      if (pending.detail) {
        const pre = el("pre", null, pending.detail);
        pre.style.whiteSpace = "pre-wrap";
        pre.style.fontFamily = "var(--mono)";
        pre.style.fontSize = "12.5px";
        body.appendChild(pre);
      }
      body.appendChild(el("div", "help",
        "The turn is paused until you answer. Closing this dialog does not deny — use Respond in the banner to reopen."));
    },
    actions: [
      { label: "Deny", danger: true, close: true, run: () => answer(false) },
      { label: "Allow", primary: true, close: true, run: () => answer(true) },
    ],
  });
}

function showQuestion(pending) {
  if (!pending) return;
  const questions = pending.questions || [];
  if (!questions.length) return;
  const picks = questions.map(() => []);
  const notes = questions.map(() => "");

  const render = () => modal({
    title: questions.length === 1 ? "The agent is asking"
                                  : `The agent is asking ${questions.length} things`,
    wide: true,
    build(body) {
      body.appendChild(el("div", "help",
        "The turn is paused until you answer or cancel. Closing this dialog does not cancel — use Answer in the banner to reopen."));
      questions.forEach((q, qi) => {
        const block = el("div", "q-block");
        if (q.header) block.appendChild(el("div", "q-head", q.header));
        if (q.question) block.appendChild(renderMarkdown(q.question));
        if (q.multi_select) block.appendChild(el("div", "help", "Pick as many as apply"));
        (q.options || []).forEach((opt) => {
          const active = picks[qi].includes(opt.label);
          const b = el("button", "q-opt");
          b.type = "button";
          b.setAttribute("aria-pressed", String(active));
          b.appendChild(el("span", "qmark",
            q.multi_select ? (active ? "[x]" : "[ ]") : (active ? "(o)" : "( )")));
          const col = el("div");
          col.appendChild(el("div", null, opt.label));
          if (opt.description) col.appendChild(el("div", "qdesc", opt.description));
          b.appendChild(col);
          b.addEventListener("click", () => {
            if (q.multi_select) {
              const at = picks[qi].indexOf(opt.label);
              if (at >= 0) picks[qi].splice(at, 1); else picks[qi].push(opt.label);
            } else {
              picks[qi] = [opt.label];
            }
            render();
          });
          block.appendChild(b);
          // Some options take free text with the pick (grok's "Request
          // changes" becomes the revision note it then waits for).
          if (q.note_for && q.note_for === opt.label && active) {
            const input = el("input");
            input.placeholder = q.note_hint || "Your answer";
            input.value = notes[qi];
            input.addEventListener("input", () => { notes[qi] = input.value; });
            block.appendChild(input);
          }
        });
        body.appendChild(block);
      });
    },
    actions: [
      {
        label: "Cancel", close: true, run: async () => {
          const profile = profileById(state.open.profileId);
          try {
            await call(profile, `/api/jobs/${state.job.id}/question`,
              { method: "POST", body: { request_id: pending.request_id, cancel: true } });
          } catch (e) { toast(e.message); }
        },
      },
      {
        label: "Send answer", primary: true, close: true,
        disabled: picks.some((p) => !p.length),
        run: async () => {
          const profile = profileById(state.open.profileId);
          try {
            await call(profile, `/api/jobs/${state.job.id}/question`, {
              method: "POST",
              body: { request_id: pending.request_id, answers: picks, notes },
            });
          } catch (e) { toast(e.message); }
        },
      },
    ],
  });
  render();
}

// ------------------------------------------------------------- attachments
// Same contract as the phone apps: POST the raw bytes to /api/attachments,
// then reference the host path in the prompt so the agent can open the file.

async function uploadAttachment(file) {
  if (!state.open || !file) return;
  const profile = profileById(state.open.profileId);
  if (!profile) return;
  if (file.size <= 0) { toast("Empty file"); return; }
  if (file.size > MAX_UPLOAD_BYTES) {
    toast(`${file.name} is too large (max ${humanSize(MAX_UPLOAD_BYTES)})`);
    return;
  }
  const name = file.name || "file";
  composerNote(`Uploading ${name}…`);
  const url = profile.baseUrl.replace(/\/+$/, "")
    + "/api/attachments?name=" + encodeURIComponent(name);
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 120000);
  try {
    const res = await fetch(url, {
      method: "POST",
      headers: {
        "X-Auth-Token": profile.token,
        "Content-Type": "application/octet-stream",
      },
      body: file,
      signal: ctrl.signal,
      credentials: "omit",
      cache: "no-store",
    });
    let data = null;
    try { data = await res.json(); } catch { /* non-JSON */ }
    if (!res.ok) {
      throw new DaemonError(res.status,
        (data && data.error) || (res.status === 413
          ? "Attachment too large" : `HTTP ${res.status}`));
    }
    const path = data && data.path;
    if (!path) throw new DaemonError(0, "Daemon did not return a path");
    // Prefill like TranscriptPage.qml: [attached: /host/path]
    const prompt = $("prompt");
    const sep = prompt.value && !/\s$/.test(prompt.value) ? " " : "";
    prompt.value = prompt.value + sep + "[attached: " + path + "]";
    prompt.dispatchEvent(new Event("input"));
    prompt.focus();
    composerNote(`Attached ${name} (${humanSize(data.size || file.size)})`);
  } catch (e) {
    if (e.name === "AbortError") composerNote("Upload timed out", true);
    else composerNote(e.message || "Upload failed", true);
  } finally {
    clearTimeout(timer);
  }
}

async function uploadFiles(fileList) {
  const files = [...(fileList || [])].filter(Boolean);
  for (const f of files) await uploadAttachment(f);
}

// ------------------------------------------------------------------ inbox

async function openInbox() {
  const targets = enabledProfiles();
  modal({
    title: "Files from host",
    wide: true,
    build(body) {
      body.appendChild(el("span", "spinner"));
      body.appendChild(document.createTextNode(" Reading every daemon's drop folder…"));
    },
    actions: [{ label: "Close", close: true }],
  });

  const results = await Promise.all(targets.map(async (p) => {
    try {
      const data = await call(p, "/api/drop", { timeout: 30000 });
      return { profile: p, path: data.path || "", files: data.files || [] };
    } catch (e) {
      return { profile: p, error: e.message, files: [] };
    }
  }));

  const render = () => modal({
    title: "Files from host",
    wide: true,
    build(body) {
      results.forEach((r) => {
        const head = el("div", "frow");
        const pal = providerOf(r.profile.provider);
        const tag = el("span", "tag provider", r.profile.name);
        tag.style.setProperty("--tag", pal.accent);
        head.appendChild(tag);
        head.appendChild(el("span", "fname", r.error ? r.error : r.path));
        if (r.error) head.style.color = "var(--danger)";
        body.appendChild(head);
      });

      // Identical (name, size, mtime) across daemons is ONE file — usually
      // two profiles reaching the same daemon by different URLs. First
      // listed wins; the hidden sources ride along so a delete reads
      // honestly.
      const merged = new Map();
      results.forEach((r) => r.files.forEach((f) => {
        const key = `${f.name}|${f.size}|${f.mtime}`;
        const seen = merged.get(key);
        if (!seen) merged.set(key, { file: f, profile: r.profile, also: [] });
        else seen.also.push(r.profile.name);
      }));
      const rows = [...merged.values()].sort((a, b) => (b.file.mtime || 0) - (a.file.mtime || 0));

      if (!rows.length) {
        body.appendChild(el("p", null,
          "Nothing staged. Ask the agent to copy a file into a drop folder."));
        return;
      }
      rows.forEach((row) => {
        const line = el("div", "frow");
        const col = el("div");
        col.style.flex = "1";
        col.style.minWidth = "0";
        col.appendChild(el("div", "fname", row.file.name));
        const meta = el("div", "fmeta");
        const pal = providerOf(row.profile.provider);
        const tag = el("span", "tag provider", row.profile.name);
        tag.style.setProperty("--tag", pal.accent);
        meta.appendChild(tag);
        meta.appendChild(el("span", "tag", humanSize(row.file.size)));
        meta.appendChild(el("span", "tag", stamp((row.file.mtime || 0) * 1000)));
        col.appendChild(meta);
        if (row.also.length) {
          col.appendChild(el("div", "also", `Identical copy on ${row.also.join(", ")}`));
        }
        line.appendChild(col);

        const dl = el("button", null, "Download");
        dl.type = "button";
        dl.addEventListener("click", async () => {
          dl.disabled = true;
          dl.textContent = "…";
          try {
            const blob = await call(row.profile,
              `/api/drop/${encodeURIComponent(row.file.name)}`,
              { raw: true, timeout: 180000 });
            const url = URL.createObjectURL(blob);
            const a = el("a");
            a.href = url;
            a.download = row.file.name;
            a.click();
            setTimeout(() => URL.revokeObjectURL(url), 10000);
            dl.textContent = "Saved";
          } catch (e) {
            toast(e.message);
            dl.textContent = "Download";
          }
          dl.disabled = false;
        });
        line.appendChild(dl);

        const del = el("button", "danger", "Delete");
        del.type = "button";
        del.addEventListener("click", async () => {
          try {
            await call(row.profile,
              `/api/drop/${encodeURIComponent(row.file.name)}/delete`, { method: "POST" });
            const owner = results.find((r) => r.profile.id === row.profile.id);
            owner.files = owner.files.filter((f) => f.name !== row.file.name);
            render();
            toast(`Deleted ${row.file.name} on ${row.profile.name}`);
          } catch (e) { toast(e.message); }
        });
        line.appendChild(del);
        body.appendChild(line);
      });
    },
    actions: [{ label: "Close", close: true }],
  });
  render();
}

// ------------------------------------------------------------------ usage

function profileReportsUsage(p) {
  // Multi host: any harness with can_show_usage (Claude OAuth / Grok TUI).
  if (p && p.multi && p.providerDetails) {
    return Object.values(p.providerDetails).some(
      (d) => d && d.caps && d.caps.can_show_usage);
  }
  if (p && Array.isArray(p.providers) && p.providers.length > 1 && p.providerDetails) {
    return Object.values(p.providerDetails).some(
      (d) => d && d.caps && d.caps.can_show_usage);
  }
  return capOf(p, "can_show_usage", true);
}

function appendUsageBuckets(body, buckets, accent) {
  (buckets || []).forEach((b) => {
    const item = el("div", "usage-item");
    if (accent) item.style.setProperty("--tag", accent);
    const top = el("div", "usage-top");
    top.appendChild(el("span", null, b.title || ""));
    top.appendChild(el("span", null, `${b.percent || 0}%`));
    item.appendChild(top);
    const bar = el("div", `bar ${b.severity || "normal"}`);
    const fill = el("span");
    fill.style.width = Math.min(100, Math.max(0, b.percent || 0)) + "%";
    bar.appendChild(fill);
    item.appendChild(bar);
    if (b.resets_text) {
      const foot = el("div", "usage-foot");
      foot.appendChild(el("span", null, b.resets_text));
      item.appendChild(foot);
    }
    body.appendChild(item);
  });
}

async function openUsage() {
  const targets = enabledProfiles().filter(profileReportsUsage);
  const results = targets.map((p) => ({
    profile: p, loading: true, buckets: [], sections: null, error: "",
  }));

  const render = () => modal({
    title: "Usage",
    build(body) {
      if (!results.length) {
        body.appendChild(el("p", null, "No daemon reports usage."));
        return;
      }
      results.forEach((r) => {
        const multi = !!(r.sections && r.sections.length);
        const hostPal = (multi || profileHarnesses(r.profile).length > 1)
          ? MULTI : providerOf(r.profile.provider);
        const head = el("div", "usage-src", r.profile.name);
        head.style.setProperty("--tag", hostPal.accent);
        body.appendChild(head);
        if (r.loading) {
          const wait = el("div", "help");
          wait.appendChild(el("span", "spinner"));
          // Grok has no REST usage API: the daemon resumes a tmux TUI and
          // scrapes /usage, which can take tens of seconds.
          wait.appendChild(document.createTextNode(
            multi ? " Reading each harness…" : " Reading…"));
          body.appendChild(wait);
          return;
        }
        if (r.error && !multi) {
          const e = el("div", "help", r.error);
          e.style.color = "var(--danger)";
          body.appendChild(e);
          return;
        }
        if (multi) {
          r.sections.forEach((sec) => {
            const pal = providerOf(sec.provider);
            const sub = el("div", "usage-src", pal.label);
            sub.style.setProperty("--tag", pal.accent);
            sub.style.marginTop = "10px";
            sub.style.fontSize = "13px";
            body.appendChild(sub);
            if (sec.ok === false || sec.error) {
              const e = el("div", "help", sec.error || "Not available");
              e.style.color = "var(--dim)";
              body.appendChild(e);
              return;
            }
            if (!(sec.buckets || []).length) {
              body.appendChild(el("div", "help", "No usage data returned."));
              return;
            }
            // Section header already names the harness — strip "Claude · " prefix
            // if the multi endpoint tagged flat titles for older clients.
            const label = pal.label;
            const cleaned = (sec.buckets || []).map((b) => {
              const t = String(b.title || "");
              const prefix = label + " · ";
              if (t.startsWith(prefix)) return { ...b, title: t.slice(prefix.length) };
              return b;
            });
            appendUsageBuckets(body, cleaned, pal.accent);
          });
          return;
        }
        if (!r.buckets.length) {
          body.appendChild(el("div", "help", "No usage data returned."));
          return;
        }
        appendUsageBuckets(body, r.buckets, hostPal.accent);
      });
    },
    actions: [{ label: "Close", close: true }],
  });
  render();

  // Progressive: fast daemons (Claude OAuth) paint immediately; Grok's TUI
  // scrape is slow and must not block the sheet.
  results.forEach(async (r) => {
    try {
      const data = await call(r.profile, "/api/usage", { timeout: 95000 });
      if (data.multi && Array.isArray(data.sections)) {
        r.sections = data.sections;
        r.buckets = data.buckets || [];
        if (data.ok === false && !r.buckets.length)
          r.error = data.error || "Not available";
      } else {
        r.buckets = data.ok === false ? [] : (data.buckets || []);
        if (data.ok === false) r.error = data.error || "Not available";
      }
    } catch (e) {
      r.error = e.message;
    }
    r.loading = false;
    if (!$("modal").classList.contains("hidden")) render();
  });
}

// ---------------------------------------------------------------- options

function openOptions() {
  if (!state.open) return;
  const profile = profileById(state.open.profileId);
  if (!profile) return;
  const render = () => modal({
    title: "Turn options",
    build(body) {
      body.appendChild(el("div", "help",
        `Applies to ${profile.name} — every session on that daemon.`));
      // Session id first: easy to find when you need it for logs / /resume.
      if (state.open.sessionId) {
        const sidField = el("div", "field");
        sidField.appendChild(el("label", null, "Session id"));
        const sidRow = el("div", "sid-row");
        const mono = el("code", "sid-full", state.open.sessionId);
        sidRow.appendChild(mono);
        const copyBtn = el("button", "pill", "Copy");
        copyBtn.type = "button";
        copyBtn.addEventListener("click", () => copySessionId(state.open.sessionId));
        sidRow.appendChild(copyBtn);
        sidField.appendChild(sidRow);
        body.appendChild(sidField);
      }
      const group = (label, values, current, set, display = (v) => v) => {
        if (!values.length) return;
        const f = el("div", "field");
        f.appendChild(el("label", null, label));
        const bar = el("div", "pillbar");
        values.forEach((v) => {
          const b = el("button", "pill", display(v));
          b.type = "button";
          b.setAttribute("aria-pressed", String(v === current));
          b.addEventListener("click", () => { set(v); store.save(); render(); });
          bar.appendChild(b);
        });
        f.appendChild(bar);
        body.appendChild(f);
      };
      // Open session's harness (multi tags each row); else profile default.
      const harness = sessionProvider(state.open && state.open.session, profile)
        || (profileHarnesses(profile)[0] || profile.provider || "");
      const canInteractive = capOf(profile, "interactive", true, harness || null);
      const modes = canInteractive ? ["interactive", "headless"] : ["headless"];
      group("Execution", modes,
        normalizeExecMode(execModeOf(profile, harness || null), canInteractive),
        (v) => { profile.execMode = normalizeExecMode(v, canInteractive); },
        execModeLabel);
      body.appendChild(el("div", "help",
        "Both modes auto-run tools (no permission prompts on the phone)."));
      const models = modelsOf(profile, harness || null);
      // BB Session sheet parity: show what the OPEN session last used.
      const sessionModel = String(
        (state.open.session && state.open.session.model) || "").trim();
      if (sessionModel && sessionModel !== "default" && sessionModel !== "interactive") {
        body.appendChild(el("div", "help",
          `This session last ran on ${sessionModel}`));
      }
      if (capOf(profile, "can_set_model", true, harness || null) && models.length) {
        const cur = profile.model && models.includes(profile.model)
          ? profile.model : models[0];
        group("Model", models, cur, (v) => { profile.model = v; });
      }
      const efforts = effortsOf(profile, harness || null);
      if (capOf(profile, "can_set_effort", false, harness || null) && efforts.length) {
        const curE = profile.effort && efforts.includes(profile.effort)
          ? profile.effort : efforts[0];
        group("Reasoning effort", efforts, curE, (v) => { profile.effort = v; });
      }
      const toggle = (label, checked, onChange) => {
        const f = el("div", "field");
        const cb = el("label");
        const box = el("input");
        box.type = "checkbox";
        box.style.width = "auto";
        box.checked = checked;
        box.addEventListener("change", () => onChange(box.checked));
        cb.appendChild(box);
        cb.appendChild(document.createTextNode(" " + label));
        f.appendChild(cb);
        body.appendChild(f);
      };
      toggle("Include agent-spawned sessions in the list", !!state.settings.showAll, (on) => {
        state.settings.showAll = on;
        store.save();
        refreshSessions();
      });
      toggle("Sound cues (status, done, error, attention)", soundCuesOn(), (on) => {
        setSoundCues(on);
      });
    },
    actions: [{ label: "Done", close: true }],
  });
  render();
}

// ------------------------------------------------------------------- boot

function wire() {
  // Browsers block AudioContext until a gesture; unlock on first interaction
  // and keep unlocking on later gestures in case the context got suspended.
  const unlock = () => { unlockChime(); };
  document.addEventListener("pointerdown", unlock, { capture: true });
  document.addEventListener("keydown", unlock, { capture: true });

  $("btn-sound").addEventListener("click", () => setSoundCues(!soundCuesOn()));
  syncSoundButton();

  $("btn-profiles").addEventListener("click", openProfiles);
  $("btn-new").addEventListener("click", openNewSession);
  $("btn-inbox").addEventListener("click", openInbox);
  $("btn-usage").addEventListener("click", openUsage);
  $("btn-options").addEventListener("click", openOptions);
  $("btn-refresh").addEventListener("click", () => {
    loadTail();
    refreshSessions();
    // Transcript reload used to leave a dismissed ask stranded; re-surface it.
    if (state.job && state.job.pendingQuestion) showQuestion(state.job.pendingQuestion);
    else if (state.job && state.job.pendingPermission) {
      showPermission(state.job.pendingPermission, { force: true });
    }
  });
  $("btn-live-tui")?.addEventListener("click", () => {
    if (state.liveTui) closeLiveTui();
    else openLiveTui();
  });
  $("btn-live-tui-close")?.addEventListener("click", () => closeLiveTui());
  $("live-tui")?.querySelectorAll("[data-tui-key]").forEach((btn) => {
    btn.addEventListener("click", () => {
      sendLiveTuiKeys([btn.getAttribute("data-tui-key")]);
      $("live-tui-pane")?.focus();
    });
  });
  $("live-tui-pane")?.addEventListener("focus", () => {
    state.liveTuiKeys = true;
    state.liveTuiEscArmed = false;
  });
  $("live-tui-pane")?.addEventListener("blur", () => {
    state.liveTuiKeys = false;
    state.liveTuiEscArmed = false;
  });
  $("live-tui-pane")?.addEventListener("keydown", (e) => {
    if (!state.liveTui) return;
    // Double Esc releases keyboard capture back to the page.
    if (e.key === "Escape") {
      if (state.liveTuiEscArmed) {
        e.preventDefault();
        state.liveTuiEscArmed = false;
        $("live-tui-input")?.focus();
        return;
      }
      state.liveTuiEscArmed = true;
      setTimeout(() => { state.liveTuiEscArmed = false; }, 600);
    } else {
      state.liveTuiEscArmed = false;
    }
    const name = liveTuiKeyName(e);
    if (!name) return;
    e.preventDefault();
    if (name.length === 1 && !e.ctrlKey && !e.metaKey) {
      sendLiveTuiKeys(null, name);
    } else {
      sendLiveTuiKeys([name]);
    }
  });
  $("btn-live-tui-send")?.addEventListener("click", () => {
    const input = $("live-tui-input");
    const text = (input?.value || "").trim();
    if (!text) return;
    // Full line into TUI (Enter included) via key path: text + Enter.
    sendLiveTuiKeys(["Enter"], text);
    if (input) input.value = "";
  });
  $("live-tui-input")?.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      $("btn-live-tui-send")?.click();
    }
  });
  $("btn-stop").addEventListener("click", async () => {
    if (!state.job) return;
    const profile = profileById(state.open.profileId);
    try {
      await call(profile, `/api/jobs/${state.job.id}/stop`, { method: "POST" });
      composerNote("Stopping…");
    } catch (e) { toast(e.message); }
  });
  $("modal-close").addEventListener("click", closeModal);
  $("modal").addEventListener("click", (e) => { if (e.target === $("modal")) closeModal(); });

  let searchTimer;
  $("search").addEventListener("input", (e) => {
    state.query = e.target.value;
    clearTimeout(searchTimer);
    // Each keystroke would otherwise fan out one request per daemon.
    searchTimer = setTimeout(refreshSessions, 300);
  });

  const prompt = $("prompt");
  const grow = () => {
    prompt.style.height = "auto";
    prompt.style.height = Math.min(Math.max(prompt.scrollHeight, 38), 220) + "px";
  };
  prompt.addEventListener("input", grow);
  prompt.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      const text = prompt.value;
      prompt.value = "";
      grow();
      send(text);
    }
  });
  // Paste images (screenshots) straight into the open session.
  prompt.addEventListener("paste", (e) => {
    const items = e.clipboardData && e.clipboardData.items;
    if (!items || !state.open) return;
    const files = [];
    for (const item of items) {
      if (item.kind === "file") {
        const f = item.getAsFile();
        if (f) files.push(f);
      }
    }
    if (!files.length) return;
    e.preventDefault();
    uploadFiles(files);
  });
  $("btn-send").addEventListener("click", () => {
    const text = prompt.value;
    prompt.value = "";
    grow();
    send(text);
  });

  const fileInput = $("file-attach");
  $("btn-attach").addEventListener("click", () => {
    if (!state.open) return;
    fileInput.value = "";
    fileInput.click();
  });
  fileInput.addEventListener("change", () => {
    if (fileInput.files && fileInput.files.length) uploadFiles(fileInput.files);
  });

  // Drop files onto the composer (or the whole chat pane).
  const dropHost = $("composer");
  const onDrag = (e) => {
    if (![...e.dataTransfer.types].includes("Files")) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "copy";
    dropHost.classList.add("dragover");
  };
  dropHost.addEventListener("dragenter", onDrag);
  dropHost.addEventListener("dragover", onDrag);
  dropHost.addEventListener("dragleave", (e) => {
    if (!dropHost.contains(e.relatedTarget)) dropHost.classList.remove("dragover");
  });
  dropHost.addEventListener("drop", (e) => {
    e.preventDefault();
    dropHost.classList.remove("dragover");
    if (e.dataTransfer.files && e.dataTransfer.files.length) {
      uploadFiles(e.dataTransfer.files);
    }
  });

  // No Cmd/Ctrl+K — conflicts with macOS native shortcuts (e.g. clear line /
  // browser chrome). Search is focused by clicking the field.
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !$("modal").classList.contains("hidden")) {
      closeModal();
    }
  });
}

async function boot() {
  const saved = store.load();
  state.profiles = saved.profiles.map(normalizeProfile);
  state.settings = saved.settings || {};

  wire();
  renderFilters();
  renderSessions();
  renderStatus();

  if (!state.profiles.length) await offerOwnOrigin();

  await Promise.all(state.profiles.map(pingProfile));
  renderFilters();
  syncStreams();
  await refreshSessions();

  if (!state.profiles.length || state.profiles.every((p) => !p.token)) openProfiles();
}

/**
 * First run convenience only.
 *
 * This page is not owned by any daemon — it can live on disk, on a static
 * host, anywhere. But if it happens to be served from something that answers
 * /api/ping like an agentremoted daemon, offer that host as the first profile so the
 * user only pastes a token. Nothing is assumed: a non-daemon origin (or
 * file://, where there is no origin) simply adds nothing.
 */
async function offerOwnOrigin() {
  if (!/^https?:$/.test(location.protocol)) return;
  const probe = normalizeProfile({ baseUrl: location.origin, token: "" });
  try {
    const ping = await call(probe, "/api/ping", { timeout: 4000 });
    if (!ping || ping.app !== "agentremoted") return;
    probe.name = location.host;
    probe.provider = ping.provider || "";
    probe.caps = ping.caps || {};
    probe.host = ping.host || "";
    probe.version = ping.version || "";
    state.profiles.push(probe);
    store.save();
  } catch {
    /* not a daemon — the user adds daemons by hand, which is the normal case */
  }
}

boot();
