var mdstrip = require('./mdstrip');
var stripMarkdown = mdstrip.stripMarkdown;
var decorateOff = mdstrip.decorateOff;

function utf8clip(s, max) {
  s = String(s || '');
  var out = '', i, c, n = 0;
  for (i = 0; i < s.length; i++) {
    c = s.charCodeAt(i);
    n += c < 0x80 ? 1 : c < 0x800 ? 2 : (c >= 0xd800 && c <= 0xdbff) ? 4 : 3;
    if (c >= 0xd800 && c <= 0xdbff) i++; // skip trail surrogate
    if (n > max) break;
    out = s.slice(0, i + 1);
  }
  return out;
}

function utf8len(s) {
  s = String(s || '');
  var n = 0, i, c;
  for (i = 0; i < s.length; i++) {
    c = s.charCodeAt(i);
    n += c < 0x80 ? 1 : c < 0x800 ? 2 : (c >= 0xd800 && c <= 0xdbff) ? 4 : 3;
    if (c >= 0xd800 && c <= 0xdbff) i++;
  }
  return n;
}

var STATE_WEIGHT = {
  needs_answer: 0,
  failed: 1,
  working: 2,
  turn_finished: 3
};

function lastActiveNum(row) {
  var v = row && row.last_active;
  if (v == null || v === '') v = row && row.started;
  if (v == null || v === '') return 0;
  if (typeof v === 'number' && isFinite(v)) return v;
  var s = String(v).replace(/^\s+|\s+$/g, '');
  var n;
  var ms;
  if (!s) return 0;
  if (/^-?\d+(\.\d+)?$/.test(s)) {
    n = parseFloat(s);
    return isFinite(n) ? n : 0;
  }
  ms = Date.parse(s);
  return isNaN(ms) ? 0 : ms / 1000;
}

function inferState(row) {
  var st;
  if (!row) return 'turn_finished';
  st = row.focus_state;
  if (st) return String(st);
  if (row.pending_permission || row.pending_question || row.pending) {
    return 'needs_answer';
  }
  if (row.running || row.working) return 'working';
  if (row.failed || row.status === 'error') return 'failed';
  return 'turn_finished';
}

function stateWeight(state) {
  var w = STATE_WEIGHT[state];
  return w == null ? 4 : w;
}

function takeFocus(sessions) {
  return (sessions || []).slice(0, 12);
}

function rankFocus(sessions) {
  var rows = (sessions || []).slice();
  rows.sort(function (a, b) {
    var wa = stateWeight(inferState(a));
    var wb = stateWeight(inferState(b));
    if (wa !== wb) return wa - wb;
    return lastActiveNum(b) - lastActiveNum(a);
  });
  return takeFocus(rows);
}

function rowSid(row) {
  return String((row && (row.id || row.session_id)) || '');
}

function clipFocusRow(row, i, gen, n) {
  row = row || {};
  return {
    KIND: 2,
    FOCUS_I: i,
    FOCUS_N: n,
    FOCUS_GEN: gen,
    SID: utf8clip(rowSid(row), 64),
    TITLE: utf8clip(row.title || row.name || '', 40),
    STATE: utf8clip(inferState(row), 16),
    PROV: utf8clip(row.provider || '', 16),
    PHASE: utf8clip(row.phase || '', 24),
    UNREAD: row.focus_unread ? 1 : 0
  };
}

function rowChanged(a, b) {
  if (!a || !b) return true;
  return a.SID !== b.SID || a.TITLE !== b.TITLE || a.STATE !== b.STATE ||
    a.PROV !== b.PROV || a.PHASE !== b.PHASE || a.UNREAD !== b.UNREAD;
}

