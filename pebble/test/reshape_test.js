var assert = require('assert');
var reshape = require('../src/pkjs/reshape');
var utf8clip = reshape.utf8clip;

function bytes(s) {
  return Buffer.byteLength(s, 'utf8');
}

assert.strictEqual(utf8clip('', 8), '');
assert.strictEqual(utf8clip(null, 8), '');
assert.strictEqual(utf8clip(undefined, 4), '');
assert.strictEqual(utf8clip('hello', 5), 'hello');
assert.strictEqual(utf8clip('hello', 3), 'hel');
assert.strictEqual(utf8clip('hello', 0), '');

// CJK: 你 / 好 are 3 UTF-8 bytes each.
assert.strictEqual(utf8clip('你好', 3), '你');
assert.strictEqual(utf8clip('你好', 5), '你');
assert.strictEqual(utf8clip('你好', 6), '你好');
assert.ok(bytes(utf8clip('你好世界', 8)) <= 8);

// BMP + CJK mix.
assert.strictEqual(utf8clip('A你B', 1), 'A');
assert.strictEqual(utf8clip('A你B', 4), 'A你');
assert.strictEqual(utf8clip('A你B', 5), 'A你B');

// Surrogate emoji (👍 is 4 UTF-8 bytes). After i++ the trail is included in slice.
var thumb = '\uD83D\uDC4D';
assert.strictEqual(thumb.length, 2);
assert.strictEqual(bytes(thumb), 4);
assert.strictEqual(utf8clip(thumb, 4), thumb);
assert.strictEqual(utf8clip(thumb, 3), '');
assert.strictEqual(utf8clip(thumb + 'abc', 4), thumb);
assert.strictEqual(utf8clip(thumb + 'abc', 5), thumb + 'a');

// Emoji + CJK: clip must not split the surrogate pair or a 3-byte han.
assert.strictEqual(utf8clip(thumb + '你', 4), thumb);
assert.strictEqual(utf8clip(thumb + '你', 6), thumb);
assert.strictEqual(utf8clip(thumb + '你', 7), thumb + '你');
assert.strictEqual(utf8clip('你' + thumb, 3), '你');
assert.strictEqual(utf8clip('你' + thumb, 7), '你' + thumb);
assert.strictEqual(utf8clip('hi' + thumb + '你', 2), 'hi');
assert.strictEqual(utf8clip('hi' + thumb + '你', 6), 'hi' + thumb);

// Titles: 40-byte FOCUS_ROW cap, including CJK / emoji.
var forty = 'abcdefghijklmnopqrstuvwxyzabcdefghijklmn';
assert.strictEqual(forty.length, 40);
assert.strictEqual(utf8clip(forty, 40), forty);
assert.strictEqual(utf8clip(forty + 'Z', 40), forty);
assert.ok(bytes(utf8clip('你好世界你好世界你好世界你好', 40)) <= 40);
assert.strictEqual(utf8clip('你好世界你好世界你好世界你好', 40), '你好世界你好世界你好世界你');
assert.strictEqual(bytes(utf8clip('你好世界你好世界你好世界你好', 40)), 39);
assert.ok(bytes(utf8clip('map ' + thumb + ' 路径', 40)) <= 40);
var clippedTitle = reshape.clipFocusRow({
  id: 'sid-1',
  title: '你好世界你好世界你好世界你好',
  provider: 'claude',
  focus_state: 'needs_answer'
}, 0, 1, 1);
assert.strictEqual(clippedTitle.TITLE, '你好世界你好世界你好世界你');
assert.ok(bytes(clippedTitle.TITLE) <= 40);
assert.strictEqual(clippedTitle.SID, 'sid-1');
assert.strictEqual(clippedTitle.STATE, 'needs_answer');
assert.strictEqual(clippedTitle.PROV, 'claude');
assert.strictEqual(clippedTitle.KIND, 2);
assert.strictEqual(clippedTitle.FOCUS_GEN, 1);

// Focus rank fallback (same comparator as daemon /api/focus).
function ids(rows) {
  return rows.map(function (r) { return r.id; });
}

