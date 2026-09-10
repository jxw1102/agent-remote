#include "app.h"

#include <string.h>

void utf8_copy(char *dst, size_t dst_sz, const char *src) {
  size_t i = 0;
  if (!dst || dst_sz == 0) {
    return;
  }
  if (!src) {
    dst[0] = '\0';
    return;
  }
  while (src[i] && i + 1 < dst_sz) {
    unsigned char c = (unsigned char)src[i];
    size_t need = 1;
    if ((c & 0x80) == 0) {
      need = 1;
    } else if ((c & 0xE0) == 0xC0) {
      need = 2;
    } else if ((c & 0xF0) == 0xE0) {
      need = 3;
    } else if ((c & 0xF8) == 0xF0) {
      need = 4;
    }
    if (i + need + 1 > dst_sz) {
      break;
    }
    memcpy(dst + i, src + i, need);
    i += need;
  }
  dst[i] = '\0';
}

const char *msg_str(DictionaryIterator *iter, uint32_t key) {
  Tuple *t = dict_find(iter, key);
  if (!t || t->type != TUPLE_CSTRING) {
    return "";
  }
  return t->value->cstring;
}

int32_t msg_int(DictionaryIterator *iter, uint32_t key, int32_t fallback) {
  Tuple *t = dict_find(iter, key);
  if (!t) {
    return fallback;
  }
  return t->value->int32;
}

static void keepalive_focus(void) {
  ar_watchdog_kick();
  if (g_ar.conn == AR_CONN_PHONE &&
      connection_service_peek_pebble_app_connection()) {
    ar_set_conn(AR_CONN_OK);
  }
}

static void inbox_received(DictionaryIterator *iter, void *context) {
  Tuple *kind_t = dict_find(iter, MESSAGE_KEY_KIND);
  int32_t kind;
  (void)context;
  if (!kind_t) {
    return;
  }
  kind = kind_t->value->int32;

  if (kind == AR_KIND_HELLO) {
    ArConn conn = (ArConn)msg_int(iter, MESSAGE_KEY_CONN, AR_CONN_UNKNOWN);
    /* Incomplete CONN=1 hellos omit MULTI/PROVS — keep the last catalogue. */
    if (dict_find(iter, MESSAGE_KEY_HOST)) {
      utf8_copy(g_ar.host, sizeof(g_ar.host), msg_str(iter, MESSAGE_KEY_HOST));
    }
    if (dict_find(iter, MESSAGE_KEY_VER)) {
      utf8_copy(g_ar.ver, sizeof(g_ar.ver), msg_str(iter, MESSAGE_KEY_VER));
    }
    if (dict_find(iter, MESSAGE_KEY_PROVS)) {
      utf8_copy(g_ar.provs, sizeof(g_ar.provs), msg_str(iter, MESSAGE_KEY_PROVS));
    }
    if (dict_find(iter, MESSAGE_KEY_MULTI)) {
      g_ar.multi = msg_int(iter, MESSAGE_KEY_MULTI, 0);
    }
    if (dict_find(iter, MESSAGE_KEY_FONT)) {
      g_ar.font = msg_int(iter, MESSAGE_KEY_FONT, 1);
      if (g_ar.font < 0) {
        g_ar.font = 0;
      }
      if (g_ar.font > 2) {
        g_ar.font = 2;
      }
      ar_mark_dirty();
    }
    if (!connection_service_peek_pebble_app_connection()) {
      ar_set_conn(AR_CONN_PHONE);
      return;
    }
    ar_set_conn(conn);
    ar_watchdog_kick();
    return;
  }

  if (kind == AR_KIND_FOCUS_ROW) {
    FocusRow row;
    int32_t gen = msg_int(iter, MESSAGE_KEY_FOCUS_GEN, 0);
    int32_t i = msg_int(iter, MESSAGE_KEY_FOCUS_I, -1);
    keepalive_focus();
    memset(&row, 0, sizeof(row));
    utf8_copy(row.sid, sizeof(row.sid), msg_str(iter, MESSAGE_KEY_SID));
    utf8_copy(row.title, sizeof(row.title), msg_str(iter, MESSAGE_KEY_TITLE));
    utf8_copy(row.state, sizeof(row.state), msg_str(iter, MESSAGE_KEY_STATE));
    utf8_copy(row.prov, sizeof(row.prov), msg_str(iter, MESSAGE_KEY_PROV));
    utf8_copy(row.phase, sizeof(row.phase), msg_str(iter, MESSAGE_KEY_PHASE));
    row.unread = msg_int(iter, MESSAGE_KEY_UNREAD, 0);
    focus_apply_row((int)i, gen, &row);
    return;
  }

  if (kind == AR_KIND_FOCUS_END) {
    keepalive_focus();
    focus_apply_end((int)msg_int(iter, MESSAGE_KEY_FOCUS_N, 0),
                    msg_int(iter, MESSAGE_KEY_FOCUS_GEN, 0));
    return;
  }

  if (kind == AR_KIND_ERR) {
    const char *err = msg_str(iter, MESSAGE_KEY_ERR);
    keepalive_focus();
    error_win_show(err[0] ? err : "error");
    ar_mark_dirty();
    return;
  }

  if (kind == AR_KIND_SESS || kind == AR_KIND_PERM || kind == AR_KIND_QUEST ||
      kind == AR_KIND_CHIME || kind == AR_KIND_JOB || kind == AR_KIND_MSG ||
      kind == AR_KIND_TX) {
    keepalive_focus();
    poll_ui_inbox(kind, iter);
  }
}

