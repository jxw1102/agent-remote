var http = require('./http');
var reshape = require('./reshape');

var queueFn = null;
var pendingFn = null;
var postFn = null;
var pollIdle = 25;
var pollWork = 4;
var fontSize = 1;
var timer = null;
var inflight = null;
var reqGen = 0;
var focusGen = 0;
var lastRows = [];
var lastSessions = [];
var lastBySid = {};
var lastJobs = [];
var trackers = {};
var ping = null;
var pollOk = true;
var forceJobs = false;
var wantNow = false;
var feedSeeded = false;
var lastStatusAt = 0;
var liveSid = null;
var lastJobSigs = {};
var sentTakeover = null;
var takeoverAcked = false;
var cancelledRids = {};
var burstUntil = 0;
var TAKEOVER_RETRY_MS = 8000;
var messages = [];
var msgSid = null;
var lastMsgSig = '';

function setIdle(s) {
  var n = parseInt(s, 10);
  if (isNaN(n) || n < 15) n = 25;
  if (n > 45) n = 45;
  pollIdle = n;
}

function setWork(s) {
  var n = parseInt(s, 10);
  if (isNaN(n) || n < 3) n = 4;
  if (n > 8) n = 8;
  pollWork = n;
}

function setFont(n) {
  n = parseInt(n, 10);
  if (isNaN(n) || n < 0) n = 0;
  if (n > 2) n = 2;
  fontSize = n;
}

function init(opts) {
  queueFn = opts && opts.queue;
  pendingFn = opts && opts.focusPending;
  postFn = opts && opts.post;
}

function queue(msg) {
  if (queueFn) queueFn(msg);
}

function helloFromPing(conn) {
  var provs = '';
  if (!ping) return { KIND: 1, CONN: conn, FONT: fontSize };
  if (ping.providers && ping.providers.length) {
    provs = ping.providers.join(',');
  } else if (ping.provider) {
    provs = String(ping.provider);
  }
  return {
    KIND: 1,
    CONN: conn,
    HOST: reshape.utf8clip(ping.host || '', 24),
    VER: reshape.utf8clip(ping.version || '', 12),
    MULTI: ping.multi ? 1 : 0,
    PROVS: reshape.utf8clip(provs, 64),
    FONT: fontSize
  };
}

function workingNow() {
  if (burstUntil && Date.now() < burstUntil) return true;
  if (sentTakeover) return true;
  if (reshape.focusHasLive(lastRows)) return true;
  if (reshape.jobsLive(lastJobs)) return true;
  return false;
}

function intervalSec() {
  if (burstUntil && Date.now() < burstUntil) return 2;
  return workingNow() ? pollWork : pollIdle;
}

function schedule() {
  var sec;
  if (timer) clearTimeout(timer);
  sec = intervalSec();
  timer = setTimeout(onTimer, sec * 1000);
  console.log('[poll] ' + (workingNow() ? 'work ' : 'idle ') + sec + 's');
}

function onTimer() {
  timer = null;
  if (inflight) {
    console.log('[poll] skip in-flight');
    schedule();
    return;
  }
  tick();
}

function handleErr(err) {
  pollOk = false;
  if (err && err.status === 401) queue({ KIND: 1, CONN: 3 });
  else queue({ KIND: 1, CONN: 1 });
}

function dispatchFocus(sessions) {
  var rows = (ping && ping.focus) ? reshape.takeFocus(sessions) : reshape.rankFocus(sessions);
  var next = [];
  var i;
  var msg;
  var full;
  var pending = pendingFn && pendingFn();
  var row;
  lastSessions = rows;
  lastBySid = {};
  focusGen++;
  for (i = 0; i < rows.length; i++) {
    lastBySid[reshape.rowSid(rows[i])] = reshape.clipLast(rows[i].last_text || '');
    row = reshape.overlayPhase(rows[i], trackers);
    next.push(reshape.clipFocusRow(row, i, focusGen, rows.length));
  }
  full = reshape.needsFullFocus(lastRows, next, pending);
  for (i = 0; i < next.length; i++) {
    msg = next[i];
    if (full || reshape.rowChanged(lastRows[i], msg)) {
      console.log('[msg] KIND=2 i=' + i + '/' + next.length);
      queue(msg);
    }
  }
  lastRows = next;
  queue({ KIND: 3, FOCUS_N: next.length, FOCUS_GEN: focusGen });
}

