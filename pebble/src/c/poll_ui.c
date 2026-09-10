#include "app.h"

#include <string.h>

TakeState g_take;

void takeover_leave(void) {
  chime_remind_stop();
  light_leave();
}

void takeover_dismiss(const char *jid) {
  if (jid && jid[0] && g_take.jid[0] && strcmp(g_take.jid, jid) != 0) {
    return;
  }
  if (perm_win_is_up() || quest_win_is_up()) {
    ar_dictation_stop();
  }
  if (perm_win_is_up()) {
    perm_win_pop();
  }
  if (quest_win_is_up()) {
    quest_win_pop();
  }
  g_take.rid[0] = '\0';
}

static void apply_sess(DictionaryIterator *iter) {
  const char *sid = msg_str(iter, MESSAGE_KEY_SID);
  const char *title = msg_str(iter, MESSAGE_KEY_TITLE);
  const char *prov = msg_str(iter, MESSAGE_KEY_PROV);
  if (g_sess.sid[0] && sid[0] && strcmp(sid, g_sess.sid) != 0) {
    if (strncmp(g_sess.sid, "job:", 4) != 0 || strncmp(sid, "job:", 4) == 0) {
      return;
    }
  }
  if (sid[0]) {
    utf8_copy(g_sess.sid, sizeof(g_sess.sid), sid);
  }
  if (title[0]) {
    utf8_copy(g_sess.title, sizeof(g_sess.title), title);
  }
  if (prov[0]) {
    utf8_copy(g_sess.prov, sizeof(g_sess.prov), prov);
  }
  utf8_copy(g_sess.phase, sizeof(g_sess.phase), msg_str(iter, MESSAGE_KEY_PHASE));
  utf8_copy(g_sess.last, sizeof(g_sess.last), msg_str(iter, MESSAGE_KEY_LAST));
  utf8_copy(g_sess.jid, sizeof(g_sess.jid), msg_str(iter, MESSAGE_KEY_JID));
  utf8_copy(g_sess.status, sizeof(g_sess.status), msg_str(iter, MESSAGE_KEY_STATUS));
  g_sess.started = msg_int(iter, MESSAGE_KEY_STARTED, 0);
  if (g_sess.started <= 0) {
    /* freeze elapsed at whatever we last showed */
  } else {
    g_sess.elapsed_frozen = 0;
  }
  g_sess.msg_got = 0;
  sess_win_mark_dirty();
  ar_update_tick();
}

static int msg_want(int32_t n, int32_t k) {
  (void)k;
  if (n <= 0) {
    return 0;
  }
  return 2;
}

static void apply_msg(DictionaryIterator *iter) {
  const char *sid = msg_str(iter, MESSAGE_KEY_SID);
  int32_t k = msg_int(iter, MESSAGE_KEY_MSG_I, 0);
  int32_t n = msg_int(iter, MESSAGE_KEY_MSG_N, 0);
  int32_t more = msg_int(iter, MESSAGE_KEY_MSG_MORE, 0);
  int32_t role = msg_int(iter, MESSAGE_KEY_MSG_ROLE, 1);
  const char *text = msg_str(iter, MESSAGE_KEY_MSG_TEXT);
  int slot;
  int want;

  if (sid[0] && g_sess.sid[0] && strcmp(sid, g_sess.sid) != 0) {
    if (strncmp(g_sess.sid, "job:", 4) != 0 || strncmp(sid, "job:", 4) == 0) {
      return;
    }
    utf8_copy(g_sess.sid, sizeof(g_sess.sid), sid);
  }
  if (n <= 0) {
    return;
  }
  want = msg_want(n, k);
  if ((g_sess.msg_got > 0 && k != g_sess.rx_k) || g_sess.msg_got >= want) {
    g_sess.msg_got = 0;
  }
  slot = g_sess.msg_got;
  if (slot < AR_MSG_PAIR) {
    utf8_copy(g_sess.rx[slot].text, sizeof(g_sess.rx[slot].text), text);
    g_sess.rx[slot].role = role;
    g_sess.msg_got = slot + 1;
    g_sess.rx_k = k;
  }
  if (g_sess.msg_got < want) {
    return;
  }
  for (slot = 0; slot < g_sess.msg_got && slot < AR_MSG_PAIR; slot++) {
    g_sess.msgs[slot] = g_sess.rx[slot];
  }
  g_sess.msg_shown = g_sess.msg_got;
  g_sess.msg_i = k;
  g_sess.msg_n = n;
  g_sess.msg_more = more;
  g_sess.msg_loaded = true;
  sess_win_mark_dirty();
}