var ranked = reshape.rankFocus([
  { id: 'fin', focus_state: 'turn_finished', last_active: 500 },
  { id: 'need-old', focus_state: 'needs_answer', last_active: 10 },
  { id: 'work', focus_state: 'working', last_active: 900 },
  { id: 'fail', focus_state: 'failed', last_active: 50 },
  { id: 'need-new', focus_state: 'needs_answer', last_active: 800 }
]);
assert.deepStrictEqual(ids(ranked), ['need-new', 'need-old', 'fail', 'work', 'fin']);

// Same state: more recent last_active first. ISO vs unix epoch.
ranked = reshape.rankFocus([
  { id: 'iso', focus_state: 'working', last_active: '2026-01-02T00:00:00Z' },
  { id: 'unix', focus_state: 'working', last_active: 2000000000 }
]);
assert.strictEqual(ranked[0].id, 'unix');

ranked = reshape.rankFocus([
  { id: 'older', focus_state: 'turn_finished', last_active: '2026-01-01T00:00:00Z' },
  { id: 'newer', focus_state: 'turn_finished', last_active: '2026-06-01T00:00:00Z' }
]);
assert.deepStrictEqual(ids(ranked), ['newer', 'older']);

// Sessions fallback without focus_state: infer running / pending / failed.
ranked = reshape.rankFocus([
  { id: 'idle', last_active: 9 },
  { id: 'run', running: true, last_active: 1 },
  { id: 'ask', pending_question: true, last_active: 2 },
  { id: 'err', status: 'error', last_active: 3 }
]);
assert.deepStrictEqual(ids(ranked), ['ask', 'err', 'run', 'idle']);
assert.strictEqual(reshape.inferState({ pending_permission: true }), 'needs_answer');
assert.strictEqual(reshape.inferState({ working: true }), 'working');
assert.strictEqual(reshape.inferState({ failed: true }), 'failed');

// Cap 12, keep daemon order prefix after sort.
var many = [];
var i;
for (i = 0; i < 20; i++) {
  many.push({
    id: 's' + i,
    focus_state: i === 0 ? 'needs_answer' : 'turn_finished',
    last_active: i
  });
}
ranked = reshape.rankFocus(many);
assert.strictEqual(ranked.length, 12);
assert.strictEqual(ranked[0].id, 's0');
assert.strictEqual(ranked[1].id, 's19');

// rankFocus does not mutate the input.
var src = [
  { id: 'b', focus_state: 'turn_finished', last_active: 1 },
  { id: 'a', focus_state: 'needs_answer', last_active: 0 }
];
ranked = reshape.rankFocus(src);
assert.strictEqual(src[0].id, 'b');
assert.strictEqual(ranked[0].id, 'a');

assert.strictEqual(reshape.sidMembershipOverHalf(
  [{ SID: 'a' }, { SID: 'b' }],
  [{ SID: 'a' }, { SID: 'b' }]
), false);
assert.strictEqual(reshape.sidMembershipOverHalf(
  [{ SID: 'a' }, { SID: 'b' }],
  [{ SID: 'c' }, { SID: 'd' }, { SID: 'e' }]
), true);

// /api/focus is already sorted — take the prefix, do not re-rank.
var daemonOrder = [
  { id: 'need', focus_state: 'needs_answer', last_active: 1 },
  { id: 'old-fin', focus_state: 'turn_finished', last_active: 1 },
  { id: 'new-fin', focus_state: 'turn_finished', last_active: 9 }
];
assert.deepStrictEqual(ids(reshape.takeFocus(daemonOrder)), ['need', 'old-fin', 'new-fin']);
assert.notDeepStrictEqual(ids(reshape.rankFocus(daemonOrder)), ids(reshape.takeFocus(daemonOrder)));
assert.strictEqual(reshape.takeFocus(many).length, 12);
assert.strictEqual(reshape.takeFocus(many)[0].id, 's0');
assert.strictEqual(reshape.takeFocus(many)[1].id, 's1');

assert.strictEqual(reshape.needsFullFocus([], [{ SID: 'a' }], false), true);
assert.strictEqual(reshape.needsFullFocus([{ SID: 'a' }], [{ SID: 'a' }], false), false);
assert.strictEqual(reshape.needsFullFocus([{ SID: 'a' }], [{ SID: 'a' }], true), true);
assert.strictEqual(reshape.needsFullFocus(
  [{ SID: 'a' }, { SID: 'b' }],
  [{ SID: 'c' }, { SID: 'd' }, { SID: 'e' }],
  false
), true);

