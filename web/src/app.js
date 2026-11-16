// Agent Remote — browser client for agentremoted.
//
// Same model as the Android and BlackBerry apps: a *profile* is one daemon,
// the provider comes from that daemon's /api/ping (never from a build flag),
// and every enabled profile's sessions merge into one list.
//
// It is served BY a daemon, so the host you opened is same-origin; the others
// are reached cross-origin, which is why the daemon sends CORS headers. Auth
// is the token header, kept in localStorage — never a cookie.

import { renderMarkdown, inlineInto, paintCode } from "./md.js";
import { qrSvg } from "./qr.js";

const PROVIDERS = {
  claude: { label: "Claude", accent: "#d97757", heading: "#e08a5c", inline: "#e0a183" },
  grok: { label: "Grok", accent: "#00d4ff", heading: "#b9a2f0", inline: "#67e8f9" },
  codex: { label: "Codex", accent: "#484cf4", heading: "#7b7ef7", inline: "#a9abfa" },
  deepseek: { label: "DeepSeek", accent: "#4d6bfe", heading: "#7b93ff", inline: "#93a8ff" },
  dsh: { label: "DeepSeek", accent: "#4d6bfe", heading: "#7b93ff", inline: "#93a8ff" },
  cursor: { label: "Cursor", accent: "#d946ef", heading: "#e879f9", inline: "#f0abfc" },
  "cursor-agent": { label: "Cursor", accent: "#d946ef", heading: "#e879f9", inline: "#f0abfc" },
  copilot: { label: "Copilot", accent: "#8957e5", heading: "#b392f0", inline: "#d2a8ff" },
  "github-copilot": { label: "Copilot", accent: "#8957e5", heading: "#b392f0", inline: "#d2a8ff" },
  codebuddy: { label: "CodeBuddy", accent: "#58c08b", heading: "#7fd4a8", inline: "#a8e3c8" },
  cbc: { label: "CodeBuddy", accent: "#58c08b", heading: "#7fd4a8", inline: "#a8e3c8" },
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
// Stay well under Cloudflare's ~100s HTTP timeout on a slow link.
const UPLOAD_CHUNK_BYTES = 512 * 1024;
// Phone photos are often 1.5–4 MB; the slow path is usually CF tunnel +
// cellular, not the daemon (localhost 4 MB lands in ~3 ms). Downscale +
// re-encode before POST so a typical shot is a few hundred KB.
const IMAGE_UPLOAD_MAX_EDGE = 1920;
const IMAGE_UPLOAD_JPEG_QUALITY = 0.82;
const IMAGE_UPLOAD_COMPRESS_MIN = 400 * 1024; // leave small screenshots alone

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
  // model/effort: prefer modelByHarness / effortByHarness per CLI; plain
  // model/effort kept for single-harness and backward-compatible save files.
  profiles: [],          // [{id, name, baseUrl, token, …, model, effort, modelByHarness, effortByHarness}]
  settings: {},
  rows: [],              // merged session list
  rowsFocus: false,      // true when `rows` came from a Focus fetch
  feeds: {},             // profileId -> {error, count}
  loading: false,
  query: "",
  filter: null,          // profileId or null
  active: {},            // profileId -> [activeJobDto]
  sessionsRev: {},       // profileId -> last sessions_rev seen on its stream
  streams: {},           // profileId -> EventSource
  conn: {},              // profileId -> { state, since } (see setConn)
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
  liveTuiStream: null,   // EventSource of pushed frames (daemon ≥ 2.22)
  liveTuiRetry: null,    // timer: ask for the stream again after falling back to polling
  liveTuiPolling: false, // a polled frame request is in flight
  liveTuiPollAgain: false,
  liveTuiSeq: 0,
  liveTuiKeys: false,    // pane has keyboard focus
  liveTuiCols: 0,        // host pane width in columns (frame.cols)
  askedQuestion: null,   // request_id already auto-opened (reopen via banner)
  askedPermission: null, // request_id already auto-opened (reopen via banner)
  answeredQuestions: new Set(), // request_ids the user already submitted
  gen: 0,                // fan-out generation guard (session list / search)
  openGen: 0,            // transcript generation — drops stale loadTail/poll paint
  stick: true,           // transcript follows new rows (false once read back)
  lastTop: 0,            // last seen transcript scrollTop (direction)
  gestureAt: 0,          // ms of the last reader scroll — see readerScrolling()
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
      sessionId: j.session_id || "",
      newSessionId: j.new_session_id || "",
    });
    const prev = chimeJobs.get(key);
    if (!prev) {
      // First sight: seed only. Blipping every job already running when the
      // page opens would be noise. Already-blocked turns still need the
      // attention cue so a question waiting on another session is heard.
      if (sig === "permission" || sig === "question") playChime("attention");
    } else if (prev.sig !== sig) {
      if (sig === "permission" || sig === "question") playChime("attention");
      else {
        playChime("status");
        // Status blip means a new tool/phase — process view steps live on the
        // journal, not the SSE frame. Pull them in so the strip does not lag
        // a full second (or the end-of-turn load) behind the sound.
        scheduleProcessRefresh(profileId, j);
      }
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
 *
 * When the finished job belongs to the open transcript, paint the journal
 * (process steps included) *before* the end chime — otherwise the sound
 * lands while the strip still shows the previous turn's tools.
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
    const st = String(snap.status || "").trim();
    // SSE reconnect / daemon restart drops jobs from the active list while
    // they are still running — do not chime yet; they will reappear.
    if (st === "starting" || st === "running") {
      chimeEnded.delete(key); // allow a real end later
      return;
    }
    // User-initiated stop is silent (Android parity).
    if (st === "stopped") {
      // Still refresh the open transcript so a mid-turn stop settles the UI
      // when SSE notices the drop before the open-session poll does.
      if (!meta.alreadyPainted) {
        await paintOpenAfterJob(profileId, jobId, snap, meta);
      }
      return;
    }
    // Final answer + process steps before the bi-bi, when this is the open
    // row. pollJob passes alreadyPainted after its own loadTail so we only
    // play the sound here and do not fetch twice.
    if (!meta.alreadyPainted) {
      await paintOpenAfterJob(profileId, jobId, snap, meta);
    }
    // Only explicit error is a failure; pruned jobs (404 handled below) and
    // empty/unknown status are treated as success.
    if (st === "error") playChime("error");
    else playChime("done");
    // The stream cannot say failed-vs-finished, so let the daemon re-decide
    // now that the job is over. Without this the tag needed a manual refresh.
    refreshSessions();
  } catch {
    // 404 after prune of a finished job → success cue, not failure.
    if (!meta.alreadyPainted) {
      await paintOpenAfterJob(profileId, jobId, null, meta);
    }
    playChime("done");
    refreshSessions();
  }
}

/** True when a finished job is the transcript currently on screen. */
function jobMatchesOpen(profileId, jobId, snap, meta = {}) {
  const open = state.open;
  if (!open || open.profileId !== profileId) return false;
  if (state.job && state.job.id === jobId) return true;
  const openSid = open.sessionId || "";
  if (!openSid) return false;
  if (sessionIsJobPlaceholder(openSid, jobId)) return true;
  const candidates = [
    snap && snap.session_id,
    snap && snap.new_session_id,
    meta.sessionId,
    meta.newSessionId,
  ].filter(Boolean);
  return candidates.includes(openSid);
}

/**
 * Reload the open transcript after a job ends (or is stopped) so process
 * steps and the final answer land before the end chime. No-op when another
 * session is open. Single-flight so pollJob + SSE do not double-fetch.
 */
let paintAfterJobInFlight = null;
async function paintOpenAfterJob(profileId, jobId, snap, meta = {}) {
  if (!jobMatchesOpen(profileId, jobId, snap, meta)) return;
  if (!state.open) return;
  // Process view is the common lag case; always reload on end so the answer
  // bubble is not delayed either.
  const gen = state.openGen;
  const sid = state.open.sessionId;
  const pid = state.open.profileId;
  if (paintAfterJobInFlight) {
    try { await paintAfterJobInFlight; } catch { /* ignore */ }
    return;
  }
  paintAfterJobInFlight = loadTail(gen, { keepLive: false, follow: true })
    .catch(() => {})
    .finally(() => { paintAfterJobInFlight = null; });
  await paintAfterJobInFlight;
  // Drop if the user switched away mid-fetch.
  if (!isOpenStill(gen, pid, sid)) return;
  // They watched the turn finish on this page — same as opening the row
  // afterwards: dim the Focus "turn finished" unread styling.
  markSeen(profileById(pid), sid);
}

// ---- process view live refresh ------------------------------------------
// Process steps arrive from the journal (`?detail=steps`), not the status
// SSE. Status chimes fire as soon as phase/tool flips; without a pull the
// strip trails the sound by up to a poll cycle (or until turn end).

const PROCESS_REFRESH_MIN_MS = 900;
let processRefreshTimer = null;
let processRefreshLast = 0;
let processRefreshInFlight = null;

function frameMatchesOpen(profileId, frame) {
  const open = state.open;
  if (!open || !frame || open.profileId !== profileId) return false;
  if (state.job && state.job.id === frame.job_id) return true;
  const openSid = open.sessionId || "";
  if (!openSid) return false;
  return frame.session_id === openSid || frame.new_session_id === openSid;
}

/** Throttled journal reload when process view is on for the open session. */
function scheduleProcessRefresh(profileId, frame) {
  if (!state.open || !processViewOn(state.open.sessionId)) return;
  if (!frameMatchesOpen(profileId, frame)) return;
  const now = Date.now();
  const wait = Math.max(0, PROCESS_REFRESH_MIN_MS - (now - processRefreshLast));
  if (processRefreshTimer) return;
  processRefreshTimer = setTimeout(() => {
    processRefreshTimer = null;
    processRefreshLast = Date.now();
    runProcessRefresh();
  }, wait);
}

function runProcessRefresh() {
  if (!state.open || !processViewOn(state.open.sessionId)) return;
  if (processRefreshInFlight) return;
  const gen = state.openGen;
  const sid = state.open.sessionId;
  const pid = state.open.profileId;
  processRefreshInFlight = loadTail(gen, { keepLive: true, follow: true })
    .catch(() => {})
    .finally(() => { processRefreshInFlight = null; });
  // Fire-and-forget; openGen guard inside loadTail drops stale paints.
  void processRefreshInFlight.then(() => {
    if (!isOpenStill(gen, pid, sid)) return;
  });
}

// ---- codex goals --------------------------------------------------------

/** "4m" / "2h 10m" — short enough for a status line. */
function shortSpan(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0));
  if (s < 60) return s + "s";
  const m = Math.floor(s / 60);
  if (m < 60) return m + "m";
  const h = Math.floor(m / 60);
  return h + "h " + (m % 60) + "m";
}

/**
 * The daemon's goal watchdog for one session.
 *
 * A codex thread can carry a goal it restarts itself for after every turn —
 * until it doesn't, and then it sits idle with work left. The daemon can
 * watch for that and type `/goal resume`; this is the switch, and the
 * answer to "what is it doing right now".
 */
async function paintGoal(box, profile, sessionId) {
  box.textContent = "";
  box.appendChild(el("div", "help", "Looking for a goal…"));
  let row = null;
  let failed = "";
  try {
    const data = await call(profile, "/api/goals", { timeout: 15000 });
    const rows = (data && data.goals) || [];
    row = rows.find((g) => String(g.session_id || "") === sessionId) || null;
  } catch (e) {
    failed = e && e.message ? e.message : "could not read the goals";
  }
  box.textContent = "";
  if (failed) { box.appendChild(el("div", "help", failed)); return; }
  // No goal on this thread: say nothing rather than explain an absence.
  if (!row) { box.remove(); return; }

  box.appendChild(el("label", null, "Goal"));
  box.appendChild(el("div", "goalobj", row.objective || "(no objective)"));

  const cb = el("label");
  const check = el("input");
  check.type = "checkbox";
  check.style.width = "auto";
  check.checked = !!row.watching;
  check.disabled = String(row.status || "") !== "active";
  check.addEventListener("change", async () => {
    check.disabled = true;
    try {
      await call(profile, "/api/goals/watch",
                 { method: "POST", body: { session_id: sessionId, on: check.checked } });
    } catch (e) {
      toast(e && e.message ? e.message : "could not change the watch");
    }
    paintGoal(box, profile, sessionId);
  });
  cb.appendChild(check);
  cb.appendChild(document.createTextNode(
    " Keep it moving — type /goal resume when it stalls"));
  box.appendChild(cb);

  box.appendChild(el("div", "help", goalStatusLine(row)));
}

/** One line of plain truth about a goal's state. */
function goalStatusLine(row) {
  const status = String(row.status || "");
  if (status !== "active") return "This goal is " + (status || "gone") + ".";
  if (row.stopped_why) {
    return "The daemon stopped: " + row.stopped_why
      + ". Switch it back on to try again.";
  }
  const bits = [];
  if (row.working) bits.push("Working now.");
  else if (!row.live_tui) bits.push("No live TUI on the host — send this session a message first, so there is a prompt to type into.");
  else {
    bits.push("Idle for " + shortSpan(row.idle_s) + ".");
    if (row.watching) {
      bits.push("A resume follows " + shortSpan(row.idle_threshold_s) + " of quiet.");
    }
  }
  if (row.resumes) {
    bits.push("Resumed " + row.resumes + (row.resumes === 1 ? " time" : " times")
      + (row.last_resume_at
        ? ", last " + shortSpan(Date.now() / 1000 - row.last_resume_at) + " ago." : "."));
  }
  if (!row.watching && !row.stopped_why) {
    bits.push("Codex restarts a goal itself; this is for when it does not.");
  }
  return bits.join(" ");
}

// ---- process view -------------------------------------------------------
//
// The default transcript is the result: what was asked, what was answered.
// Process view additionally asks the daemon for `steps` — the tool calls,
// their output and the thinking that happened between those messages.
// Per session and off by default, so nobody who liked the old view is moved.

function processViewOn(sessionId) {
  const map = state.settings.processView || {};
  return !!map[sessionId || (state.open && state.open.sessionId) || ""];
}

function setProcessView(sessionId, on) {
  const map = state.settings.processView || {};
  if (on) map[sessionId] = true;
  else delete map[sessionId];
  state.settings.processView = map;
  store.save();
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

/**
 * Download one drop entry without a size ceiling.
 *
 * The body is read piecewise with an IDLE timeout (no bytes for 60 s), not a
 * total one, so a 1 GB file on a slow link is fine. When the daemon serves
 * the file with Accept-Ranges (2.14.2+), a dropped link resumes with Range +
 * If-Range instead of starting over; a zipped folder restarts. `sink` is
 * either a FileSystemWritableFileStream (Chromium's save picker, straight to
 * disk) or null to collect a Blob, which browsers spill to disk themselves.
 */
async function downloadDrop(profile, name, { sink = null, onProgress } = {}) {
  const url = profile.baseUrl.replace(/\/+$/, "") + `/api/drop/${encodeURIComponent(name)}`;
  const parts = [];
  let got = 0;
  let total = 0;
  let etag = "";
  let served = name;
  let resumable = false;
  for (let attempt = 0; ; attempt++) {
    const ctrl = new AbortController();
    let idle = setTimeout(() => ctrl.abort(), 60000);
    const poke = () => { clearTimeout(idle); idle = setTimeout(() => ctrl.abort(), 60000); };
    const headers = { "X-Auth-Token": profile.token };
    if (got > 0) {
      headers.Range = `bytes=${got}-`;
      if (etag) headers["If-Range"] = etag;
    }
    try {
      const res = await fetch(url, {
        headers, signal: ctrl.signal, credentials: "omit", cache: "no-store",
      });
      if (!res.ok) {
        let msg = `HTTP ${res.status}`;
        try { const j = await res.json(); if (j && j.error) msg = j.error; } catch { /* binary */ }
        throw new DaemonError(res.status, msg);
      }
      if (got > 0 && res.status !== 206) {
        // The host file changed (or a zip was rebuilt): start over.
        got = 0;
        parts.length = 0;
        if (sink) { await sink.truncate(0); await sink.seek(0); }
      }
      const hdrName = res.headers.get("X-Drop-Name");
      if (hdrName) {
        try { served = decodeURIComponent(hdrName); } catch { served = hdrName; }
      }
      total = Number(res.headers.get("X-Drop-Size")) || total;
      etag = res.headers.get("ETag") || etag;
      resumable = res.headers.get("Accept-Ranges") === "bytes";
      const reader = res.body.getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        poke();
        if (sink) await sink.write(value); else parts.push(value);
        got += value.length;
        if (onProgress) onProgress(got, total);
      }
      clearTimeout(idle);
      if (total && got < total) throw new Error("connection closed early");
      break;
    } catch (e) {
      clearTimeout(idle);
      if (e instanceof DaemonError) throw e;
      // Only a resumable, part-received file is worth another go; anything
      // else would just repeat the same failure.
      if (!(resumable && got > 0) || attempt >= 8) {
        throw new DaemonError(0, e.name === "AbortError"
          ? "The download stalled (no data for 60 s)"
          : (e.message || "Download failed"));
      }
      await new Promise((r) => setTimeout(r, Math.min(15000, 1000 * 2 ** attempt)));
    }
  }
  return { name: served, size: got, blob: sink ? null : new Blob(parts) };
}

// ------------------------------------------------------------------ time

/**
 * Normalize last_active/started to ms since epoch.
 * Daemon running-job rows send a Unix *seconds* float; store sessions send
 * ISO-8601 strings. Date.parse(number) is NaN, so unhandled floats used to
 * sort to 0 and bury the working session at the bottom of the list.
 */