function jobMsg(tr, sid) {
  return {
    KIND: 11,
    SID: reshape.utf8clip(reshape.jobSid(tr, sid), 64),
    JID: reshape.utf8clip(tr && tr.id || '', 48),
    PHASE: reshape.utf8clip(tr && tr.phase || '', 24),
    STARTED: tr && tr.started_at ? Math.floor(tr.started_at) : 0,
    STATUS: reshape.utf8clip(tr && tr.status || '', 12),
    PROV: reshape.utf8clip(tr && tr.provider || '', 16)
  };
}

function jobSig(tr) {
  if (!tr) return '';
  return (tr.id || '') + '|' + (tr.phase || '') + '|' + (tr.status || '') + '|' +
    (tr.started_at || 0) + '|' + (tr.pending_permission ? 1 : 0) +
    (tr.pending_question ? 1 : 0);
}

function emitJob(tr, sid, force) {
  var sig;
  var id;
  var outSid = sid;
  if (!tr) {
    if (sid) {
      queue({ KIND: 11, SID: sid, JID: '', PHASE: '', STARTED: 0, STATUS: '' });
    }
    return;
  }
  if (sid && sid.indexOf('job:') === 0) {
    outSid = reshape.enrolledSid(tr, sid);
  }
  sig = jobSig(tr) + '|' + outSid;
  id = tr.id || sid;
  if (!force && lastJobSigs[id] === sig) return;
  lastJobSigs[id] = sig;
  queue(jobMsg(tr, outSid));
}

function sendChunk(k) {
  var pair = reshape.chunkPair(messages, k);
  var sid = msgSid || liveSid || '';
  var i;
  var item;
  if (!pair.items.length) {
    return;
  }
  for (i = 0; i < pair.items.length; i++) {
    item = pair.items[i];
    queue({
      KIND: 5,
      SID: reshape.utf8clip(sid, 64),
      MSG_I: pair.k,
      MSG_N: pair.n,
      MSG_ROLE: item.role,
      MSG_TEXT: reshape.utf8clip(item.text || '', reshape.MSG_TEXT_MAX),
      MSG_MORE: pair.more
    });
  }
}

function jobTouchesLive(id) {
  var tr;
  if (!liveSid) return false;
  if (liveSid === id || liveSid === ('job:' + id)) return true;
  tr = trackers[id];
  if (tr && reshape.jobMatchesSid(tr, liveSid)) return true;
  return false;
}

function loadMessages(gen, sid, cb) {
  if (!sid) {
    cb();
    return;
  }
  inflight = http.xhr('GET',
    '/api/sessions/' + encodeURIComponent(sid) + '/messages?limit=32',
    null,
    function (err, data) {
      var list;
      var sig;
      if (gen !== reqGen) return;
      inflight = null;
      if (!err && data && sid === liveSid) {
        list = data.messages;
        if (!list || !list.slice) list = [];
        messages = reshape.takeMessages(list);
        msgSid = sid;
        sig = messages.map(function (m) {
          return (m.role || 0) + ':' + (m.text || '');
        }).join('\n');
        if (messages.length && sig !== lastMsgSig) {
          lastMsgSig = sig;
          sendChunk(0);
        }
      }
      cb();
    });
}

function sendSess(sid) {
  var i;
  var row = null;
  var tr = reshape.trackerForSid(trackers, sid);
  var last = lastBySid[sid] || '';
  for (i = 0; i < lastRows.length; i++) {
    if (lastRows[i].SID === sid) {
      row = lastRows[i];
      break;
    }
  }
  queue({
    KIND: 4,
    SID: reshape.utf8clip(sid, 64),
    TITLE: row ? row.TITLE : '',
    PROV: reshape.utf8clip((tr && tr.provider) || (row && row.PROV) || '', 16),
    PHASE: reshape.utf8clip((tr && tr.phase) || (row && row.PHASE) || '', 24),
    STARTED: tr && tr.started_at ? Math.floor(tr.started_at) : 0,
    LAST: reshape.clipLast(last),
    JID: reshape.utf8clip(tr && tr.id || '', 48),
    STATUS: reshape.utf8clip(tr && tr.status || '', 12)
  });
  if (tr) emitJob(tr, sid, true);
}