// capsFor: multi provider_details vs single-harness root caps.
var multiPing = {
  multi: true,
  caps: { requires_cwd: true, interactive: true },
  provider_details: {
    grok: { caps: { requires_cwd: false, interactive: true, stop: true } },
    claude: { caps: { requires_cwd: true, interactive: true, stop: true } },
    deepseek: { caps: { requires_cwd: true, interactive: false, stop: true } }
  }
};
assert.strictEqual(reshape.capsFor(multiPing, 'grok').requires_cwd, false);
assert.strictEqual(reshape.capsFor(multiPing, 'claude').requires_cwd, true);
assert.strictEqual(reshape.capsFor(multiPing, 'deepseek').interactive, false);
assert.strictEqual(reshape.capsFor(multiPing, 'grok').interactive, true);
var singlePing = { provider: 'grok', caps: { requires_cwd: false, interactive: true } };
assert.strictEqual(reshape.capsFor(singlePing, 'grok').requires_cwd, false);
assert.strictEqual(reshape.capsFor(singlePing, 'grok').interactive, true);
assert.strictEqual(reshape.capsFor({ multi: false, caps: { interactive: true } }, 'claude').interactive, true);
assert.ok(!reshape.capsFor({ multi: true, caps: { requires_cwd: true } }, 'grok').interactive);

assert.strictEqual(reshape.permissionMode(multiPing, 'grok'), 'interactive');
assert.strictEqual(reshape.permissionMode(multiPing, 'claude'), 'interactive');
assert.strictEqual(reshape.permissionMode(multiPing, 'deepseek'), 'bypassPermissions');
assert.strictEqual(reshape.permissionMode(singlePing, 'grok'), 'interactive');
assert.strictEqual(reshape.permissionMode({ provider: 'deepseek', caps: { interactive: false } }, 'deepseek'),
  'bypassPermissions');
assert.strictEqual(reshape.permissionMode({ multi: true, caps: { interactive: true } }, 'grok'),
  'interactive');
assert.strictEqual(reshape.permissionMode({
  multi: true,
  caps: { interactive: true, requires_cwd: true },
  provider_details: { grok: { caps: { interactive: true } } }
}, 'grok'), 'interactive');
assert.strictEqual(reshape.permissionMode({
  multi: true,
  caps: { interactive: true },
  provider_details: { grok: { caps: { interactive: false } } }
}, 'grok'), 'bypassPermissions');
assert.ok(!reshape.capsFor({
  multi: true,
  caps: { interactive: true, requires_cwd: true },
  provider_details: { grok: { caps: { interactive: true } } }
}, 'deepseek').interactive);
assert.strictEqual(reshape.permissionMode({
  multi: true,
  caps: { interactive: true, requires_cwd: true },
  provider_details: { grok: { caps: { interactive: true } } }
}, 'deepseek'), 'bypassPermissions');
assert.strictEqual(reshape.permissionMode(null, 'x'), 'bypassPermissions');
assert.ok(reshape.permissionMode(multiPing, 'grok') === 'interactive' ||
  reshape.permissionMode(multiPing, 'grok') === 'bypassPermissions');
assert.notStrictEqual(reshape.permissionMode(multiPing, 'deepseek'), '');
assert.notStrictEqual(reshape.permissionMode(multiPing, 'deepseek'), undefined);

function permQ(opts, header, question) {
  return {
    request_id: 'j1-q1',
    questions: [{
      header: header == null ? 'Permission' : header,
      question: question || 'Do you want to proceed?',
      multi_select: false,
      options: opts.map(function (l) { return { label: l }; })
    }]
  };
}

var two = reshape.classifyProceed(permQ(['Yes', 'No']));
assert.strictEqual(two.qkind, 1);
assert.strictEqual(two.yes_label, 'Yes');
assert.strictEqual(two.no_label, 'No');
assert.strictEqual(two.always_label, '');

var dontAsk = reshape.classifyProceed(permQ([
  'Yes',
  "Yes, and don't ask again for grep commands…",
  'No (esc)'
]));
assert.strictEqual(dontAsk.qkind, 1);
assert.strictEqual(dontAsk.yes_label, 'Yes');
assert.strictEqual(dontAsk.no_label, 'No (esc)');
assert.notStrictEqual(dontAsk.no_label, 'No');
assert.ok(dontAsk.always_label.indexOf("don't ask") >= 0);