function sidMembershipOverHalf(prev, next) {
  var oldS = {};
  var newS = {};
  var i;
  var sid;
  var inter = 0;
  var oldN = 0;
  var newN = 0;
  var changed;
  var base;
  for (i = 0; i < (prev || []).length; i++) {
    sid = prev[i] && prev[i].SID;
    if (sid && !oldS[sid]) {
      oldS[sid] = true;
      oldN++;
    }
  }
  for (i = 0; i < (next || []).length; i++) {
    sid = next[i] && next[i].SID;
    if (sid && !newS[sid]) {
      newS[sid] = true;
      newN++;
    }
  }
  for (sid in oldS) {
    if (Object.prototype.hasOwnProperty.call(oldS, sid) && newS[sid]) inter++;
  }
  changed = (oldN - inter) + (newN - inter);
  base = oldN > newN ? oldN : newN;
  return base > 0 && changed * 2 > base;
}

function needsFullFocus(prev, next, pending) {
  return !prev || !prev.length || !!pending || sidMembershipOverHalf(prev, next);
}

function capsFor(ping, prov) {
  ping = ping || {};
  if (ping.multi && ping.provider_details) {
    return (ping.provider_details[prov] && ping.provider_details[prov].caps) || {};
  }
  return ping.caps || {};
}

function permissionMode(ping, prov) {
  return capsFor(ping, prov).interactive ? 'interactive' : 'bypassPermissions';
}

function projectRows(projects) {
  if (!projects) return [];
  if (projects.projects && projects.projects.slice) return projects.projects;
  if (projects.slice) return projects;
  return [];
}

function pickCwd(projects, provider) {
  var rows = projectRows(projects);
  var i;
  var p;
  var tagged = [];
  var untagged = [];
  var pool;
  var best = null;
  var bestN = -1;
  var n;
  provider = String(provider || '');
  for (i = 0; i < rows.length; i++) {
    p = rows[i];
    if (!p || !p.cwd) continue;
    if (!p.provider) untagged.push(p);
    else if (!provider || p.provider === provider) tagged.push(p);
  }
  pool = tagged.concat(untagged);
  for (i = 0; i < pool.length; i++) {
    n = lastActiveNum(pool[i]);
    if (!best || n > bestN) {
      best = pool[i];
      bestN = n;
    }
  }
  return (best && best.cwd) || '';
}

function buildNewSession(ping, prompt, provider, projects) {
  var multi = !!(ping && ping.multi);
  var prov = String(provider || '');
  var caps;
  var cwd;
  var body;
  if (multi && !prov) return { error: 'provider' };
  if (!prov && ping && ping.provider) prov = String(ping.provider);
  caps = capsFor(ping, prov);
  body = {
    prompt: String(prompt || ''),
    permission_mode: permissionMode(ping, prov)
  };
  if (multi) body.provider = prov;
  if (caps.requires_cwd) {
    cwd = pickCwd(projects, prov);
    if (!cwd) return { error: 'no cwd' };
    body.cwd = cwd;
  }
  return { body: body };
}

function enrolledSid(tr, fallback) {
  if (tr && tr.session_id) return tr.session_id;
  if (tr && tr.new_session_id) return tr.new_session_id;
  if (fallback) return fallback;
  if (tr && tr.id) return 'job:' + tr.id;
  return '';
}

function continueSid(sid, trackers) {
  return enrolledSid(trackerForSid(trackers, sid), sid);
}

function startsCI(s, pfx) {
  s = String(s || '');
  pfx = String(pfx || '');
  return s.slice(0, pfx.length).toLowerCase() === pfx.toLowerCase();
}

function containsCI(s, sub) {
  return String(s || '').toLowerCase().indexOf(String(sub || '').toLowerCase()) >= 0;
}

function optionLabel(o) {
  if (o == null) return '';
  if (typeof o === 'string') return o;
  return String(o.label || '');
}