static void inbox_dropped(AppMessageResult reason, void *context) {
  (void)context;
  APP_LOG(APP_LOG_LEVEL_ERROR, "inbox drop %d", (int)reason);
}

static void outbox_failed(DictionaryIterator *iter, AppMessageResult reason, void *context) {
  Tuple *cmd;
  (void)context;
  APP_LOG(APP_LOG_LEVEL_ERROR, "outbox fail %d", (int)reason);
  if (!iter) {
    return;
  }
  cmd = dict_find(iter, MESSAGE_KEY_CMD);
  if (cmd && (cmd->value->int32 == AR_CMD_CONTINUE || cmd->value->int32 == AR_CMD_NEW)) {
    confirm_win_note_tx(AR_TX_FAIL);
  }
}

void msg_send_hello(void) {
  DictionaryIterator *iter;
  if (app_message_outbox_begin(&iter) != APP_MSG_OK) {
    return;
  }
  dict_write_int32(iter, MESSAGE_KEY_CMD, AR_CMD_HELLO_REQ);
  app_message_outbox_send();
  ar_watchdog_kick();
}

void msg_send_open(const char *sid) {
  DictionaryIterator *iter;
  if (app_message_outbox_begin(&iter) != APP_MSG_OK) {
    return;
  }
  dict_write_int32(iter, MESSAGE_KEY_CMD, AR_CMD_OPEN);
  dict_write_cstring(iter, MESSAGE_KEY_SID, sid ? sid : "");
  app_message_outbox_send();
}

bool msg_send_continue(const char *sid, const char *prompt) {
  DictionaryIterator *iter;
  if (!sid || !sid[0] || !prompt || !prompt[0]) {
    return false;
  }
  if (app_message_outbox_begin(&iter) != APP_MSG_OK) {
    return false;
  }
  dict_write_int32(iter, MESSAGE_KEY_CMD, AR_CMD_CONTINUE);
  dict_write_cstring(iter, MESSAGE_KEY_SID, sid);
  dict_write_cstring(iter, MESSAGE_KEY_PROMPT, prompt);
  dict_write_cstring(iter, MESSAGE_KEY_PROV, g_sess.prov);
  app_message_outbox_send();
  return true;
}

void msg_send_chunk(int32_t k) {
  DictionaryIterator *iter;
  if (k < 0) {
    k = 0;
  }
  g_sess.msg_got = 0;
  if (app_message_outbox_begin(&iter) != APP_MSG_OK) {
    return;
  }
  dict_write_int32(iter, MESSAGE_KEY_CMD, AR_CMD_CHUNK);
  dict_write_cstring(iter, MESSAGE_KEY_SID, g_sess.sid);
  dict_write_int32(iter, MESSAGE_KEY_CHUNK, k);
  app_message_outbox_send();
}

bool msg_send_new(const char *prov, const char *prompt) {
  DictionaryIterator *iter;
  if (!prompt || !prompt[0]) {
    return false;
  }
  if (app_message_outbox_begin(&iter) != APP_MSG_OK) {
    return false;
  }
  dict_write_int32(iter, MESSAGE_KEY_CMD, AR_CMD_NEW);
  dict_write_cstring(iter, MESSAGE_KEY_PROMPT, prompt);
  dict_write_cstring(iter, MESSAGE_KEY_PROV, prov ? prov : "");
  app_message_outbox_send();
  return true;
}