var editLeftover = reshape.classifyProceed(permQ([
  'Yes',
  'Yes, allow all edits during this session',
  'No (esc)'
]));
assert.strictEqual(editLeftover.qkind, 0);

var notPerm = reshape.classifyProceed(permQ(['Yes', 'No'], 'Ask', 'Pick a name'));
assert.strictEqual(notPerm.qkind, 0);

assert.strictEqual(reshape.classifyProceed({
  questions: [
    { header: 'Permission', options: [{ label: 'Yes' }, { label: 'No' }] },
    { header: 'Other', options: [{ label: 'A' }, { label: 'B' }] }
  ]
}).qkind, 0);

// ANSWERS packing / split. Never JSON.parse the raw label.
assert.deepStrictEqual(reshape.parseAnswers('Yes'), [['Yes']]);
assert.deepStrictEqual(reshape.parseAnswers('No (esc)'), [['No (esc)']]);
assert.deepStrictEqual(reshape.parseAnswers('A\x1fC'), [['A', 'C']]);
assert.deepStrictEqual(reshape.parseAnswers('A\x1eC'), [['A'], ['C']]);
assert.deepStrictEqual(reshape.parseAnswers('A\x1fB\x1eC'), [['A', 'B'], ['C']]);
assert.strictEqual(reshape.packAnswers([['No (esc)']]), 'No (esc)');
assert.strictEqual(reshape.packAnswers([['A', 'C']]), 'A\x1fC');
assert.strictEqual(reshape.packAnswers([['A'], ['C']]), 'A\x1eC');
assert.deepStrictEqual(
  reshape.parseAnswers(reshape.packAnswers([["Yes, and don't ask again…"]])),
  [["Yes, and don't ask again…"]]
);

// Outbox: omit empty QNOTE; cancel when dict > 768.
var sid = 'aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee';
var jid = '0123456789ab';
var rid = '0123456789ab-q1';
assert.ok(reshape.questionDictBytes(sid, jid, rid, 'Yes', '') <= 768);
assert.ok(reshape.questionDictBytes(sid, jid, rid, 'Yes', null) <=
  reshape.questionDictBytes(sid, jid, rid, 'Yes', 'note'));
assert.strictEqual(reshape.shouldCancelQuestion(sid, jid, rid, [['Yes']], ['', '']), false);
assert.deepStrictEqual('need tests\x1e'.split('\x1e'), ['need tests', '']);
assert.deepStrictEqual('\x1eplease'.split('\x1e'), ['', 'please']);
assert.deepStrictEqual('a\x1e\x1ec'.split('\x1e'), ['a', '', 'c']);
// 200-byte first note must still emit RS so notes[] stays two slots.
var twoHundred = new Array(201).join('x');
assert.strictEqual(twoHundred.length, 200);
var packedNotes = reshape.packNotes([twoHundred, ''], 200);
assert.ok(packedNotes.indexOf('\x1e') >= 0);
assert.strictEqual(packedNotes.split('\x1e').length, 2);
assert.deepStrictEqual(reshape.parseNotes(packedNotes, 2)[1], '');
assert.ok(reshape.utf8len(packedNotes.split('\x1e')[0]) <= 199);
assert.strictEqual(reshape.packNotes([twoHundred, '', 'c'], 200).split('\x1e').length, 3);
assert.strictEqual(reshape.packNotes(['hi', twoHundred], 200).split('\x1e')[0], 'hi');
assert.deepStrictEqual(reshape.parseNotes('only', 2), ['only', '']);
assert.deepStrictEqual(reshape.parseNotes('a\x1eb\x1ec', 2), ['a', 'b']);
assert.ok(reshape.questionDictBytes(sid, jid, rid, 'Other', '') <
  reshape.questionDictBytes(sid, jid, rid, 'Other', 'need tests'));
assert.strictEqual(reshape.shouldCancelQuestion(sid, jid, rid, [['Other']], ['']), false);
assert.strictEqual(reshape.shouldCancelQuestion(
  sid, jid, rid, [['Request changes']], ['ship v1']
), false);
assert.strictEqual(reshape.shouldCancelQuestion(
  new Array(65).join('s'), new Array(49).join('j'), new Array(49).join('r'),
  [[new Array(401).join('x')]],
  [new Array(201).join('n')]
), true);

