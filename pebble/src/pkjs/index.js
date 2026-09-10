var clay = require('./clay');
var http = require('./http');
var reshape = require('./reshape');
var utf8clip = reshape.utf8clip;
var poll = require('./poll');

var base = '';
var token = '';
var pollIdle = 25;
var pollWork = 4;
var fontSize = 1;

var outbox = [];
var sending = false;
var retries = 0;
var pingGen = 0;
var pingXhr = null;
var sendSlot = http.makeSlot();
var gateGen = 0;
var gateXhr = null;

function clamp(n, lo, hi) {
  n = parseInt(n, 10);
  if (isNaN(n)) n = lo;
  if (n < lo) return lo;
  if (n > hi) return hi;
  return n;
}

function normalizeUrl(raw) {
  var s = String(raw || '').replace(/^\s+|\s+$/g, '').replace(/\/+$/, '');
  if (!s) return '';
  if (s.indexOf('http://') !== 0 && s.indexOf('https://') !== 0) {
    s = 'http://' + s;
  }
  return s;
}

function loadLocal() {
  base = localStorage.getItem('ar.base') || '';
  token = localStorage.getItem('ar.token') || '';
  pollIdle = clamp(localStorage.getItem('ar.pollIdle') || 25, 15, 45);
  pollWork = clamp(localStorage.getItem('ar.pollWork') || 4, 3, 8);
  fontSize = clamp(localStorage.getItem('ar.font') || 1, 0, 2);
  http.setCreds(base, token);
  if (poll.setFont) poll.setFont(fontSize);
}

function hasCreds() {
  return !!(base && token);
}

function dropNackHead() {
  var dropped = outbox.shift();
  if (!dropped || dropped.KIND !== 5) return;
  if (outbox[0] && outbox[0].KIND === 5 &&
      outbox[0].MSG_I === dropped.MSG_I &&
      String(outbox[0].SID || '') === String(dropped.SID || '')) {
    outbox.shift();
  }
}

function kickOutbox() {
  if (sending || !outbox.length) return;
  sending = true;
  var msg = outbox[0];
  Pebble.sendAppMessage(msg, function () {
    outbox.shift();
    sending = false;
    retries = 0;
    kickOutbox();
  }, function () {
    sending = false;
    if (retries < 1) {
      retries++;
      console.log('[msg] NACK retry');
      kickOutbox();
    } else {
      console.log('[msg] NACK drop');
      dropNackHead();
      retries = 0;
      kickOutbox();
    }
  });
}

function focusPending() {
  var i;
  for (i = 0; i < outbox.length; i++) {
    if (outbox[i] && outbox[i].FOCUS_GEN != null) return true;
  }
  return false;
}

function queueMsg(msg) {
  var i;
  var start;
  // FOCUS_GEN drops stale rows only. Never splice KIND=5 MSG — a pair
  // must arrive complete, and an in-flight MSG is already unacked.
  if (msg && msg.FOCUS_GEN != null) {
    start = sending ? 1 : 0;
    for (i = outbox.length - 1; i >= start; i--) {
      if (outbox[i].KIND === 5) continue;
      if (outbox[i].FOCUS_GEN != null && outbox[i].FOCUS_GEN < msg.FOCUS_GEN) {
        outbox.splice(i, 1);
      }
    }
  }
  outbox.push(msg);
  kickOutbox();
}

function pingThenHello() {
  pingGen++;
  gateGen++;
  var gen = pingGen;
  var hadSend = sendSlot.busy();
  poll.stop();
  if (pingXhr) {
    try { pingXhr.abort(); } catch (e) {}
    pingXhr = null;
  }
  sendSlot.abort();
  if (hadSend) {
    queueMsg({ KIND: 10, TX: 2 });
    queueMsg({ KIND: 9, CHIME: 2 });
  }
  if (gateXhr) {
    try { gateXhr.abort(); } catch (e) {}
    gateXhr = null;
  }
  if (!hasCreds()) {
    queueMsg({ KIND: 1, CONN: 4, FONT: fontSize });
    return;
  }
  queueMsg(poll.helloMsg ? poll.helloMsg(1) : { KIND: 1, CONN: 1 });
  pingXhr = http.xhr('GET', '/api/ping', null, function (err, data) {
    if (gen !== pingGen) return;
    pingXhr = null;
    if (err) {
      if (err.status === 401) {
        queueMsg({ KIND: 1, CONN: 3 });
        return;
      }
      queueMsg({ KIND: 1, CONN: 1 });
      queueMsg({ KIND: 8, ERR: 'daemon' });
      return;
    }
    var provs = '';
    if (data && data.providers && data.providers.length) {
      provs = data.providers.join(',');
    } else if (data && data.provider) {
      provs = String(data.provider);
    }
    queueMsg({
      KIND: 1,
      CONN: 2,
      HOST: utf8clip(data && data.host || '', 24),
      VER: utf8clip(data && data.version || '', 12),
      MULTI: data && data.multi ? 1 : 0,
      PROVS: utf8clip(provs, 64),
      FONT: fontSize
    });
    poll.setIdle(pollIdle);
    poll.setWork(pollWork);
    if (poll.setFont) poll.setFont(fontSize);
    poll.start(data);
  });
}