void msg_send_providers_req(void) {
  DictionaryIterator *iter;
  if (app_message_outbox_begin(&iter) != APP_MSG_OK) {
    return;
  }
  dict_write_int32(iter, MESSAGE_KEY_CMD, AR_CMD_PROVIDERS_REQ);
  app_message_outbox_send();
}

void msg_send_stop(const char *sid, const char *jid) {
  DictionaryIterator *iter;
  if (!jid || !jid[0]) {
    return;
  }
  if (app_message_outbox_begin(&iter) != APP_MSG_OK) {
    return;
  }
  dict_write_int32(iter, MESSAGE_KEY_CMD, AR_CMD_STOP);
  dict_write_cstring(iter, MESSAGE_KEY_SID, sid ? sid : "");
  dict_write_cstring(iter, MESSAGE_KEY_JID, jid);
  dict_write_cstring(iter, MESSAGE_KEY_PROV, g_sess.prov);
  app_message_outbox_send();
}

bool msg_send_perm(bool allow, const char *message) {
  DictionaryIterator *iter;
  if (app_message_outbox_begin(&iter) != APP_MSG_OK) {
    return false;
  }
  dict_write_int32(iter, MESSAGE_KEY_CMD, AR_CMD_PERM);
  dict_write_cstring(iter, MESSAGE_KEY_SID, g_take.sid);
  dict_write_cstring(iter, MESSAGE_KEY_JID, g_take.jid);
  dict_write_cstring(iter, MESSAGE_KEY_RID, g_take.rid);
  dict_write_int32(iter, MESSAGE_KEY_ALLOW, allow ? 1 : 0);
  if (message && message[0]) {
    dict_write_cstring(iter, MESSAGE_KEY_PROMPT, message);
  }
  app_message_outbox_send();
  return true;
}

static uint32_t question_bytes(bool cancel, const char *answers, const char *qnote) {
  uint32_t i32 = sizeof(int32_t);
  if (cancel) {
    return dict_calc_buffer_size(5, i32,
                                 (uint32_t)(strlen(g_take.sid) + 1),
                                 (uint32_t)(strlen(g_take.jid) + 1),
                                 (uint32_t)(strlen(g_take.rid) + 1),
                                 i32);
  }
  if (qnote && qnote[0]) {
    return dict_calc_buffer_size(6, i32,
                                 (uint32_t)(strlen(g_take.sid) + 1),
                                 (uint32_t)(strlen(g_take.jid) + 1),
                                 (uint32_t)(strlen(g_take.rid) + 1),
                                 (uint32_t)(strlen(answers ? answers : "") + 1),
                                 (uint32_t)(strlen(qnote) + 1));
  }
  return dict_calc_buffer_size(5, i32,
                               (uint32_t)(strlen(g_take.sid) + 1),
                               (uint32_t)(strlen(g_take.jid) + 1),
                               (uint32_t)(strlen(g_take.rid) + 1),
                               (uint32_t)(strlen(answers ? answers : "") + 1));
}

bool msg_send_question(bool cancel, const char *answers, const char *qnote) {
  DictionaryIterator *iter;
  bool drop_note = !qnote || !qnote[0];
  if (!cancel && question_bytes(false, answers, drop_note ? NULL : qnote) > AR_OUTBOX) {
    cancel = true;
    answers = NULL;
    qnote = NULL;
  }
  if (app_message_outbox_begin(&iter) != APP_MSG_OK) {
    return false;
  }
  dict_write_int32(iter, MESSAGE_KEY_CMD, AR_CMD_QUESTION);
  dict_write_cstring(iter, MESSAGE_KEY_SID, g_take.sid);
  dict_write_cstring(iter, MESSAGE_KEY_JID, g_take.jid);
  dict_write_cstring(iter, MESSAGE_KEY_RID, g_take.rid);
  if (cancel) {
    dict_write_int32(iter, MESSAGE_KEY_CANCEL, 1);
  } else {
    dict_write_cstring(iter, MESSAGE_KEY_ANSWERS, answers ? answers : "");
    if (qnote && qnote[0]) {
      dict_write_cstring(iter, MESSAGE_KEY_QNOTE, qnote);
    }
  }
  app_message_outbox_send();
  return !cancel;
}

void msg_init(void) {
  app_message_register_inbox_received(inbox_received);
  app_message_register_inbox_dropped(inbox_dropped);
  app_message_register_outbox_failed(outbox_failed);
  app_message_open(AR_INBOX, AR_OUTBOX);
}

void msg_deinit(void) {
  app_message_deregister_callbacks();
}