// Jobs GET skip vs live Focus / last jobs / OPEN/HELLO.
assert.strictEqual(reshape.shouldGetJobs({
  focusRows: [{ STATE: 'turn_finished' }],
  lastJobs: []
}), false);
assert.strictEqual(reshape.shouldGetJobs({
  focusRows: [{ STATE: 'needs_answer' }],
  lastJobs: []
}), true);
assert.strictEqual(reshape.shouldGetJobs({
  focusRows: [{ STATE: 'working' }],
  lastJobs: []
}), true);
assert.strictEqual(reshape.shouldGetJobs({
  focusRows: [{ STATE: 'turn_finished' }],
  lastJobs: [{ id: 'j', status: 'running' }]
}), true);
assert.strictEqual(reshape.shouldGetJobs({ force: true, focusRows: [], lastJobs: [] }), true);
assert.strictEqual(reshape.shouldGetJobs({ hello: true, lastJobs: [] }), true);
assert.strictEqual(reshape.shouldGetJobs({ open: true, lastJobs: [] }), true);
assert.ok(reshape.focusHasLive([{ STATE: 'working' }]));
assert.ok(reshape.jobsLive([{ status: 'starting' }]));
assert.ok(reshape.jobsLive([{ status: 'done', pending_question: true }]));
assert.ok(!reshape.jobsLive([{ status: 'done' }]));
assert.strictEqual(reshape.jobIsActive({ status: 'done' }), false);
assert.strictEqual(reshape.jobIsActive({ status: 'error' }), false);
assert.strictEqual(reshape.jobIsActive({ status: 'done', pending_question: true }), true);
assert.strictEqual(reshape.activeJobs([
  { id: 'a', status: 'done' },
  { id: 'b', status: 'running' }
]).length, 1);
assert.strictEqual(reshape.shouldGetJobs({
  focusRows: [{ STATE: 'turn_finished' }],
  lastJobs: reshape.activeJobs([{ id: 'j', status: 'done' }])
}), false);

// Never since=0 after first subscribe.
var tr = { subscribed: true, cursor: 40 };
assert.strictEqual(reshape.snapshotSince(tr, { event_count: 80 }, 'delta'), 40);
assert.notStrictEqual(reshape.snapshotSince(tr, { event_count: 80 }, 'delta'), 0);
assert.strictEqual(reshape.snapshotSince({ subscribed: false }, { event_count: 80 }, 'pending'), 80);
assert.strictEqual(reshape.snapshotSince({ subscribed: true, cursor: 80 }, { event_count: 80 }, 'pending'), 80);
var snaps = reshape.neededSnapshots(
  reshape.mergeBriefs({}, [{
    id: 'j1', status: 'running', event_count: 50,
    pending_permission: true, session_id: 's1'
  }]),
  [{ id: 'j1', status: 'running', event_count: 50, pending_permission: true }]
);
assert.strictEqual(snaps.length, 1);
assert.strictEqual(snaps[0].since, 50);
assert.notStrictEqual(snaps[0].since, 0);

var tmap = reshape.mergeBriefs({}, [{
  id: 'j1', status: 'running', event_count: 12, session_id: 's1'
}]);
assert.strictEqual(tmap.j1.cursor, 12);
assert.strictEqual(tmap.j1.subscribed, true);
snaps = reshape.neededSnapshots(tmap, [{
  id: 'j1', status: 'running', event_count: 18
}]);
assert.strictEqual(snaps[0].since, 12);

reshape.applySnapshot(tmap, 'j1', {
  next_seq: 18,
  status: 'running',
  pending_permission: { request_id: 'j1-p1', tool_name: 'Bash', detail: 'ls' },
  events: [{ kind: 'tool', name: 'Bash' }]
});
assert.strictEqual(tmap.j1.cursor, 18);
assert.strictEqual(tmap.j1.perm.request_id, 'j1-p1');
assert.ok(tmap.j1.phase.indexOf('Allow') === 0);