function queuePerm(tr) {
  var p = tr.perm;
  queue({
    KIND: 6,
    SID: reshape.utf8clip(reshape.jobSid(tr, liveSid), 64),
    JID: reshape.utf8clip(tr.id, 48),
    RID: reshape.utf8clip(p.request_id || '', 48),
    TOOL: reshape.utf8clip(p.tool_name || '', 24),
    DETAIL: reshape.utf8clip(p.detail || '', 120),
    TITLE: reshape.utf8clip('', 40)
  });
}

function queueQuest(tr) {
  var q = tr.quest;
  var questions = q.questions || [];
  var cls = reshape.classifyProceed(q);
  var qi;
  var oi;
  var qq;
  var opts;
  var lab;
  var note;
  for (qi = 0; qi < questions.length; qi++) {
    qq = questions[qi] || {};
    opts = qq.options || [];
    note = qq.note_for || '';
    for (oi = 0; oi < opts.length; oi++) {
      lab = typeof opts[oi] === 'string' ? opts[oi] : (opts[oi] && opts[oi].label) || '';
      queue({
        KIND: 7,
        SID: reshape.utf8clip(reshape.jobSid(tr, liveSid), 64),
        JID: reshape.utf8clip(tr.id, 48),
        RID: reshape.utf8clip(q.request_id || '', 48),
        QTEXT: oi === 0 ? reshape.utf8clip(qq.question || qq.header || '', 80) : '',
        QOPT: lab,
        Q_I: oi,
        Q_N: opts.length,
        Q_QI: qi,
        Q_QN: questions.length,
        QMULTI: qq.multi_select ? 1 : 0,
        QNOTEFOR: reshape.utf8clip(note, 40),
        QKIND: cls.qkind
      });
    }
  }
}

function postCancelQuestion(jid, rid) {
  if (postFn) {
    postFn('POST', '/api/jobs/' + encodeURIComponent(jid) + '/question',
      {request_id: rid, cancel: true}, function () {});
  } else {
    http.xhr('POST', '/api/jobs/' + encodeURIComponent(jid) + '/question',
      {request_id: rid, cancel: true}, function () {});
  }
}

function dismissTakeover(jid) {
  if (!sentTakeover) return;
  if (jid && sentTakeover.jid !== jid) return;
  queue({
    KIND: sentTakeover.kind,
    RID: '',
    JID: sentTakeover.jid,
    SID: sentTakeover.sid || ''
  });
  sentTakeover = null;
}

function sendTakeover() {
  var i;
  var row;
  var tr;
  var qs;
  var rid;
  var kind;
  var target = null;
  for (i = 0; i < lastRows.length; i++) {
    if (lastRows[i].STATE === 'needs_answer') {
      target = lastRows[i];
      break;
    }
  }
  if (!target) {
    if (sentTakeover) dismissTakeover(sentTakeover.jid);
    return;
  }
  tr = reshape.trackerForSid(trackers, target.SID);
  if (!tr) return;
  if (tr.perm) {
    rid = tr.perm.request_id || '';
    kind = 6;
    if (sentTakeover && sentTakeover.rid === rid && sentTakeover.kind === kind) {
      if (takeoverAcked) return;
      if (Date.now() - (sentTakeover.at || 0) < TAKEOVER_RETRY_MS) return;
    } else if (sentTakeover && sentTakeover.rid !== rid) {
      dismissTakeover(sentTakeover.jid);
    }
    queuePerm(tr);
    sentTakeover = {kind: kind, jid: tr.id, rid: rid, sid: target.SID, at: Date.now()};
    takeoverAcked = false;
    return;
  }
  if (tr.quest) {
    qs = tr.quest.questions || [];
    rid = tr.quest.request_id || '';
    kind = 7;
    if (!qs.length) return;
    if (cancelledRids[rid]) {
      if (sentTakeover) dismissTakeover(sentTakeover.jid);
      return;
    }
    if (qs.length > 4 || reshape.labelsOver80(tr.quest)) {
      cancelledRids[rid] = true;
      postCancelQuestion(tr.id, rid);
      queue({ KIND: 8, ERR: 'open on phone' });
      if (sentTakeover) dismissTakeover(sentTakeover.jid);
      return;
    }
    if (sentTakeover && sentTakeover.rid === rid && sentTakeover.kind === kind) {
      if (takeoverAcked) return;
      if (Date.now() - (sentTakeover.at || 0) < TAKEOVER_RETRY_MS) return;
    } else if (sentTakeover && sentTakeover.rid !== rid) {
      dismissTakeover(sentTakeover.jid);
    }
    queueQuest(tr);
    sentTakeover = {kind: kind, jid: tr.id, rid: rid, sid: target.SID, at: Date.now()};
    takeoverAcked = false;
  }
}