function epochOf(v) {
  if (v == null || v === "") return 0;
  if (typeof v === "number" && Number.isFinite(v)) {
    // Seconds vs milliseconds (daemon time.time() is seconds).
    return v < 1e12 ? v * 1000 : v;
  }
  const s = String(v).trim();
  if (!s) return 0;
  // Numeric string (JSON sometimes stringifies oddly).
  if (/^\d+(\.\d+)?$/.test(s)) {
    const n = parseFloat(s);
    if (Number.isFinite(n)) return n < 1e12 ? n * 1000 : n;
  }
  const t = Date.parse(s);
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
/** Local time for message hover tooltips (native `title`). Today → time only. */
function messageHoverTime(ts) {
  if (ts == null || ts === "") return "";
  let ms = typeof ts === "number" ? ts : epochOf(ts);
  // Unix seconds (Codex sometimes stores whole seconds as numbers).
  if (ms > 0 && ms < 1e12) ms *= 1000;
  if (!ms || !Number.isFinite(ms)) return "";
  const d = new Date(ms);
  if (Number.isNaN(d.getTime())) return "";
  const timeOpts = {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    hourCycle: "h23",
  };
  if (d.toDateString() === new Date().toDateString()) {
    return d.toLocaleTimeString([], timeOpts);
  }
  // Other days: month + day + time (year omitted — rarely useful in a chat).
  return d.toLocaleString([], {
    month: "short",
    day: "numeric",
    ...timeOpts,
  });
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
 * Human phrase for what the agent is doing — banner line 1 (description).
 * Daemon puts the human tool description in phase_detail and the raw
 * command/path in tool_detail (line 2).
 */
function phaseLine(frame, job, pal) {
  const agent = (pal && pal.label) || "Agent";
  if (!frame) return shortDetail(job && job.toolLine) || `${agent} is working…`;
  const phase = frame.phase || "";
  // Description line — do not middle-ellipsis paths here; shortDetail only
  // for long free text. Prefer full phase_detail up to ~100 chars.
  const detail = shortDetail(frame.phase_detail || "", 100);
  if (phase === "thinking") return `${agent} is thinking…`;
  if (phase === "writing") return `${agent} is writing…`;
  if (phase === "editing") return detail ? `Editing · ${detail}` : "Editing files…";
  if (phase === "reading") return detail ? `Reading · ${detail}` : "Reading files…";
  if (phase === "searching") return detail ? `Searching · ${detail}` : "Searching the code…";
  if (phase === "running") return detail ? `Running · ${detail}` : "Running a command…";
  if (phase === "browsing") return detail ? `Browsing · ${detail}` : "Browsing the web…";
  if (phase === "delegating") return "Delegating to a subagent…";
  if (phase === "asking") return detail ? `Asking · ${detail}` : "Waiting for your answer…";
  if (phase && detail) return `${phase} · ${detail}`;
  if (phase) return phase;
  // Fallback: last tool name only (command goes on line 2).
  const tool = shortDetail(frame.tool || "", 32);
  if (tool) return `⚙ ${tool}`;
  return shortDetail(job && job.toolLine, 100) || `${agent} is working…`;
}

/**
 * Banner line 2: the raw command / path / pattern from tool_detail.
 * Empty when the daemon only has a description (same string on both would
 * just repeat). Includes things like
 * `[attached: ~/.agentremoted/uploads/….png]`.
 */
function commandLine(frame, job) {
  if (!frame && !(job && job.toolLine)) return "";
  const cmd = (frame && (frame.tool_detail || ""))
    || (job && job.toolLine)
    || "";
  const desc = (frame && (frame.phase_detail || "")) || "";
  const t = String(cmd).replace(/\s+/g, " ").trim();
  if (!t) return "";
  // Don't repeat the description on line 2.
  if (desc && t === String(desc).replace(/\s+/g, " ").trim()) return "";
  // Longer clip so attachment paths stay readable.
  return shortDetail(t, 160);
}

/**
 * Live status for the banner: { line1, line2 }.
 * line1 = description + elapsed (+ queued); line2 = command when present.
 */
function liveStatusParts(frame, job, pal) {
  let line1 = phaseLine(frame, job, pal);
  const secs = frame && typeof frame.elapsed_s === "number"
    ? frame.elapsed_s
    : Math.max(0, Math.round((Date.now() - (job.startedAt || Date.now())) / 1000));
  line1 += `  ·  ${elapsed(secs)}`;
  const queued = (job.queued && job.queued.length)
    || (frame && frame.queued_count)
    || 0;
  if (queued) line1 += `  ·  ${queued} queued`;
  return { line1, line2: commandLine(frame, job) };
}

/** @deprecated single-string form kept for any call sites / debug */
function liveStatusLine(frame, job, pal) {
  const { line1, line2 } = liveStatusParts(frame, job, pal);
  return line2 ? `${line1}\n${line2}` : line1;
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

/** Provider colours: the daemon owns them (palette / palettes in ping).
 *  PROVIDERS stays as the offline/older-daemon fallback. */
function applyRemotePalettes(ping) {
  const map = (ping && ping.palettes) || {};
  if (!Object.keys(map).length && ping && ping.palette && ping.provider) {
    map[ping.provider] = ping.palette;
  }
  let changed = false;
  Object.entries(map).forEach(([name, p]) => {
    if (!p || !p.accent) return;
    const cur = PROVIDERS[name] || {};
    if (cur.accent === p.accent && (!p.heading || cur.heading === p.heading)
        && (!p.inline || cur.inline === p.inline)) return;
    PROVIDERS[name] = {
      ...cur,
      ...(p.label ? { label: p.label } : {}),
      accent: p.accent || cur.accent,
      heading: p.heading || cur.heading,
      inline: p.inline || cur.inline,
    };
    changed = true;
  });
  return changed;
}

/**
 * Session-state words and colours from the daemon (≥ 2.23). The built-in
 * FOCUS_LABELS and CSS colours stay as the fallback for an older daemon.
 */
const STATE_ROLE_VARS = { attention: ["--attention", "--warn"], danger: ["--danger"], ok: ["--ok"] };
function applyRemoteStates(ping) {
  const states = (ping && Array.isArray(ping.states)) ? ping.states : [];
  let changed = false;
  const root = document.documentElement;
  states.forEach((st) => {
    if (!st || !st.id) return;
    if (st.label && FOCUS_LABELS[st.id] !== st.label) {
      FOCUS_LABELS[st.id] = st.label;
      changed = true;
    }
    const vars = STATE_ROLE_VARS[st.role] || [];
    if (!/^#[0-9a-fA-F]{6}$/.test(st.color || "")) return;
    vars.forEach((name) => {
      if (root.style.getPropertyValue(name).trim() !== st.color) {
        root.style.setProperty(name, st.color);
        changed = true;
      }
    });
  });
  return changed;
}

async function pingProfile(profile) {
  try {
    const ping = await call(profile, "/api/ping", { timeout: 10000 });
    if (applyRemotePalettes(ping)) {
      // Palettes changed: repaint what is coloured by provider. (This used
      // to call an undefined render(), and the ReferenceError skipped every
      // assignment below - caps, models, focus, share - for this ping.)
      renderFilters();
      renderSessions();
      if (state.open) {
        const op = profileById(state.open.profileId);
        applyAccent(sessionProvider(state.open.session, op));
      }
    }
    if (applyRemoteStates(ping)) {
      renderSessions();
      renderStatus();
    }
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
    // Focus list (agentremoted ≥ 2.6): absent on older daemons, which then
    // contribute nothing to Focus mode rather than dumping every session in.
    profile.focus = !!ping.focus;
    // Session share (agentremoted ≥ 2.7): hosted read-only /share/<token>.
    profile.share = !!ping.share;
    // Linked host files (agentremoted ≥ 2.20): /api/sessions/<id>/file.
    profile.linkedFiles = !!ping.linked_files;
    // Changes view (agentremoted ≥ 2.24): /api/sessions/<id>/changes.
    profile.changes = !!ping.changes;
    // Live TUI frames pushed over SSE + ordered key batches (agentremoted ≥ 2.22).
    profile.tuiStream = !!ping.tui_stream;
    profile.chunkedUpload = !!ping.chunked_upload
      || !!(ping.caps && ping.caps.chunked_upload);
    if (typeof ping.max_upload_mb === "number" && ping.max_upload_mb > 0) {
      profile.maxUploadMb = ping.max_upload_mb;
    }
    store.save();
    syncShareButton();
    // Models, efforts and capabilities may have just arrived or changed.
    if (state.open && state.open.profileId === profile.id) { syncComposer(); syncChangesButton(); }
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
/** Always allow these even if an older daemon omits them from /api/ping. */
const ALWAYS_SLASH = ["/rewind", "/goal"];
const slashOf = (profile, harness = null) => {
  let list = [];
  if (harness && profile && profile.providerDetails && profile.providerDetails[harness])
    list = profile.providerDetails[harness].slash_commands || [];
  else
    list = (profile && profile.slashCommands) || [];
  // De-dupe while keeping daemon order first.
  const seen = new Set(list);
  for (const c of ALWAYS_SLASH) if (!seen.has(c)) { list = list.concat([c]); seen.add(c); }
  return list;
};
const effortsOf = (profile, harness = null) => {
  if (harness && profile && profile.providerDetails && profile.providerDetails[harness])
    return profile.providerDetails[harness].efforts || [];
  return (profile && profile.efforts) || [];
};

/**
 * Model/effort for a harness. Multi hosts used to keep one profile.model for
 * every CLI — starting Codex after Claude sent `claude-fable-5` as `-m`.
 * Prefer per-harness map; never return a value outside this harness's list.
 */
function modelOf(profile, harness = null) {
  if (!profile) return "";
  const h = (harness || profile.provider || "").toLowerCase();
  const models = modelsOf(profile, h || null);
  const map = profile.modelByHarness || {};
  const stored = (h && map[h]) || "";
  if (stored && (!models.length || models.includes(stored))) return stored;
  if (profile.model && models.includes(profile.model)) return profile.model;
  return models[0] || "";
}
function setModelFor(profile, harness, model) {
  if (!profile) return;
  const h = (harness || profile.provider || "").toLowerCase();
  if (!profile.modelByHarness) profile.modelByHarness = {};
  if (h) profile.modelByHarness[h] = model;
  profile.model = model;
}
function effortOf(profile, harness = null) {
  if (!profile) return "";
  const h = (harness || profile.provider || "").toLowerCase();
  const efforts = effortsOf(profile, h || null);
  const map = profile.effortByHarness || {};
  const stored = (h && map[h]) || "";
  if (stored && (!efforts.length || efforts.includes(stored))) return stored;
  if (profile.effort && efforts.includes(profile.effort)) return profile.effort;
  return efforts[0] || "";
}
function setEffortFor(profile, harness, effort) {
  if (!profile) return;
  const h = (harness || profile.provider || "").toLowerCase();
  if (!profile.effortByHarness) profile.effortByHarness = {};
  if (h) profile.effortByHarness[h] = effort;
  profile.effort = effort;
}

// ----------------------------------------------------------- onboarding

const DAEMON_INSTALL_CMD =
  "curl -fsSL https://raw.githubusercontent.com/jxw1102/agent-remote/main/install.sh | bash";
const OTHER_CLIENTS_URL = "https://github.com/jxw1102/agent-remote#clients";
const DAEMON_REPO_URL = "https://github.com/jxw1102/agent-remote";

/** Shared empty-state mark (chevron + underscore on a disc). */
function appendEmptyLogo(parent) {
  const logo = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  logo.setAttribute("class", "empty-logo");
  logo.setAttribute("viewBox", "0 0 108 108");
  logo.setAttribute("width", "54");
  logo.setAttribute("height", "54");
  logo.setAttribute("aria-hidden", "true");
  logo.innerHTML = [
    '<rect width="108" height="108" rx="54" fill="#12121a"/>',
    '<path d="M34 40 L48 54 L34 68" stroke="#d97757" stroke-width="7"',
    ' fill="none" stroke-linecap="round" stroke-linejoin="round"/>',
    '<path d="M56 68 L76 68" stroke="#00d4ff" stroke-width="7"',
    ' stroke-linecap="round"/>',
  ].join("");
  parent.appendChild(logo);
}

/**
 * First-run / empty-list guide: clients need a running agentremoted.
 * @param {{ compact?: boolean }} opts
 */
function buildDaemonGuide(opts = {}) {
  const empty = el("div", opts.compact ? "empty guide" : "empty welcome");
  appendEmptyLogo(empty);
  empty.appendChild(el("h2", null, "Connect a daemon to start"));
  const lead = el("p");
  lead.innerHTML = "This page is only a <strong>client</strong>. Install "
    + "<strong>agentremoted</strong> next to Claude Code, Grok, Codex, or "
    + "DeepSeek on your Mac or a server, then add it here with its URL and token.";
  empty.appendChild(lead);
  const steps = document.createElement("ol");
  steps.className = "welcome-steps";
  [
    "Install the daemon: <code>" + DAEMON_INSTALL_CMD + "</code>",
    "Copy the token from <code>~/.agentremoted/token</code> and add a profile.",
  ].forEach((html) => {
    const li = document.createElement("li");
    li.innerHTML = html;
    steps.appendChild(li);
  });
  empty.appendChild(steps);
  const actions = el("div", "welcome-actions");
  const add = el("button", "primary", "Add a daemon");
  add.type = "button";
  add.addEventListener("click", openProfiles);
  actions.appendChild(add);
  empty.appendChild(actions);
  // Own line under the button — riding in the actions row it fought the
  // button for width and the long label wrapped badly.
  const link = document.createElement("a");
  link.className = "welcome-link below";
  link.href = OTHER_CLIENTS_URL;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = "Other platforms →";
  empty.appendChild(link);
  return empty;
}

function showWelcomeIfNeeded() {
  const tr = $("transcript");
  if (!tr) return;
  // Only replace the idle welcome; never clobber an open session view.
  if (state.open) return;
  tr.innerHTML = "";
  if (!state.profiles.length) {
    tr.appendChild(buildDaemonGuide());
  } else {
    const empty = el("div", "empty");
    appendEmptyLogo(empty);
    empty.appendChild(el("h2", null, "Pick a session"));
    empty.appendChild(el("p", null,
      "Every daemon you add shows up in one list on the left — Claude, Grok, Codex, and DeepSeek side by side."));
    tr.appendChild(empty);
  }
}

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
  // Agent-spawned sessions are always filtered out: subagent transcripts and
  // shells that never got a turn are not work you started, and no setting
  // brings them back. `?all=1` still exists on the daemon for debugging.
  const all = "";

  // Search: progressive NDJSON stream — paint each hit as it arrives.
  if (query) {
    const collected = [];
    const seen = new Set(); // profileId/sessionId
    let paintTimer = 0;
    const paint = () => {
      if (gen !== state.gen) return;
      state.rows = collected.slice().sort((a, b) => b.sortKey - a.sortKey);
      state.loading = true; // still scanning body for more hits
      renderSessions();
      renderStatus();
    };
    const schedulePaint = () => {
      if (paintTimer) return;
      paintTimer = setTimeout(() => { paintTimer = 0; paint(); }, 40);
    };
    const addHit = (profile, s) => {
      const sid = s.id || s.session_id || "";
      const key = `${profile.id}/${sid}`;
      if (sid && seen.has(key)) return;
      if (sid) seen.add(key);
      collected.push({
        profileId: profile.id,
        profileName: profile.name || profile.baseUrl,
        provider: s.provider || profile.provider || "",
        session: s,
        sortKey: epochOf(s.last_active) || epochOf(s.started),
      });
      schedulePaint();
    };

    // Clear stale non-matching rows on first paint of a new query.
    if (gen === state.gen) {
      state.rows = [];
      renderSessions();
    }

    await Promise.all(targets.map(async (profile) => {
      const path = `/api/sessions/search?q=${encodeURIComponent(query)}&limit=40&stream=1${all}`;
      let hits = 0;
      try {
        await streamSearch(profile, path, (s) => {
          hits += 1;
          addHit(profile, s);
        }, gen, 45000);
        state.feeds[profile.id] = { count: hits };
      } catch (e) {
        // Network / old proxy: fall back to batch JSON search.
        try {
          const data = await call(profile,
            `/api/sessions/search?q=${encodeURIComponent(query)}&limit=40${all}`,
            { timeout: 45000 });
          (data.results || []).forEach((s) => addHit(profile, s));
          state.feeds[profile.id] = { count: (data.results || []).length };
        } catch (e2) {
          state.feeds[profile.id] = { error: e2.message || e.message };
        }
      }
    }));

    if (gen !== state.gen) return;
    if (paintTimer) clearTimeout(paintTimer);
    // Search mode: keep hit ranking (no pin) — user asked for matches.
    state.rows = collected.sort((a, b) => b.sortKey - a.sortKey);
    state.loading = false;
    renderSessions();
    renderStatus();
    return;
  }

  const collected = [];
  await Promise.all(targets.map(async (profile) => {
    // Focus mode asks the daemon for the rows directly rather than filtering
    // /api/sessions here: a project you have not touched in weeks falls
    // outside the recency window, and that is exactly the row you must not
    // lose. /api/focus is membership-scoped, so it cannot be truncated away.
    if (focusMode() && !focusCapable(profile)) {
      // Contributing its whole session list instead would silently fill the
      // list with sessions the human never enrolled.
      state.feeds[profile.id] = { count: 0, note: "no focus support" };
      return;
    }
    const path = focusMode()
      ? "/api/focus"
      : `/api/sessions?limit=40${all}`;
    try {
      const data = await call(profile, path, { timeout: 45000 });
      const list = data.sessions || [];
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
  state.rows = finalizeSessionRows(collected);
  // Which mode these rows came from. Without this the Focus chip briefly
  // showed the All list's count (e.g. "Focus · 120") between the click and
  // the fetch landing.
  state.rowsFocus = focusMode();
  syncOpenFocusFlag();
  state.loading = false;
  renderSessions();
  renderStatus();
}

/**
 * Adopt the freshly fetched focus flag for the open session, so the row
 * button stops lying after the card changed on another device.
 */
function syncOpenFocusFlag() {
  const open = state.open;
  if (!open) return;
  const row = state.rows.find((r) => r.session && r.session.id === open.sessionId);
  if (!row || typeof row.session.focus !== "boolean") return;
  open.session = { ...open.session, focus: row.session.focus };
}

/**
 * Prefer the better of two rows for the same session id (running > richer meta).
 */
function preferSessionRow(a, b) {
  const score = (r) => {
    let n = 0;
    if (r.session && r.session.running) n += 100;
    if (r.session && r.session.job_id) n += 10;
    if (r.session && (r.session.title || r.session.last_text)) n += 5;
    if (r.session && r.session.cwd) n += 2;
    n += (r.sortKey || 0) / 1e15; // tiny recency tie-break
    return n;
  };
  return score(a) >= score(b) ? a : b;
}

/**
 * Keep the open + actively working sessions visible and sorted to the top.
 * Injects a row when the list API omitted them (round-robin starvation,
 * stale last_active, multi-host flood) so the session you're in never
 * "vanishes" from the left column.
 *
 * Dedupe is by session **id** (not profileId/id): two web profiles pointed at
 * the same daemon used to list every session twice.
 */
function finalizeSessionRows(collected) {
  const byId = new Map(); // sessionId -> row
  const now = Date.now();

  const put = (row) => {
    const sid = (row.session && row.session.id) || "";
    if (!sid) return;
    const prev = byId.get(sid);
    byId.set(sid, prev ? preferSessionRow(prev, row) : row);
  };

  collected.forEach(put);

  // A new session is listed as job:<id> until the harness names it. If that
  // placeholder is still the open transcript, follow the real uuid so
  // messages/continue stop 404ing after the turn.
  if (state.open && isJobPlaceholder(state.open.sessionId)) {
    const jid = String(state.open.sessionId).slice(4);
    for (const row of collected) {
      if (row.profileId !== state.open.profileId || !row.session) continue;
      const sid = String(row.session.id || "");
      if (!sid || isJobPlaceholder(sid)) continue;
      if (String(row.session.job_id || "") === jid) {
        state.open.sessionId = sid;
        state.open.session = { ...state.open.session, ...row.session, id: sid };
        if (state.job && sessionIsJobPlaceholder(state.job.sessionId, jid)) {
          state.job.sessionId = sid;
        }
        break;
      }
    }
  }

  // Inject sessions known from the live status stream but missing in /api/sessions.
  for (const [pid, jobs] of Object.entries(state.active || {})) {
    const profile = profileById(pid);
    for (const job of jobs || []) {
      const sid = (job.new_session_id || job.session_id || "").trim();
      if (!sid) continue;
      if (byId.has(sid)) {
        // Mark existing row as running without cloning a second entry.
        const row = byId.get(sid);
        row.session = { ...row.session, running: true,
          job_id: job.job_id || job.id || row.session.job_id || "" };
        row.sortKey = Math.max(row.sortKey || 0, now);
        continue;
      }
      // Focus mode: membership is the daemon's call, so a running session that
      // is not a card must not be conjured into the list. Upgrading rows we
      // already have (above) is fine; inventing new ones is not.
      if (focusMode()) continue;
      const prompt = String(job.prompt || "").replace(/\s+/g, " ").trim();
      put({
        profileId: pid,
        profileName: (profile && (profile.name || profile.baseUrl)) || "Daemon",
        provider: job.provider || (profile && profile.provider) || "",
        session: {
          id: sid,
          title: prompt ? prompt.slice(0, 80) : "Working…",
          last_active: now,
          last_text: prompt.slice(0, 200),
          cwd: job.cwd || "",
          running: true,
          job_id: job.job_id || job.id || "",
        },
        sortKey: now,
      });
    }
  }

  // Inject the open session if still missing (idle open, or list starved it).
  // Not in Focus mode: reading a session you have marked done must not quietly
  // put it back on the list.
  if (state.open && state.open.sessionId && state.open.profileId
      && !focusMode()) {
    const sid = state.open.sessionId;
    if (!byId.has(sid)) {
      const profile = profileById(state.open.profileId);
      const s = state.open.session || { id: sid };
      put({
        profileId: state.open.profileId,
        profileName: (profile && (profile.name || profile.baseUrl)) || "Daemon",
        provider: (s.provider || state.open.provider
          || (profile && profile.provider) || ""),
        session: { ...s, id: sid },
        sortKey: epochOf(s.last_active) || now,
      });
    }
  }

  const rows = [...byId.values()];
  const working = workingKeys();
  const openKey = state.open
    ? `${state.open.profileId}/${state.open.sessionId}` : "";
  const openSid = (state.open && state.open.sessionId) || "";
  for (const row of rows) {
    const key = `${row.profileId}/${row.session.id}`;
    let sk = row.sortKey || epochOf(row.session.last_active)
      || epochOf(row.session.started) || 0;
    // Pin working / open / daemon-flagged running to "now" so they stay first.
    if (row.session.running || working.has(key)
        || row.session.id === openSid || key === openKey) {
      sk = Math.max(sk, now);
    }
    row.sortKey = sk;
  }
  rows.sort((a, b) => b.sortKey - a.sortKey);
  return rows;
}

/**
 * Progressive search: daemon streams NDJSON lines
 *   {"type":"hit","session":{...}} / {"type":"done",...}
 */
async function streamSearch(profile, path, onHit, gen, timeout = 45000) {
  const url = profile.baseUrl.replace(/\/+$/, "") + path;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeout);
  try {
    const res = await fetch(url, {
      method: "GET",
      headers: { "X-Auth-Token": profile.token, Accept: "application/x-ndjson" },
      signal: ctrl.signal,
      credentials: "omit",
      cache: "no-store",
    });
    if (!res.ok) {
      const msg = res.status === 401 ? "Token rejected by the daemon" : `HTTP ${res.status}`;
      throw new DaemonError(res.status, msg);
    }
    // Non-streaming JSON fallback if proxy rewrote content-type.
    const ctype = (res.headers.get("content-type") || "").toLowerCase();
    if (ctype.includes("application/json") && !ctype.includes("ndjson")) {
      const data = await res.json();
      (data.results || []).forEach((s) => { if (gen === state.gen) onHit(s); });
      return;
    }
    if (!res.body || !res.body.getReader) {
      const text = await res.text();
      text.split("\n").forEach((line) => {
        if (!line.trim() || gen !== state.gen) return;
        try {
          const ev = JSON.parse(line);
          if (ev.type === "hit" && ev.session) onHit(ev.session);
        } catch { /* skip */ }
      });
      return;
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      if (gen !== state.gen) {
        try { reader.cancel(); } catch { /* ignore */ }
        break;
      }
      buf += dec.decode(value, { stream: true });
      let nl;
      while ((nl = buf.indexOf("\n")) >= 0) {
        const line = buf.slice(0, nl).trim();
        buf = buf.slice(nl + 1);
        if (!line) continue;
        try {
          const ev = JSON.parse(line);
          if (ev.type === "hit" && ev.session) onHit(ev.session);
          else if (ev.type === "error") throw new DaemonError(0, ev.error || "search failed");
        } catch (e) {
          if (e instanceof DaemonError) throw e;
          // partial/malformed line — ignore
        }
      }
    }
  } catch (e) {
    if (e instanceof DaemonError) throw e;
    if (e.name === "AbortError") throw new DaemonError(0, "The daemon did not answer in time");
    throw new DaemonError(0, e.message || "Request failed");
  } finally {
    clearTimeout(timer);
  }
}

function visibleRows() {
  return state.rows.filter((r) => !state.filter || r.profileId === state.filter);
}

// ------------------------------------------------------------------- focus
// The focus list is a *filter* over this same session list: rows keep their
// layout and gain one state tag. Membership lives on the daemon (so every
// client agrees), and the daemon derives the tag from live job state.

const FOCUS_STATES = ["needs_answer", "failed", "working", "turn_finished"];
const FOCUS_LABELS = {
  needs_answer: "needs answer",
  failed: "failed",
  working: "working",
  turn_finished: "turn finished",
};
// Why each tag is showing, for the row's tooltip.
const FOCUS_HINTS = {
  needs_answer: "Blocked on you — a question, a plan to approve, or a tool permission",
  failed: "The last turn ended in an error",
  working: "The agent is running",
  turn_finished: "The turn ended; it is waiting on your next instruction",
};

/**
 * The focus tag as of *now*, not as of the last list fetch.
 *
 * The SSE stream carries only in-flight jobs, so it can prove two of the four
 * states outright and refute a third:
 *
 *   - a job in the stream and blocked  -> needs_answer
 *   - a job in the stream              -> working
 *   - NO job in the stream             -> whatever the row says, it is not
 *                                         running any more
 *
 * It cannot tell `failed` from `turn_finished` (both are absent from the
 * stream), so those keep the daemon's value; chimeJobEnded refreshes the list
 * when a turn ends, which is what promotes a finished turn to `failed`.
 */
function liveFocusState(session, key, working, blocked) {
  if (blocked.has(key)) return "needs_answer";
  if (working.has(key)) return "working";
  const said = String((session && session.focus_state) || "");
  // Stale "still running" from the fetch: the stream has moved on.
  if (said === "working" || said === "needs_answer") return "turn_finished";
  return said;
}

const focusMode = () => !!state.settings.focusMode;
const focusCapable = (profile) => profile && profile.focus !== false;
const shareCapable = (profile) => !!(profile && profile.share);

function setFocusMode(on) {
  state.settings.focusMode = !!on;
  store.save();
  renderFilters();
  refreshSessions();
}

/**
 * Tell the daemon this session has been looked at.
 *
 * Purely cosmetic: it dims a finished turn's tag, it does not change any
 * state. Best-effort — an older daemon 404s and the tag simply stays lit.
 *
 * Called when the human opens the session from the list, and when a turn
 * finishes while they already have that transcript on screen (watching the
 * result is as good as opening it afterwards).
 */
async function markSeen(profile, sessionId) {
  if (!profile || !sessionId || !focusCapable(profile)) return;
  try {
    await focusCall(profile, `/api/focus/${encodeURIComponent(sessionId)}/seen`);
  } catch { return; /* older daemon, or not in focus — leave the tag lit */ }
  // The cursor moved on the daemon, so dim the tag now instead of making the
  // reader refresh to see their own click take effect. `focus_seen_local`
  // also overrides the "was working" clause below, which would otherwise keep
  // the row lit until the next list fetch. It lives on the row object, so the
  // next fetch replaces it and the server's value rules again.
  let touched = false;
  (state.rows || []).forEach((row) => {
    if (row.profileId !== profile.id) return;
    if (!row.session || row.session.id !== sessionId) return;
    row.session.focus_unread = false;
    row.session.focus_seen_local = true;
    touched = true;
  });
  if (touched) renderSessions();
}

/** POST a focus/title action to the daemon that owns the session. */
async function focusCall(profile, path, body) {
  return call(profile, path, { method: "POST", body: body || {} });
}

/** List title: never echo a bare attachment filename (transcript shows the chip). */
function listTitle(session) {
  const t = String((session && session.title) || "").trim();
  if (!t) return "Untitled session";
  if (looksLikeFilenameTitle(t) || isAttachmentOnly(t)) {
    const folder = String((session && session.cwd) || "").replace(/\/+$/, "").split("/").pop();
    const label = attachmentLabel(t);
    return folder ? `${label} · ${folder}` : label;
  }
  return t;
}

/** List preview: hide if it only repeats the title or is [attached: …]. */
function listPreview(session, query) {
  const raw = query
    ? (session.snippet || session.last_text || "")
    : (session.last_text || "");
  const text = String(raw).replace(/\s+/g, " ").trim();
  if (!text) return "";
  if (isAttachmentOnly(text)) return "";
  const title = listTitle(session).toLowerCase();
  if (text.toLowerCase() === title) return "";
  // Same screenshot name as title (common when daemon still sends filename).
  if (looksLikeFilenameTitle(text) && looksLikeFilenameTitle(listTitle(session))) return "";
  return text;
}

function isAttachmentOnly(text) {
  const lines = String(text || "").split("\n").map((l) => l.trim()).filter(Boolean);
  if (!lines.length) return false;
  return lines.every((l) => /^\[attached:\s*[^\]]+\]$/i.test(l));
}

function looksLikeFilenameTitle(text) {
  const t = String(text || "").trim();
  if (!t) return false;
  if (/\.(png|jpe?g|gif|webp|heic|bmp|pdf|mov|mp4|m4a|wav|zip)$/i.test(t)) return true;
  const low = t.toLowerCase();
  return low.startsWith("screenshot") && /\.(png|jpe?g|heic|webp)$/i.test(low);
}

function attachmentLabel(text) {
  const m = String(text || "").match(/\[attached:\s*([^\]]+)\]/i)
    || String(text || "").match(/([^/\\]+\.\w+)\s*$/);
  const name = m ? String(m[1] || m[0]).split(/[/\\]/).pop() : "";
  const ext = (name.split(".").pop() || "").toLowerCase();
  if (["png", "jpg", "jpeg", "gif", "webp", "heic", "bmp"].includes(ext)) return "Image";
  if (ext === "pdf") return "PDF";
  if (["mp4", "mov", "webm"].includes(ext)) return "Video";
  if (["mp3", "wav", "m4a", "aac"].includes(ext)) return "Audio";
  return "Attachment";
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
  // What the Focus view holds, by state: "1 needs answer" says what to do
  // next. It rides on the Focus switch (its tooltip), not on a line of its own.
  state.focusTally = "";
  if (focusMode() && !state.loading) {
    const tally = {};
    visibleRows().forEach((r) => {
      const s = r.session.focus_state;
      if (s) tally[s] = (tally[s] || 0) + 1;
    });
    state.focusTally = FOCUS_STATES.filter((s) => tally[s])
      .map((s) => `${tally[s]} ${FOCUS_LABELS[s]}`).join(" · ");
  }
  renderFilters();
  const box = $("list-status");
  box.textContent = "";
  if (state.loading) {
    const spin = el("span", "spinner");
    spin.setAttribute("aria-label", "Loading");
    box.appendChild(spin);
  }
  // A dead daemon must never read as "it has no sessions". With several
  // daemons the daemon button and its menu say which and why; with one there
  // is no such button, so it is said here.
  if (state.profiles.length < 2) {
    Object.entries(state.feeds).filter(([, f]) => f.error).forEach(([pid, f]) => {
      const p = profileById(pid);
      const line = el("div", "err", `${(p && p.name) || "Daemon"}: ${f.error}`);
      line.title = line.textContent;
      box.appendChild(line);
    });
  }
}

/**
 * The list is narrowed by two different questions, and each gets the control
 * that fits it, folded into a row that was already there (the header is two
 * rows: view and tools, then find / where / new):
 *
 *   which sessions   Focus | All     two views of one list, so a two-way
 *                                    switch that names both
 *   on which daemon  All daemons ▾   one button and a menu, however many
 *                                    daemons there are
 *
 * They used to be one run of look-alike chips: "Focus" was a toggle that never
 * said what the other choice was, "All" sat beside it meaning all *daemons*,
 * and a chip per daemon wrapped onto a second row from the third daemon on.
 *
 * Called on every status frame, so it only touches the page when what it
 * would draw has changed (otherwise the menu would close under the pointer).
 */
let filtersDrawn = "";

function renderFilters() {
  const box = $("filters");          // row 1: Focus | All
  const dbox = $("filter-daemon");   // row 2, beside the search field
  const focusOn = focusMode();
  const hasFocus = state.profiles.some((p) => focusCapable(p));
  // How many sessions are being carried. Only the Focus fetch knows: the
  // other view is a page of the newest sessions, which holds some of them.
  let carried = 0;
  if (hasFocus && focusOn && state.rowsFocus && !state.loading) {
    carried = visibleRows().length;
  }
  const picked = state.filter ? profileById(state.filter) : null;
  if (state.filter && !picked) state.filter = null;   // that daemon was removed
  const daemons = state.profiles.length >= 2 ? state.profiles.map((p) => {
    const feed = state.feeds[p.id];
    return [p.id, p.name || p.baseUrl, profileHostAccent(p), (feed && feed.error) || "",
            connOf(p.id), p.host || "", p.version || ""];
  }) : [];
  const sig = JSON.stringify([hasFocus, focusOn, carried, state.focusTally || "",
                              state.filter, daemons]);
  if (sig === filtersDrawn) return;
  filtersDrawn = sig;
  const menuWasOpen = !!dbox.querySelector(".filter-pop");
  box.textContent = "";
  dbox.textContent = "";

  if (hasFocus) {
    const seg = el("div", "seg");
    seg.setAttribute("role", "group");
    seg.setAttribute("aria-label", "Which sessions to list");
    const mk = (label, on, title, count) => {
      const b = el("button", "seg-opt", label);
      b.type = "button";
      b.setAttribute("aria-pressed", String(on));
      b.title = title;
      if (count) b.appendChild(el("span", "seg-count", String(count)));
      return b;
    };
    const f = mk("Focus", focusOn, "Only the sessions you are carrying"
      + (focusOn ? `\n${carried ? carried + " in focus: " : ""}${state.focusTally || "Nothing in focus"}` : ""), carried);
    const a = mk("All", !focusOn, "Every session, newest first", 0);
    f.addEventListener("click", () => { if (!focusMode()) setFocusMode(true); });
    a.addEventListener("click", () => { if (focusMode()) setFocusMode(false); });
    seg.append(f, a);
    box.appendChild(seg);
  }

  if (!daemons.length) return;
  const broken = daemons.filter((d) => d[3]).length;
  const btn = el("button", "chip chip-daemon");
  btn.type = "button";
  btn.setAttribute("aria-haspopup", "true");
  btn.setAttribute("aria-expanded", "false");
  if (picked) {
    btn.style.setProperty("--chip", profileHostAccent(picked));
    btn.style.setProperty("--chip-soft", profileHostAccent(picked) + "24");
    btn.setAttribute("aria-pressed", "true");
  }
  if (picked) {
    btn.appendChild(el("span", "chip-daemon-name", picked.name || picked.baseUrl));
  } else {
    // Every daemon: a funnel, not words — this sits beside the search field.
    const ico = el("span", "chip-daemon-ico");
    ico.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none"'
      + ' stroke="currentColor" stroke-width="1.9" stroke-linecap="round"'
      + ' stroke-linejoin="round"><path d="M4 6h16l-6 7v5l-4 2v-7z"/></svg>';
    btn.appendChild(ico);
  }
  btn.setAttribute("aria-label", picked
    ? `Showing ${picked.name || picked.baseUrl} only — choose a daemon`
    : "Showing every daemon — choose one");
  if (broken) {
    const warn = el("span", "chip-daemon-warn");
    warn.title = broken === 1 ? "One daemon cannot be reached" : `${broken} daemons cannot be reached`;
    warn.setAttribute("aria-label", warn.title);
    btn.appendChild(warn);
  }
  btn.appendChild(el("span", "chip-caret", "▾"));
  btn.title = picked ? `Only ${picked.name || picked.baseUrl} — choose a daemon`
                     : "Every daemon — choose one";
  dbox.appendChild(btn);

  const closeMenu = () => {
    const pop = dbox.querySelector(".filter-pop");
    if (pop) pop.remove();
    btn.setAttribute("aria-expanded", "false");
    document.removeEventListener("click", onOutside, true);
    document.removeEventListener("keydown", onKey, true);
  };
  const onOutside = (e) => { if (!dbox.contains(e.target)) closeMenu(); };
  const onKey = (e) => {
    if (e.key !== "Escape") return;
    e.stopPropagation();
    closeMenu();
    btn.focus();
  };
  const pick = (id) => {
    state.filter = id;
    closeMenu();
    renderStatus();     // redraws this row
    renderSessions();
    dbox.querySelector(".chip-daemon")?.focus();
  };
  const openMenu = ({ focus = true } = {}) => {
    const pop = el("div", "filter-pop");
    pop.setAttribute("role", "menu");
    const item = (id, name, accent, hint, bad) => {
      const o = el("button", "pop-opt filter-opt");
      o.type = "button";
      o.setAttribute("role", "menuitemradio");
      o.setAttribute("aria-checked", String((state.filter || null) === id));
      o.setAttribute("aria-selected", String((state.filter || null) === id));
      const main = el("span", "pop-main");
      const dot = el("span", "filter-dot");
      if (accent) dot.style.background = accent;
      main.append(dot, document.createTextNode(name));
      o.appendChild(main);
      if (hint) o.appendChild(el("span", bad ? "pop-hint filter-bad" : "pop-hint", hint));
      o.addEventListener("click", () => pick(id));
      return o;
    };
    pop.appendChild(item(null, "All daemons", "", "", false));
    daemons.forEach(([id, name, accent, error, conn, host, version]) => {
      const hint = error
        || [conn === "live" ? "" : CONN_TEXT[conn] || conn, host, version ? "v" + version : ""]
          .filter(Boolean).join(" · ");
      pop.appendChild(item(id, name, accent, hint, !!error));
    });
    dbox.appendChild(pop);
    btn.setAttribute("aria-expanded", "true");
    document.addEventListener("click", onOutside, true);
    document.addEventListener("keydown", onKey, true);
    if (focus) (pop.querySelector('[aria-checked="true"]') || pop.firstChild).focus();
  };
  btn.addEventListener("click", () => {
    if (dbox.querySelector(".filter-pop")) closeMenu(); else openMenu();
  });
  // A daemon came or went while the menu was open: draw it again, open.
  if (menuWasOpen) openMenu({ focus: false });
}

/**
 * The list is repainted on every status frame (about once a second per
 * daemon). It used to be emptied and rebuilt each time, which restarted the
 * "working" animation, dropped keyboard focus and hover, and made a row jump
 * under the pointer. Now each row is built, compared with what is already on
 * screen, and only swapped in if it differs.
 */
const listNodes = new Map();   // key -> { html, node }

function reconcileList(host, want) {
  const keys = new Set(want.map(([k]) => k));
  for (const [k, old] of listNodes) {
    if (!keys.has(k)) { old.node.remove(); listNodes.delete(k); }
  }
  let cursor = host.firstChild;
  for (const [key, fresh] of want) {
    const html = fresh.outerHTML;
    const old = listNodes.get(key);
    let node;
    let refocus = false;
    if (old && old.html === html && old.node.parentNode === host) {
      node = old.node;
      node._row = fresh._row;   // same pixels, newer data behind the click
    } else {
      node = fresh;
      if (old) {
        refocus = old.node.contains(document.activeElement);
        if (cursor === old.node) cursor = cursor.nextSibling;
        old.node.remove();
      }
      listNodes.set(key, { html, node });
    }
    if (node === cursor) cursor = cursor.nextSibling;
    else host.insertBefore(node, cursor);
    // A row that changed while it had the keyboard keeps it.
    if (refocus && node.focus) node.focus({ preventScroll: true });
  }
  while (cursor) { const next = cursor.nextSibling; cursor.remove(); cursor = next; }
}

function renderSessions() {
  const host = $("sessions");
  const want = [];
  const rows = visibleRows();
  const working = workingKeys();
  const blocked = blockedKeys();

  if (!state.profiles.length) {
    // Full "Connect a daemon" guide lives in the transcript pane only —
    // never paint it twice (list + main). This column is the session list.
    const empty = el("div", "empty list-quiet");
    empty.appendChild(el("p", null, "Sessions appear here."));
    reconcileList(host, [["empty:none", empty]]);
    return;
  }
  if (!rows.length && !state.loading) {
    const empty = el("div", "empty");
    empty.appendChild(el("h2", null, state.query ? "No matches" : "Nothing here yet"));
    empty.appendChild(el("p", null, state.query
      ? `No session on any daemon mentions “${state.query}”.`
      : "Start a session and it will show up here, whichever daemon runs it."));
    reconcileList(host, [["empty:" + state.query, empty]]);
    return;
  }

  let lastDay = "";
  const used = new Set();
  rows.forEach((row) => {
    if (!state.query) {
      const day = dayLabel(row.sortKey);
      if (day !== lastDay) {
        lastDay = day;
        want.push(["day:" + day, el("div", "day", day)]);
      }
    }
    const key = `${row.profileId}/${row.session.id}`;
    const pal = providerOf(row.provider);
    const btn = el("button", "row");
    btn.type = "button";
    // Handlers read the row through the node: a row that is kept on screen
    // is handed the newer data (see reconcileList).
    btn._row = row;
    if (state.open && state.open.profileId === row.profileId
        && state.open.sessionId === row.session.id) {
      btn.setAttribute("aria-current", "true");
    }

    // Done, revealed on hover (CSS) so idle rows stay clean. Only on rows that
    // are actually in Focus — there is no "Track" counterpart because joining
    // is automatic: acting on a session through the daemon enrols it. Nested
    // inside the row button like .session-id-tag, and it stops propagation so
    // the click never also opens the session.
    if (row.session.focus === true && focusCapable(profileById(row.profileId))) {
      const done = el("button", "row-done");
      done.type = "button";
      // Circled check, drawn inline: an icon floats over the row content
      // without needing a text slot the layout would have to reserve.
      done.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" fill="none"'
        + ' stroke="currentColor" stroke-width="1.9" stroke-linecap="round"'
        + ' stroke-linejoin="round"><circle cx="12" cy="12" r="9"/>'
        + '<path d="M8 12.5l2.5 2.5L16 9.5"/></svg>';
      done.title = "Done — take it off Focus";
      done.setAttribute("aria-label", done.title);
      done.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        setRowFocus(btn._row, false);
      });
      btn.appendChild(done);
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
    top.appendChild(el("div", "row-title", listTitle(row.session)));
    top.appendChild(el("div", "row-when", stamp(row.sortKey)));
    btn.appendChild(top);

    const meta = el("div", "row-meta");
    // Host chip in this session's harness colour (Mbp+Grok → cyan, not multi purple).
    // No separate Grok/Claude/Codex tag — host colour already implies harness.
    const hostTag = el("span", "tag host", row.profileName);
    hostTag.style.setProperty("--tag", pal.accent);
    hostTag.title = (pal.label || row.provider)
      ? `${row.profileName} · ${pal.label || row.provider}`
      : "Daemon profile";
    meta.appendChild(hostTag);
    const folder = String(row.session.cwd || "").replace(/\/+$/, "").split("/").pop();
    if (folder) {
      const f = el("span", "row-folder", folder);
      f.title = row.session.cwd;
      meta.appendChild(f);
    }
    if (row.session.git_branch) {
      const br = el("span", "row-branch", "⑂ " + row.session.git_branch);
      br.title = "Branch " + row.session.git_branch;
      meta.appendChild(br);
    }
    // The daemon's word for it, so the list, Focus and every other client agree.
    if (blocked.has(key)) meta.appendChild(el("span", "tag waiting", FOCUS_LABELS.needs_answer));
    // Focus state tag. Same row layout as any other session — one more chip.
    // Focus mode only: in All mode it is noise on rows the human never
    // enrolled, and it duplicates the working dot. Suppressed when the row
    // already says "waiting for you", the same fact in stronger words.
    const bstate = liveFocusState(row.session, key, working, blocked);
    if (focusMode() && bstate && FOCUS_LABELS[bstate] && !blocked.has(key)) {
      const cls = `tag focus focus-${bstate.replace(/_/g, "-")}`;
      const unread = bstate === "turn_finished"
        && !row.session.focus_seen_local
        && (row.session.focus_unread !== false
            || row.session.focus_state === "working");
      const tag = el("span", unread ? `${cls} focus-unread` : cls,
                     FOCUS_LABELS[bstate]);
      tag.title = (FOCUS_HINTS[bstate] || "")
        + (unread ? " — you have not opened it since" : "");
      meta.appendChild(tag);
    }
    if (row.session.id) {
      // Enough of the id to tell two sessions apart; the tooltip has all of
      // it and a click copies all of it, without opening the session.
      const idTag = el("button", "tag session-id-tag", String(row.session.id).slice(0, 8));
      idTag.setAttribute("aria-label", "Copy session id " + row.session.id);
      idTag.type = "button";
      idTag.title = `Copy session id\n${row.session.id}`;
      idTag.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        copySessionId(btn._row.session.id);
      });
      meta.appendChild(idTag);
    }
    btn.appendChild(meta);

    btn.addEventListener("contextmenu", (e) => {
      e.preventDefault();
      if (btn._row.session.id) copySessionId(btn._row.session.id);
    });

    const preview = listPreview(row.session, state.query);
    if (preview) {
      const box = el("div", "row-preview");
      // One line of what was last said, as prose: the markdown marks of a
      // reply ("**Progress:**") are noise at this size.
      highlightInto(box, String(preview).replace(/\*\*|__|`/g, "")
        .replace(/\s+/g, " ").trim(), state.query);
      btn.appendChild(box);
    }

    btn.addEventListener("click", () => openSession(btn._row));
    let nodeKey = "row:" + key;
    while (used.has(nodeKey)) nodeKey += "+";
    used.add(nodeKey);
    want.push([nodeKey, btn]);
  });
  reconcileList(host, want);
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

// ------------------------------------------------- panes (compact width)
//
// At phone width the list and the chat take turns. Opening a session shows
// the chat and adds a history entry, so the browser's Back (and the Android
// back gesture) returns to the list instead of leaving the page.

const compactQuery = window.matchMedia("(max-width: 720px)");
const isCompact = () => compactQuery.matches;
let chatHistoryPushed = false;

function showChatPane(on) {
  const app = $("app");
  const was = app.classList.contains("show-chat");
  app.classList.toggle("show-chat", !!on);
  if (on && !was && isCompact() && !chatHistoryPushed) {
    try {
      history.pushState({ pane: "chat" }, "");
      chatHistoryPushed = true;
    } catch { /* file:// or a sandboxed frame: the Back button still works */ }
  }
}

function backToList() {
  if (chatHistoryPushed) history.back();   // popstate does the rest
  else showChatPane(false);
}

window.addEventListener("popstate", () => {
  chatHistoryPushed = false;
  $("app").classList.remove("show-chat");
});

// ---------------------------------------------------------- follow scroll
//
// New output follows the bottom only while the reader is already there.
// Someone reading earlier messages keeps their place and gets a pill that
// counts what arrived; it takes them down when they choose.

const NEAR_BOTTOM_PX = 120;
let unseenLive = 0;

function nearBottom(view) {
  return view.scrollHeight - view.scrollTop - view.clientHeight <= NEAR_BOTTOM_PX;
}

function syncJumpPill() {
  const pill = $("jump-latest");
  if (!pill) return;
  const view = $("transcript");
  const show = !!state.open && !nearBottom(view)
    && view.scrollHeight > view.clientHeight + NEAR_BOTTOM_PX;
  pill.classList.toggle("hidden", !show);
  if (!show) { unseenLive = 0; return; }
  pill.textContent = unseenLive > 0
    ? `↓ ${unseenLive} new`
    : "↓ Jump to latest";
  // Keep it just above the composer, whatever height that currently is.
  // Keep it just above whatever is docked under the transcript.
  const h = ["composer", "gate"].reduce((sum, id) => {
    const n = $(id);
    return sum + (n && !n.classList.contains("hidden") ? n.offsetHeight : 0);
  }, 0);
  pill.style.setProperty("--jump-bottom", `${h + 14}px`);
}

function scrollToLatest(smooth) {
  const view = $("transcript");
  unseenLive = 0;
  state.stick = true;
  state.gestureAt = 0;
  // Animate a short hop; a long transcript would take seconds to glide.
  const far = view.scrollHeight - view.scrollTop > view.clientHeight * 3;
  if (smooth && !far && view.scrollTo) {
    view.scrollTo({ top: view.scrollHeight, behavior: "smooth" });
  } else {
    view.scrollTop = view.scrollHeight;
  }
  syncJumpPill();
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
  showChatPane(true);
  clearGate();
  unseenLive = 0;
  // Bump openGen BEFORE any await so an in-flight loadTail/pollJob from the
  // previous session can never paint into this transcript (two running jobs
  // used to cross-wire session A text into session B).
  const gen = ++state.openGen;
  const sessionId = row.session.id;
  state.open = {
    profileId: row.profileId,
    sessionId,
    session: row.session,
    profileName: row.profileName,
  };
  state.items = [];
  state.total = 0;
  state.earliest = 0;
  state.job = null;
  state.askedQuestion = null;
  state.askedPermission = null;
  // A session always opens at its newest row, whatever the last one did.
  state.stick = true;
  state.lastTop = 0;
  state.gestureAt = 0;
  applyAccent(row.provider);

  $("chat-head").classList.remove("hidden");
  syncShareButton();
  $("composer").classList.remove("hidden");
  closePop();
  syncComposer();
  syncChangesButton();
  if (side.open) { side.data = null; side.out = ""; side.form = false; loadChanges(); }
  $("chat-title").textContent = row.session.title || "Session";
  renderChatSub();
  updateLiveTuiButton();
  renderSessions();
  // Opening it is the only honest signal the output was read — dims the tag.
  markSeen(profileById(row.profileId), sessionId);

  $("transcript").innerHTML = "";
  const loading = el("div", "empty");
  loading.appendChild(el("span", "spinner"));
  $("transcript").appendChild(loading);

  await loadTail(gen);
  if (!isOpenStill(gen, row.profileId, sessionId)) return;
  // Adopt whatever is already running for this session (started here, from
  // the desktop TUI, or on another device). Prefer a job blocked on the human
  // so Answer/Respond can reappear after a dismissed modal.
  const frames = state.active[row.profileId] || [];
  const match = (j) => j.session_id === sessionId || j.new_session_id === sessionId
    || sessionIsJobPlaceholder(sessionId, j.job_id || j.id)
    || (row.session && row.session.job_id
      && (j.job_id === row.session.job_id || j.id === row.session.job_id));
  const blocked = frames.find((j) => match(j) && !isSyntheticJobId(j.job_id)
    && (j.pending_question || j.pending_permission));
  const running = frames.find((j) => match(j) && !isSyntheticJobId(j.job_id));
  if (blocked || running) {
    attachJob((blocked || running).job_id, sessionId, { adopted: true });
  } else {
    // SSE only lists in-flight turns. A finished job can still hold an
    // unanswered AskUserQuestion (Stop + panel race) — recover via /api/jobs.
    recoverPendingGate(row.profileId, sessionId, gen);
  }
  updateLiveTuiButton();
}

/**
 * Open-session recovery: find a recent job for this session that still has a
 * pending question/permission and attach it so the Answer banner appears.
 */
async function recoverPendingGate(profileId, sessionId, gen) {
  const profile = profileById(profileId);
  if (!profile || !sessionId) return;
  try {
    const data = await call(profile, "/api/jobs");
    const jobs = (data && data.jobs) || [];
    const candidates = jobs.filter((j) =>
      j && (j.session_id === sessionId || j.new_session_id === sessionId)
      && !isSyntheticJobId(j.id));
    // Prefer explicit pending flags (daemon ≥ 2.6.4), then newest first.
    candidates.sort((a, b) => (b.started_at || 0) - (a.started_at || 0));
    let hit = candidates.find((j) => j.pending_question || j.pending_permission);
    if (!hit) {
      // Older daemons omit the flags — probe the newest few snapshots.
      for (const j of candidates.slice(0, 4)) {
        if (!isOpenStill(gen, profileId, sessionId)) return;
        try {
          const snap = await call(profile, `/api/jobs/${j.id}?since=0`);
          if (snap && (snap.pending_question || snap.pending_permission)) {
            hit = j;
            break;
          }
        } catch { /* try next */ }
      }
    }
    if (!hit) return;
    if (!isOpenStill(gen, profileId, sessionId)) return;
    attachJob(hit.id, sessionId, { adopted: true });
  } catch {
    // Best-effort — banner stays empty if the daemon is unreachable.
  }
}

/**
 * Take one row out of Focus.
 *
 * The gesture lives on the row rather than the chat header: you decide a
 * project is finished while scanning the list, not after opening it, and a
 * header button could only ever act on the one session already open.
 *
 * There is no inverse here — a session rejoins Focus by being worked on, which
 * the daemon notices for itself.
 */
async function setRowFocus(row, member) {
  const profile = profileById(row.profileId);
  if (!focusCapable(profile) || member) return;
  const key = encodeURIComponent(row.session.id);
  try {
    const res = await focusCall(profile, `/api/focus/${key}/done`);
    const now = !!res.focus;
    row.session = { ...row.session, focus: now };
    if (state.open && state.open.sessionId === row.session.id) {
      state.open.session = { ...state.open.session, focus: now };
    }
    refreshSessions();
  } catch (e) {
    toast(`Focus: ${e.message}`);
  }
}

function syncShareButton() {
  const btn = $("btn-share");
  if (!btn) return;
  const open = state.open;
  const profile = open && profileById(open.profileId);
  btn.classList.toggle("hidden", !shareCapable(profile));
}

// ----------------------------------------------------------------- changes
//
// What the agent changed, before you answer it: the session folder's working
// tree against its last commit. Read-only on the daemon; commit and push are
// explicit actions here, run through the same /api/shell as a "!command".

const side = { open: false, loading: false, data: null, error: "", gen: 0, out: "", form: false, busy: false };

function syncChangesButton() {
  const btn = $("btn-changes");
  if (!btn) return;
  const profile = state.open && profileById(state.open.profileId);
  const can = !!(profile && profile.changes && state.open.session
    && state.open.session.cwd);
  btn.classList.toggle("hidden", !can);
  btn.setAttribute("aria-pressed", String(side.open));
  if (!can && side.open) closeSide();
}

function openSide() {
  if (!state.open) return;
  side.open = true;
  side.out = "";
  side.form = false;
  $("side").classList.remove("hidden");
  $("app").classList.add("with-side");
  $("btn-changes").setAttribute("aria-pressed", "true");
  loadChanges();
  // On a narrow window the pane covers everything: put focus inside it.
  if (window.matchMedia("(max-width: 1100px)").matches) $("side-back").focus();
}

function closeSide() {
  const hadFocus = $("side").contains(document.activeElement);
  side.open = false;
  side.gen += 1;
  $("side").classList.add("hidden");
  $("app").classList.remove("with-side");
  $("btn-changes").setAttribute("aria-pressed", "false");
  if (hadFocus) $("btn-changes").focus();
}

async function loadChanges() {
  if (!side.open || !state.open) return;
  const profile = profileById(state.open.profileId);
  const sid = state.open.sessionId;
  const gen = ++side.gen;
  side.loading = true;
  side.error = "";
  renderSide();
  try {
    const data = await call(profile,
      `/api/sessions/${encodeURIComponent(sid)}/changes`, { timeout: 30000 });
    if (gen !== side.gen) return;
    side.data = data;
  } catch (e) {
    if (gen !== side.gen) return;
    side.error = e.message || "Could not read the changes";
  }
  side.loading = false;
  renderSide();
}

/** Split one unified diff into per-file sections. */
function splitDiff(text) {
  const out = [];
  let cur = null;
  String(text || "").split("\n").forEach((line) => {
    if (line.startsWith("diff --git ")) {
      const m = /^diff --git a\/(.*) b\/(.*)$/.exec(line);
      cur = { path: m ? m[2] : line.slice(11), lines: [] };
      out.push(cur);
      return;
    }
    if (cur) cur.lines.push(line);
  });
  return out;
}

/**
 * What kind of line this is. `inHunk` is false for a file's header, the only
 * place "--- a/x" and "+++ b/x" are names: after the first "@@", a line that
 * starts "---" is a removed line whose text starts "--" (a SQL comment, a
 * Markdown rule), and it must be shown as removed.
 */
function diffLineClass(line, inHunk) {
  if (line.startsWith("@@")) return "hunk";
  if (!inHunk) return "meta";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  if (line.startsWith(" ")) return "";
  return "meta";
}

function renderSide() {
  const body = $("side-body");
  const sub = $("side-sub");
  const foot = $("side-foot");
  const keepScroll = body.scrollTop;
  body.textContent = "";
  foot.textContent = "";
  foot.classList.add("hidden");
  const d = side.data;
  if (side.out) body.appendChild(el("pre", "side-out", side.out));
  if (side.loading && !d) {
    sub.textContent = "";
    const note = el("div", "side-note");
    note.appendChild(el("span", "spinner"));
    note.appendChild(document.createTextNode(" Reading the working tree…"));
    body.appendChild(note);
    return;
  }
  if (side.error) {
    sub.textContent = "";
    body.appendChild(el("div", "side-note err", side.error));
    return;
  }
  if (!d) return;
  if (!d.repo) {
    sub.textContent = d.cwd || "";
    body.appendChild(el("div", "side-note",
      "This session's folder is not a git repository, so there is nothing to compare with."));
    return;
  }
  const bits = [d.branch || "detached"];
  if (d.ahead) bits.push(`↑${d.ahead}`);
  if (d.behind) bits.push(`↓${d.behind}`);
  const files = d.files || [];
  bits.push(files.length
    ? `${files.length} ${files.length === 1 ? "file" : "files"} · +${d.added} −${d.removed}`
    : "no changes");
  sub.textContent = bits.join(" · ") + (side.loading ? " · refreshing…" : "");
  sub.title = d.root || "";

  if (!files.length) {
    body.appendChild(el("div", "side-note",
      "Nothing differs from the last commit."
      + (d.ahead ? ` ${d.ahead} ${d.ahead === 1 ? "commit is" : "commits are"} not pushed yet.` : "")));
  } else {
    const sections = splitDiff(d.diff);
    const anchors = new Map();
    const list = el("ul", "chg-files");
    files.forEach((f) => {
      const li = el("li");
      const row = el("button", "chg-file");
      row.type = "button";
      row.appendChild(el("span", "chg-path", f.from ? `${f.from} → ${f.path}` : f.path));
      row.appendChild(el("span", "chg-kind k-" + String(f.status).replace(/\s+/g, "-"), f.status));
      const count = el("span", "chg-count");
      if (f.binary) count.textContent = "binary";
      else if (f.added || f.removed) {
        count.appendChild(el("span", "chg-add", "+" + f.added));
        count.appendChild(document.createTextNode(" "));
        count.appendChild(el("span", "chg-del", "−" + f.removed));
      }
      row.appendChild(count);
      row.addEventListener("click", () => {
        const target = anchors.get(f.path);
        if (target) target.scrollIntoView({ block: "start" });
      });
      // No section to jump to: binary, too large, or past the diff's cap.
      row.disabled = !sections.some((sec) => sec.path === f.path);
      li.appendChild(row);
      list.appendChild(li);
    });
    body.appendChild(list);
    if (d.more_files) {
      body.appendChild(el("div", "side-note", `…and ${d.more_files} more files not listed.`));
    }
    sections.forEach((sec) => {
      const box = el("section", "diff-file");
      box.appendChild(el("div", "diff-name", sec.path));
      const pre = el("pre", "diff-lines");
      let inHunk = false;
      sec.lines.forEach((line) => {
        if (line.startsWith("@@")) inHunk = true;
        if (!inHunk && /^(index |similarity index |new file mode|deleted file mode|old mode|new mode)/.test(line)) return;
        const cls = diffLineClass(line, inHunk);
        pre.appendChild(el("span", "diff-line" + (cls ? " " + cls : ""), line || " "));
      });
      box.appendChild(pre);
      anchors.set(sec.path, box);
      body.appendChild(box);
    });
    if (d.truncated) {
      body.appendChild(el("div", "side-note",
        "The diff is too long to show in full. The file list above is complete."));
    }
  }
  body.scrollTop = keepScroll;

  // Git actions. Each one says exactly what it will run.
  if (!files.length && !d.ahead && !d.can_publish) return;
  foot.classList.remove("hidden");
  if (side.form) {
    const msg = el("textarea");
    msg.id = "side-commit-msg";
    msg.placeholder = "Commit message";
    msg.setAttribute("aria-label", "Commit message");
    foot.appendChild(msg);
    foot.appendChild(el("span", "help",
      `Stages and commits all ${files.length} changed ${files.length === 1 ? "file" : "files"} on ${d.branch || "this branch"}.`));
    const cancel = el("button", null, "Cancel");
    cancel.type = "button";
    cancel.addEventListener("click", () => { side.form = false; renderSide(); });
    const go = el("button", "primary", "Commit");
    go.type = "button";
    go.disabled = true;
    msg.addEventListener("input", () => { go.disabled = !msg.value.trim() || side.busy; });
    go.addEventListener("click", () => {
      const text = msg.value.trim();
      if (!text) return;
      // Single-quoted for the shell: the message is data, never syntax.
      runGit("git add -A && git commit -m '" + text.replace(/'/g, "'\\''") + "'");
    });
    foot.appendChild(cancel);
    foot.appendChild(go);
    msg.focus();
    return;
  }
  if (files.length) {
    const commit = el("button", null, "Commit…");
    commit.type = "button";
    commit.disabled = side.busy;
    commit.addEventListener("click", () => { side.form = true; renderSide(); });
    foot.appendChild(commit);
  }
  // Push what is ahead of the upstream; or, for a branch that has none yet
  // and somewhere to go (the daemon says so: not a detached HEAD, not a
  // repository without an origin), publish it and track it.
  if ((d.upstream && d.ahead) || d.can_publish) {
    const cmd = d.upstream ? "git push" : "git push -u origin HEAD";
    const push = el("button", null, d.upstream ? `Push ${d.ahead}` : "Publish branch");
    push.type = "button";
    push.disabled = side.busy;
    push.title = cmd;
    push.addEventListener("click", () => {
      const where = d.upstream ? ` to ${d.upstream}` : " to origin";
      if (!window.confirm(`Push ${d.branch || "this branch"}${where}? Others will see these commits.`)) return;
      runGit(cmd);
    });
    foot.appendChild(push);
  }
  foot.appendChild(el("span", "help", side.busy ? "Running…" : ""));
}

/** Run one git command in the session's folder and show what it printed. */
async function runGit(command) {
  if (!state.open || side.busy) return;
  const profile = profileById(state.open.profileId);
  const gen = side.gen;
  side.busy = true;
  side.form = false;
  renderSide();
  let out = "";
  try {
    const res = await call(profile, "/api/shell", {
      method: "POST",
      timeout: 40000,
      body: { command, session_id: state.open.sessionId,
              cwd: (side.data && side.data.root) || state.open.session.cwd || "" },
    });
    out = `$ ${command.split(" -m ")[0]}\n` + ((res.output || "").trim() || "(no output)")
      + (res.exit_code ? `\n(exit code ${res.exit_code})` : "");
  } catch (e) {
    out = `$ ${command.split(" -m ")[0]}\n${e.message || "failed"}`;
  }
  side.busy = false;
  // Another session was opened, or the pane closed, while this ran: its
  // output belongs to neither.
  if (gen !== side.gen || !side.open) return;
  side.out = out;
  loadChanges();
}

/**
 * Mint a 7-day read-only URL hosted by this session's daemon.
 * The link is the daemon origin + /share/<token> — no daemon auth token.
 */
async function openShare() {
  const open = state.open;
  if (!open) return;
  const profile = profileById(open.profileId);
  if (!shareCapable(profile)) {
    toast("This daemon is too old to share sessions");
    return;
  }
  const key = encodeURIComponent(open.sessionId);
  let url = "";
  let expiresIn = 0;
  let status = "Creating a read-only link…";
  const paint = (body) => {
    body.textContent = "";
    body.appendChild(el("div", "help",
      "Anyone with this link can read the transcript for 7 days. "
      + "They cannot send messages, list other sessions, or use your daemon token."));
    if (url) {
      const box = el("div", "share-url", url);
      box.title = url;
      body.appendChild(box);
      const note = el("div", "help",
        expiresIn
          ? `Expires in ${Math.max(1, Math.round(expiresIn / 86400))} day`
            + (Math.round(expiresIn / 86400) === 1 ? "" : "s") + "."
          : "Expires in 7 days.");
      body.appendChild(note);
    } else {
      body.appendChild(el("p", null, status));
    }
  };
  const sheet = modal({
    title: "Share session",
    build: paint,
    actions: [
      { label: "Close", close: true },
      {
        label: "Copy link",
        primary: true,
        close: false,
        disabled: true,
        id: "share-copy",
        run: async () => {
          if (!url) return;
          try {
            await copyText(url);
            toast("Share link copied");
          } catch {
            toast("Could not copy the link");
          }
        },
      },
    ],
  });
  try {
    const data = await call(profile, `/api/sessions/${key}/share`, {
      method: "POST",
      body: {},
    });
    const path = data.path || (data.token ? "/share/" + data.token : "");
    const origin = String(profile.baseUrl || "").replace(/\/+$/, "");
    url = (origin && path) ? origin + path : (data.url || "");
    expiresIn = Number(data.expires_in) || 0;
    status = "";
    paint(sheet.body);
    const copyBtn = $("share-copy");
    if (copyBtn) copyBtn.disabled = !url;
  } catch (e) {
    status = e.message || "Could not create a share link";
    paint(sheet.body);
  }
}

/**
 * Rename the open session: type a title, or ask the daemon to derive one from
 * the transcript. Default names are often unrecognisable across a dozen
 * parallel projects, which is the whole reason this exists.
 */
function openRename() {
  const open = state.open;
  if (!open) return;
  const profile = profileById(open.profileId);
  if (!focusCapable(profile)) {
    toast("This daemon is too old to rename sessions");
    return;
  }
  const key = encodeURIComponent(open.sessionId);
  let input;
  let note;

  const apply = (title) => {
    open.session = { ...open.session, title };
    $("chat-title").textContent = title || "Session";
    // Keep the list row in step without a full refetch.
    const row = state.rows.find((r) => r.session && r.session.id === open.sessionId);
    if (row) row.session = { ...row.session, title };
    renderSessions();
  };

  const { foot } = modal({
    title: "Rename session",
    build(body) {
      const f = el("div", "field");
      f.appendChild(el("label", null, "Title"));
      input = el("input");
      input.type = "text";
      input.value = String((open.session && open.session.title) || "");
      input.placeholder = "e.g. BB10 pager chime";
      input.maxLength = 120;
      f.appendChild(input);
      note = el("div", "help",
        "Leave it empty to go back to the name the agent derived.");
      f.appendChild(note);
      body.appendChild(f);
      setTimeout(() => { input.focus(); input.select(); }, 0);
    },
    actions: [
      {
        label: "Regenerate",
        close: false,
        async run() {
          note.textContent = "Asking the model for a title…";
          try {
            const res = await call(profile,
              `/api/sessions/${key}/title/regenerate`,
              { method: "POST", body: {}, timeout: 45000 });
            input.value = res.title || "";
            note.textContent = "Generated from the transcript. Save to keep it.";
          } catch (e) {
            note.textContent = e.message || "Could not generate a title";
          }
        },
      },
      {
        label: "Save",
        primary: true,
        async run() {
          try {
            const res = await call(profile, `/api/sessions/${key}/title`,
              { method: "POST", body: { title: input.value } });
            apply(res.title || "");
            if (!res.title) refreshSessions(); // provider title comes back
          } catch (e) {
            toast(`Rename: ${e.message}`);
          }
        },
      },
      { label: "Cancel" },
    ],
  });
  // Enter saves, so a rename is two keystrokes from the header button.
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      const save = [...foot.querySelectorAll("button")]
        .find((b) => b.textContent === "Save");
      if (save) save.click();
    }
  });
}

/** True when this transcript generation still owns the open pane. */
function isOpenStill(gen, profileId, sessionId) {
  return gen === state.openGen
    && state.open
    && state.open.profileId === profileId
    && state.open.sessionId === sessionId;
}

/** Synthetic status rows for busy host TUIs (no /api/jobs/<id> to poll). */
function isSyntheticJobId(id) {
  return !id || String(id).startsWith("tui-");
}

/** New-session list id before the harness reports a uuid (`job:<jobid>`). */
function jobPlaceholder(jobId) {
  const id = String(jobId || "").trim();
  if (!id) return "";
  return id.startsWith("job:") ? id : `job:${id}`;
}

function isJobPlaceholder(sessionId) {
  return String(sessionId || "").startsWith("job:");
}

function sessionIsJobPlaceholder(sessionId, jobId) {
  const sid = String(sessionId || "");
  const key = jobPlaceholder(jobId);
  return !!(sid && key && (sid === key || sid === String(jobId || "")));
}

/**
 * Is state.job safe to use for the currently open transcript?
 * Mid-turn send used to POST /api/jobs/<id>/input without this check — so a
 * job left attached from session B received prompts typed while viewing A.
 */
function jobBelongsToOpen(job = state.job) {
  if (!job || !job.id || isSyntheticJobId(job.id) || !state.open) return false;
  if (job.profileId && job.profileId !== state.open.profileId) return false;
  if (typeof job.openGen === "number" && job.openGen !== state.openGen) return false;
  const openSid = state.open.sessionId || "";
  if (sessionIsJobPlaceholder(openSid, job.id)
      && sessionIsJobPlaceholder(job.sessionId, job.id)) return true;
  // Require an explicit pin: never guess from a stale attachment.
  if (!job.sessionId || job.sessionId !== openSid) return false;
  return true;
}

/** Drop a job watch that no longer matches the open session (wrong pin). */
function dropJobIfNotForOpen() {
  if (state.job && !jobBelongsToOpen(state.job)) stopJobWatch();
}

// ---------------------------------------------------------------- live TUI

function profileSupportsLiveTui(profile, harness) {
  if (!profile) return false;
  return !!capOf(profile, "live_tui", false, harness)
    || !!capOf(profile, "interactive", false, harness);
}

function setIconIdle(btn, idle) {
  if (!btn) return;
  btn.classList.toggle("icon-idle", !!idle);
  btn.setAttribute("aria-hidden", idle ? "true" : "false");
  btn.tabIndex = idle ? -1 : 0;
}

function updateLiveTuiButton() {
  const btn = $("btn-live-tui");
  if (!btn) return;
  const open = state.open;
  if (!open || !open.sessionId) {
    setIconIdle(btn, true);
    return;
  }
  const profile = profileById(open.profileId);
  const harness = sessionProvider(open.session, profile);
  if (!profileSupportsLiveTui(profile, harness)) {
    setIconIdle(btn, true);
    return;
  }
  // Always keep the slot; only the idle flag changes — never display:none.
  setIconIdle(btn, false);
  btn.setAttribute("aria-pressed", state.liveTui ? "true" : "false");
  btn.title = state.liveTui
    ? "Back to transcript"
    : "Live TUI — host terminal for this session";
}

function openLiveTui() {
  if (!state.open || !state.open.sessionId) return;
  state.liveTui = true;
  state.liveTuiSeq = 0;
  state.liveTuiCols = 0;
  $("live-tui").classList.remove("hidden");
  $("transcript").classList.add("hidden");
  $("composer").classList.add("hidden");
  updateLiveTuiButton();
  const screen = $("live-tui-screen");
  screen.textContent = "Connecting to host TUI…";
  $("live-tui-pane").classList.add("empty-tui");
  $("live-tui-pane").style.fontSize = "";
  $("live-tui-status").textContent = "Host TUI";
  $("live-tui-status").classList.remove("live");
  // Capture from the first frame: the pane is the whole point of this view,
  // and one click on the text field below hands the keyboard (and the IME)
  // straight back.
  focusLiveTuiPane();
  updateLiveTuiCapture();
  startLiveTuiFeed();
}

/**
 * Where frames come from. A daemon ≥ 2.22 pushes one the moment the pane
 * changes (GET /sse/sessions/<id>/tui), so an echo is one hop behind the
 * key. Older daemons are polled every 400 ms, as before.
 */
function startLiveTuiFeed() {
  stopLiveTuiFeed();
  const open = state.open;
  const profile = open && profileById(open.profileId);
  if (!profile) return;
  if (!profile.tuiStream || typeof EventSource === "undefined") {
    startLiveTuiPolling();
    return;
  }
  const gen = state.openGen;
  const { profileId, sessionId } = open;
  // EventSource cannot send headers; the daemon takes the token in the query
  // on its stream endpoints (same as /sse/status).
  const src = new EventSource(profile.baseUrl.replace(/\/+$/, "")
    + `/sse/sessions/${encodeURIComponent(sessionId)}/tui?ansi=1&token=`
    + encodeURIComponent(profile.token));
  state.liveTuiStream = src;
  let first = true;
  src.onmessage = (ev) => {
    if (state.liveTuiStream !== src) return;
    if (!state.liveTui || !isOpenStill(gen, profileId, sessionId)) { src.close(); return; }
    let frame = null;
    try { frame = JSON.parse(ev.data); } catch { return; }
    paintLiveTuiFrame(frame, first);
    first = false;
  };
  src.onerror = () => {
    if (state.liveTuiStream !== src) return;
    if (src.readyState === EventSource.CLOSED) {
      // Refused outright (an old proxy, a daemon restarting under us): poll
      // for now, and ask for the stream again shortly. Polling used to be
      // where this ended, for as long as the pane stayed open.
      state.liveTuiStream = null;
      startLiveTuiPolling();
      state.liveTuiRetry = setTimeout(() => {
        if (state.liveTui && isOpenStill(gen, profileId, sessionId)) startLiveTuiFeed();
      }, LIVE_TUI_STREAM_RETRY_MS);
      return;
    }
    $("live-tui-status").textContent = "Host TUI · reconnecting…";
    $("live-tui-status").classList.remove("live");
    first = true;   // repaint whatever comes back, even if it looks the same
  };
}

const LIVE_TUI_STREAM_RETRY_MS = 10000;

function startLiveTuiPolling() {
  clearInterval(state.liveTuiTimer);
  pollLiveTui(true);
  state.liveTuiTimer = setInterval(() => pollLiveTui(false), 400);
}

function stopLiveTuiFeed() {
  clearInterval(state.liveTuiTimer);
  state.liveTuiTimer = null;
  clearTimeout(state.liveTuiRetry);
  state.liveTuiRetry = null;
  if (state.liveTuiStream) state.liveTuiStream.close();
  state.liveTuiStream = null;
}

function closeLiveTui() {
  flushLiveTuiText();
  state.liveTui = false;
  state.liveTuiKeys = false;
  stopLiveTuiFeed();
  const box = $("live-tui");
  if (box) box.classList.add("hidden");
  const tr = $("transcript");
  if (tr) tr.classList.remove("hidden");
  if (state.open) {
    $("composer")?.classList.remove("hidden");
  }
  updateLiveTuiCapture();
  updateLiveTuiButton();
}

async function pollLiveTui(force) {
  if (!state.liveTui || !state.open) return;
  const gen = state.openGen;
  const profileId = state.open.profileId;
  const sessionId = state.open.sessionId;
  const profile = profileById(profileId);
  if (!profile) return;
  // One request at a time. The timer used to send another every 400 ms
  // whether or not the last had come back, so a daemon that was slow to
  // answer was sent more work the slower it got.
  if (state.liveTuiPolling) {
    if (force) state.liveTuiPollAgain = true;
    return;
  }
  state.liveTuiPolling = true;
  try {
    // Colour clients opt in; default daemon payload is plain for BB.
    const frame = await call(
      profile,
      `/api/sessions/${encodeURIComponent(sessionId)}/tui?ansi=1`,
    );
    // Drop the frame if the user switched sessions (or left Live TUI).
    if (!state.liveTui || !isOpenStill(gen, profileId, sessionId)) return;
    paintLiveTuiFrame(frame, force);
  } catch (e) {
    if (!state.liveTui || !isOpenStill(gen, profileId, sessionId)) return;
    $("live-tui-status").textContent = e.message || "Live TUI error";
    $("live-tui-status").classList.remove("live");
  } finally {
    state.liveTuiPolling = false;
    if (state.liveTuiPollAgain) {
      // A repaint was asked for (a key was sent, another session was opened)
      // while this one was out.
      state.liveTuiPollAgain = false;
      pollLiveTui(true);
    }
  }
}

/** Draw one frame, however it arrived (pushed or polled). */
function paintLiveTuiFrame(frame, force) {
  {
    const status = $("live-tui-status");
    const pane = $("live-tui-pane");
    const screen = $("live-tui-screen");
    if (!frame || frame.attached === false) {
      status.textContent = frame?.error || "No host TUI attached";
      status.classList.remove("live");
      state.liveTuiSeq = 0;
      if (force || !pane.dataset.hadFrame) {
        screen.textContent = frame?.error
          || "No interactive TUI for this session. Start a turn in Interactive mode.";
        pane.classList.add("empty-tui");
        pane.style.fontSize = "";
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
      screen.innerHTML = ansiToHtml(raw);
    } else {
      screen.textContent = raw;
    }
    // Host pane width: daemon >= 2.13.0 reports it. Older ones get measured,
    // and that measurement only ever grows — sizing to the widest line of
    // each frame would re-scale the whole pane every time a line was drawn.
    state.liveTuiCols = frame.cols > 0
      ? frame.cols
      : Math.max(state.liveTuiCols, widestLine(raw));
    fitLiveTuiPane();
    if (atBottom) pane.scrollTop = pane.scrollHeight;
    status.textContent = frame.job_id
      ? `Host TUI · job ${String(frame.job_id).slice(0, 8)}`
      : "Host TUI · live";
    status.classList.add("live");
  }
}

/** Longest visible line of a frame — the pane width an old daemon never sent. */
function widestLine(raw) {
  let wide = 0;
  const plain = String(raw || "").replace(/\u001b\[[0-9;:<=>?]*[ -/]*[@-~]/g, "");
  for (const line of plain.split("\n")) {
    if (line.length > wide) wide = line.length;
  }
  return Math.min(Math.max(wide, 40), 220);
}

// A TUI is a grid, so the pane never reflows it — it scales the glyphs until
// the host's own column count fits the window (down to a floor, after which
// the pane scrolls). Wrapping 120 columns into a narrow pane turned every box
// border into confetti, which is what "too wide" actually looked like.
const TUI_FONT_MIN = 7.5;
const TUI_FONT_MAX = 13;
let _tuiCharRatio = 0;

/** Width of one monospace glyph, as a fraction of the font size. */
function tuiCharRatio(pane) {
  if (_tuiCharRatio) return _tuiCharRatio;
  try {
    const cs = getComputedStyle(pane);
    const ctx = document.createElement("canvas").getContext("2d");
    ctx.font = `${cs.fontWeight} 100px ${cs.fontFamily}`;
    const w = ctx.measureText("0".repeat(64)).width / 64 / 100;
    if (w > 0.2 && w < 1) _tuiCharRatio = w;
  } catch (_e) { /* canvas blocked — fall through to the usual mono ratio */ }
  if (!_tuiCharRatio) _tuiCharRatio = 0.6;
  return _tuiCharRatio;
}

function fitLiveTuiPane() {
  const pane = $("live-tui-pane");
  const cols = state.liveTuiCols;
  if (!pane || !cols) return;
  const cs = getComputedStyle(pane);
  const padX = (parseFloat(cs.paddingLeft) || 0) + (parseFloat(cs.paddingRight) || 0);
  const avail = pane.clientWidth - padX;
  if (avail <= 0) return;              // pane still hidden
  const want = avail / (cols * tuiCharRatio(pane));
  const size = Math.max(TUI_FONT_MIN, Math.min(TUI_FONT_MAX, Math.floor(want * 10) / 10));
  pane.style.fontSize = size + "px";
}

// Keystrokes are a stream, so they are sent as one, in order, and never two
// requests at a time: fired off in parallel they can reach a threaded daemon
// out of order, and "cd " arriving as "c d" is not a typo the user made.
//
// What is pressed while a request is in flight is gathered and goes out as
// ONE request when it returns (`events`, agentremoted ≥ 2.22). Typing is then
// never more than one round trip behind, however slow the link; before, each
// key queued its own round trip and a fast typist outran a tunnel.
const _tuiQueue = [];      // { text } | { keys } | { line: { text, keys } }
let _tuiSending = false;

function sendLiveTuiKeys(keys, text) {
  if (text && keys && keys.length) {
    // A whole line from the text field: text, a pause, then Enter. The
    // pause is the daemon's (some harnesses read a fast CR as pasted text).
    _tuiQueue.push({ line: { text, keys } });
  } else if (text) {
    const last = _tuiQueue[_tuiQueue.length - 1];
    if (last && last.text !== undefined) last.text += text;
    else _tuiQueue.push({ text });
  } else if (keys && keys.length) {
    _tuiQueue.push({ keys: keys.slice() });
  }
  pumpLiveTuiKeys();
}

async function pumpLiveTuiKeys() {
  if (_tuiSending) return;
  _tuiSending = true;
  try {
    while (_tuiQueue.length) {
      const open = state.open;
      const profile = open && profileById(open.profileId);
      if (!profile || !open.sessionId) { _tuiQueue.length = 0; break; }
      let body;
      if (_tuiQueue[0].line) {
        body = _tuiQueue.shift().line;
      } else if (profile.tuiStream) {
        const events = [];
        while (_tuiQueue.length && !_tuiQueue[0].line && events.length < 200) {
          events.push(_tuiQueue.shift());
        }
        body = { events };
      } else {
        body = _tuiQueue.shift();      // older daemon: one item per request
      }
      try {
        await call(
          profile,
          `/api/sessions/${encodeURIComponent(open.sessionId)}/tui/keys`,
          { method: "POST", body },
        );
        // A pushed stream is already bringing the echo; a polled one is asked.
        if (!state.liveTuiStream) setTimeout(() => pollLiveTui(true), 80);
      } catch (e) {
        _tuiQueue.length = 0;          // do not type the rest into a dead pane
        toast(e.message || "TUI input failed");
      }
    }
  } finally {
    _tuiSending = false;
  }
}

// Kept for callers that flush before a chord: typed text now goes straight
// into the queue, so there is nothing held back to flush.
function typeLiveTuiText(ch) { sendLiveTuiKeys(null, ch); }
function flushLiveTuiText() {}

// ---- keyboard capture ---------------------------------------------------
// Two keyboards share this view. The pane forwards raw keystrokes to the
// host terminal; the text field below is an ordinary input, so a system IME
// (Chinese, Japanese, …) can compose there and send a finished line. Whoever
// holds DOM focus owns the keyboard — nothing is ever forwarded twice.

/** Named keys the daemon maps to tmux tokens (modifiers compose onto these). */
const TUI_NAMED_KEYS = {
  Escape: "Escape",
  Enter: "Enter",
  Backspace: "Backspace",
  Tab: "Tab",
  Delete: "Delete",
  Insert: "Insert",
  ArrowUp: "Up",
  ArrowDown: "Down",
  ArrowLeft: "Left",
  ArrowRight: "Right",
  Home: "Home",
  End: "End",
  PageUp: "PageUp",
  PageDown: "PageDown",
};
/** Keydowns that carry no character: modifiers, locks, IME bookkeeping. */
const TUI_MODIFIER_KEYS = new Set([
  "Shift", "Control", "Alt", "Meta", "CapsLock", "NumLock", "ScrollLock",
  "AltGraph", "ContextMenu", "Dead", "Unidentified", "Process",
]);
// Chrome and Safari keep these for themselves: forwarding Ctrl+W would close
// the tab mid-turn instead of deleting a word. The soft-key row is the way in.
const TUI_BROWSER_CTRL = new Set(["n", "t", "w", "q"]);

/**
 * Turn a keydown on the pane into what the daemon should receive:
 * `{text}` for a character the user typed, `{keys:[name]}` for a chord,
 * or null to leave the event to the browser.
 */
function liveTuiKeySpec(e) {
  if (e.isComposing || e.keyCode === 229) return null;   // IME mid-composition
  const k = e.key || "";
  if (!k || TUI_MODIFIER_KEYS.has(k)) return null;
  // ⌘ stays with the browser so Cmd+C / Cmd+V / Cmd+R still work on a Mac.
  if (e.metaKey) return null;
  const mods = [];
  if (e.ctrlKey) mods.push("Ctrl");
  if (e.altKey) mods.push("Alt");

  const named = TUI_NAMED_KEYS[k] || (/^F([1-9]|1[0-2])$/.test(k) ? k : "");
  if (named) {
    // Shift is a real modifier on a named key: Shift+Tab, Shift+↑, Shift+↵.
    if (e.shiftKey) mods.push("Shift");
    return { keys: [mods.concat(named).join("+")] };
  }

  if (!mods.length) {
    // Plain typing — the browser already applied shift and the layout.
    return k.length === 1 ? { text: k } : null;
  }
  // Ctrl/Alt with a character: read the physical key, because Alt+b on macOS
  // arrives as "∫" and Ctrl+· on some layouts arrives as nothing useful.
  const base = tuiChordChar(e);
  if (!base) return null;
  if (e.ctrlKey && !e.altKey && TUI_BROWSER_CTRL.has(base.toLowerCase())) return null;
  // Ctrl+C copies when there is something selected, and interrupts otherwise —
  // the same bargain every terminal emulator in a browser makes.
  if (e.ctrlKey && !e.altKey && base.toLowerCase() === "c"
      && String(window.getSelection?.() || "").trim()) return null;
  return { keys: [mods.concat(base).join("+")] };
}

/** The character a Ctrl/Alt chord means, from the physical key where needed. */
function tuiChordChar(e) {
  const code = e.code || "";
  let m = /^Key([A-Z])$/.exec(code);
  if (m) return m[1];
  m = /^Digit([0-9])$/.exec(code);
  if (m) return m[1];
  const k = e.key || "";
  return k.length === 1 ? k : "";
}

function focusLiveTuiPane() {
  const pane = $("live-tui-pane");
  if (!pane) return;
  pane.focus({ preventScroll: true });
}

/** One chip, one truth: where the next keystroke goes. */
function updateLiveTuiCapture() {
  const on = !!state.liveTuiKeys;
  const chip = $("btn-tui-capture");
  if (chip) {
    // Two spellings: the long one, and a short one for a narrow window
    // where the chip shares a row with the soft keys.
    chip.textContent = "";
    chip.append(el("span", "cap-long", on ? "Keys: TUI" : "Keys: text field"),
                el("span", "cap-short", on ? "Keys ✓" : "Keys"));
    chip.setAttribute("aria-label", on
      ? "Keystrokes go to the host terminal"
      : "Keystrokes go to the text field");
    chip.setAttribute("aria-pressed", on ? "true" : "false");
    chip.classList.toggle("on", on);
  }
  const hint = $("live-tui-hint");
  if (hint) {
    hint.textContent = on
      ? "Shift+Esc (or the text field) takes the keyboard back"
      : "Click the pane to send keys";
  }
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
  // Profile name + harness label when the host is multi (one machine, several CLIs).
  let tagText = open.profileName || (profile && profile.name) || "Daemon";
  if (profileHarnesses(profile).length > 1 && harness)
    tagText = `${tagText} · ${pal.label}`;
  const tag = el("span", "tag provider", tagText);
  tag.style.setProperty("--tag", pal.accent);
  sub.appendChild(tag);
  // Only when it is NOT live: what is on screen may be out of date.
  const conn = connOf(open.profileId);
  if (conn === "reconnecting" || conn === "offline") {
    const lost = el("span", "tag conn-tag conn-" + conn, CONN_TEXT[conn]);
    lost.title = conn === "offline"
      ? "This daemon cannot be reached. What you see may be out of date."
      : "The live connection dropped and is being restored.";
    sub.appendChild(lost);
  }
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

async function loadTail(gen = state.openGen, { keepLive = false, follow = false } = {}) {
  const open = state.open;
  if (!open) return;
  const profileId = open.profileId;
  const sessionId = open.sessionId;
  const profile = profileById(profileId);
  if (!profile) return;
  // Mid-turn reloads: keep live echoes the journal has not caught up with
  // (Android loadTail(keepLive) parity). End-of-turn reloads leave this false
  // so disk fully replaces the stream.
  if (!keepLive && state.job && !["done", "error", "stopped"].includes(state.job.status || "")) {
    keepLive = true;
  }
  try {
    const page = await call(profile,
      `/api/sessions/${encodeURIComponent(sessionId)}/messages?limit=${PAGE}`
        + (processViewOn(sessionId) ? "&detail=steps" : ""),
      { timeout: 60000 });
    // Another session may have been opened while this request was in flight.
    if (!isOpenStill(gen, profileId, sessionId)) return;
    state.total = page.total || 0;
    state.earliest = page.offset || 0;
    const fetched = expandMessages(page.messages || [], page.offset || 0);
    if (keepLive) {
      const settled = new Set(
        fetched.map((it) => `${it.role}\0${String(it.text || "").trim()}`));
      const live = state.items.filter((it) => it.live
        && !settled.has(`${it.role}\0${String(it.text || "").trim()}`));
      state.items = fetched.concat(live);
    } else {
      state.items = fetched;
    }
    renderTranscript(follow ? "follow" : true);
  } catch (e) {
    if (!isOpenStill(gen, profileId, sessionId)) return;
    $("transcript").innerHTML = "";
    const box = el("div", "empty");
    box.appendChild(el("h2", null, "Could not load this session"));
    box.appendChild(el("p", null, e.message));
    $("transcript").appendChild(box);
  }
}

async function loadOlder(btn) {
  const open = state.open;
  const gen = state.openGen;
  if (!open) return;
  const profileId = open.profileId;
  const sessionId = open.sessionId;
  const profile = profileById(profileId);
  if (!profile || state.earliest <= 0) return;
  const from = Math.max(0, state.earliest - PAGE);
  const count = state.earliest - from;
  btn.disabled = true;
  btn.textContent = "Loading…";
  try {
    const page = await call(profile,
      `/api/sessions/${encodeURIComponent(sessionId)}/messages?offset=${from}&limit=${count}`
        + (processViewOn(sessionId) ? "&detail=steps" : ""),
      { timeout: 60000 });
    if (!isOpenStill(gen, profileId, sessionId)) return;
    state.earliest = page.offset || 0;
    // Keep the reader where they were: measure, prepend, restore.
    const view = $("transcript");
    const before = view.scrollHeight - view.scrollTop;
    state.items = expandMessages(page.messages || [], page.offset || 0).concat(state.items);
    renderTranscript(false);
    view.scrollTop = view.scrollHeight - before;
  } catch (e) {
    if (!isOpenStill(gen, profileId, sessionId)) return;
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
    const ts = m.ts || m.timestamp || m.started || "";
    if (m.role === "user" && text.startsWith("[shell] ! ") && text.includes("\n[output]\n")) {
      const command = text.split("\n[output]\n")[0].replace(/^\[shell\] /, "").trim();
      const body = text.split("\n[output]\n")[1].split("\n[silent]")[0].trim();
      out.push({ id, role: "user", text: command, ts, steps: m.steps || null });
      if (body) out.push({ id: id + ":out", role: "assistant", text: body, ts });
      return;
    }
    out.push({ id, role: m.role, text, metaKind: m.metaKind || "", ts,
               steps: m.steps || null });
  });
  return out;
}

/**
 * The transcript follows a live turn only while the reader is actually at the
 * bottom. Scrolling up stops it dead: a working row that keeps repainting
 * must never drag the page back down under a reader going the other way.
 *
 * `stick` is recomputed from the real scroll position, so a rebuild that
 * restores a position corrects it by itself; `gestureAt` covers the moment a
 * gesture starts, when the reader is still inches from the bottom and a
 * forced scrollTop would cut the flick short.
 */
function watchTranscriptScroll() {
  const view = $("transcript");
  if (!view) return;
  const gesture = () => { state.gestureAt = Date.now(); };
  ["wheel", "touchstart", "touchmove", "touchend"].forEach((ev) => {
    view.addEventListener(ev, gesture, { passive: true });
  });
  view.addEventListener("scroll", () => {
    const top = view.scrollTop;
    // Momentum keeps firing scrolls long after the finger is gone; each one
    // moving up renews the window, so the flick runs to its own stop.
    if (top < state.lastTop - 1) gesture();
    state.lastTop = top;
    state.stick = view.scrollHeight - top - view.clientHeight < 24;
  }, { passive: true });
}

const readerScrolling = () => Date.now() - state.gestureAt < 400;

/** Follow the tail of a live turn — but only when the reader is still there. */
function followBottom(view) {
  if (!state.stick || readerScrolling()) return;
  view.scrollTop = view.scrollHeight;
}

/** Jump to the bottom because the reader acted (opened, sent, refreshed). */
function jumpBottom(view) {
  state.stick = true;
  state.gestureAt = 0;
  view.scrollTop = view.scrollHeight;
  state.lastTop = view.scrollTop;
}

function renderTranscript(toBottom) {
  const view = $("transcript");
  // A mid-turn reload (process view, end-of-turn repaint) rebuilds the whole
  // thread. For a reader who has scrolled back that must not become a jump to
  // the bottom — hold their distance from it across the rebuild instead.
  const hold = toBottom && !state.stick
    ? view.scrollHeight - view.scrollTop
    : 0;
  view.textContent = "";
  const thread = el("div", "thread");

  if (state.earliest > 0) {
    const btn = el("button", "load-older", "Load earlier messages");
    btn.type = "button";
    btn.addEventListener("click", () => loadOlder(btn));
    thread.appendChild(btn);
  }

  state.items.forEach((item) => {
    thread.appendChild(renderMessage(item));
    // Steps are siblings of the bubble, not contents of it — the bubble keeps
    // its own width and styling whether or not the process view is on.
    if (item.steps && item.steps.length) thread.appendChild(renderSteps(item));
  });
  view.appendChild(thread);
  syncWorkingTail();
  if (hold) {
    view.scrollTop = Math.max(0, view.scrollHeight - hold);
    state.lastTop = view.scrollTop;
  } else if (toBottom) {
    jumpBottom(view);
  }
  syncJumpPill();
}

/**
 * Tools row: Copy (+ extras) on the left; timestamp only for user prompts,
 * right-aligned. Shown on hover.
 */
function messageToolsRow(item, extraButtons, { showTime = false } = {}) {
  const tools = el("div", "msg-tools");
  tools.appendChild(copyButton(item.text));
  (extraButtons || []).forEach((b) => tools.appendChild(b));
  if (showTime) {
    const when = messageHoverTime(item.ts);
    if (when) {
      const time = el("span", "msg-time", when);
      time.title = when;
      tools.appendChild(time);
    }
  }
  return tools;
}

const STEP_LABEL = { tool_use: "▸", tool_result: "↳", thinking: "✻" };

/** Fill a process-view step <code> with optional syntax highlight. */
function paintStepBody(codeEl, text, lang) {
  if (lang) paintCode(codeEl, text, lang);
  else {
    codeEl.replaceChildren();
    codeEl.textContent = text || "";
  }
}

function renderSteps(item) {
  // Rows sit BELOW the bubble they belong to: the daemon attaches each step
  // to the message it followed, so top-to-bottom is the order it happened.
  const box = el("div", "steps");
  (item.steps || []).forEach((s) => {
    const row = el("div",
      `step step-${s.kind}${s.ok === false ? " step-err" : ""}`);
    const head = el("button", "step-head");
    head.type = "button";
    let title;
    if (s.kind === "tool_use") title = s.name || "tool";
    else if (s.kind === "tool_result") title = s.ok === false ? "error" : "result";
    else title = "thinking";
    head.appendChild(el("span", "step-mark", STEP_LABEL[s.kind] || "·"));
    head.appendChild(el("span", "step-title", title));
    if (s.kind === "thinking" && s.recorded === false) {
      head.appendChild(el("span", "step-note", "not recorded by this CLI"));
      head.disabled = true;
      row.appendChild(head);
      box.appendChild(row);
      return;
    }
    head.appendChild(el("span", "step-detail",
      s.detail || (s.preview || "").split("\n")[0].slice(0, 120)));
    if (s.bytes) head.appendChild(el("span", "step-size", humanSize(s.bytes)));
    // step-body is a <pre>; when the daemon knows a language (path → py/js/…
    // or body is a unified diff), fill a <code> child via the same tokeniser
    // fenced markdown blocks use.
    const body = el("pre", "step-body");
    const codeEl = el("code");
    paintStepBody(codeEl, s.preview || "", s.lang || "");
    body.appendChild(codeEl);
    body.hidden = true;
    head.addEventListener("click", async () => {
      body.hidden = !body.hidden;
      // Fetch the rest only on first expand — that is what keeps a 200KB
      // tool result out of every window fetch.
      if (body.hidden || !s.truncated || s.loaded) return;
      s.loaded = true;
      const profile = profileById(state.open && state.open.profileId);
      try {
        const full = await call(profile,
          `/api/sessions/${encodeURIComponent(state.open.sessionId)}`
          + `/steps/${encodeURIComponent(s.ref)}`, { timeout: 60000 });
        paintStepBody(codeEl, full.text || s.preview || "", s.lang || "");
      } catch (e) {
        s.loaded = false;
        toast(e.message);
      }
    });
    row.appendChild(head);
    row.appendChild(body);
    box.appendChild(row);
  });
  return box;
}

function renderMessage(item) {
  const wrap = el("div", `msg ${item.role}${item.severity === "error" ? " error" : ""}`);
  if (item.role === "user") {
    wrap.appendChild(inlineInto(el("span", "body"), item.text));
    const extras = [];
    const profile = profileById(state.open.profileId);
    // Rewind is daemon-side session-file surgery (daemon ≥ 2.5), so it works
    // in BOTH execution modes — gate only on the harness's advertised cap.
    const rwHarness = sessionProvider(state.open && state.open.session, profile);
    if (profile && capOf(profile, "rewind", false, rwHarness)) {
      const back = userStepsBack(item.id);
      if (back > 0) {
        const b = el("button", null, "Rewind to here");
        b.type = "button";
        b.addEventListener("click", () => confirmRewind(item, back));
        extras.push(b);
      }
    }
    wrap.appendChild(messageToolsRow(item, extras, { showTime: true }));
    return wrap;
  }
  if (item.role === "status" || item.role === "notice") {
    wrap.textContent = item.text;
    return wrap;
  }
  wrap.appendChild(renderMarkdown(item.text));
  wrap.appendChild(messageToolsRow(item));
  return wrap;
}

// ------------------------------------------------------- linked host files
//
// Agents (Codex above all) answer with links to bare host paths:
// [findings.txt](/Users/me/proj/out/findings.txt:12). Left alone the browser
// resolves that against THIS page's origin — the static web host — and 404s.
// The daemon (2.20.0+) serves such a file at /api/sessions/<id>/file, but
// only when the session's own transcript links it and it sits inside the
// session's working folder; anything else is a plain 404.

const LINK_LOC = /(?::(\d+)(?::\d+)?|#L(\d+)(?:-L?\d+)?)$/;

function hostPathLink(a) {
  const href = a.getAttribute("href") || "";
  // "/x" but not "//host/x" (protocol-relative) — a host path, not a URL.
  if (!href.startsWith("/") || href.startsWith("//")) return null;
  let path = href;
  try { path = decodeURIComponent(href); } catch { /* keep it raw */ }
  const m = path.match(LINK_LOC);
  const line = m ? Number(m[1] || m[2]) || 0 : 0;
  return { href, path, line, name: path.replace(LINK_LOC, "").split("/").pop() };
}

async function openHostFile(link) {
  const open = state.open;
  const profile = open && profileById(open.profileId);
  if (!profile) return;
  // The flag may be stale (saved before the daemon was upgraded): ask again.
  if (!profile.linkedFiles) await pingProfile(profile);
  if (!profile.linkedFiles) {
    toast(`Opening host files needs agentremoted 2.20.0 or newer (this daemon is ${profile.version || "older"})`);
    return;
  }
  toast(`Opening ${link.name}…`);
  let blob;
  try {
    blob = await call(profile,
      `/api/sessions/${encodeURIComponent(open.sessionId)}/file?path=${encodeURIComponent(link.path)}`,
      { raw: true, timeout: 60000 });
  } catch (e) {
    toast(e.status === 404
      ? `${link.name} is not available: the daemon only opens files this session linked inside its working folder or ~/Public`
      : e.status === 413 ? `${link.name} is too large to open (over 10 MB)`
      : `Could not open ${link.name}: ${e.message}`);
    return;
  }
  const type = (blob.type || "").toLowerCase();
  if (type.startsWith("image/") || type === "application/pdf") {
    // Images and PDFs only: the daemon never labels HTML or SVG as such, so
    // nothing opened here can run script in this page's origin.
    const url = URL.createObjectURL(blob);
    if (type === "application/pdf") {
      window.open(url, "_blank", "noopener");
    } else {
      modal({
        title: link.name, wide: true,
        build(body) {
          const img = el("img", "host-file-img");
          img.src = url;
          img.alt = link.name;
          body.appendChild(img);
        },
        actions: [{ label: "Close", primary: true }],
      });
    }
    setTimeout(() => URL.revokeObjectURL(url), 120000);
    return;
  }
  if (!type.startsWith("text/")) {
    saveBlob(blob, link.name);
    return;
  }
  const text = await blob.text();
  // Markdown reads formatted, like the transcript it came from. A link to a
  // specific line opens the source instead, so that line can be marked.
  const isMd = /\.(md|markdown|mdown|mkd|mdx)$/i.test(link.path.replace(LINK_LOC, ""));
  let formatted = isMd && !(link.line > 0);
  let paint = null;
  modal({
    title: link.name, wide: true,
    build(body) {
      body.appendChild(el("div", "host-file-path", link.path.replace(LINK_LOC, "")));
      const view = el("div");
      body.appendChild(view);
      // Links inside the file go through the daemon too (it still refuses
      // anything the session did not link).
      wireHostFileLinks(view);
      paint = () => {
        view.replaceChildren(formatted ? markdownFile(text) : sourceView(text, link.line));
      };
      paint();
    },
    actions: [
      ...(isMd ? [{ label: formatted ? "Source" : "Formatted", close: false, run: (btn) => {
        formatted = !formatted;
        if (btn) btn.textContent = formatted ? "Source" : "Formatted";
        if (paint) paint();
      } }] : []),
      { label: "Copy", close: false, run: async () => {
        try { await copyText(text); toast("Copied"); } catch { toast("Copy failed"); }
      } },
      { label: "Download", close: false, run: () => saveBlob(blob, link.name) },
      { label: "Close", primary: true },
    ],
  });
}

function markdownFile(text) {
  const box = renderMarkdown(text);
  box.classList.add("host-file-md");
  return box;
}

function sourceView(text, line) {
  const pre = el("pre", "host-file");
  const lines = text.split("\n");
  if (line > 0 && line <= lines.length) {
    pre.appendChild(document.createTextNode(lines.slice(0, line - 1).join("\n")
      + (line > 1 ? "\n" : "")));
    const mark = el("mark", "host-file-line", lines[line - 1] || " ");
    pre.appendChild(mark);
    pre.appendChild(document.createTextNode(
      (line < lines.length ? "\n" : "") + lines.slice(line).join("\n")));
    requestAnimationFrame(() => mark.scrollIntoView({ block: "center" }));
  } else {
    pre.textContent = text;
  }
  return pre;
}

function saveBlob(blob, name) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name || "file";
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60000);
}

function wireHostFileLinks(host) {
  host.addEventListener("click", (ev) => {
    const a = ev.target && ev.target.closest && ev.target.closest("a[href]");
    if (!a || !host.contains(a)) return;
    const link = hostPathLink(a);
    if (!link) return;
    ev.preventDefault();
    openHostFile(link);
  });
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
        "This cannot be undone. Conversation only — file changes on the host are not reverted.");
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

// --------------------------------------- composer: per-turn chips, commands
//
// Execution, model and effort are choices about the NEXT message, so they
// sit on the composer where the message is written. They used to be three
// rows deep in the Options dialog, which is how a turn got sent headless by
// someone who thought it was interactive.

/** Daemon + harness the open session's next turn goes to. */
function turnContext() {
  if (!state.open) return null;
  const profile = profileById(state.open.profileId);
  if (!profile) return null;
  return { profile, harness: sessionProvider(state.open.session, profile) || null };
}

const EXEC_HINT = {
  interactive: "Runs in the terminal on the host. Slash commands work.",
  headless: "Runs without a terminal. Messages queue while a turn runs.",
};

/** One entry per chip: what it is, what it can be, what it is now. */
function turnChoices() {
  const ctx = turnContext();
  if (!ctx) return [];
  const { profile, harness } = ctx;
  const out = [];
  const canInteractive = capOf(profile, "interactive", true, harness);
  out.push({
    key: "exec", label: "Execution",
    values: canInteractive ? ["interactive", "headless"] : ["headless"],
    current: execModeOf(profile, harness),
    display: execModeLabel,
    hint: (v) => EXEC_HINT[v] || "",
    set: (v) => { profile.execMode = normalizeExecMode(v, canInteractive); },
  });
  const models = modelsOf(profile, harness);
  if (capOf(profile, "can_set_model", true, harness) && models.length) {
    out.push({
      key: "model", label: "Model", values: models,
      current: modelOf(profile, harness),
      display: (v) => v, hint: () => "",
      set: (v) => setModelFor(profile, harness, v),
    });
  }
  const efforts = effortsOf(profile, harness);
  if (capOf(profile, "can_set_effort", false, harness) && efforts.length) {
    out.push({
      key: "effort", label: "Effort", values: efforts,
      current: effortOf(profile, harness),
      display: (v) => v, hint: () => "",
      set: (v) => setEffortFor(profile, harness, v),
    });
  }
  return out;
}

// The one popup above the composer: a chip's menu, or the command list.
const pop = {
  kind: null,        // null | "chip" | "slash"
  key: "",           // which chip
  anchor: null,
  items: [],         // slash: the commands on offer
  index: 0,          // slash: highlighted row
  dismissed: null,   // slash: the text Escape was pressed on
};
let chipSig = "";

function closePop({ refocus = false } = {}) {
  const box = $("composer-pop");
  const anchor = pop.anchor;
  if (pop.kind === "chip" && anchor) anchor.setAttribute("aria-expanded", "false");
  pop.kind = null;
  pop.key = "";
  pop.anchor = null;
  pop.items = [];
  box.classList.add("hidden");
  box.innerHTML = "";
  $("prompt").removeAttribute("aria-activedescendant");
  if (refocus && anchor && anchor.isConnected) anchor.focus();
}

/** Show the popup with its left edge on `anchor`, kept inside the composer. */
function placePop(anchor) {
  const box = $("composer-pop");
  const host = $("composer").getBoundingClientRect();
  box.classList.remove("hidden");
  box.style.left = "0px";
  const want = anchor.getBoundingClientRect().left - host.left;
  const max = host.width - box.offsetWidth - 8;
  box.style.left = Math.max(8, Math.min(want, max)) + "px";
}

function renderChips({ force = false } = {}) {
  const bar = $("composer-chips");
  const choices = turnChoices();
  const sig = JSON.stringify(choices.map((c) => [c.key, c.current, c.values]));
  if (!force && sig === chipSig) return;
  chipSig = sig;
  if (pop.kind === "chip") closePop();
  bar.innerHTML = "";
  bar.classList.toggle("hidden", !choices.length);
  choices.forEach((c) => {
    const b = el("button", "cchip");
    b.type = "button";
    b.dataset.key = c.key;
    b.appendChild(el("span", "cchip-k", c.label));
    b.appendChild(el("span", "cchip-v", c.display(c.current) || "default"));
    const what = `${c.label}: ${c.display(c.current) || "default"}`;
    b.title = what;
    if (c.values.length < 2) {
      // Nothing to choose, but still worth seeing what the turn will use.
      b.disabled = true;
      b.setAttribute("aria-label", what);
    } else {
      b.setAttribute("aria-label", what + ". Change");
      b.setAttribute("aria-haspopup", "listbox");
      b.setAttribute("aria-expanded", "false");
      b.addEventListener("click", () => {
        if (pop.kind === "chip" && pop.key === c.key) closePop();
        else openChipMenu(c.key, b);
      });
    }
    bar.appendChild(b);
  });
}

function openChipMenu(key, anchor) {
  const choice = turnChoices().find((c) => c.key === key);
  const ctx = turnContext();
  if (!choice || !ctx) return;
  closePop();
  const box = $("composer-pop");
  box.setAttribute("role", "listbox");
  box.setAttribute("aria-label", choice.label);
  let current = null;
  choice.values.forEach((v) => {
    const o = el("button", "pop-opt");
    o.type = "button";
    o.setAttribute("role", "option");
    o.setAttribute("aria-selected", String(v === choice.current));
    o.appendChild(el("span", "pop-main", choice.display(v)));
    const hint = choice.hint(v);
    if (hint) o.appendChild(el("span", "pop-hint", hint));
    o.addEventListener("click", () => {
      choice.set(v);
      store.save();
      closePop();
      syncComposer();
      syncSlashMenu();
      const again = $("composer-chips").querySelector(`[data-key="${key}"]`);
      if (again) again.focus();
    });
    if (v === choice.current) current = o;
    box.appendChild(o);
  });
  box.appendChild(el("div", "pop-foot",
    `Used for every session on ${ctx.profile.name}`
    + (ctx.profile.multi && ctx.harness && key !== "exec" ? ` · ${ctx.harness}` : "")));
  pop.kind = "chip";
  pop.key = key;
  pop.anchor = anchor;
  anchor.setAttribute("aria-expanded", "true");
  placePop(anchor);
  (current || box.querySelector(".pop-opt"))?.focus();
}

/** Arrow keys inside a chip's menu; Escape hands focus back to the chip. */
function onPopKeydown(e) {
  if (pop.kind !== "chip") return;
  const opts = [...$("composer-pop").querySelectorAll(".pop-opt")];
  const at = opts.indexOf(document.activeElement);
  let to = -1;
  if (e.key === "ArrowDown") to = (at + 1) % opts.length;
  else if (e.key === "ArrowUp") to = (at - 1 + opts.length) % opts.length;
  else if (e.key === "Home") to = 0;
  else if (e.key === "End") to = opts.length - 1;
  else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closePop({ refocus: true }); return; }
  else if (e.key === "Tab") { closePop(); return; }
  if (to >= 0) { e.preventDefault(); opts[to].focus(); }
}

/** The commands this session's harness advertises that match what is typed. */
function slashMatches(text) {
  const ctx = turnContext();
  if (!ctx || !/^\/[A-Za-z0-9_-]*$/.test(text)) return [];
  const typed = text.toLowerCase();
  return slashOf(ctx.profile, ctx.harness)
    .filter((c) => c.toLowerCase().startsWith(typed));
}

/** Open, update or close the command list to match the composer text. */
function syncSlashMenu() {
  const prompt = $("prompt");
  const text = prompt.value;
  if (pop.kind === "chip") return;
  const items = document.activeElement === prompt ? slashMatches(text) : [];
  // Nothing to offer, or the only match is already typed out in full.
  if (!items.length || (items.length === 1 && items[0] === text)
      || pop.dismissed === text) {
    if (pop.kind === "slash") closePop();
    if (pop.dismissed !== text) pop.dismissed = null;
    return;
  }
  pop.dismissed = null;
  const ctx = turnContext();
  const headless = execModeOf(ctx.profile, ctx.harness) !== "interactive";
  const keep = pop.kind === "slash" ? pop.items[pop.index] : null;
  pop.kind = "slash";
  pop.anchor = prompt;
  pop.items = items;
  pop.index = Math.max(0, items.indexOf(keep));
  const box = $("composer-pop");
  box.innerHTML = "";
  box.setAttribute("role", "listbox");
  box.setAttribute("aria-label", "Commands");
  items.forEach((cmd, i) => {
    const o = el("div", "pop-opt");
    o.id = "slash-opt-" + i;
    o.setAttribute("role", "option");
    o.appendChild(el("span", "pop-main mono", cmd));
    // /rewind is done by the daemon itself, so it works in either mode.
    if (headless && cmd !== "/rewind") o.appendChild(el("span", "pop-hint", "needs Interactive"));
    // mousedown, not click: keep the caret in the composer.
    o.addEventListener("mousedown", (e) => { e.preventDefault(); pickSlash(cmd); });
    box.appendChild(o);
  });
  box.appendChild(el("div", "pop-foot", "↑↓ choose · Enter or Tab fill in · Esc close"));
  paintSlashIndex();
  placePop(prompt);
}

function paintSlashIndex() {
  const box = $("composer-pop");
  [...box.querySelectorAll(".pop-opt")].forEach((o, i) => {
    o.setAttribute("aria-selected", String(i === pop.index));
    if (i === pop.index) {
      $("prompt").setAttribute("aria-activedescendant", o.id);
      o.scrollIntoView({ block: "nearest" });
    }
  });
}

function pickSlash(cmd) {
  const prompt = $("prompt");
  prompt.value = cmd + " ";
  closePop();
  prompt.dispatchEvent(new Event("input"));
  prompt.focus();
  prompt.setSelectionRange(prompt.value.length, prompt.value.length);
}

/**
 * Keys the command list takes from the composer. Returns true when the key
 * was used, so Enter fills in a command instead of sending half of one.
 */
function slashKeydown(e) {
  if (pop.kind !== "slash" || !pop.items.length) return false;
  const n = pop.items.length;
  if (e.key === "ArrowDown") pop.index = (pop.index + 1) % n;
  else if (e.key === "ArrowUp") pop.index = (pop.index - 1 + n) % n;
  else if (e.key === "Escape") {
    pop.dismissed = $("prompt").value;
    closePop();
    e.preventDefault();
    e.stopPropagation();
    return true;
  } else if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
    const cmd = pop.items[pop.index];
    // The command is already typed in full: Enter means send it.
    if (e.key === "Enter" && cmd === $("prompt").value) { closePop(); return false; }
    e.preventDefault();
    pickSlash(cmd);
    return true;
  } else return false;
  e.preventDefault();
  paintSlashIndex();
  return true;
}

/** Send says what it will do: a headless turn in progress queues the message. */
function syncSendButton() {
  const ctx = turnContext();
  const running = !!(state.job && jobBelongsToOpen(state.job));
  const queues = running && ctx
    && execModeOf(ctx.profile, ctx.harness) !== "interactive";
  const b = $("btn-send");
  b.textContent = queues ? "Queue" : "Send";
  b.title = queues ? "Runs after the current turn"
    : (running ? "Typed into the running session" : "");
}

function syncComposer() {
  renderChips();
  syncSendButton();
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
  // Snapshot the open session BEFORE any await — a mid-send row switch must
  // not re-route this prompt or re-pin the job to the wrong transcript.
  const openProfileId = state.open.profileId;
  const openSessionId = state.open.sessionId;
  const openGen = state.openGen;
  const profile = profileById(openProfileId);
  if (!profile || !openSessionId) return;

  dropJobIfNotForOpen();

  if (raw.startsWith("!")) return runShell(raw.slice(1).trim());


  if (/^\/[A-Za-z][A-Za-z0-9_-]*$/.test(raw.split(" ")[0])) {
    const cmd = raw.split(" ")[0];
    const interactive = execModeOf(profile,
      sessionProvider(state.open && state.open.session, profile)) === "interactive";
    // /rewind never reaches the harness — the daemon rewinds the session
    // journal itself (≥ 2.5) — so it is exempt from the interactive-only
    // rule. The advertised-list check below still gates it (old daemons
    // don't list it).
    if (!interactive && cmd !== "/rewind") {
      composerNote(`${cmd} needs interactive execution — headless turns cannot run commands`, true);
      return;
    }
    // No hardcoded whitelist: the daemon advertises each harness's real
    // built-ins (claude/grok/codex: /compact /exit /rewind), so anything
    // off the OPEN session's list is refused before it costs a turn on a
    // command that harness would not understand.
    const known = slashOf(profile, sessionProvider(state.open && state.open.session, profile));
    if (!known.includes(cmd)) {
      composerNote(known.length
        ? `${cmd} is not available here — try: ${known.slice(0, 6).join(" ")}`
        : "This daemon does not advertise any slash commands", true);
      return;
    }
  }

  // Only type/queue into a job that is pinned to THIS open session.
  const job = (state.job && jobBelongsToOpen(state.job)) ? state.job : null;
  const paint = { gen: openGen, profileId: openProfileId, sessionId: openSessionId };
  appendLive({ role: "user", text: raw }, paint);

  if (job) {
    const interactive = execModeOf(profile,
      sessionProvider(state.open && state.open.session, profile)) === "interactive";
    const jobId = job.id;
    try {
      await call(profile, `/api/jobs/${jobId}/${interactive ? "input" : "queue"}`,
        { method: "POST", body: { prompt: raw } });
      // Still note only if this transcript is open (delivery already targeted jobId).
      if (isOpenStill(openGen, openProfileId, openSessionId)) {
        composerNote(interactive ? "Typed into the session" : "Queued");
      }
    } catch (e) {
      if (isOpenStill(openGen, openProfileId, openSessionId)) {
        composerNote(e.message, true);
      }
    }
    return;
  }

  try {
    const harness = sessionProvider(state.open && state.open.session, profile);
    // Always address the snapshotted session id — never state.open after await.
    const res = await call(profile,
      `/api/sessions/${encodeURIComponent(openSessionId)}/continue`,
      {
        method: "POST",
        body: {
          prompt: raw,
          permission_mode: wireExecMode(execModeOf(profile, harness)),
          model: modelOf(profile, harness),
          effort: effortOf(profile, harness),
        },
      });
    if (res && res.job_id && isOpenStill(openGen, openProfileId, openSessionId)) {
      attachJob(res.job_id, openSessionId);
    }
  } catch (e) {
    appendLive({ role: "notice", text: e.message, severity: "error" }, paint);
  }
}

// Directive appended to a `!` shell result. The old wording ended with
// "wait for the next user instruction" — which the model simply echoed back
// ("yes, I'll wait for your next instruction"). Nothing to parrot, no reply.
const SHELL_SILENT =
  "[silent] Shell result for context only. Do not reply or acknowledge this message.";

async function runShell(command) {
  if (!command || !state.open) return;
  const openProfileId = state.open.profileId;
  const openSessionId = state.open.sessionId;
  const openGen = state.openGen;
  const profile = profileById(openProfileId);
  if (!profile || !openSessionId) return;
  const paint = { gen: openGen, profileId: openProfileId, sessionId: openSessionId };
  const cwd = (state.open.session && state.open.session.cwd) || "";
  appendLive({ role: "user", text: "! " + command }, paint);
  composerNote("Running…");
  try {
    const res = await call(profile, "/api/shell", {
      method: "POST",
      timeout: 40000,
      body: {
        command,
        session_id: openSessionId,
        cwd,
      },
    });
    if (!isOpenStill(openGen, openProfileId, openSessionId)) return;
    const body = (res.output || "").replace(/\s+$/, "") || "(no output)";
    appendLive({ role: "assistant", text: "```\n" + body + "\n```" }, paint);
    composerNote("");
    // Hand it to the agent as context, with a directive not to reply.
    const prompt = `[shell] ! ${command}\n[output]\n\`\`\`\n${body.slice(0, 8000)}`
      + (res.exit_code ? `\n(exit code ${res.exit_code})` : "")
      + "\n```\n" + SHELL_SILENT;
    const harness = sessionProvider(state.open && state.open.session, profile);
    const started = await call(profile,
      `/api/sessions/${encodeURIComponent(openSessionId)}/continue`,
      { method: "POST", body: { prompt, permission_mode: wireExecMode(execModeOf(profile, harness)),
                                model: modelOf(profile, harness),
                                effort: effortOf(profile, harness) } });
    if (started && started.job_id && isOpenStill(openGen, openProfileId, openSessionId)) {
      attachJob(started.job_id, openSessionId);
    }
  } catch (e) {
    if (isOpenStill(openGen, openProfileId, openSessionId)) {
      composerNote(e.message, true);
    }
  }
}

function appendLive(item, { gen = state.openGen, profileId = null, sessionId = null } = {}) {
  // Never paint live rows into a transcript that is no longer open.
  if (profileId != null && sessionId != null) {
    if (!isOpenStill(gen, profileId, sessionId)) return;
  } else if (gen !== state.openGen || !state.open) {
    return;
  }
  const row = {
    id: `live-${state.items.length}-${Date.now()}`,
    live: true,
    ...item,
  };
  // Prefer an explicit ts from the event; otherwise stamp with now.
  if (!row.ts) row.ts = new Date().toISOString();
  state.items.push(row);
  const thread = $("transcript").querySelector(".thread");
  if (thread) {
    thread.appendChild(renderMessage(state.items[state.items.length - 1]));
    syncWorkingTail();
    const view = $("transcript");
    // The reader's own prompt pulls them back down; the agent's output does
    // not — reading back through a long turn has to survive new rows, and
    // the pill counts what arrived below them.
    if (row.role === "user") jumpBottom(view);
    else {
      followBottom(view);
      if (!nearBottom(view)) unseenLive += 1;
    }
    syncJumpPill();
  } else {
    renderTranscript(true);
  }
}

/**
 * True when this assistant text is already on screen (journal loadTail and/or
 * an earlier live paint). Opening a mid-turn session loads updates.jsonl first,
 * then job events since=0 re-stream the same flushes — without this the user
 * sees every bubble twice.
 */
/**
 * The part of a replayed job (since=0) that the transcript does not hold yet.
 *
 * Opening a session reads its newest page from disk, then adopts the running
 * turn and replays that turn from its first event. A turn that has run for
 * hours said far more than the 40 rows assistantTextAlreadyShown looks at, so
 * its early answers used to land BELOW the newest ones and the transcript
 * looked stale until a refresh. Disk is the truth: keep only what comes after
 * the last replayed text already on screen. When none of it is on screen the
 * page from disk is newer than all of it, so nothing is replayed; later polls
 * and the end-of-turn reload carry on from there.
 */
function replayTail(events) {
  const shown = new Set();
  for (const it of state.items) {
    if (it.role === "assistant" && !it.live) shown.add(String(it.text || "").trim());
  }
  let last = -1;
  events.forEach((ev, i) => {
    if (ev.kind === "text" && ev.text && shown.has(String(ev.text).trim())) last = i;
  });
  return events.slice(last + 1).filter((ev) => ev.kind !== "text" || last >= 0);
}

function assistantTextAlreadyShown(text) {
  const t = String(text || "").trim();
  if (!t) return true;
  // Prefer a recent-window scan (cheap) over scanning the full transcript.
  const start = Math.max(0, state.items.length - 40);
  for (let i = state.items.length - 1; i >= start; i--) {
    const it = state.items[i];
    if (it.role === "assistant" && String(it.text || "").trim() === t) return true;
  }
  return false;
}

// -------------------------------------------------------------- job watch

function attachJob(jobId, sessionId = null, { adopted = false } = {}) {
  if (isSyntheticJobId(jobId)) return;
  const open = state.open;
  // Prefer explicit pin; fall back to open only when attaching for the
  // currently visible transcript.
  const pinnedSid = sessionId
    || (open && open.sessionId)
    || "";
  if (!pinnedSid) return;
  // Already watching this turn — do not reset since=0 (that would re-paint
  // every text event on top of the journal rows already on screen).
  if (state.job && state.job.id === jobId && state.jobTimer) {
    // Correct a wrong/empty pin (never leave session B's job labeled as A).
    state.job.sessionId = pinnedSid;
    state.job.profileId = open ? open.profileId : state.job.profileId;
    state.job.openGen = state.openGen;
    return;
  }
  // Silent teardown only: stopJobWatch() re-renders the banner, and with
  // state.job momentarily null the banner's auto-attach path would call back
  // into attachJob — mutual recursion until the stack blew, leaving no job
  // attached and no Answer UI for a pending question.
  detachJobWatch();
  // Pin the job to the session that owned it when we attached — never to
  // whatever happens to be open later if the user switches rows mid-turn.
  state.job = {
    id: jobId,
    sessionId: pinnedSid,
    profileId: open ? open.profileId : null,
    openGen: state.openGen,
    status: "starting",
    queued: [],
    startedAt: Date.now(),
    toolLine: "",
    pendingQuestion: null,
    pendingPermission: null,
    lastPendingQuestion: null,
    lastPendingPermission: null,
    // Adopted mid-turn: the transcript on screen was just read from disk,
    // which already holds everything the turn said so far. The first
    // snapshot replays the job from seq 0; see replayTail().
    replayAfterShown: adopted,
  };
  state.jobSince = 0;
  state.jobFails = 0;
  // Allow an immediate status blip for the new turn (global gap timer).
  chimeLastStatusMs = 0;
  renderBanner();
  setIconIdle($("btn-stop"), false);
  syncSendButton();
  state.jobTimer = setInterval(pollJob, 250);
  pollJob();
}

/** Tear down the job watch without touching the banner (see attachJob). */
function detachJobWatch() {
  clearGate();
  if (state.jobTimer) clearInterval(state.jobTimer);
  state.jobTimer = null;
  state.job = null;
  state.jobLastFetch = 0;
  // Keep the 34×34 Stop slot empty — do not collapse the row.
  setIconIdle($("btn-stop"), true);
  syncSendButton();
}

function stopJobWatch() {
  detachJobWatch();
  renderBanner();
}

/**
 * Event-driven polling, same doorbell the apps use: the status stream already
 * pushes each job's next_seq about once a second, so the expensive job fetch
 * only fires when that cursor moves. The timer is the fallback for when the
 * stream is down.
 */
/**
 * Does this job snapshot belong to the transcript currently open?
 * Rejects cross-session bleed when two jobs run and the user switches rows.
 */
function jobSnapBelongsToOpen(job, snap) {
  if (!job || !state.open) return false;
  if (job.profileId && state.open.profileId !== job.profileId) return false;
  // openGen moved (user opened another session) — even if ids briefly match.
  if (typeof job.openGen === "number" && job.openGen !== state.openGen) return false;
  const openSid = state.open.sessionId || "";
  const pinned = job.sessionId || "";
  const snapSid = (snap && snap.session_id) || "";
  const snapNew = (snap && snap.new_session_id) || "";
  const reported = [snapSid, snapNew].filter(Boolean);
  if (sessionIsJobPlaceholder(openSid, job.id)) return true;
  if (reported.length) {
    // Open row is already this job's session (or its fork target).
    if (reported.includes(openSid)) return true;
    // Still viewing the pinned parent while the daemon only reports the fork.
    if (pinned && openSid === pinned) return true;
    // Wrong session entirely (e.g. job for A while open is B).
    return false;
  }
  // No session ids on snap yet — trust the pin from attach time.
  if (pinned) return openSid === pinned;
  return true;
}

async function pollJob() {
  const job = state.job;
  if (!job || !state.open) return;
  // Job was attached for a different open session — drop it (never paint
  // session B's stream into A, and never keep it as the send target).
  if (!jobBelongsToOpen(job)) {
    stopJobWatch();
    return;
  }
  renderBanner();

  const profile = profileById(job.profileId || state.open.profileId);
  if (!profile) return;
  let frame = (state.active[profile.id] || []).find((j) => j.job_id === job.id);
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
  const paintCtx = {
    gen: job.openGen != null ? job.openGen : state.openGen,
    profileId: job.profileId || (state.open && state.open.profileId),
    sessionId: job.sessionId || (state.open && state.open.sessionId),
  };
  let snap;
  try {
    snap = await call(profile, `/api/jobs/${job.id}?since=${state.jobSince}`);
    state.jobFails = 0;
  } catch {
    if (++state.jobFails >= 5) {
      appendLive({ role: "notice", text: "Lost contact with the daemon", severity: "error" }, paintCtx);
      stopJobWatch();
    }
    return;
  } finally {
    job.inFlight = false;
  }
  if (state.job !== job) return; // detached while the request was in flight
  if (!jobSnapBelongsToOpen(job, snap)) {
    // User switched sessions (or this job is for another row) — do not paint.
    stopJobWatch();
    return;
  }

  state.jobSince = snap.next_seq || 0;
  let events = snap.events || [];
  if (job.replayAfterShown) {
    job.replayAfterShown = false;
    events = replayTail(events);
  }
  events.forEach((ev) => {
    if (ev.kind === "text" && ev.text) {
      // Journal messages for the open session already include flushed assistant
      // chunks; skip live echoes that would double-paint (see screenshot of
      // identical back-to-back assistant bubbles on working main sessions).
      if (!assistantTextAlreadyShown(ev.text)) {
        appendLive({ role: "assistant", text: ev.text }, paintCtx);
      }
    } else if (ev.kind === "tool") {
      job.toolLine = [ev.name, ev.detail].filter(Boolean).join("  ");
    }
  });
  job.status = snap.status || "";
  job.queued = snap.queued || [];

  // Keep last known ask/permission payloads so dismiss → Answer can reopen
  // even if a lagging poll briefly omits pending_* while the SSE flag is still
  // true (or the user closed the modal without cancelling on the daemon).
  // Re-read after await — stream may have moved while the job fetch ran.
  frame = (state.active[profile.id] || []).find((j) => j.job_id === job.id);
  if (snap.pending_permission) {
    job.pendingPermission = snap.pending_permission;
    job.lastPendingPermission = snap.pending_permission;
  } else if (!(frame && frame.pending_permission)) {
    job.pendingPermission = null;
  } else {
    job.pendingPermission = job.lastPendingPermission || job.pendingPermission;
  }
  if (snap.pending_question) {
    job.pendingQuestion = snap.pending_question;
    job.lastPendingQuestion = snap.pending_question;
  } else if (!(frame && frame.pending_question)) {
    // Only drop when the stream also says the gate is gone — and not while
    // this request_id is still the one we optimistically cleared after Submit.
    const held = job.lastPendingQuestion && job.lastPendingQuestion.request_id;
    if (!held || state.answeredQuestions.has(held)) {
      job.pendingQuestion = null;
      if (state.answeredQuestions.has(held)) job.lastPendingQuestion = null;
    } else {
      job.pendingQuestion = job.lastPendingQuestion;
    }
  } else {
    job.pendingQuestion = job.lastPendingQuestion || job.pendingQuestion;
  }

  // Auto-open once per request_id. Dismissing the modal does NOT cancel the
  // ask on the daemon — renderBanner keeps an "Answer" / "Respond" CTA so
  // the user can reopen (same idea as the phone's QuestionSheet banner).
  // Do not clear askedQuestion when pending flickers null (race while the
  // daemon applies picks) — that reopened the same panel after Submit.
  syncGate(job);
  if (!job.pendingPermission) state.askedPermission = null;
  // The card is idempotent per request id, so it is offered on every tick
  // (also after leaving this session and coming back). What must not come
  // back is an ask already answered here while the daemon catches up.
  if (job.pendingPermission
      && !state.answeredQuestions.has(`p:${job.pendingPermission.request_id}`)) {
    state.askedPermission = job.pendingPermission.request_id;
    showPermission(job.pendingPermission);
  }
  const qid = job.pendingQuestion && job.pendingQuestion.request_id;
  if (job.pendingQuestion && qid && !state.answeredQuestions.has(qid)) {
    state.askedQuestion = qid;
    showQuestion(job.pendingQuestion);
  }

  // A headless resume forks the session; follow the fork — only if we still
  // own this open transcript (never rewrite another row's open id).
  if (snap.new_session_id && state.open && snap.new_session_id !== state.open.sessionId
      && jobSnapBelongsToOpen(job, snap)
      && (state.open.sessionId === job.sessionId
        || state.open.sessionId === snap.session_id
        || sessionIsJobPlaceholder(state.open.sessionId, job.id)
        || !job.sessionId)) {
    state.open.sessionId = snap.new_session_id;
    if (state.open.session) state.open.session.id = snap.new_session_id;
    job.sessionId = snap.new_session_id;
    paintCtx.sessionId = snap.new_session_id;
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
  // A turn can report done/error while AskUserQuestion is still open (Stop
  // fired with the panel up). Keep watching so the Answer banner and modal
  // stay available until the gate clears.
  const gateOpen = !!(job.pendingQuestion || job.pendingPermission
    || snap.pending_question || snap.pending_permission);
  if (["done", "error", "stopped"].includes(snap.status) && !gateOpen) {
    const notes = [];
    // The turn is over: what it changed is now worth a fresh look.
    if (side.open) loadChanges();
    // Only real job errors — never "failed" for empty/unknown status.
    if (snap.status === "error") notes.push(snap.error || "The turn failed");
    if (snap.status === "stopped") notes.push("Stopped");
    if (snap.dropped_queued) notes.push(`${snap.dropped_queued} queued prompt(s) dropped`);
    const endGen = paintCtx.gen;
    const endProfile = paintCtx.profileId;
    const endSid = job.sessionId || paintCtx.sessionId;
    stopJobWatch();
    // Replace the live echoes with what the daemon actually persisted —
    // only if this session is still the open one. keepLive:false so disk
    // fully replaces mid-turn stream rows (no double bubbles). Paint
    // *before* the end chime so process steps are on screen when it rings.
    // Shared with the SSE end-path (paintOpenAfterJob is single-flight).
    if (isOpenStill(endGen, endProfile, endSid)
        || (state.open && state.open.sessionId === endSid && state.open.profileId === endProfile)) {
      await paintOpenAfterJob(profile.id, job.id, snap, {
        sessionId: endSid,
        newSessionId: snap.new_session_id || "",
      });
      if (notes.length && state.open && state.open.sessionId === endSid) {
        appendLive({ role: "notice", text: notes.join(" · "),
                     severity: snap.status === "error" ? "error" : "" });
      }
    }
    // End cue is shared with the global SSE watcher (deduped by key). If SSE
    // already chimed after its own paint, this is a no-op; if we got here
    // first, chimeJobEnded only plays the sound (alreadyPainted).
    if (snap.status === "done" || snap.status === "error") {
      await chimeJobEnded(profile.id, job.id, {
        nextSeq: state.jobSince,
        sessionId: endSid,
        newSessionId: snap.new_session_id || "",
        alreadyPainted: true,
      });
    }
    refreshSessions();
  } else if (processViewOn(state.open && state.open.sessionId)) {
    // Mid-turn: tool rows in the job stream only update the banner. When
    // process view is on, also pull journal steps (throttled) so the strip
    // tracks the work, not just the chime.
    const sawTool = (snap.events || []).some((ev) => ev.kind === "tool");
    if (sawTool) {
      scheduleProcessRefresh(profile.id, {
        job_id: job.id,
        session_id: job.sessionId || snap.session_id,
        new_session_id: snap.new_session_id,
      });
    }
  }
  renderBanner();
}

function renderBanner() {
  renderBannerStrip();
  syncWorkingTail();
}

/**
 * The transcript's own "still working" row: the banner sits above the
 * scroll, so a reader at the bottom of a long turn saw nothing moving where
 * they were looking. It mirrors the banner (same text, the ask colour when a
 * question or permission waits) and always stays the thread's last child.
 */
function syncWorkingTail() {
  const view = $("transcript");
  const thread = view && view.querySelector(".thread");
  const old = document.getElementById("working-tail");
  const box = $("banner");
  const on = thread && box && !box.classList.contains("hidden");
  if (!on) {
    if (old) old.remove();
    return;
  }
  const ask = box.classList.contains("needs-answer");
  const lineEl = box.querySelector(".banner-line");
  const text = ask
    ? (lineEl ? lineEl.textContent : "Waiting for you")
    : (lineEl ? lineEl.textContent : "Working");
  let tail = old;
  if (!tail) {
    tail = el("div", "msg status working-tail");
    tail.id = "working-tail";
    const dots = el("span", "working-dots");
    for (let i = 0; i < 3; i++) dots.appendChild(el("span"));
    tail.appendChild(dots);
    tail.appendChild(el("span", "working-text"));
  }
  tail.classList.toggle("ask", ask);
  tail.lastChild.textContent = text;
  if (tail.parentNode !== thread || thread.lastChild !== tail) thread.appendChild(tail);
  followBottom(view);
}

function renderBannerStrip() {
  const box = $("banner");
  if (!state.open) {
    box.classList.add("hidden");
    box.classList.remove("needs-answer");
    return;
  }
  const profile = profileById(state.open.profileId);
  if (!profile) {
    box.classList.add("hidden");
    box.classList.remove("needs-answer");
    return;
  }
  const sid = state.open.sessionId;
  const frames = state.active[profile.id] || [];
  // Never drive the banner from a job pinned to another session.
  dropJobIfNotForOpen();
  // Match by watched job only if it belongs here; else any frame for this sid.
  let frame = jobBelongsToOpen(state.job)
    ? frames.find((j) => j.job_id === state.job.id)
    : null;
  if (!frame && sid) {
    frame = frames.find((j) =>
      !isSyntheticJobId(j.job_id)
      && (j.session_id === sid || j.new_session_id === sid)) || null;
  }
  // If the stream says this session is blocked but we are not watching the
  // real job (dismissed, refreshed, synthetic tui row, …), attach it so Answer
  // has a job id and a pending payload to reopen.
  if (frame && !isSyntheticJobId(frame.job_id)
      && (frame.pending_question || frame.pending_permission)
      && (!jobBelongsToOpen(state.job) || state.job.id !== frame.job_id)) {
    // Pin to the open session id so a concurrent job for another row cannot
    // stream into this transcript.
    attachJob(frame.job_id, sid, { adopted: true });
    return; // attachJob → pollJob will re-render
  }
  // Synthetic tui-* rows can now flag pending_question, but Answer POSTs need
  // the real job id — recover via /api/jobs when we only have the fake row.
  if (frame && isSyntheticJobId(frame.job_id)
      && (frame.pending_question || frame.pending_permission)
      && !jobBelongsToOpen(state.job)
      && !state._recoveringGate) {
    state._recoveringGate = true;
    recoverPendingGate(profile.id, sid, state.openGen).finally(() => {
      state._recoveringGate = false;
    });
  }

  const job = jobBelongsToOpen(state.job) ? state.job : null;
  if (!job) {
    box.classList.add("hidden");
    box.classList.remove("needs-answer");
    return;
  }
  const pal = providerOf(sessionProvider(state.open && state.open.session, profile));
  // Sound cues are driven by chimeFromActive (every profile's SSE list), not
  // only this open-session banner — so a question/done on another session
  // still beeps while you are reading this one.

  // Prefer full pending payload; fall back to last seen + SSE boolean flag so
  // a dismissed modal never strands the user without an Answer button.
  const pendingQ = job.pendingQuestion || job.lastPendingQuestion || null;
  const pendingP = job.pendingPermission || job.lastPendingPermission || null;
  const needsQ = !!(pendingQ || (frame && frame.pending_question));
  const needsP = !!(pendingP || (frame && frame.pending_permission));
  // Don't show Answer for a request_id we already submitted/cancelled.
  const qAnswered = pendingQ && pendingQ.request_id
    && state.answeredQuestions.has(pendingQ.request_id);
  const showQ = needsQ && !qAnswered;

  box.textContent = "";
  box.classList.remove("hidden");
  box.classList.toggle("needs-answer", !!(showQ || needsP));
  box.appendChild(el("span", "pulse"));

  // Blocking human gates: keep a re-open control even after the modal is
  // dismissed (✕ / backdrop / Escape). Cancel and Send answer still go
  // through the modal itself.
  if (showQ) {
    box.appendChild(el("span", "banner-line", "A question is waiting for your answer"));
    const btn = el("button", "banner-action primary", "Answer");
    btn.type = "button";
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      reopenQuestion();
    });
    box.appendChild(btn);
  } else if (needsP) {
    const tool = (pendingP && (pendingP.tool_name || pendingP.toolName)) || "a tool";
    box.appendChild(el("span", "banner-line", `Permission needed · ${tool}`));
    const btn = el("button", "banner-action primary", "Respond");
    btn.type = "button";
    btn.addEventListener("click", (e) => {
      e.stopPropagation();
      reopenPermission();
    });
    box.appendChild(btn);
  } else {
    // Two-line strip: description (+ elapsed) on line 1, raw command /
    // path / [attached: …] on line 2 when the daemon provided it.
    const { line1, line2 } = liveStatusParts(frame, job, pal);
    box.appendChild(el("span", "banner-line", line1));
    if (line2) box.appendChild(el("span", "banner-cmd", line2));
  }
}

/** Banner Answer: re-fetch pending payload if needed, then open the modal. */
async function reopenQuestion() {
  const job = state.job;
  const profile = state.open && profileById(state.open.profileId);
  if (!job || !profile) return;
  let pending = job.pendingQuestion || job.lastPendingQuestion;
  if (!pending || !(pending.questions && pending.questions.length)) {
    try {
      // Full snapshot (since=0) so we always get pending_question even if our
      // event cursor is already past the question event.
      const snap = await call(profile, `/api/jobs/${job.id}?since=0`);
      if (snap.pending_question) {
        pending = snap.pending_question;
        job.pendingQuestion = pending;
        job.lastPendingQuestion = pending;
      }
      if (typeof snap.next_seq === "number") state.jobSince = snap.next_seq;
    } catch (e) {
      toast(e.message || "Could not reload the question");
      return;
    }
  }
  if (!pending || !(pending.questions && pending.questions.length)) {
    toast("No pending question on the daemon");
    renderBanner();
    return;
  }
  // Allow reopen after a prior dismiss (askedQuestion already equals this id).
  showQuestion(pending, { force: true });
}

async function reopenPermission() {
  const job = state.job;
  const profile = state.open && profileById(state.open.profileId);
  if (!job || !profile) return;
  let pending = job.pendingPermission || job.lastPendingPermission;
  if (!pending) {
    try {
      const snap = await call(profile, `/api/jobs/${job.id}?since=0`);
      if (snap.pending_permission) {
        pending = snap.pending_permission;
        job.pendingPermission = pending;
        job.lastPendingPermission = pending;
      }
      if (typeof snap.next_seq === "number") state.jobSince = snap.next_seq;
    } catch (e) {
      toast(e.message || "Could not reload the permission prompt");
      return;
    }
  }
  if (!pending) {
    toast("No pending permission on the daemon");
    renderBanner();
    return;
  }
  showPermission(pending, { force: true });
}

// ---------------------------------------------------------------- streams

// Per daemon: "connecting" | "live" | "reconnecting" | "offline".
// A dropped stream used to blank every "working" mark at once and say
// nothing, so a turn that was still running looked finished.
const CONN_GRACE_MS = 15000;
const connTimers = {};
const connRetry = {};          // profileId -> { delay, timer }

function connOf(profileId) {
  return (state.conn[profileId] && state.conn[profileId].state) || "connecting";
}

function setConn(id, next) {
  const cur = state.conn[id];
  if (cur && cur.state === next) return;
  state.conn[id] = { state: next, since: Date.now() };
  clearTimeout(connTimers[id]);
  delete connTimers[id];
  if (next === "reconnecting" || next === "connecting") {
    // Keep showing what was last known while the browser retries, but not
    // for ever: after the grace period it is no longer worth believing.
    connTimers[id] = setTimeout(() => setConn(id, "offline"), CONN_GRACE_MS);
  }
  if (next === "offline") {
    state.active[id] = [];
    renderStatus();
    renderSessions();
    renderBanner();
  }
  renderConn();
}

function dropStream(id, { forget = false } = {}) {
  const src = state.streams[id];
  if (src) src.close();
  delete state.streams[id];
  if (connRetry[id]) clearTimeout(connRetry[id].timer);
  if (forget) {
    clearTimeout(connTimers[id]);
    delete connTimers[id];
    delete connRetry[id];
    delete state.conn[id];
    delete state.active[id];
    delete state.sessionsRev[id];
    // Tear-down is not "job finished" — forget without end-chimes.
    chimeForgetProfile(id);
  }
}

/** Reconnect every daemon that is not live, now (Retry, or the network is back). */
function reconnectNow() {
  enabledProfiles().forEach((p) => {
    if (connOf(p.id) === "live") return;
    dropStream(p.id);
    if (connRetry[p.id]) connRetry[p.id].delay = 0;
  });
  syncStreams();
}

function syncStreams() {
  const wanted = new Map(enabledProfiles().map((p) => [p.id, p]));
  for (const [id, src] of Object.entries(state.streams)) {
    const p = wanted.get(id);
    if (!p || src._url !== streamUrl(p)) dropStream(id, { forget: true });
  }
  for (const id of Object.keys(state.conn)) {
    if (!wanted.has(id)) dropStream(id, { forget: true });
  }
  wanted.forEach((profile, id) => {
    if (state.streams[id]) return;
    const url = streamUrl(profile);
    // EventSource cannot send headers, which is exactly why the daemon also
    // accepts the token as a query parameter on this endpoint.
    const src = new EventSource(url);
    src._url = url;
    if (!state.conn[id]) setConn(id, "connecting");
    src.onopen = () => {
      if (connRetry[id]) connRetry[id].delay = 0;
      setConn(id, "live");
    };
    src.onmessage = (ev) => {
      let jobs = [];
      let newSession = false;
      try {
        const frame = JSON.parse(ev.data);
        jobs = frame.active || [];
        state.active[id] = jobs;
        // sessions_rev (daemon ≥ 2.21) bumps when a new session appears —
        // started from the host TUI, another client, or a headless turn.
        // Re-list so it shows without a manual refresh. The first frame only
        // records the baseline: connecting must not trigger a reload.
        if (typeof frame.sessions_rev === "number") {
          const prev = state.sessionsRev[id];
          state.sessionsRev[id] = frame.sessions_rev;
          if (prev !== undefined && frame.sessions_rev !== prev) newSession = true;
        }
      } catch { return; }
      setConn(id, "live");
      // Chimes for every session on this daemon, open or not.
      chimeFromActive(id, jobs);
      renderStatus();
      renderSessions();
      renderBanner();
      if (newSession) refreshSessions();
    };
    src.onerror = () => {
      // Do NOT chime ends here — a glitch would bi-bi every running job.
      if (state.streams[id] !== src) return;
      if (src.readyState === EventSource.CLOSED) {
        // The browser gave up (the daemon answered with an error, e.g. a
        // rejected token). It will not retry on its own, so we do, slower
        // each time.
        setConn(id, "offline");
        dropStream(id);
        const r = connRetry[id] || (connRetry[id] = { delay: 0, timer: 0 });
        r.delay = Math.min(60000, r.delay ? r.delay * 2 : 5000);
        r.timer = setTimeout(syncStreams, r.delay);
      } else if (connOf(id) === "live") {
        // EventSource is reconnecting by itself; say so and keep the last
        // known state on screen for the grace period.
        setConn(id, "reconnecting");
      }
    };
    state.streams[id] = src;
  });
  renderConn();
}

const CONN_TEXT = { live: "live", connecting: "connecting…",
                    reconnecting: "reconnecting…", offline: "offline" };

/** The line under the session list, and the tag in the open session's header. */
function renderConn() {
  const box = $("conn");
  if (!box) return;
  const profiles = enabledProfiles();
  box.classList.toggle("hidden", !profiles.length);
  const states = profiles.map((p) => connOf(p.id));
  const sig = JSON.stringify(profiles.map((p, i) => [p.id, p.name, states[i]]));
  if (sig !== box.dataset.sig) {
    box.dataset.sig = sig;
    box.textContent = "";
    const allLive = states.every((st) => st === "live");
    if (allLive && profiles.length > 1) {
      const item = el("span", "conn-item conn-live");
      item.appendChild(el("span", "conn-dot"));
      item.appendChild(document.createTextNode(`Live · ${profiles.length} daemons`));
      box.appendChild(item);
    } else {
      profiles.forEach((p, i) => {
        if (profiles.length > 1 && states[i] === "live") return;
        const item = el("span", "conn-item conn-" + states[i]);
        item.appendChild(el("span", "conn-dot"));
        const text = profiles.length > 1 ? `${p.name} ${CONN_TEXT[states[i]]}`
          : CONN_TEXT[states[i]].replace(/^./, (ch) => ch.toUpperCase());
        item.appendChild(document.createTextNode(text));
        box.appendChild(item);
      });
    }
    if (states.some((st) => st === "offline" || st === "reconnecting")) {
      const retry = el("button", "conn-retry", "Retry");
      retry.type = "button";
      retry.addEventListener("click", () => { reconnectNow(); refreshSessions(); });
      box.appendChild(retry);
    }
    if (state.open) renderChatSub();
  }
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
  // One action at a time: every footer button is dead while one is running.
  let busy = false;
  const buttons = [];
  actions.forEach((a) => {
    const b = el("button", a.primary ? "primary" : (a.danger ? "danger" : null), a.label);
    b.type = "button";
    if (a.id) b.id = a.id;
    if (a.disabled) b.disabled = true;
    b.addEventListener("click", async () => {
      // An async action keeps the modal on screen until it resolves — over a
      // tunnel that is a second or more. Without this guard a second tap on
      // Start ran the whole action again: two POST /api/sessions/new, two
      // sessions, one prompt (codex 01a1e3ad-4265 and -468a, 1.03s apart).
      if (busy) return;
      busy = true;
      buttons.forEach((x) => { x.disabled = true; });
      try {
        if (a.run) await a.run(b);
      } finally {
        busy = false;
        // Leave them disabled when the modal is closing: re-enabling paints
        // a live-looking footer for the frame before it hides.
        if (a.close === false) buttons.forEach((x, i) => {
          x.disabled = !!actions[i].disabled;
        });
      }
      if (a.close !== false) closeModal();
    });
    buttons.push(b);
    foot.appendChild(b);
  });
  // Remember where focus was the first time the dialog opens (a dialog that
  // repaints itself must not overwrite that with one of its own buttons).
  if (host.classList.contains("hidden")) {
    const active = document.activeElement;
    modalReturnFocus = active && !host.contains(active) ? active : null;
  }
  host.classList.remove("hidden");
  host.querySelector(".modal-card").style.width = wide ? "min(900px, 100%)" : "";
  if (!host.contains(document.activeElement)) {
    const first = body.querySelector(MODAL_FOCUSABLE)
      || foot.querySelector(".primary:not(:disabled)")
      || $("modal-close");
    if (first) first.focus({ preventScroll: true });
  }
  return { body, foot };
}

let modalReturnFocus = null;
const MODAL_FOCUSABLE = 'button:not(:disabled), [href], input:not(:disabled), '
  + 'select:not(:disabled), textarea:not(:disabled), [tabindex]:not([tabindex="-1"])';

/** Keep Tab inside the open dialog (it is aria-modal). */
function trapModalTab(e) {
  if (e.key !== "Tab") return;
  const host = $("modal");
  if (host.classList.contains("hidden")) return;
  const items = [...host.querySelectorAll(MODAL_FOCUSABLE)]
    .filter((n) => n.offsetParent !== null);
  if (!items.length) return;
  const first = items[0];
  const last = items[items.length - 1];
  if (!host.contains(document.activeElement)) {
    e.preventDefault(); first.focus();
  } else if (e.shiftKey && document.activeElement === first) {
    e.preventDefault(); last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault(); first.focus();
  }
}

function closeModal() {
  const wasOpen = !$("modal").classList.contains("hidden");
  $("modal").classList.add("hidden");
  if (wasOpen && modalReturnFocus && document.contains(modalReturnFocus)) {
    modalReturnFocus.focus({ preventScroll: true });
  }
  modalReturnFocus = null;
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
          "One agentremoted host can front Claude, Grok, Codex and DeepSeek at once — add the host once, then pick the harness when you start a session. Add a second profile only for another machine."));
      }
    },
    actions: [
      { label: "Add daemon", primary: true, close: false, run: () => editProfile(-1) },
      { label: "Done", close: true },
    ],
  });
}