function classifyProceed(pending) {
  var questions = pending && pending.questions;
  var q;
  var opts;
  var i;
  var lab;
  var yes = '';
  var no = '';
  var always = '';
  var leftover = false;
  var slots;
  if (!questions || questions.length !== 1) return {qkind: 0};
  q = questions[0] || {};
  if (q.multi_select) return {qkind: 0};
  if (!(q.header === 'Permission' || /Do you want to proceed\?/i.test(String(q.question || '')))) {
    return {qkind: 0};
  }
  opts = q.options || [];
  if (opts.length < 2) return {qkind: 0};
  for (i = 0; i < opts.length; i++) {
    lab = optionLabel(opts[i]);
    if (utf8len(lab) > 80) return {qkind: 0};
  }
  for (i = 0; i < opts.length; i++) {
    lab = optionLabel(opts[i]);
    if (!yes && startsCI(lab, 'Yes') && !containsCI(lab, "don't ask")) yes = lab;
  }
  for (i = 0; i < opts.length; i++) {
    lab = optionLabel(opts[i]);
    if (!no && startsCI(lab, 'No')) no = lab;
  }
  for (i = 0; i < opts.length; i++) {
    lab = optionLabel(opts[i]);
    if (!always && containsCI(lab, "don't ask")) always = lab;
  }
  if (!yes || !no) return {qkind: 0};
  slots = {};
  slots[yes] = 1;
  slots[no] = 1;
  if (always) slots[always] = 1;
  for (i = 0; i < opts.length; i++) {
    lab = optionLabel(opts[i]);
    if (!slots[lab]) leftover = true;
  }
  if (leftover) return {qkind: 0};
  return {qkind: 1, yes_label: yes, no_label: no, always_label: always};
}

function labelsOver80(pending) {
  var qs = pending && pending.questions;
  var i, j, opts, lab;
  if (!qs) return false;
  for (i = 0; i < qs.length; i++) {
    opts = (qs[i] && qs[i].options) || [];
    for (j = 0; j < opts.length; j++) {
      lab = optionLabel(opts[j]);
      if (utf8len(lab) > 80) return true;
    }
  }
  return false;
}

function parseAnswers(s) {
  if (s == null || s === '') return [];
  return String(s).split('\x1e').map(function (slot) {
    if (slot.indexOf('\x1f') >= 0) {
      return slot.split('\x1f').filter(function (x) { return x !== ''; });
    }
    return slot ? [slot] : [];
  });
}

function packAnswers(answers) {
  return (answers || []).map(function (slot) {
    if (slot == null) return '';
    if (Object.prototype.toString.call(slot) === '[object Array]') {
      return slot.join('\x1f');
    }
    return String(slot);
  }).join('\x1e');
}

/* Match C pack_notes: AR_QNOTE_LEN+1 buffer, always s_qn-1 RS, clip content. */
function packNotes(notes, max) {
  var qn = (notes || []).length;
  var dstSz = (max == null ? 200 : max) + 1;
  var parts = [];
  var n = 0;
  var qi;
  var reserved;
  var cap;
  var clipped;
  for (qi = 0; qi < qn; qi++) {
    if (qi) {
      if (n + 1 >= dstSz) break;
      parts.push('\x1e');
      n += 1;
    }
    reserved = qn - 1 - qi;
    if (n + reserved + 1 > dstSz) break;
    cap = dstSz - n - reserved;
    clipped = utf8clip(String(notes[qi] || ''), cap > 0 ? cap - 1 : 0);
    parts.push(clipped);
    n += utf8len(clipped);
  }
  return parts.join('');
}

function parseNotes(s, qn) {
  var notes = (s == null || s === '') ? [] : String(s).split('\x1e');
  if (qn == null) return notes;
  while (notes.length < qn) notes.push('');
  if (notes.length > qn) notes = notes.slice(0, qn);
  return notes;
}

function estimateDict(obj) {
  var n = 0;
  var payload = 0;
  var k;
  var v;
  for (k in obj) {
    if (!Object.prototype.hasOwnProperty.call(obj, k)) continue;
    v = obj[k];
    if (v === undefined || v === null) continue;
    n++;
    if (typeof v === 'number') payload += 4;
    else payload += utf8len(String(v)) + 1;
  }
  return 1 + 7 * n + payload;
}

function questionDictBytes(sid, jid, rid, answersStr, qnoteStr) {
  var o = {
    CMD: 7,
    SID: sid || '',
    JID: jid || '',
    RID: rid || '',
    ANSWERS: answersStr || ''
  };
  if (qnoteStr) o.QNOTE = qnoteStr;
  return estimateDict(o);
}