function noteCmd(cmd) {
  if (cmd === 6 || cmd === 7) takeoverAcked = true;
}

function getPing() {
  return ping;
}

function afterWork(gen) {
  if (gen !== reqGen) return;
  if (wantNow) {
    wantNow = false;
    tick();
    return;
  }
  schedule();
}

function remapLiveSid() {
  var tr;
  var real;
  if (!liveSid || liveSid.indexOf('job:') !== 0) return false;
  tr = reshape.trackerForSid(trackers, liveSid);
  real = reshape.enrolledSid(tr, '');
  if (real && real.indexOf('job:') !== 0 && real !== liveSid) {
    liveSid = real;
    return true;
  }
  return false;
}

function finishTick(gen, openedSid, needMsgs) {
  var i;
  var row;
  var tr;
  var remapped;
  if (gen !== reqGen) return;
  remapped = remapLiveSid();
  if (liveSid && (openedSid || remapped)) {
    sendSess(liveSid);
    needMsgs = true;
  }
  if (liveSid && workingNow()) needMsgs = true;
  for (i = 0; i < lastRows.length; i++) {
    row = lastRows[i];
    tr = reshape.trackerForSid(trackers, row.SID);
    if (tr) emitJob(tr, row.SID, false);
  }
  if (liveSid) {
    tr = reshape.trackerForSid(trackers, liveSid);
    if (!tr) emitJob(null, liveSid, true);
    else emitJob(tr, liveSid, false);
  }
  sendTakeover();
  if (needMsgs && liveSid) {
    loadMessages(gen, liveSid, function () {
      afterWork(gen);
    });
    return;
  }
  afterWork(gen);
}

function confirmEnded(gen, vanished, openedSid, needMsgs) {
  var id;
  var tr;
  var since;
  if (gen !== reqGen) return;
  if (!vanished.length) {
    finishTick(gen, openedSid, needMsgs);
    return;
  }
  id = vanished.shift();
  tr = trackers[id] || {};
  since = tr.cursor != null ? tr.cursor : (tr.event_count || 0);
  inflight = http.xhr('GET', '/api/jobs/' + encodeURIComponent(id) + '?since=' + since, null,
    function (err, snap) {
      var st;
      var ch;
      var refresh = needMsgs;
      if (gen !== reqGen) return;
      inflight = null;
      st = (!err && snap && snap.status) ? snap.status : '';
      ch = reshape.endChime(st, err);
      if (ch != null) {
        queue({ KIND: 9, CHIME: ch });
        console.log('[diff] ' + (ch === 2 ? 'err' : 'done') + ' jid=' + id);
        if (jobTouchesLive(id)) refresh = true;
        delete trackers[id];
        delete lastJobSigs[id];
      }
      confirmEnded(gen, vanished, openedSid, refresh);
    });
}

function runSnapshots(gen, snaps, diff, openedSid) {
  var s;
  var tr;
  var prevPhase;
  var now;
  if (gen !== reqGen) return;
  if (!snaps.length) {
    confirmEnded(gen, (diff.vanished || []).slice(), openedSid, false);
    return;
  }
  s = snaps.shift();
  inflight = http.xhr('GET', '/api/jobs/' + encodeURIComponent(s.id) + '?since=' + s.since, null,
    function (err, snap) {
      if (gen !== reqGen) return;
      inflight = null;
      if (!err && snap) {
        tr = trackers[s.id];
        prevPhase = tr && tr.phase;
        reshape.applySnapshot(trackers, s.id, snap);
        tr = trackers[s.id];
        now = Date.now();
        if (feedSeeded && !diff.sawAttn && prevPhase && tr && tr.phase && tr.phase !== prevPhase) {
          if (now - lastStatusAt > 2000) {
            queue({ KIND: 9, CHIME: 0 });
            lastStatusAt = now;
            console.log('[diff] tick jid=' + s.id);
          }
        }
      }
      runSnapshots(gen, snaps, diff, openedSid);
    });
}