var seeded = reshape.diffFeed([], [{
  id: 'j1', status: 'running', pending_permission: false
}], false);
assert.strictEqual(seeded.chime, null);
var attn = reshape.diffFeed(
  [{ id: 'j1', status: 'running', pending_permission: false }],
  [{ id: 'j1', status: 'running', pending_permission: true }],
  true
);
assert.strictEqual(attn.chime, 3);
var start = reshape.diffFeed([], [{ id: 'j2', status: 'starting' }], true);
assert.strictEqual(start.chime, 0);
var ended = reshape.diffFeed(
  [{ id: 'j1', status: 'running' }],
  [{ id: 'j1', status: 'done' }],
  true
);
assert.deepStrictEqual(ended.vanished, ['j1']);
assert.strictEqual(ended.chime, null);
var stillAsk = reshape.diffFeed(
  [{ id: 'j1', status: 'running', pending_question: true }],
  [{ id: 'j1', status: 'done', pending_question: true }],
  true
);
assert.deepStrictEqual(stillAsk.vanished, []);
var pruned = reshape.diffFeed(
  [{ id: 'j1', status: 'running' }],
  [],
  true
);
assert.deepStrictEqual(pruned.vanished, ['j1']);
assert.strictEqual(reshape.endChime('error'), 2);
assert.strictEqual(reshape.endChime('done'), 1);
assert.strictEqual(reshape.endChime('running'), null);
assert.strictEqual(reshape.endChime('', { status: 404 }), 1);
assert.strictEqual(reshape.endChime('', { status: 500 }), 2);
assert.strictEqual(reshape.endChime('', { status: 0 }), 2);

// stripMarkdown: unwrap fences and links; keep ** / # / lists for the watch.
var strip = reshape.stripMarkdown;
assert.strictEqual(strip('hello **world**'), 'hello **world**');
assert.strictEqual(strip('hello __world__'), 'hello __world__');
assert.strictEqual(strip('# Title\nbody'), '# Title\nbody');
assert.strictEqual(strip('##  Head'), '## Head');
assert.strictEqual(strip('see [docs](https://example.com/x) now'), 'see docs now');
assert.strictEqual(strip('```js\ncode()\n```\nmore'), 'code()\nmore');
assert.strictEqual(strip('  a   b\n\nc  '), 'a b\n\nc');
assert.strictEqual(strip('**bold** and [x](http://y)'), '**bold** and x');
assert.strictEqual(strip(null), '');
assert.ok(strip('# hi\n[a](b)').indexOf('#') >= 0);
assert.ok(strip('# hi\n[a](b)').indexOf('](') < 0);

// utf8clip 180-byte MSG_TEXT cap (BMP, CJK, surrogate emoji).
var ascii180 = new Array(181).join('a');
assert.strictEqual(ascii180.length, 180);
assert.strictEqual(utf8clip(ascii180, 180), ascii180);
assert.strictEqual(utf8clip(ascii180 + 'Z', 180), ascii180);
assert.strictEqual(bytes(utf8clip(ascii180 + 'Z', 180)), 180);
var han = new Array(61).join('你');
assert.strictEqual(bytes(han), 180);
assert.strictEqual(utf8clip(han, 180), han);
assert.strictEqual(utf8clip(han + '你', 180), han);
assert.ok(bytes(utf8clip(han + '你', 180)) <= 180);
var em = new Array(46).join(thumb);
assert.strictEqual(bytes(em), 180);
assert.strictEqual(utf8clip(em, 180), em);
assert.strictEqual(utf8clip(em + thumb, 180), em);
assert.ok(bytes(utf8clip('a' + em, 180)) <= 180);
assert.strictEqual(utf8clip('a' + em, 180), 'a' + em.slice(0, -2));
assert.ok(bytes(utf8clip(han + thumb, 180)) <= 180);

// takeMessages: drop status, clip MSG_TEXT_MAX, no markdown markers.
var taken = reshape.takeMessages([
  {role: 'user', text: 'one'},
  {role: 'status', text: 'thought hard'},
  {role: 'assistant', text: '**two**'},
  {role: 'status', text: 'worked'}
]);
assert.strictEqual(taken.length, 2);
assert.strictEqual(taken[0].role, 0);
assert.strictEqual(taken[0].text, 'one');
assert.strictEqual(taken[1].role, 1);
assert.strictEqual(taken[1].text, '**two**');

taken = reshape.takeMessages([
  {role: 'assistant', text: new Array(reshape.MSG_TEXT_MAX + 20).join('x')}
]);
assert.ok(bytes(taken[0].text) <= reshape.MSG_TEXT_MAX);
assert.strictEqual(taken[0].text.length, reshape.MSG_TEXT_MAX);
assert.strictEqual(reshape.MSG_TEXT_MAX, 700);
taken = reshape.takeMessages([
  {role: 'assistant', text: new Array(400).join('x')}
]);
assert.strictEqual(taken[0].text.length, 399);