function shouldCancelQuestion(sid, jid, rid, answers, notes) {
  var packed = packAnswers(answers);
  var qnote = '';
  var i;
  if (notes && notes.length) {
    for (i = 0; i < notes.length; i++) {
      if (notes[i]) {
        qnote = notes.join('\x1e');
        break;
      }
    }
  }
  return questionDictBytes(sid, jid, rid, packed, qnote) > 768;
}

function focusHasLive(rows) {
  var i;
  var st;
  for (i = 0; i < (rows || []).length; i++) {
    st = rows[i] && (rows[i].STATE || rows[i].focus_state || inferState(rows[i]));
    if (st === 'working' || st === 'needs_answer') return true;
  }
  return false;
}

function jobIsActive(j) {
  var st;
  if (!j) return false;
  st = String(j.status || '');
  if (st === 'starting' || st === 'running') return true;
  return !!(j.pending_permission || j.pending_question);
}

function activeJobs(jobs) {
  var out = [];
  var i;
  for (i = 0; i < (jobs || []).length; i++) {
    if (jobIsActive(jobs[i])) out.push(jobs[i]);
  }
  return out;
}

function jobsLive(jobs) {
  var i;
  for (i = 0; i < (jobs || []).length; i++) {
    if (jobIsActive(jobs[i])) return true;
  }
  return false;
}

function shouldGetJobs(opts) {
  opts = opts || {};
  if (opts.force || opts.hello || opts.open) return true;
  if (focusHasLive(opts.focusRows)) return true;
  if (opts.lastJobs && opts.lastJobs.length) return true;
  return false;
}

function snapshotSince(track, brief, reason) {
  var eventCount = (brief && typeof brief.event_count === 'number') ? brief.event_count : 0;
  if (track && track.subscribed && track.cursor != null) {
    if (reason === 'pending' && eventCount > track.cursor) return track.cursor;
    if (reason === 'pending') return eventCount;
    return track.cursor;
  }
  return eventCount;
}

function jobMatchesSid(job, sid) {
  if (!job || !sid) return false;
  if (job.id === sid || ('job:' + job.id) === sid) return true;
  if (job.session_id === sid || job.new_session_id === sid) return true;
  return false;
}

function trackerForSid(trackers, sid) {
  var id;
  var t;
  if (!sid || !trackers) return null;
  if (trackers[sid]) return trackers[sid];
  if (sid.indexOf('job:') === 0 && trackers[sid.slice(4)]) return trackers[sid.slice(4)];
  for (id in trackers) {
    if (!Object.prototype.hasOwnProperty.call(trackers, id)) continue;
    t = trackers[id];
    if (jobMatchesSid(t, sid)) return t;
  }
  return null;
}

function jobSid(job, prefer) {
  if (prefer) return prefer;
  if (!job) return '';
  return job.session_id || job.new_session_id || (job.id ? ('job:' + job.id) : '');
}

function phaseFromSnap(track, snap) {
  var events;
  var i;
  var ev;
  if (snap && snap.pending_question) return 'asking';
  if (snap && snap.pending_permission && typeof snap.pending_permission === 'object') {
    return utf8clip('Allow ' + (snap.pending_permission.tool_name || 'tool') + '?', 24);
  }
  events = (snap && snap.events) || [];
  for (i = events.length - 1; i >= 0; i--) {
    ev = events[i];
    if (ev && ev.kind === 'tool' && ev.name) return utf8clip(ev.name, 24);
  }
  return (track && track.phase) || 'working';
}

function mergeBriefs(trackers, jobs) {
  var i;
  var j;
  var t;
  for (i = 0; i < (jobs || []).length; i++) {
    j = jobs[i];
    if (!j || !j.id) continue;
    t = trackers[j.id];
    if (!t) t = trackers[j.id] = {id: j.id};
    t.status = j.status;
    t.provider = j.provider || t.provider || '';
    t.session_id = j.session_id || t.session_id || '';
    t.new_session_id = j.new_session_id || t.new_session_id || '';
    t.started_at = j.started_at;
    t.event_count = typeof j.event_count === 'number' ? j.event_count : 0;
    t.pending_permission = !!j.pending_permission;
    t.pending_question = !!j.pending_question;
    if (!t.pending_permission) t.perm = null;
    if (!t.pending_question) t.quest = null;
    if (!t.subscribed) {
      t.cursor = t.event_count;
      t.subscribed = true;
    }
    if (!t.phase) {
      t.phase = t.pending_question ? 'asking' : 'working';
    }
  }
  return trackers;
}