function afterJobs(gen, jobs, openedSid) {
  var diff;
  var now;
  var snaps;
  var active;
  if (gen !== reqGen) return;
  active = reshape.activeJobs(jobs);
  diff = reshape.diffFeed(lastJobs, active, feedSeeded);
  reshape.mergeBriefs(trackers, active);
  lastJobs = active;
  now = Date.now();
  if (diff.chime != null) {
    queue({ KIND: 9, CHIME: diff.chime });
    lastStatusAt = now;
    if (diff.chime === 3) console.log('[diff] attn jid=' + (diff.attnId || ''));
    else if (diff.chime === 0) console.log('[diff] start');
  }
  feedSeeded = true;
  snaps = reshape.neededSnapshots(trackers, active);
  runSnapshots(gen, snaps, diff, openedSid);
}

function tick() {
  var path;
  var gen;
  var force;
  var openedSid;
  if (!ping) return;
  if (inflight) {
    wantNow = true;
    return;
  }
  gen = ++reqGen;
  force = forceJobs;
  forceJobs = false;
  openedSid = (force && liveSid) ? liveSid : null;
  path = ping.focus ? '/api/focus' : '/api/sessions?limit=12';
  inflight = http.xhr('GET', path, null, function (err, data) {
    var sessions;
    var needJobs;
    if (gen !== reqGen) return;
    inflight = null;
    if (err) {
      handleErr(err);
      schedule();
      return;
    }
    if (!pollOk) queue(helloFromPing(2));
    pollOk = true;
    sessions = (data && data.sessions);
    if (!sessions || !sessions.slice) sessions = [];
    dispatchFocus(sessions);
    needJobs = reshape.shouldGetJobs({
      force: force,
      focusRows: lastRows,
      lastJobs: lastJobs
    });
    if (!needJobs) {
      afterJobs(gen, lastJobs, openedSid);
      return;
    }
    inflight = http.xhr('GET', '/api/jobs', null, function (err2, data2) {
      var jobs;
      if (gen !== reqGen) return;
      inflight = null;
      if (err2) {
        handleErr(err2);
        schedule();
        return;
      }
      jobs = (data2 && data2.jobs);
      if (!jobs || !jobs.slice) jobs = [];
      afterJobs(gen, jobs, openedSid);
    });
  });
}

function stop() {
  reqGen++;
  if (timer) {
    clearTimeout(timer);
    timer = null;
  }
  if (inflight) {
    try { inflight.abort(); } catch (e) {}
    inflight = null;
  }
}

function start(pingData) {
  stop();
  ping = pingData || ping;
  pollOk = true;
  lastRows = [];
  lastJobs = [];
  lastSessions = [];
  lastBySid = {};
  trackers = {};
  feedSeeded = false;
  lastJobSigs = {};
  messages = [];
  msgSid = null;
  lastMsgSig = '';
  sentTakeover = null;
  takeoverAcked = false;
  cancelledRids = {};
  forceJobs = true;
  if (!ping) return;
  tick();
}

function now() {
  if (!ping) return;
  if (timer) {
    clearTimeout(timer);
    timer = null;
  }
  if (inflight) {
    wantNow = true;
    return;
  }
  tick();
}

function open(sid) {
  liveSid = sid || '';
  if (msgSid !== liveSid) {
    messages = [];
    msgSid = liveSid;
    lastMsgSig = '';
  }
  forceJobs = true;
  now();
}

function chunk(k, sid) {
  if (sid && msgSid && sid !== msgSid) return;
  sendChunk(k);
}

function burst(ms, holdMs) {
  burstUntil = Date.now() + (holdMs || 15000);
  console.log('[poll] burst');
  now();
}

function clearTakeover(jid) {
  dismissTakeover(jid);
}

module.exports = {
  init: init,
  setIdle: setIdle,
  setWork: setWork,
  setFont: setFont,
  start: start,
  stop: stop,
  now: now,
  open: open,
  chunk: chunk,
  burst: burst,
  clearTakeover: clearTakeover,
  noteCmd: noteCmd,
  ping: getPing,
  helloMsg: helloFromPing,
  resolveSid: function (sid) {
    return reshape.continueSid(sid, trackers);
  },
  queueHello: function () {
    if (!ping) return;
    queue(helloFromPing(pollOk ? 2 : 1));
  }
};