function jobPath(jid, tail) {
  return '/api/jobs/' + encodeURIComponent(jid || '') + tail;
}

function onGateErr(err, jid) {
  if (err && err.status === 404) {
    queueMsg({ KIND: 9, CHIME: 2 });
    poll.clearTakeover(jid);
  } else if (err) {
    queueMsg({ KIND: 9, CHIME: 2 });
    queueMsg({ KIND: 8, ERR: utf8clip(err.error || 'error', 48) });
  }
  poll.now();
}

function postPerm(p) {
  var jid = p.JID;
  var body = { request_id: p.RID, allow: !!p.ALLOW };
  var gen = ++gateGen;
  if (p.PROMPT) body.message = p.PROMPT;
  if (gateXhr) {
    try { gateXhr.abort(); } catch (e) {}
    gateXhr = null;
  }
  gateXhr = http.xhr('POST', jobPath(jid, '/permission'), body, function (err) {
    if (gen !== gateGen) return;
    gateXhr = null;
    onGateErr(err, jid);
  });
}

function postQuestion(p) {
  var jid = p.JID;
  var body = { request_id: p.RID };
  var gen = ++gateGen;
  if (p.CANCEL) {
    body.cancel = true;
  } else {
    body.answers = reshape.parseAnswers(p.ANSWERS);
    if (p.QNOTE) body.notes = reshape.parseNotes(p.QNOTE, body.answers.length);
  }
  if (gateXhr) {
    try { gateXhr.abort(); } catch (e) {}
    gateXhr = null;
  }
  gateXhr = http.xhr('POST', jobPath(jid, '/question'), body, function (err) {
    if (gen !== gateGen) return;
    gateXhr = null;
    onGateErr(err, jid);
  });
}

function continueOk(sid, jid, prov) {
  queueMsg({
    KIND: 10,
    TX: 1,
    SID: utf8clip(sid, 64),
    JID: utf8clip(jid, 48),
    STATUS: 'starting',
    PHASE: 'working'
  });
  if (jid) {
    queueMsg({
      KIND: 11,
      SID: utf8clip(sid, 64),
      JID: utf8clip(jid, 48),
      STATUS: 'starting',
      PHASE: 'working',
      STARTED: Math.floor(Date.now() / 1000),
      PROV: utf8clip(prov, 16)
    });
  }
  poll.burst();
}

function sendContinue(sid, prompt, prov, mode, allowRetry) {
  sendSlot.xhr('POST', '/api/sessions/' + encodeURIComponent(sid) + '/continue',
    { prompt: prompt, permission_mode: mode },
    function (err, data) {
      var jid;
      var real;
      if (err) {
        queueMsg({ KIND: 9, CHIME: 2 });
        if (err.status === 409) {
          queueMsg({ KIND: 10, TX: 2, ERR: 'busy' });
        } else if (err.status === 404) {
          real = (poll.resolveSid && poll.resolveSid(sid)) || sid;
          if (allowRetry && sid.indexOf('job:') === 0 && real.indexOf('job:') !== 0) {
            sendContinue(real, prompt, prov, mode, false);
            return;
          }
          if (sid.indexOf('job:') === 0) {
            queueMsg({ KIND: 10, TX: 2 });
            poll.burst();
            return;
          }
          queueMsg({ KIND: 10, TX: 2, ERR: 'gone' });
          poll.now();
        } else {
          queueMsg({ KIND: 10, TX: 2 });
          queueMsg({ KIND: 8, ERR: utf8clip(err.error || 'error', 48) });
        }
        return;
      }
      jid = (data && data.job_id) || '';
      continueOk(sid, jid, prov);
    });
}

function postContinue(p) {
  var sid = p.SID || '';
  var prompt = p.PROMPT || '';
  var ping = poll.ping && poll.ping();
  var prov = p.PROV || (ping && ping.provider) || '';
  var mode = reshape.permissionMode(ping, prov);
  if (!sid) return;
  if (poll.resolveSid) sid = poll.resolveSid(sid);
  queueMsg({ KIND: 10, TX: 0 });
  sendContinue(sid, prompt, prov, mode, true);
}

function failSend(errText) {
  queueMsg({ KIND: 9, CHIME: 2 });
  queueMsg({ KIND: 10, TX: 2 });
  queueMsg({ KIND: 8, ERR: utf8clip(errText || 'error', 48) });
}

function acceptNew(sid, jid, prov) {
  queueMsg({
    KIND: 10,
    TX: 1,
    SID: utf8clip(sid, 64),
    JID: utf8clip(jid, 48),
    STATUS: 'starting',
    PHASE: 'working'
  });
  if (jid) {
    queueMsg({
      KIND: 11,
      SID: utf8clip(sid, 64),
      JID: utf8clip(jid, 48),
      STATUS: 'starting',
      PHASE: 'working',
      STARTED: Math.floor(Date.now() / 1000),
      PROV: utf8clip(prov, 16)
    });
    poll.open(sid);
  }
  poll.burst();
}