function neededSnapshots(trackers, jobs) {
  var out = [];
  var i;
  var j;
  var t;
  var needPending;
  var needDelta;
  for (i = 0; i < (jobs || []).length; i++) {
    j = jobs[i];
    if (!j || !j.id) continue;
    t = trackers[j.id];
    if (!t) continue;
    needPending = (j.pending_permission || j.pending_question) && !t.perm && !t.quest;
    needDelta = t.cursor != null && typeof j.event_count === 'number' && j.event_count > t.cursor;
    if (needPending && needDelta) {
      out.push({id: j.id, since: snapshotSince(t, j, 'delta'), reason: 'both'});
    } else if (needPending) {
      out.push({id: j.id, since: snapshotSince(t, j, 'pending'), reason: 'pending'});
    } else if (needDelta) {
      out.push({id: j.id, since: snapshotSince(t, j, 'delta'), reason: 'delta'});
    }
  }
  return out;
}

function applySnapshot(trackers, id, snap) {
  var t = trackers[id];
  if (!t) t = trackers[id] = {id: id};
  t.subscribed = true;
  if (snap && typeof snap.next_seq === 'number') t.cursor = snap.next_seq;
  if (snap && snap.status) t.status = snap.status;
  if (snap && snap.session_id) t.session_id = snap.session_id;
  if (snap && snap.new_session_id) t.new_session_id = snap.new_session_id;
  if (snap && snap.pending_permission && typeof snap.pending_permission === 'object') {
    t.perm = snap.pending_permission;
    t.pending_permission = true;
  } else {
    t.perm = null;
    t.pending_permission = false;
  }
  if (snap && snap.pending_question && typeof snap.pending_question === 'object') {
    t.quest = snap.pending_question;
    t.pending_question = true;
  } else {
    t.quest = null;
    t.pending_question = false;
  }
  t.phase = phaseFromSnap(t, snap);
  return t;
}

function diffFeed(prevJobs, jobs, feedSeeded) {
  var i;
  var j;
  var p;
  var vanished = [];
  var sawStart = false;
  var sawAttn = false;
  var attnId = '';
  var prevBy = {};
  var curBy = {};
  var needs;
  var pneeds;
  var chime = null;
  var prev = activeJobs(prevJobs);
  var cur = activeJobs(jobs);
  for (i = 0; i < prev.length; i++) {
    p = prev[i];
    if (p && p.id) prevBy[p.id] = p;
  }
  for (i = 0; i < cur.length; i++) {
    j = cur[i];
    if (!j || !j.id) continue;
    curBy[j.id] = j;
    needs = !!(j.pending_permission || j.pending_question);
    p = prevBy[j.id];
    if (!p) sawStart = true;
    else {
      pneeds = !!(p.pending_permission || p.pending_question);
      if (needs && !pneeds) {
        sawAttn = true;
        attnId = j.id;
      }
    }
  }
  for (i = 0; i < prev.length; i++) {
    p = prev[i];
    if (p && p.id && !curBy[p.id]) vanished.push(p.id);
  }
  if (feedSeeded) {
    if (sawAttn) chime = 3;
    else if (vanished.length) chime = null;
    else if (sawStart) chime = 0;
  }
  return {
    chime: chime,
    vanished: vanished,
    sawStart: sawStart,
    sawAttn: sawAttn,
    attnId: attnId
  };
}

function endChime(status, httpErr) {
  var st = String(status || '').toLowerCase();
  if (st === 'running' || st === 'starting') return null;
  if (st === 'error') return 2;
  if (httpErr && httpErr.status !== 404) return 2;
  return 1;
}

var MSG_TEXT_MAX = 700;