/**
 * The pairing payload the phones scan: one daemon, spelled out.
 *
 * Same scheme the BlackBerry app already answers to for voice links, so a
 * profile QR is just another agentremote:// address — nothing proprietary,
 * and anyone can read what they are about to scan.
 */
function pairingUri({ name, baseUrl, token }) {
  const url = normalizeBaseUrl(baseUrl);
  const tok = String(token || "").trim();
  if (!url || !tok) return "";
  const q = ["url=" + encodeURIComponent(url), "token=" + encodeURIComponent(tok)];
  const label = String(name || "").trim();
  if (label) q.push("name=" + encodeURIComponent(label));
  return "agentremote://profile?" + q.join("&");
}

function editProfile(index) {
  const existing = index >= 0 ? state.profiles[index] : null;
  let testLine;
  modal({
    title: existing ? "Edit daemon" : "Add daemon",
    build(body) {
      // A profile is useless without a daemon; say what one is and where
      // to get it before asking for its address.
      const intro = el("div", "help");
      intro.innerHTML = "This page is a remote control for <strong>agentremoted</strong>, "
        + "a small daemon that runs on the computer where your coding agents live. "
        + "Enter that daemon's address and its token. "
        + '<a href="' + DAEMON_REPO_URL + '" target="_blank" rel="noopener noreferrer">'
        + "Get the daemon and read the docs</a>.";
      body.appendChild(intro);
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
        "http:// is assumed; add https:// for a TLS daemon. "
        + "From a phone off your LAN, tunnel localhost with Cloudflare: "
        + "cloudflared tunnel --url http://localhost:8473 "
        + "— paste the https://….trycloudflare.com URL here.");
      url.placeholder = "192.168.1.20:8473  or  https://….trycloudflare.com";
      const token = mk("Token", existing && existing.token,
        "The contents of ~/.agentremoted/token on that host", "password");
      testLine = el("div", "help");
      body.appendChild(testLine);

      // Pairing code: typing a 40-character token on a phone keyboard is the
      // worst part of setting one up, so let its camera do it. Redrawn as
      // the fields change — the code always matches what is on screen, even
      // for a daemon that has not been saved yet.
      const qr = el("div", "qrpair");
      const paintQr = () => {
        qr.textContent = "";
        const uri = pairingUri({
          name: name.value, baseUrl: url.value, token: token.value,
        });
        if (!uri) {
          qr.appendChild(el("div", "help",
            "Fill in the address and the token and a pairing code appears here."));
          return;
        }
        const art = el("div", "qrart");
        art.innerHTML = qrSvg(uri, { ec: "M" });
        qr.appendChild(art);
        qr.appendChild(el("div", "help",
          "Scan this from Agent Remote on Android or BlackBerry (Add profile → "
          + "Scan QR) to copy this daemon across. It carries the token, so treat "
          + "the picture as the token itself."));
      };
      [name, url, token].forEach((f) => f.addEventListener("input", paintQr));
      paintQr();
      body.appendChild(qr);

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
          showWelcomeIfNeeded();
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
            let authLine = "";
            if (ping.auth && ping.auth.detail) {
              authLine = `\nAuth: ${ping.auth.detail}`;
            } else if (ping.provider_details) {
              const bits = Object.entries(ping.provider_details).map(([n, d]) => {
                const a = d && d.auth;
                return a ? `${n}:${a.status || "?"}` : null;
              }).filter(Boolean);
              if (bits.length) authLine = `\nAuth: ${bits.join(" · ")}`;
            }
            testLine.textContent =
              `${labels} on ${ping.host || "the daemon"} · agentremoted ${ping.version}${authLine}`;
            const st = (ping.auth && ping.auth.status) || "";
            testLine.style.color = (st === "missing" || st === "expired")
              ? "var(--warn)" : "var(--ok)";
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
          showWelcomeIfNeeded();
          paintProfilesModal();
        },
      },
    ],
  });
}