function postNewBody(ping, prompt, prov, projects) {
  var built = reshape.buildNewSession(ping, prompt, prov, projects);
  if (built.error) {
    failSend(built.error);
    return;
  }
  sendSlot.xhr('POST', '/api/sessions/new', built.body, function (err, data) {
    var jid;
    var sid;
    if (err) {
      failSend(err.error || 'error');
      return;
    }
    jid = (data && data.job_id) || '';
    sid = jid ? ('job:' + jid) : '';
    acceptNew(sid, jid, prov);
  });
}

function postNew(p) {
  var ping = poll.ping && poll.ping();
  var prompt = p.PROMPT || '';
  var prov = p.PROV || '';
  var caps;
  if (!prompt) return;
  if (ping && ping.multi && !prov) {
    failSend('provider');
    return;
  }
  if (!prov && ping) prov = ping.provider || '';
  caps = reshape.capsFor(ping, prov);
  queueMsg({ KIND: 10, TX: 0 });
  if (!caps.requires_cwd) {
    postNewBody(ping, prompt, prov, null);
    return;
  }
  sendSlot.xhr('GET', '/api/projects', null, function (err, data) {
    if (err) {
      failSend(err.error || 'error');
      return;
    }
    postNewBody(ping, prompt, prov, data);
  });
}

function postStop(p) {
  var jid = p.JID;
  var ping = poll.ping && poll.ping();
  var caps;
  if (!jid) return;
  caps = reshape.capsFor(ping, p.PROV || (ping && ping.provider) || '');
  if (caps.stop === false) return;
  http.xhr('POST', jobPath(jid, '/stop'), {}, function (err) {
    if (err) {
      queueMsg({ KIND: 9, CHIME: 2 });
      if (err.status !== 404) {
        queueMsg({ KIND: 8, ERR: utf8clip(err.error || 'error', 48) });
      }
    }
    poll.now();
  });
}

function clayVal(parsed, id) {
  var v = parsed && parsed[id];
  if (v && typeof v === 'object' && v.value !== undefined) return v.value;
  return v;
}

poll.init({ queue: queueMsg, focusPending: focusPending });

Pebble.addEventListener('ready', function () {
  loadLocal();
  poll.setIdle(pollIdle);
  poll.setWork(pollWork);
  console.log('[ar] ready');
  pingThenHello();
});

Pebble.addEventListener('appmessage', function (e) {
  var payload = (e && e.payload) || {};
  var cmd = payload.CMD;
  console.log('[msg] CMD=' + cmd);
  if (cmd === 1) {
    pingThenHello();
  } else if (cmd === 2) {
    poll.now();
  } else if (cmd === 3) {
    poll.open(payload.SID);
  } else if (cmd === 4) {
    postContinue(payload);
  } else if (cmd === 5) {
    postNew(payload);
  } else if (cmd === 6) {
    if (poll.noteCmd) poll.noteCmd(cmd);
    postPerm(payload);
  } else if (cmd === 7) {
    if (poll.noteCmd) poll.noteCmd(cmd);
    postQuestion(payload);
  } else if (cmd === 8) {
    postStop(payload);
  } else if (cmd === 9) {
    poll.chunk(payload.CHUNK, payload.SID);
  } else if (cmd === 10) {
    if (poll.queueHello) poll.queueHello();
  }
});

Pebble.addEventListener('showConfiguration', function () {
  loadLocal();
  clay.meta.userData = {
    base: base,
    token: token,
    pollWork: pollWork,
    pollIdle: pollIdle,
    font: fontSize
  };
  Pebble.openURL(clay.generateUrl());
});

Pebble.addEventListener('webviewclosed', function (e) {
  if (!e || !e.response) return;
  var raw = e.response;
  var parsed;
  // Clay getSettings: skip decode when the firmware already gave JSON.
  if (!raw.match(/^\{/)) {
    try {
      raw = decodeURIComponent(raw);
    } catch (err) {}
  }
  try {
    parsed = JSON.parse(raw);
  } catch (err) {
    console.log('[ar] clay parse fail');
    return;
  }
  if (!parsed || typeof parsed !== 'object') return;
  if (!Object.prototype.hasOwnProperty.call(parsed, 'daemon_url') ||
      !Object.prototype.hasOwnProperty.call(parsed, 'token')) {
    return;
  }
  var url = normalizeUrl(clayVal(parsed, 'daemon_url') || '');
  var tok = String(clayVal(parsed, 'token') || '');
  var work = clamp(clayVal(parsed, 'poll_work') || 4, 3, 8);
  var idle = clamp(clayVal(parsed, 'poll_idle') || 25, 15, 45);
  var font = clamp(clayVal(parsed, 'font_size') != null ? clayVal(parsed, 'font_size') : 1, 0, 2);
  localStorage.setItem('ar.base', url);
  localStorage.setItem('ar.token', tok);
  localStorage.setItem('ar.pollWork', String(work));
  localStorage.setItem('ar.pollIdle', String(idle));
  localStorage.setItem('ar.font', String(font));
  console.log('[ar] clay save url=' + (url ? 'set' : 'empty'));
  loadLocal();
  pingThenHello();
});