static void apply_job(DictionaryIterator *iter) {
  const char *sid = msg_str(iter, MESSAGE_KEY_SID);
  const char *jid = msg_str(iter, MESSAGE_KEY_JID);
  const char *phase = msg_str(iter, MESSAGE_KEY_PHASE);
  const char *status = msg_str(iter, MESSAGE_KEY_STATUS);
  const char *prov = msg_str(iter, MESSAGE_KEY_PROV);
  int32_t started = msg_int(iter, MESSAGE_KEY_STARTED, 0);

  focus_apply_phase(sid, phase);

  if (g_sess.sid[0] &&
      (strcmp(g_sess.sid, sid) == 0 || (jid[0] && strcmp(g_sess.jid, jid) == 0))) {
    if (sid[0]) {
      utf8_copy(g_sess.sid, sizeof(g_sess.sid), sid);
    }
    if (jid[0]) {
      utf8_copy(g_sess.jid, sizeof(g_sess.jid), jid);
    }
    utf8_copy(g_sess.phase, sizeof(g_sess.phase), phase);
    utf8_copy(g_sess.status, sizeof(g_sess.status), status);
    if (prov[0]) {
      utf8_copy(g_sess.prov, sizeof(g_sess.prov), prov);
    }
    if (started <= 0 && g_sess.started > 0) {
      int elapsed = (int)(time(NULL) - (time_t)g_sess.started);
      g_sess.elapsed_frozen = elapsed < 0 ? 0 : elapsed;
    }
    g_sess.started = started;
    sess_win_mark_dirty();
    ar_update_tick();
  }
}

static void apply_perm(DictionaryIterator *iter) {
  const char *rid = msg_str(iter, MESSAGE_KEY_RID);
  const char *jid = msg_str(iter, MESSAGE_KEY_JID);
  if (!rid[0]) {
    takeover_dismiss(jid);
    return;
  }
  if (strcmp(g_take.rid, rid) != 0) {
    ar_dictation_stop();
  }
  memset(&g_take, 0, sizeof(g_take));
  utf8_copy(g_take.sid, sizeof(g_take.sid), msg_str(iter, MESSAGE_KEY_SID));
  utf8_copy(g_take.jid, sizeof(g_take.jid), jid);
  utf8_copy(g_take.rid, sizeof(g_take.rid), rid);
  utf8_copy(g_take.tool, sizeof(g_take.tool), msg_str(iter, MESSAGE_KEY_TOOL));
  utf8_copy(g_take.detail, sizeof(g_take.detail), msg_str(iter, MESSAGE_KEY_DETAIL));
  if (!g_take.detail[0]) {
    utf8_copy(g_take.detail, sizeof(g_take.detail), g_sess.title);
  }
  g_take.qkind = 0;
  perm_win_show();
}