function normalizeBaseUrl(raw) {
  let url = String(raw || "").trim().replace(/\/+$/, "");
  if (url && !/^https?:\/\//i.test(url)) url = "http://" + url;
  // Strip accidental /claude|/grok|/codex suffixes — multi lives at the root.
  return url.replace(/\/(claude|grok|codex|deepseek|dsh|cursor|cursor-agent|copilot|codebuddy|cbc)$/i, "");
}

function normalizeProfile(p) {
  const url = normalizeBaseUrl(p.baseUrl);
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
    // Ping flags survive a reload: a boot ping that fails or is still in
    // flight must not make a 2.20 daemon look like it lacks the feature.
    focus: !!p.focus,
    share: !!p.share,
    linkedFiles: !!p.linkedFiles,
    tuiStream: !!p.tuiStream,
    chunkedUpload: !!p.chunkedUpload,
    ...(typeof p.maxUploadMb === "number" && p.maxUploadMb > 0
      ? { maxUploadMb: p.maxUploadMb } : {}),
    execMode: normalizeExecMode(p.execMode || ""),
    model: p.model || "",
    effort: p.effort || "",
    modelByHarness: (p.modelByHarness && typeof p.modelByHarness === "object")
      ? { ...p.modelByHarness } : {},
    effortByHarness: (p.effortByHarness && typeof p.effortByHarness === "object")
      ? { ...p.effortByHarness } : {},
  };
}