assert.strictEqual(reshape.clipLast('see **bold** [x](http://y)'), 'see bold x');
assert.ok(bytes(reshape.clipLast(new Array(200).join('x'))) <= 96);
assert.strictEqual(reshape.clipLast('# Head\nmore'), 'Head more');

// A page is one user prompt + following assistant replies. Down pages older turns.
var turns = [];
for (i = 0; i < 4; i++) {
  turns.push({role: 0, text: 'u' + i});
  turns.push({role: 1, text: 'a' + i});
}
var pair = reshape.chunkPair(turns, 0);
assert.strictEqual(pair.n, 4);
assert.strictEqual(pair.k, 0);
assert.strictEqual(pair.more, 1);
assert.strictEqual(pair.items[0].role, 0);
assert.strictEqual(pair.items[0].text, 'u3');
assert.strictEqual(pair.items[1].role, 1);
assert.strictEqual(pair.items[1].text, 'a3');
pair = reshape.chunkPair(turns, 1);
assert.strictEqual(pair.items[0].text, 'u2');
assert.strictEqual(pair.items[1].text, 'a2');
assert.strictEqual(pair.more, 1);
pair = reshape.chunkPair(turns, 3);
assert.strictEqual(pair.items[0].text, 'u0');
assert.strictEqual(pair.items[1].text, 'a0');
assert.strictEqual(pair.more, 0);
pair = reshape.chunkPair(turns, 99);
assert.strictEqual(pair.k, 3);
assert.strictEqual(pair.more, 0);
pair = reshape.chunkPair([{role: 1, text: 'only'}], 0);
assert.strictEqual(pair.n, 1);
assert.strictEqual(pair.items[0].text, '');
assert.strictEqual(pair.items[1].text, 'only');
assert.strictEqual(pair.more, 0);
pair = reshape.chunkPair([], 0);
assert.strictEqual(pair.n, 0);
assert.strictEqual(pair.items.length, 0);
pair = reshape.chunkPair(turns, -3);
assert.strictEqual(pair.k, 0);

pair = reshape.chunkPair([
  {role: 0, text: 'ask'},
  {role: 1, text: 'one'},
  {role: 1, text: 'two'}
], 0);
assert.strictEqual(pair.n, 1);
assert.strictEqual(pair.items[0].text, 'ask');
assert.strictEqual(pair.items[1].text, 'one\ntwo');

var manyTurns = [];
for (i = 0; i < 10; i++) {
  manyTurns.push({role: 0, text: 'u' + i});
  manyTurns.push({role: 1, text: 'a' + i});
}
pair = reshape.chunkPair(manyTurns, 0);
assert.strictEqual(pair.n, 8);
assert.strictEqual(pair.items[0].text, 'u9');
pair = reshape.chunkPair(manyTurns, 7);
assert.strictEqual(pair.items[0].text, 'u2');
assert.strictEqual(pair.more, 0);

// pickCwd: most recently active matching provider; untagged fallback; never invent.
assert.strictEqual(reshape.pickCwd([
  { cwd: '/old', provider: 'claude', last_active: 1 },
  { cwd: '/new', provider: 'claude', last_active: 9 },
  { cwd: '/grok', provider: 'grok', last_active: 99 }
], 'claude'), '/new');
assert.strictEqual(reshape.pickCwd([
  { cwd: '/any', last_active: 5 }
], 'claude'), '/any');
assert.strictEqual(reshape.pickCwd([
  { cwd: '/g', provider: 'grok', last_active: 9 }
], 'claude'), '');
assert.strictEqual(reshape.pickCwd({ projects: [] }, 'claude'), '');
assert.strictEqual(reshape.pickCwd(null, 'claude'), '');
assert.strictEqual(reshape.pickCwd({
  projects: [
    { cwd: '/iso', provider: 'claude', last_active: '2026-01-01T00:00:00Z' },
    { cwd: '/unix', provider: 'claude', last_active: 2000000000 }
  ]
}, 'claude'), '/unix');