function clipLast(s) {
  return utf8clip(decorateOff(stripMarkdown(s || '')).replace(/\n+/g, ' '), 96);
}

function takeMessages(list) {
  var out = [];
  var i;
  var m;
  var role;
  list = list || [];
  for (i = 0; i < list.length; i++) {
    m = list[i] || {};
    role = String(m.role || '');
    if (role === 'status') continue;
    out.push({
      role: role === 'user' ? 0 : 1,
      text: utf8clip(stripMarkdown(m.text || ''), MSG_TEXT_MAX)
    });
  }
  if (out.length > 32) out = out.slice(out.length - 32);
  return out;
}

function takeTurns(messages) {
  var turns = [];
  var cur = null;
  var i;
  var m;
  for (i = 0; i < (messages || []).length; i++) {
    m = messages[i] || {};
    if (m.role === 0) {
      if (cur) turns.push(cur);
      cur = { user: m.text || '', assistant: '' };
    } else {
      if (!cur) cur = { user: '', assistant: '' };
      if (cur.assistant) cur.assistant += '\n';
      cur.assistant += (m.text || '');
    }
  }
  if (cur) turns.push(cur);
  if (turns.length > 8) turns = turns.slice(turns.length - 8);
  return turns;
}

function chunkPair(messages, k) {
  var turns = takeTurns(messages);
  var n = turns.length;
  var t;
  k = parseInt(k, 10);
  if (isNaN(k) || k < 0) k = 0;
  if (n <= 0) return {k: 0, n: 0, more: 0, items: []};
  if (k > n - 1) k = n - 1;
  t = turns[n - 1 - k];
  return {
    k: k,
    n: n,
    more: k < n - 1 ? 1 : 0,
    items: [
      { role: 0, text: t.user || '' },
      { role: 1, text: t.assistant || '' }
    ]
  };
}

function overlayPhase(row, trackers) {
  var copy = {};
  var k;
  var t;
  for (k in row) {
    if (Object.prototype.hasOwnProperty.call(row, k)) copy[k] = row[k];
  }
  t = trackerForSid(trackers, rowSid(row));
  if (t && t.phase) copy.phase = t.phase;
  return copy;
}

module.exports = {
  utf8clip: utf8clip,
  utf8len: utf8len,
  MSG_TEXT_MAX: MSG_TEXT_MAX,
  stripMarkdown: stripMarkdown,
  clipLast: clipLast,
  takeMessages: takeMessages,
  takeTurns: takeTurns,
  chunkPair: chunkPair,
  lastActiveNum: lastActiveNum,
  inferState: inferState,
  takeFocus: takeFocus,
  rankFocus: rankFocus,
  clipFocusRow: clipFocusRow,
  rowChanged: rowChanged,
  rowSid: rowSid,
  sidMembershipOverHalf: sidMembershipOverHalf,
  needsFullFocus: needsFullFocus,
  capsFor: capsFor,
  permissionMode: permissionMode,
  pickCwd: pickCwd,
  buildNewSession: buildNewSession,
  enrolledSid: enrolledSid,
  continueSid: continueSid,
  classifyProceed: classifyProceed,
  labelsOver80: labelsOver80,
  parseAnswers: parseAnswers,
  packAnswers: packAnswers,
  packNotes: packNotes,
  parseNotes: parseNotes,
  estimateDict: estimateDict,
  questionDictBytes: questionDictBytes,
  shouldCancelQuestion: shouldCancelQuestion,
  focusHasLive: focusHasLive,
  jobIsActive: jobIsActive,
  activeJobs: activeJobs,
  jobsLive: jobsLive,
  shouldGetJobs: shouldGetJobs,
  snapshotSince: snapshotSince,
  jobMatchesSid: jobMatchesSid,
  trackerForSid: trackerForSid,
  jobSid: jobSid,
  phaseFromSnap: phaseFromSnap,
  mergeBriefs: mergeBriefs,
  neededSnapshots: neededSnapshots,
  applySnapshot: applySnapshot,
  diffFeed: diffFeed,
  endChime: endChime,
  overlayPhase: overlayPhase
};