// ----------------------------------------------------------- new session

function openNewSession() {
  const candidates = enabledProfiles();
  if (!candidates.length) { openProfiles(); return; }
  let picked = candidates.find((p) => state.open && p.id === state.open.profileId) || candidates[0];
  let harness = (harnessesOf(picked)[0] || picked.provider || "claude");
  let projects = [];

  // What the user typed, kept OUTSIDE the DOM. Every pill (model, effort,
  // execution, harness) re-renders the whole modal body, and so does the
  // /api/projects reply — rebuilding the inputs. Without this, picking a
  // model threw away a half-written first message, and a slow project list
  // wiped whatever was typed while it loaded.
  let draftCwd = "";
  let draftPrompt = "";
  let live = null;   // the inputs of the generation currently on screen

  /** Pull the on-screen values into the draft before the body is rebuilt. */
  const keepDraft = () => {
    if (!live) return;
    if (live.cwd && live.cwd.isConnected) draftCwd = live.cwd.value;
    if (live.prompt && live.prompt.isConnected) draftPrompt = live.prompt.value;
  };

  // clearCwd: switching daemon host — the old path belongs to the old
  // machine, so it goes; the first message is the user's own words and stays.
  const render = ({ clearCwd = false } = {}) => {
    keepDraft();
    if (clearCwd) draftCwd = "";
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
              if (p.id === picked.id) return;
              picked = p;
              harness = harnessesOf(picked)[0] || picked.provider || "claude";
              projects = [];
              // Another machine: its folders are not this one's.
              render({ clearCwd: true });
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
        cwd.value = draftCwd;
        cwd.addEventListener("input", () => { draftCwd = cwd.value; });
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
          b.addEventListener("click", () => {
            cwd.value = proj.cwd;
            draftCwd = proj.cwd;
          });
          plist.appendChild(b);
        });
        body.appendChild(plist);

        const pf = el("div", "field");
        pf.style.marginTop = "14px";
        pf.appendChild(el("label", null, "First message"));
        const prompt = el("textarea");
        prompt.rows = 5;
        prompt.value = draftPrompt;
        prompt.addEventListener("input", () => { draftPrompt = prompt.value; });
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

        // Model / effort for THIS harness only (never bleed Claude → Codex).
        const models = modelsOf(picked, harness);
        if (capOf(picked, "can_set_model", true, harness) && models.length) {
          const mf = el("div", "field");
          mf.style.marginTop = "10px";
          mf.appendChild(el("label", null, "Model"));
          const mbar = el("div", "pillbar");
          const cur = modelOf(picked, harness);
          models.slice(0, 12).forEach((v) => {
            const b = el("button", "pill", v);
            b.type = "button";
            b.setAttribute("aria-pressed", String(v === cur));
            b.addEventListener("click", () => {
              setModelFor(picked, harness, v);
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
          const curE = effortOf(picked, harness);
          efforts.forEach((v) => {
            const b = el("button", "pill", v);
            b.type = "button";
            b.setAttribute("aria-pressed", String(v === curE));
            b.addEventListener("click", () => {
              setEffortFor(picked, harness, v);
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
        // Snapshot source for the next re-render (covers paste, autofill and
        // anything else that never fires 'input').
        live = { cwd, prompt };
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
                // Coerce to a model valid for this harness (not the last Claude pick).
                model: modelOf(p, h),
                effort: effortOf(p, h),
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

  // Is this form still the thing on screen? modal() un-hides the dialog, so
  // re-rendering after a Cancel would resurrect a dismissed modal (or stomp
  // whatever the user opened instead).
  const stillOpen = () => !$("modal").classList.contains("hidden")
    && !!live && !!live.prompt && live.prompt.isConnected;

  const loadProjects = async () => {
    // Which host we asked — a second daemon click while this is in flight
    // must not paint the old machine's folders under the new selection.
    const forId = picked.id;
    try {
      const data = await call(picked, "/api/projects", { timeout: 20000 });
      if (forId !== picked.id || !stillOpen()) return;
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
        // openSession already adopts a matching active job; re-pin explicitly
        // with this session id so live events cannot land on another open row.
        attachJob(jobId, sid);
      }
      return;
    }
    if (["done", "error", "stopped"].includes(snap.status)) return;
  }
}

// ------------------------------------------------------ permission / ask

// The open session's pending permission or question, shown as a card docked
// above the composer. It used to be a dialog raised on arrival, and the page
// has one dialog: a permission arriving while you filled in New session or
// edited a daemon replaced that form. A card never takes anything over, can
// be hidden without cancelling the ask, and keeps your picks across the
// poll's repaints because it is only rebuilt when the request id changes.

const gate = {
  key: "",          // "p:<request_id>" | "q:<request_id>" | ""
  kind: "",         // "permission" | "question" | "plan"
  pending: null,
  picks: [],
  notes: [],
  collapsed: false,
  busy: false,
};

const isPlanQuestion = (questions) => questions.some((q) =>
  (q.header || "").toLowerCase().includes("plan")
  || (q.question || "").length > 800);

function clearGate() {
  if (!gate.key && $("gate").classList.contains("hidden")) return;
  gate.key = "";
  gate.kind = "";
  gate.pending = null;
  gate.busy = false;
  const box = $("gate");
  const pinned = nearBottom($("transcript"));
  box.textContent = "";
  box.classList.add("hidden");
  if (pinned) $("transcript").scrollTop = $("transcript").scrollHeight;
  syncJumpPill();
}

/** Drop the card once the daemon no longer has that ask pending. */
function syncGate(job) {
  if (!gate.key) return;
  const perm = job && job.pendingPermission;
  const q = job && job.pendingQuestion;
  const live = (gate.kind === "permission")
    ? perm && `p:${perm.request_id}` === gate.key
    : q && `q:${q.request_id}` === gate.key
      && !state.answeredQuestions.has(q.request_id);
  if (!live && !gate.busy) clearGate();
}

function gateButton(label, cls, run) {
  const b = el("button", cls, label);
  b.type = "button";
  b.addEventListener("click", async () => {
    if (gate.busy) return;
    gate.busy = true;
    $("gate").querySelectorAll(".gate-foot button").forEach((x) => { x.disabled = true; });
    try { await run(); } finally { gate.busy = false; }
  });
  return b;
}

function renderGate({ focus = false } = {}) {
  const box = $("gate");
  const pending = gate.pending;
  if (!gate.key || !pending) { clearGate(); return; }
  const hadFocus = box.contains(document.activeElement);
  // The card takes height from the transcript: someone reading the latest
  // output stays on the latest output.
  const pinned = nearBottom($("transcript"));
  box.textContent = "";
  box.classList.remove("hidden");
  box.classList.toggle("collapsed", gate.collapsed);
  const inner = el("div", "gate-inner");

  const head = el("div", "gate-head");
  head.appendChild(el("span", "gate-dot"));
  const questions = pending.questions || [];
  const title = gate.kind === "permission"
    ? `Allow ${pending.tool_name || "this tool"}?`
    : gate.kind === "plan"
      ? (questions[0].header || "Plan ready for review")
      : (questions.length === 1
        ? (questions[0].header || "The agent is asking")
        : `The agent is asking ${questions.length} things`);
  head.appendChild(el("div", "gate-title", title));
  const toggle = el("button", "ghost gate-toggle", gate.collapsed ? "Show" : "Hide");
  toggle.type = "button";
  toggle.setAttribute("aria-expanded", String(!gate.collapsed));
  toggle.title = gate.collapsed
    ? "Show what is being asked"
    : "Hide for now — this does not answer or cancel it";
  toggle.addEventListener("click", () => {
    gate.collapsed = !gate.collapsed;
    renderGate({ focus: true });
  });
  head.appendChild(toggle);
  inner.appendChild(head);

  const body = el("div", "gate-body");
  const foot = el("div", "gate-foot");
  const profile = () => profileById(state.open.profileId);

  if (gate.kind === "permission") {
    if (pending.detail) body.appendChild(el("pre", null, pending.detail));
    const answer = async (allow) => {
      try {
        await call(profile(), `/api/jobs/${state.job.id}/permission`,
          { method: "POST", body: { request_id: pending.request_id, allow } });
        state.answeredQuestions.add(`p:${pending.request_id}`);
        clearGate();
      } catch (e) { toast(e.message); renderGate(); }
    };
    foot.appendChild(el("div", "help", "The turn is paused until you answer."));
    foot.appendChild(gateButton("Deny", "danger", () => answer(false)));
    foot.appendChild(gateButton("Allow", "primary", () => answer(true)));
  } else if (gate.kind === "plan") {
    body.appendChild(el("div", "help",
      "The agent wrote a plan and is waiting for you to approve it, ask for changes or quit."));
    const open = el("button", "primary", "Review plan");
    open.type = "button";
    open.addEventListener("click", () => showPlanModal(pending));
    foot.appendChild(open);
  } else {
    questions.forEach((q, qi) => {
      const block = el("div", "q-block");
      if (q.header && questions.length > 1) block.appendChild(el("div", "q-head", q.header));
      if (q.question) {
        const text = el("div", "md");
        text.appendChild(renderMarkdown(q.question));
        block.appendChild(text);
      }
      if (q.multi_select) block.appendChild(el("div", "help", "Pick as many as apply"));
      (q.options || []).forEach((opt) => {
        const active = gate.picks[qi].includes(opt.label);
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
            const at = gate.picks[qi].indexOf(opt.label);
            if (at >= 0) gate.picks[qi].splice(at, 1); else gate.picks[qi].push(opt.label);
          } else {
            gate.picks[qi] = [opt.label];
          }
          const top = body.scrollTop;
          renderGate({ focus: true });
          const nb = $("gate").querySelector(".gate-body");
          if (nb) nb.scrollTop = top;
        });
        block.appendChild(b);
        // Some options take free text with the pick (grok's "Request
        // changes" becomes the revision note it then waits for).
        if (q.note_for && q.note_for === opt.label && active) {
          const input = el("input");
          input.placeholder = q.note_hint || "Your answer";
          input.value = gate.notes[qi];
          input.addEventListener("input", () => { gate.notes[qi] = input.value; });
          block.appendChild(input);
        }
      });
      body.appendChild(block);
    });
    const settle = async (payload) => {
      try {
        await call(profile(), `/api/jobs/${state.job.id}/question`,
          { method: "POST", body: { request_id: pending.request_id, ...payload } });
        // Optimistically dismiss so a lagging poll cannot re-open this id.
        state.answeredQuestions.add(pending.request_id);
        state.askedQuestion = pending.request_id;
        if (state.job) {
          state.job.pendingQuestion = null;
          state.job.lastPendingQuestion = null;
        }
        clearGate();
        renderBanner();
      } catch (e) { toast(e.message); renderGate(); }
    };
    foot.appendChild(gateButton("Cancel", null, () => settle({ cancel: true })));
    const send = gateButton("Send answer", "primary",
      () => settle({ answers: gate.picks, notes: gate.notes }));
    send.disabled = gate.picks.some((p) => !p.length);
    foot.appendChild(send);
  }
  inner.appendChild(body);
  inner.appendChild(foot);
  box.appendChild(inner);
  if (focus || hadFocus) {
    const target = gate.collapsed ? toggle
      : (box.querySelector('.q-opt[aria-pressed="true"]')
        || box.querySelector(".gate-foot .primary:not(:disabled)")
        || box.querySelector(".q-opt") || toggle);
    if (target) target.focus({ preventScroll: true });
  }
  if (pinned) $("transcript").scrollTop = $("transcript").scrollHeight;
  syncJumpPill();
}

/** force = the banner's Respond / Answer: bring a hidden card back. */
function showPermission(pending, { force = false } = {}) {
  if (!pending) return;
  const key = `p:${pending.request_id}`;
  if (gate.key === key && !force) return;   // poll tick: already showing
  const same = gate.key === key;
  gate.key = key;
  gate.kind = "permission";
  gate.pending = pending;
  if (!same || force) gate.collapsed = false;
  renderGate({ focus: force });
}

function showQuestion(pending, { force = false } = {}) {
  if (!pending) return;
  const questions = pending.questions || [];
  if (!questions.length) return;
  const key = `q:${pending.request_id}`;
  const plan = isPlanQuestion(questions);
  if (gate.key === key && !force) return;
  if (gate.key !== key) {
    gate.key = key;
    gate.kind = plan ? "plan" : "question";
    gate.pending = pending;
    gate.picks = questions.map(() => []);
    gate.notes = questions.map(() => "");
  }
  gate.collapsed = false;
  renderGate({ focus: force });
  // A plan is pages long and is read in a dialog, but only when asked for:
  // raising it on arrival is what used to replace other dialogs.
  if (plan && force) showPlanModal(pending);
}

function showPlanModal(pending) {
  if (!pending) return;
  const questions = pending.questions || [];
  if (!questions.length) return;
  const picks = questions.map(() => []);
  const notes = questions.map(() => "");
  const isPlan = questions.some((q) =>
    (q.header || "").toLowerCase().includes("plan")
    || (q.question || "").length > 800);

  const render = () => {
    modal({
    title: isPlan
      ? (questions[0].header || "Review plan")
      : (questions.length === 1 ? "The agent is asking"
                                : `The agent is asking ${questions.length} things`),
    wide: true,
    build(body) {
      if (isPlan) {
        body.classList.add("plan-review");
        body.appendChild(el("div", "help",
          "Read the plan below, then choose Approve / Request changes / Quit. Closing this dialog does not cancel — use Answer in the banner to reopen. Live TUI also has the host pane."));
      } else {
        body.appendChild(el("div", "help",
          "The turn is paused until you answer or cancel. Closing this dialog does not cancel — use Answer in the banner to reopen."));
      }
      questions.forEach((q, qi) => {
        const block = el("div", "q-block" + (isPlan ? " q-plan" : ""));
        if (q.header && !isPlan) block.appendChild(el("div", "q-head", q.header));
        // Plan body first (scrollable), options sticky below.
        const bodyBox = el("div", isPlan ? "q-plan-body md" : null);
        if (q.question) {
          bodyBox.appendChild(renderMarkdown(q.question));
          const t = q.question.trimEnd();
          if (t.endsWith("…") || t.endsWith("...")) {
            bodyBox.appendChild(el("div", "help",
              "Plan text may be truncated here — open Live TUI (terminal icon) for the full host pane, or re-ask after the daemon upgrade that raises the plan size limit."));
          }
        }
        block.appendChild(bodyBox);
        if (q.multi_select) block.appendChild(el("div", "help", "Pick as many as apply"));
        const opts = el("div", isPlan ? "q-plan-opts" : null);
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
          opts.appendChild(b);
          // Some options take free text with the pick (grok's "Request
          // changes" becomes the revision note it then waits for).
          if (q.note_for && q.note_for === opt.label && active) {
            const input = el("input");
            input.placeholder = q.note_hint || "Your answer";
            input.value = notes[qi];
            input.addEventListener("input", () => { notes[qi] = input.value; });
            opts.appendChild(input);
          }
        });
        block.appendChild(opts);
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
            state.answeredQuestions.add(pending.request_id);
            state.askedQuestion = pending.request_id;
            if (state.job) {
              state.job.pendingQuestion = null;
              state.job.lastPendingQuestion = null;
            }
            renderBanner();
            clearGate();
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
            // Optimistically dismiss so a lagging poll cannot re-open this id.
            state.answeredQuestions.add(pending.request_id);
            state.askedQuestion = pending.request_id;
            if (state.job) {
              state.job.pendingQuestion = null;
              state.job.lastPendingQuestion = null;
            }
            renderBanner();
            clearGate();
          } catch (e) { toast(e.message); }
        },
      },
    ],
  });
    // Wider card for plan review so markdown tables stay readable.
    if (isPlan) {
      const card = $("modal") && $("modal").querySelector(".modal-card");
      if (card) card.style.width = "min(960px, 100%)";
    }
  };
  render();
}

// ------------------------------------------------------------- attachments
// Same contract as the phone apps: POST the raw bytes to /api/attachments,
// then reference the host path in the prompt so the agent can open the file.

/**
 * Shrink large camera photos before upload. Returns { blob, name, note }.
 * Non-images and small images pass through unchanged.
 */
async function prepareUploadBlob(file) {
  const type = (file.type || "").toLowerCase();
  const isImage = type.startsWith("image/")
    || /\.(jpe?g|png|webp|heic|heif|gif|bmp)$/i.test(file.name || "");
  if (!isImage || file.size < IMAGE_UPLOAD_COMPRESS_MIN) {
    return { blob: file, name: file.name || "file", note: "" };
  }
  // HEIC often cannot decode in browser canvas — fall through raw.
  if (type.includes("heic") || type.includes("heif")
      || /\.heic$/i.test(file.name || "") || /\.heif$/i.test(file.name || "")) {
    return { blob: file, name: file.name || "file", note: "" };
  }
  try {
    const bmp = await createImageBitmap(file);
    let w = bmp.width;
    let h = bmp.height;
    const edge = Math.max(w, h);
    if (edge > IMAGE_UPLOAD_MAX_EDGE) {
      const scale = IMAGE_UPLOAD_MAX_EDGE / edge;
      w = Math.max(1, Math.round(w * scale));
      h = Math.max(1, Math.round(h * scale));
    }
    const canvas = document.createElement("canvas");
    canvas.width = w;
    canvas.height = h;
    const ctx = canvas.getContext("2d");
    if (!ctx) {
      bmp.close();
      return { blob: file, name: file.name || "file", note: "" };
    }
    ctx.drawImage(bmp, 0, 0, w, h);
    bmp.close();
    const blob = await new Promise((resolve) => {
      canvas.toBlob(
        (b) => resolve(b),
        "image/jpeg",
        IMAGE_UPLOAD_JPEG_QUALITY,
      );
    });
    if (!blob || blob.size <= 0 || blob.size >= file.size * 0.95) {
      // Compression did not help (tiny PNG icon, already-small JPEG).
      return { blob: file, name: file.name || "file", note: "" };
    }
    const base = (file.name || "image").replace(/\.[^.]+$/, "") || "image";
    return {
      blob,
      name: base + ".jpg",
      note: `compressed ${humanSize(file.size)} → ${humanSize(blob.size)}`,
    };
  } catch {
    return { blob: file, name: file.name || "file", note: "" };
  }
}

/**
 * POST with upload progress (fetch has no upload progress events).
 */
function postAttachment(url, token, blob, onProgress, signal) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("X-Auth-Token", token);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.responseType = "text";
    xhr.timeout = 120000;
    if (signal) {
      if (signal.aborted) {
        reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
        return;
      }
      signal.addEventListener("abort", () => xhr.abort(), { once: true });
    }
    xhr.upload.onprogress = (ev) => {
      if (onProgress && ev.lengthComputable) {
        onProgress(ev.loaded, ev.total);
      }
    };
    xhr.onload = () => {
      let data = null;
      try { data = JSON.parse(xhr.responseText || "{}"); } catch { /* */ }
      resolve({ status: xhr.status, data });
    };
    xhr.onerror = () => reject(new Error("Network error during upload"));
    xhr.ontimeout = () => reject(Object.assign(new Error("Upload timed out"), { name: "AbortError" }));
    xhr.onabort = () => reject(Object.assign(new Error("aborted"), { name: "AbortError" }));
    xhr.send(blob);
  });
}