// New session body: provider required on multi; cwd only when THAT cap says so.
var grokNew = reshape.buildNewSession(multiPing, 'hi', 'grok', {
  projects: [{ cwd: '/Users/me/proj', provider: 'claude', last_active: 9 }]
});
assert.ok(!grokNew.error);
assert.strictEqual(grokNew.body.prompt, 'hi');
assert.strictEqual(grokNew.body.provider, 'grok');
assert.strictEqual(grokNew.body.permission_mode, 'interactive');
assert.ok(!Object.prototype.hasOwnProperty.call(grokNew.body, 'cwd'));

var claudeNew = reshape.buildNewSession(multiPing, 'hi', 'claude', {
  projects: [
    { cwd: '/old', provider: 'claude', last_active: 1 },
    { cwd: '/new', provider: 'claude', last_active: 8 }
  ]
});
assert.strictEqual(claudeNew.body.cwd, '/new');
assert.strictEqual(claudeNew.body.permission_mode, 'interactive');

var noCwd = reshape.buildNewSession(multiPing, 'hi', 'claude', { projects: [] });
assert.strictEqual(noCwd.error, 'no cwd');
assert.ok(!noCwd.body);

assert.strictEqual(reshape.buildNewSession(multiPing, 'hi', '', null).error, 'provider');
assert.strictEqual(reshape.buildNewSession(multiPing, 'hi', null, null).error, 'provider');

var dsNew = reshape.buildNewSession(multiPing, 'hi', 'deepseek', {
  projects: [{ cwd: '/ds', provider: 'deepseek', last_active: 1 }]
});
assert.strictEqual(dsNew.body.permission_mode, 'bypassPermissions');
assert.strictEqual(dsNew.body.cwd, '/ds');

var singleGrokNew = reshape.buildNewSession(singlePing, 'hello', '', null);
assert.ok(!singleGrokNew.error);
assert.ok(!Object.prototype.hasOwnProperty.call(singleGrokNew.body, 'provider'));
assert.ok(!Object.prototype.hasOwnProperty.call(singleGrokNew.body, 'cwd'));
assert.strictEqual(singleGrokNew.body.permission_mode, 'interactive');

var singleClaude = { provider: 'claude', caps: { requires_cwd: true, interactive: true } };
assert.strictEqual(reshape.buildNewSession(singleClaude, 'hi', '', { projects: [] }).error, 'no cwd');
assert.strictEqual(reshape.buildNewSession(singleClaude, 'hi', 'claude', {
  projects: [{ cwd: '/proj', last_active: 1 }]
}).body.cwd, '/proj');
assert.ok(!Object.prototype.hasOwnProperty.call(reshape.buildNewSession(singleClaude, 'hi', 'claude', {
  projects: [{ cwd: '/proj', last_active: 1 }]
}).body, 'provider'));

// details exist but PROV missing from details: empty caps, not the multi root union.
var missingProvPing = {
  multi: true,
  caps: { requires_cwd: true, interactive: true },
  provider_details: { claude: { caps: { requires_cwd: true, interactive: true } } }
};
assert.ok(!reshape.capsFor(missingProvPing, 'grok').requires_cwd);
var grokMissing = reshape.buildNewSession(missingProvPing, 'hi', 'grok', {
  projects: [{ cwd: '/x', provider: 'claude', last_active: 1 }]
});
assert.ok(!grokMissing.error);
assert.ok(!Object.prototype.hasOwnProperty.call(grokMissing.body, 'cwd'));
assert.strictEqual(grokMissing.body.permission_mode, 'bypassPermissions');

assert.strictEqual(reshape.enrolledSid({ session_id: 's1', id: 'j1' }, 'job:j1'), 's1');
assert.strictEqual(reshape.enrolledSid({ new_session_id: 's2', id: 'j1' }, 'job:j1'), 's2');
assert.strictEqual(reshape.enrolledSid({ id: 'j1' }, 'job:j1'), 'job:j1');
assert.strictEqual(reshape.enrolledSid({ id: 'j1' }, ''), 'job:j1');

var enrollMap = { j1: { id: 'j1', new_session_id: 's-real' } };
assert.strictEqual(reshape.continueSid('job:j1', enrollMap), 's-real');
assert.strictEqual(reshape.continueSid('job:j1', {}), 'job:j1');
assert.strictEqual(reshape.continueSid('already', enrollMap), 'already');

console.log('ok');