static void apply_tx(DictionaryIterator *iter) {
  int32_t tx = msg_int(iter, MESSAGE_KEY_TX, -1);
  const char *err = msg_str(iter, MESSAGE_KEY_ERR);
  const char *sid = msg_str(iter, MESSAGE_KEY_SID);
  const char *jid;
  const char *status;
  bool in_flight = confirm_win_is_sending();
  bool show_new;
  bool sid_ok;

  if (tx == AR_TX_SENDING) {
    confirm_win_note_tx(tx);
    return;
  }

  if (tx == AR_TX_OK) {
    if (g_sess.sid[0]) {
      sid_ok = !sid[0] || strcmp(sid, g_sess.sid) == 0;
    } else {
      /* New compose: ignore a stale continue TX. */
      sid_ok = sid[0] && strncmp(sid, "job:", 4) == 0;
    }
    if (!sid_ok) {
      return;
    }
    show_new = !sess_win_is_up() && !perm_win_is_up() && !quest_win_is_up() &&
               (in_flight || new_win_is_up());
    confirm_win_note_tx(tx);
    if (in_flight) {
      confirm_win_pop();
    }
    jid = msg_str(iter, MESSAGE_KEY_JID);
    status = msg_str(iter, MESSAGE_KEY_STATUS);
    g_sess.busy = false;
    if (sid[0]) {
      utf8_copy(g_sess.sid, sizeof(g_sess.sid), sid);
    }
    if (jid[0]) {
      utf8_copy(g_sess.jid, sizeof(g_sess.jid), jid);
    }
    utf8_copy(g_sess.status, sizeof(g_sess.status), status[0] ? status : "starting");
    utf8_copy(g_sess.phase, sizeof(g_sess.phase), "working");
    g_sess.started = (int32_t)time(NULL);
    g_sess.elapsed_frozen = 0;
    if (show_new) {
      new_win_pop();
      sess_win_show(NULL);
    } else {
      sess_win_mark_dirty();
      ar_update_tick();
    }
    return;
  }
  if (tx == AR_TX_FAIL) {
    confirm_win_note_tx(tx);
    if (strcmp(err, "gone") == 0) {
      if (perm_win_is_up() || quest_win_is_up()) {
        return;
      }
      if (strncmp(g_sess.sid, "job:", 4) == 0) {
        /* Enroll not finished — keep the placeholder session. */
        return;
      }
      confirm_win_pop();
      sess_win_pop();
      return;
    }
    if (in_flight && strcmp(err, "busy") == 0) {
      confirm_win_pop();
      g_sess.busy = true;
      sess_win_mark_dirty();
    }
  }
}

static void maybe_show_quest(void) {
  if (!quest_win_ready()) {
    return;
  }
  if (g_take.qkind == 1) {
    quest_win_map_proceed(&g_take);
    perm_win_show();
    return;
  }
  quest_win_show();
}

static void apply_quest(DictionaryIterator *iter) {
  const char *rid = msg_str(iter, MESSAGE_KEY_RID);
  const char *jid = msg_str(iter, MESSAGE_KEY_JID);
  int32_t qi;
  int32_t qn;
  int32_t oi;
  int32_t on;
  if (!rid[0]) {
    takeover_dismiss(jid);
    return;
  }
  if (strcmp(g_take.rid, rid) != 0) {
    ar_dictation_stop();
    quest_win_reset();
    memset(&g_take, 0, sizeof(g_take));
    utf8_copy(g_take.sid, sizeof(g_take.sid), msg_str(iter, MESSAGE_KEY_SID));
    utf8_copy(g_take.jid, sizeof(g_take.jid), jid);
    utf8_copy(g_take.rid, sizeof(g_take.rid), rid);
  }
  g_take.qkind = msg_int(iter, MESSAGE_KEY_QKIND, 0);
  qi = msg_int(iter, MESSAGE_KEY_Q_QI, 0);
  qn = msg_int(iter, MESSAGE_KEY_Q_QN, 1);
  oi = msg_int(iter, MESSAGE_KEY_Q_I, 0);
  on = msg_int(iter, MESSAGE_KEY_Q_N, 0);
  quest_win_store((int)qi, (int)qn, (int)oi, (int)on,
                  (int)msg_int(iter, MESSAGE_KEY_QMULTI, 0), (int)g_take.qkind,
                  msg_str(iter, MESSAGE_KEY_QTEXT),
                  msg_str(iter, MESSAGE_KEY_QOPT),
                  msg_str(iter, MESSAGE_KEY_QNOTEFOR));
  maybe_show_quest();
}

void poll_ui_inbox(int32_t kind, DictionaryIterator *iter) {
  switch (kind) {
    case AR_KIND_SESS:
      apply_sess(iter);
      break;
    case AR_KIND_MSG:
      apply_msg(iter);
      break;
    case AR_KIND_JOB:
      apply_job(iter);
      break;
    case AR_KIND_PERM:
      apply_perm(iter);
      break;
    case AR_KIND_QUEST:
      apply_quest(iter);
      break;
    case AR_KIND_CHIME:
      chime_play(msg_int(iter, MESSAGE_KEY_CHIME, -1));
      break;
    case AR_KIND_TX:
      apply_tx(iter);
      break;
    default:
      break;
  }
}