function uploadId() {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === "function") {
    return c.randomUUID().replace(/-/g, "");
  }
  let s = "";
  for (let i = 0; i < 32; i++) s += Math.floor(Math.random() * 16).toString(16);
  return s;
}

function sleepMs(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

async function postAttachmentRetry(url, token, blob, onProgress, signal, tries = 4) {
  let last = null;
  for (let attempt = 0; attempt < tries; attempt++) {
    if (signal && signal.aborted) {
      throw Object.assign(new Error("aborted"), { name: "AbortError" });
    }
    try {
      const res = await postAttachment(url, token, blob, onProgress, signal);
      if (res.status >= 200 && res.status < 300) return res;
      if (res.status >= 400 && res.status < 500 && res.status !== 408) {
        throw new DaemonError(res.status,
          (res.data && res.data.error) || (res.status === 413
            ? "Attachment too large" : `HTTP ${res.status}`));
      }
      last = new DaemonError(res.status,
        (res.data && res.data.error) || `HTTP ${res.status}`);
    } catch (e) {
      if (e instanceof DaemonError && e.status >= 400 && e.status < 500 && e.status !== 408) {
        throw e;
      }
      last = e;
    }
    await sleepMs(400 * (attempt + 1) * (attempt + 1));
  }
  throw last || new Error("Network error during upload");
}

function attachPathToPrompt(path) {
  const prompt = $("prompt");
  const sep = prompt.value && !/\s$/.test(prompt.value) ? " " : "";
  prompt.value = prompt.value + sep + "[attached: " + path + "]";
  prompt.dispatchEvent(new Event("input"));
  prompt.focus();
}

async function uploadBlobSingle(profile, name, blob, onProgress, signal) {
  const url = profile.baseUrl.replace(/\/+$/, "")
    + "/api/attachments?name=" + encodeURIComponent(name);
  const { status, data } = await postAttachmentRetry(
    url, profile.token, blob, onProgress, signal);
  if (status < 200 || status >= 300) {
    throw new DaemonError(status,
      (data && data.error) || (status === 413
        ? "Attachment too large" : `HTTP ${status}`));
  }
  const path = data && data.path;
  if (!path) throw new DaemonError(0, "Daemon did not return a path");
  return { path, size: (data && data.size) || blob.size };
}

async function uploadBlobChunked(profile, name, blob, onProgress, signal) {
  const totalBytes = blob.size;
  const chunkSize = UPLOAD_CHUNK_BYTES;
  const total = Math.max(1, Math.ceil(totalBytes / chunkSize));
  const id = uploadId();
  let sent = 0;
  let path = "";
  let size = totalBytes;
  for (let index = 0; index < total; index++) {
    const start = index * chunkSize;
    const end = Math.min(totalBytes, start + chunkSize);
    const piece = blob.slice(start, end);
    const url = profile.baseUrl.replace(/\/+$/, "")
      + "/api/attachments?name=" + encodeURIComponent(name)
      + "&upload_id=" + encodeURIComponent(id)
      + "&index=" + index
      + "&total=" + total;
    const { data } = await postAttachmentRetry(
      url,
      profile.token,
      piece,
      (loaded, pieceTotal) => {
        const n = sent + loaded;
        if (onProgress) onProgress(n, totalBytes);
      },
      signal,
    );
    sent = end;
    if (onProgress) onProgress(sent, totalBytes);
    if (data && data.complete && data.path) {
      path = data.path;
      size = data.size || size;
    }
  }
  if (!path) throw new DaemonError(0, "Daemon did not return a path");
  return { path, size };
}

async function uploadAttachment(file) {
  if (!state.open || !file) return;
  const profile = profileById(state.open.profileId);
  if (!profile) return;
  if (file.size <= 0) { toast("Empty file"); return; }
  const maxBytes = (Number(profile.maxUploadMb) > 0
    ? Number(profile.maxUploadMb) : 16) * 1024 * 1024;
  if (file.size > maxBytes) {
    toast(`${file.name} is too large (max ${humanSize(maxBytes)})`);
    return;
  }
  const originalName = file.name || "file";
  composerNote(`Preparing ${originalName}…`);
  const prepared = await prepareUploadBlob(file);
  if (prepared.blob.size > maxBytes) {
    toast(`${originalName} is too large after prepare (max ${humanSize(maxBytes)})`);
    return;
  }
  const name = prepared.name;
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 15 * 60 * 1000);
  const chunked = !!profile.chunkedUpload && prepared.blob.size > UPLOAD_CHUNK_BYTES;
  try {
    if (prepared.note) composerNote(`Uploading ${name} (${prepared.note})…`);
    else composerNote(`Uploading ${name} (${humanSize(prepared.blob.size)})…`);
    const onProgress = (loaded, total) => {
      const pct = total ? Math.min(99, Math.round((loaded / total) * 100)) : 0;
      composerNote(`Uploading ${name}… ${pct}%`);
    };
    const result = chunked
      ? await uploadBlobChunked(profile, name, prepared.blob, onProgress, ctrl.signal)
      : await uploadBlobSingle(profile, name, prepared.blob, onProgress, ctrl.signal);
    attachPathToPrompt(result.path);
    const saved = humanSize(result.size || prepared.blob.size);
    composerNote(prepared.note
      ? `Attached ${name} (${saved}, ${prepared.note})`
      : `Attached ${name} (${saved})`);
  } catch (e) {
    if (e.name === "AbortError") composerNote("Upload timed out", true);
    else composerNote(e.message || "Upload failed", true);
  } finally {
    clearTimeout(timer);
  }
}

async function uploadFiles(fileList) {
  const files = [...(fileList || [])].filter(Boolean);
  // Sequential: CF tunnel + phone radio prefer one stream; also keeps
  // composer notes readable. Parallel would thrash bandwidth.
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
        const isDir = row.file.type === "dir";
        col.appendChild(el("div", "fname", (isDir ? "📁 " : "") + row.file.name));
        const meta = el("div", "fmeta");
        const pal = providerOf(row.profile.provider);
        const tag = el("span", "tag provider", row.profile.name);
        tag.style.setProperty("--tag", pal.accent);
        meta.appendChild(tag);
        if (isDir) {
          // Folder size is what it weighs on the host; the zip that arrives
          // is smaller, so label it rather than passing it off as a filesize.
          const n = row.file.entries || 0;
          meta.appendChild(el("span", "tag",
            `${n}${row.file.partial ? "+" : ""} file${n === 1 ? "" : "s"}`));
        }
        meta.appendChild(el("span", "tag", humanSize(row.file.size)));
        meta.appendChild(el("span", "tag", stamp((row.file.mtime || 0) * 1000)));
        col.appendChild(meta);
        if (row.also.length) {
          col.appendChild(el("div", "also", `Identical copy on ${row.also.join(", ")}`));
        }
        line.appendChild(col);

        const dlLabel = isDir ? "Download zip" : "Download";
        const dl = el("button", null, dlLabel);
        dl.type = "button";
        dl.addEventListener("click", async () => {
          dl.disabled = true;
          // Zipping happens host-side before a byte moves, so a big folder
          // sits on "…" with no progress — say what it is doing.
          dl.textContent = isDir ? "Zipping…" : "…";
          const saveAs = isDir ? `${row.file.name}.zip` : row.file.name;
          let sink = null;
          let handle = null;
          try {
            // Big files go straight to disk where the browser lets us pick
            // the destination (Chromium); elsewhere the Blob path is used.
            // The picker must open first, while the click still counts.
            if (window.showSaveFilePicker && (row.file.size || 0) > 64 * 1024 * 1024) {
              try {
                handle = await window.showSaveFilePicker({ suggestedName: saveAs });
                sink = await handle.createWritable();
              } catch (e) {
                if (e && e.name === "AbortError") { dl.textContent = dlLabel; dl.disabled = false; return; }
                sink = null;
              }
            }
            const got = await downloadDrop(row.profile, row.file.name, {
              sink,
              onProgress: (n, total) => {
                dl.textContent = total
                  ? `${Math.floor((n * 100) / total)}%`
                  : `${(n / (1024 * 1024)).toFixed(0)} MB`;
              },
            });
            if (sink) {
              await sink.close();
            } else {
              const url = URL.createObjectURL(got.blob);
              const a = el("a");
              a.href = url;
              a.download = got.name || saveAs;
              a.click();
              setTimeout(() => URL.revokeObjectURL(url), 60000);
            }
            dl.textContent = "Saved";
          } catch (e) {
            if (sink) { try { await sink.abort(); } catch { /* already closed */ } }
            toast(e.message);
            dl.textContent = dlLabel;
          }
          dl.disabled = false;
        });
        line.appendChild(dl);

        const del = el("button", "danger", isDir ? "Delete folder" : "Delete");
        del.type = "button";
        del.addEventListener("click", async () => {
          // Files too: it is gone from the host for good, one click away
          // from Download.
          if (!window.confirm(isDir
            ? `Delete folder “${row.file.name}” on the host? All files inside are removed.`
            : `Delete “${row.file.name}” on the host? It cannot be brought back.`)) {
            return;
          }
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

/** Dedup key: same harness seat across hosts → one Usage row. */
function usageIdentityKey(provider, accountId, account, hostFallback) {
  const p = String(provider || "").toLowerCase().trim();
  const id = String(accountId || account || "").toLowerCase().trim();
  if (p && id) return `${p}|${id}`;
  // No account yet (still loading / unknown) — keep hosts separate until known.
  return `${p}|host:${hostFallback || "?"}`;
}

function cleanUsageBuckets(buckets, provider) {
  const label = providerOf(provider).label || provider || "";
  const prefix = label ? `${label} · ` : "";
  return (buckets || []).map((b) => {
    const t = String(b.title || "");
    if (prefix && t.startsWith(prefix)) return { ...b, title: t.slice(prefix.length) };
    return b;
  });
}

/**
 * Merge per-host usage into one list keyed by (provider, account).
 * Same Claude/Codex/Grok login on Mac + VPS appears once; hosts listed under.
 */
function mergeUsageResults(results) {
  const map = new Map(); // key -> entry
  const order = [];
  const push = (entry) => {
    const key = usageIdentityKey(
      entry.provider, entry.accountId, entry.account, entry.hosts[0] || "");
    const prev = map.get(key);
    if (!prev) {
      map.set(key, entry);
      order.push(key);
      return;
    }
    // Merge hosts; prefer the payload that actually has bars.
    for (const h of entry.hosts) {
      if (h && !prev.hosts.includes(h)) prev.hosts.push(h);
    }
    if ((!prev.buckets || !prev.buckets.length) && entry.buckets && entry.buckets.length) {
      prev.buckets = entry.buckets;
      prev.error = entry.error || "";
      prev.ok = entry.ok;
    } else if (entry.ok && entry.buckets && entry.buckets.length) {
      // Both have data — keep higher max% (more recent burn) as a simple pick.
      const maxOf = (bs) => Math.max(0, ...bs.map((b) => Number(b.percent) || 0), 0);
      if (maxOf(entry.buckets) >= maxOf(prev.buckets || [])) {
        prev.buckets = entry.buckets;
        prev.error = entry.error || prev.error || "";
      }
    } else if (!prev.ok && entry.error) {
      prev.error = prev.error || entry.error;
    }
    if (!prev.account && entry.account) prev.account = entry.account;
    if (!prev.accountId && entry.accountId) prev.accountId = entry.accountId;
  };

  results.forEach((r) => {
    const host = (r.profile && (r.profile.name || r.profile.baseUrl)) || "host";
    if (r.loading) {
      push({
        provider: r.profile.provider || "agent",
        account: "",
        accountId: "",
        hosts: [host],
        buckets: [],
        ok: true,
        error: "",
        loading: true,
      });
      return;
    }
    if (r.sections && r.sections.length) {
      r.sections.forEach((sec) => {
        const provider = sec.provider || r.profile.provider || "";
        push({
          provider,
          account: sec.account || "",
          accountId: sec.account_id || sec.accountId || "",
          hosts: [host],
          buckets: cleanUsageBuckets(sec.buckets || [], provider),
          ok: sec.ok !== false,
          error: sec.error || "",
          loading: false,
        });
      });
      return;
    }
    const provider = r.provider || r.profile.provider || "";
    push({
      provider,
      account: r.account || "",
      accountId: r.accountId || "",
      hosts: [host],
      buckets: cleanUsageBuckets(r.buckets || [], provider),
      ok: !r.error || !!(r.buckets && r.buckets.length),
      error: r.error || "",
      loading: false,
    });
  });
  return order.map((k) => map.get(k));
}

function appendUsageBuckets(body, buckets, accent) {
  (buckets || []).forEach((b) => {
    const item = el("div", "usage-item");
    if (accent) item.style.setProperty("--tag", accent);
    // A spend-only bucket (Cursor Enterprise: dollars, no limit) has no
    // percentage to draw; the daemon says so with show_bar=false and the
    // resets line carries the amount. Absent means true.
    const showBar = b.show_bar !== false;
    // emphasis=value: the row IS a number (Cursor spend). Draw it large.
    const top = el("div", b.emphasis === "value"
      ? "usage-top usage-hero" : "usage-top");
    top.appendChild(el("span", null, b.title || ""));
    if (showBar) top.appendChild(el("span", null, `${b.percent || 0}%`));
    item.appendChild(top);
    if (showBar) {
      const bar = el("div", `bar ${b.severity || "normal"}`);
      const fill = el("span");
      fill.style.width = Math.min(100, Math.max(0, b.percent || 0)) + "%";
      bar.appendChild(fill);
      item.appendChild(bar);
    }
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
    profile: p,
    loading: true,
    buckets: [],
    sections: null,
    error: "",
    provider: p.provider || "",
    account: "",
    accountId: "",
  }));

  const render = () => modal({
    title: "Usage",
    build(body) {
      if (!results.length) {
        body.appendChild(el("p", null, "No daemon reports usage."));
        return;
      }
      const merged = mergeUsageResults(results);
      if (!merged.length) {
        body.appendChild(el("div", "help", "No usage data returned."));
        return;
      }
      const anyLoading = results.some((r) => r.loading);
      if (anyLoading) {
        const wait = el("div", "help");
        wait.appendChild(el("span", "spinner"));
        // Grok has no REST usage API: the daemon resumes a tmux TUI and
        // scrapes /usage, which can take tens of seconds.
        wait.appendChild(document.createTextNode(" Reading daemons…"));
        body.appendChild(wait);
      }
      merged.forEach((row) => {
        if (row.loading && !(row.buckets && row.buckets.length)) return;
        const pal = providerOf(row.provider);
        const titleBits = [pal.label || row.provider || "Agent"];
        if (row.account) titleBits.push(row.account);
        const head = el("div", "usage-src", titleBits.join(" · "));
        head.style.setProperty("--tag", pal.accent);
        body.appendChild(head);
        if (row.hosts && row.hosts.length) {
          const via = el("div", "help",
            row.hosts.length > 1
              ? `Via ${row.hosts.join(" · ")}`
              : row.hosts[0]);
          via.style.marginTop = "2px";
          via.style.marginBottom = "6px";
          body.appendChild(via);
        }
        if (row.error && !(row.buckets && row.buckets.length)) {
          const e = el("div", "help", row.error);
          e.style.color = "var(--dim)";
          body.appendChild(e);
          return;
        }
        if (row.error && row.buckets && row.buckets.length) {
          const note = el("div", "help", row.error);
          note.style.color = "var(--dim)";
          note.style.marginBottom = "6px";
          body.appendChild(note);
        }
        if (!(row.buckets && row.buckets.length)) {
          body.appendChild(el("div", "help", "No usage data returned."));
          return;
        }
        appendUsageBuckets(body, row.buckets, pal.accent);
      });
    },
    actions: [{ label: "Close", close: true }],
  });
  render();

  // Progressive: fast daemons (Claude OAuth) paint immediately; Grok's TUI
  // scrape is slow and must not block the sheet. Re-merge after each host.
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
        r.provider = data.provider || r.profile.provider || "";
        r.account = data.account || "";
        r.accountId = data.account_id || data.accountId || "";
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
      // Execution, model and effort are per message: they are chips on the
      // composer now, not rows in here.
      const sessionModel = String(
        (state.open.session && state.open.session.model) || "").trim();
      if (sessionModel && sessionModel !== "default" && sessionModel !== "interactive") {
        body.appendChild(el("div", "help",
          `This session last ran on ${sessionModel}`));
      }
      body.appendChild(el("div", "help",
        "Execution, model and effort are set on the message box, under the transcript."));
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
      toggle("Sound cues (status, done, error, attention)", soundCuesOn(), (on) => {
        setSoundCues(on);
      });
      // Per session, unlike everything above it — one session you are
      // debugging wants the detail; the rest stay clean.
      if (state.open.sessionId) {
        toggle("Process view — show tool calls, results and thinking",
               processViewOn(state.open.sessionId), (on) => {
          setProcessView(state.open.sessionId, on);
          // Refetch: steps only arrive with ?detail=steps. Default gen —
          // loadTail's first arg is the open generation, not a flag.
          loadTail();
        });
        body.appendChild(el("div", "help",
          "This session only. Adds the agent's working steps under each "
          + "message; the transcript is larger to load."));
      }
      // Codex goals. The panel removes itself when this thread has none.
      if (state.open.sessionId
          && capOf(profile, "goal_watch", false, harness || null)) {
        const goalBox = el("div", "field goalbox");
        body.appendChild(goalBox);
        paintGoal(goalBox, profile, state.open.sessionId);
      }
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

  $("btn-back").addEventListener("click", backToList);
  $("jump-latest").addEventListener("click", () => scrollToLatest(true));
  $("transcript").addEventListener("scroll", syncJumpPill, { passive: true });
  window.addEventListener("resize", syncJumpPill);
  $("btn-sound").addEventListener("click", () => setSoundCues(!soundCuesOn()));
  syncSoundButton();

  wireHostFileLinks($("transcript"));
  watchTranscriptScroll();
  $("btn-profiles").addEventListener("click", openProfiles);
  $("btn-new").addEventListener("click", openNewSession);
  $("btn-inbox").addEventListener("click", openInbox);
  $("btn-usage").addEventListener("click", openUsage);
  // Narrow windows: the occasional actions are a menu under "More".
  const more = $("chat-more"), moreBtn = $("btn-more");
  const setMore = (open) => {
    more.classList.toggle("open", open);
    moreBtn.setAttribute("aria-expanded", open ? "true" : "false");
  };
  moreBtn.addEventListener("click", (e) => {
    e.stopPropagation();
    const open = !more.classList.contains("open");
    setMore(open);
    if (open) more.querySelector("button:not(.hidden)")?.focus();
  });
  more.addEventListener("click", () => setMore(false));
  document.addEventListener("click", () => setMore(false));
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && more.classList.contains("open")) {
      setMore(false);
      moreBtn.focus();
    }
  });
  $("btn-copy-id").addEventListener("click", () => {
    if (state.open && state.open.sessionId) copySessionId(state.open.sessionId);
  });
  $("btn-options").addEventListener("click", openOptions);
  $("btn-rename").addEventListener("click", openRename);
  $("btn-share")?.addEventListener("click", openShare);
  $("btn-refresh").addEventListener("click", () => {
    loadTail();
    refreshSessions();
    // Transcript reload used to leave a dismissed ask stranded; re-surface it.
    if (state.job && (state.job.pendingQuestion || state.job.lastPendingQuestion
        || state.job.pendingPermission || state.job.lastPendingPermission)) {
      if (state.job.pendingQuestion || state.job.lastPendingQuestion) reopenQuestion();
      else reopenPermission();
    } else {
      renderBanner();
    }
  });
  $("btn-live-tui")?.addEventListener("click", () => {
    if (state.liveTui) closeLiveTui();
    else openLiveTui();
  });
  // A resized window changes how many glyphs fit, never how many the host
  // draws — so the pane rescales instead of reflowing.
  window.addEventListener("resize", () => {
    if (state.liveTui) fitLiveTuiPane();
  });
  $("btn-live-tui-close")?.addEventListener("click", () => closeLiveTui());
  $("live-tui")?.querySelectorAll("[data-tui-key]").forEach((btn) => {
    // mousedown, not click: a plain click would blur the pane first and the
    // next typed character would land in the page instead of the terminal.
    btn.addEventListener("mousedown", (e) => e.preventDefault());
    btn.addEventListener("click", () => {
      sendLiveTuiKeys([btn.getAttribute("data-tui-key")]);
      focusLiveTuiPane();
    });
  });
  // preventDefault: taking focus would flip the state this handler reads.
  $("btn-tui-capture")?.addEventListener("mousedown", (e) => e.preventDefault());
  $("btn-tui-capture")?.addEventListener("click", () => {
    if (state.liveTuiKeys) $("live-tui-input")?.focus();
    else focusLiveTuiPane();
  });
  $("live-tui-pane")?.addEventListener("focus", () => {
    state.liveTuiKeys = true;
    updateLiveTuiCapture();
  });
  $("live-tui-pane")?.addEventListener("blur", () => {
    state.liveTuiKeys = false;
    flushLiveTuiText();
    updateLiveTuiCapture();
  });
  $("live-tui-pane")?.addEventListener("keydown", (e) => {
    if (!state.liveTui) return;
    // The text field owns the keyboard whenever it has focus — that is where
    // a system IME composes, and none of it may leak into the terminal.
    if (document.activeElement !== e.currentTarget) return;
    // Release chord. NOT Esc Esc: that is the agent's own rewind, and this
    // pane exists to deliver it.
    if (e.key === "Escape" && e.shiftKey && !e.ctrlKey && !e.altKey) {
      e.preventDefault();
      $("live-tui-input")?.focus();
      return;
    }
    const spec = liveTuiKeySpec(e);
    if (!spec) return;
    e.preventDefault();
    e.stopPropagation();
    if (spec.text) typeLiveTuiText(spec.text);
    else sendLiveTuiKeys(spec.keys);
  });
  // Paste straight into the terminal (Cmd/Ctrl+V is left to the browser so
  // this event is what actually carries the text).
  $("live-tui-pane")?.addEventListener("paste", (e) => {
    if (!state.liveTui) return;
    const text = e.clipboardData?.getData("text") || "";
    if (!text) return;
    e.preventDefault();
    sendLiveTuiKeys(null, text);
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
    // isComposing: Enter while an IME candidate window is open commits the
    // candidate — it is not the user sending the line.
    if (e.key === "Enter" && !e.isComposing) {
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
  document.addEventListener("keydown", trapModalTab);
  $("modal").addEventListener("click", (e) => { if (e.target === $("modal")) closeModal(); });

  let searchTimer;
  $("search").addEventListener("input", (e) => {
    state.query = e.target.value;
    clearTimeout(searchTimer);
    // Instant filter on the list we already have, then debounced server
    // full-text search (titles + body) replaces it. Avoids a fan-out on
    // every keystroke feeling like the UI is stuck.
    const q = state.query.trim();
    if (q && state.rows.length) {
      const needle = q.toLowerCase();
      const local = state.rows.filter((row) => {
        const s = row.session || {};
        const hay = [s.title, s.last_text, s.snippet, s.cwd, s.id]
          .filter(Boolean).join("\n").toLowerCase();
        return hay.includes(needle);
      });
      if (local.length) {
        state.rows = local;
        renderSessions();
      }
    } else if (!q) {
      searchTimer = setTimeout(refreshSessions, 50);
      return;
    }
    searchTimer = setTimeout(refreshSessions, 400);
  });

  const prompt = $("prompt");
  const grow = () => {
    prompt.style.height = "auto";
    prompt.style.height = Math.min(Math.max(prompt.scrollHeight, 38), 220) + "px";
  };
  prompt.addEventListener("input", grow);
  prompt.addEventListener("input", syncSlashMenu);
  prompt.addEventListener("focus", syncSlashMenu);
  prompt.addEventListener("blur", () => { if (pop.kind === "slash") closePop(); });
  $("composer-pop").addEventListener("keydown", onPopKeydown);
  $("btn-changes").addEventListener("click", () => { if (side.open) closeSide(); else openSide(); });
  $("side-close").addEventListener("click", closeSide);
  $("side-back").addEventListener("click", closeSide);
  $("side-refresh").addEventListener("click", loadChanges);
  $("side").addEventListener("keydown", (e) => {
    if (e.key === "Escape" && $("modal").classList.contains("hidden")) { e.stopPropagation(); closeSide(); }
  });
  // The machine itself lost or regained its network.
  window.addEventListener("offline", () => {
    enabledProfiles().forEach((p) => { if (connOf(p.id) === "live") setConn(p.id, "reconnecting"); });
  });
  window.addEventListener("online", reconnectNow);
  // A click anywhere else closes a chip's menu.
  document.addEventListener("mousedown", (e) => {
    if (pop.kind !== "chip") return;
    if ($("composer-pop").contains(e.target) || (pop.anchor && pop.anchor.contains(e.target))) return;
    closePop();
  });
  prompt.addEventListener("keydown", (e) => {
    // Enter that confirms an IME candidate (CJK input) is not a send.
    if (e.isComposing || e.keyCode === 229) return;
    if (slashKeydown(e)) return;
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

  showWelcomeIfNeeded();
  // "Add a daemon" buttons are wired inside buildDaemonGuide().

  await Promise.all(state.profiles.map(pingProfile));
  renderFilters();
  syncStreams();
  await refreshSessions();

  // Guide first: open the daemon form when nothing is configured yet.
  // Don't force the modal if they only need the on-page walkthrough.
  if (state.profiles.length && state.profiles.every((p) => !p.token)) {
    openProfiles();
  }
  showWelcomeIfNeeded();
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
