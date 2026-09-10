#include "md_draw.h"
#include "app.h"

#include <string.h>

typedef struct {
  GContext *ctx;
  int16_t x0;
  int16_t y0;
  int16_t width;
  int16_t x;
  int16_t y;
  int16_t line_h;
  bool draw;
  bool bold;
  bool code;
  bool any;
  GColor color;
} MdRun;

static int utf8_clen(const char *s) {
  unsigned char c;
  if (!s || !s[0]) {
    return 0;
  }
  c = (unsigned char)s[0];
  if ((c & 0x80) == 0) {
    return 1;
  }
  if ((c & 0xE0) == 0xC0) {
    return 2;
  }
  if ((c & 0xF0) == 0xE0) {
    return 3;
  }
  if ((c & 0xF8) == 0xF0) {
    return 4;
  }
  return 1;
}

static int16_t line_h_for_font(void) {
  if (g_ar.font >= 2) {
    return 28;
  }
  if (g_ar.font == 1) {
    return 22;
  }
  return 18;
}

static GFont run_font(const MdRun *r) {
  return r->bold ? ar_body_font_bold() : ar_body_font();
}

static int16_t meas_n(GFont font, const char *s, int n) {
  char buf[48];
  GSize sz;
  if (n <= 0) {
    return 0;
  }
  if (n >= (int)sizeof(buf)) {
    n = (int)sizeof(buf) - 1;
  }
  memcpy(buf, s, (size_t)n);
  buf[n] = '\0';
  sz = graphics_text_layout_get_content_size(
      buf, font, GRect(0, 0, 400, 40),
      GTextOverflowModeTrailingEllipsis, GTextAlignmentLeft);
  return sz.w;
}

static void md_newline(MdRun *r) {
  r->x = r->x0;
  r->y = (int16_t)(r->y + r->line_h);
}

static void emit(MdRun *r, const char *s, int n) {
  GFont font;
  int16_t w;
  int16_t avail;
  char buf[48];
  GColor col;
  if (n <= 0) {
    return;
  }
  font = run_font(r);
  if (n >= (int)sizeof(buf)) {
    while (n > 0) {
      int chunk = n;
      int i = 0;
      if (chunk > 36) {
        chunk = 36;
      }
      while (i + utf8_clen(s + i) <= chunk && i < chunk) {
        i += utf8_clen(s + i);
      }
      if (i <= 0) {
        i = chunk;
      }
      emit(r, s, i);
      s += i;
      n -= i;
    }
    return;
  }
  w = meas_n(font, s, n);
  avail = (int16_t)(r->x0 + r->width - r->x);
  if (w > avail && r->x > r->x0) {
    md_newline(r);
    avail = r->width;
  }
  if (w > avail && n > 1) {
    int take = 0;
    int cl;
    int16_t acc = 0;
    int16_t cw;
    while (take < n) {
      cl = utf8_clen(s + take);
      if (cl < 1) {
        cl = 1;
      }
      if (take + cl > n) {
        break;
      }
      cw = meas_n(font, s + take, cl);
      if (take > 0 && acc + cw > avail) {
        break;
      }
      acc = (int16_t)(acc + cw);
      take += cl;
    }
    if (take <= 0) {
      take = utf8_clen(s);
      if (take < 1) {
        take = 1;
      }
      if (take > n) {
        take = n;
      }
    }
    emit(r, s, take);
    emit(r, s + take, n - take);
    return;
  }
  memcpy(buf, s, (size_t)n);
  buf[n] = '\0';
  if (r->draw && r->ctx) {
    col = r->code ? GColorElectricBlue : r->color;
    graphics_context_set_text_color(r->ctx, col);
    graphics_draw_text(r->ctx, buf, font,
                       GRect(r->x, r->y, (int16_t)(w + 4), r->line_h),
                       GTextOverflowModeTrailingEllipsis, GTextAlignmentLeft, NULL);
  }
  r->x = (int16_t)(r->x + w);
  r->any = true;
}

static bool starts_list(const char *p, int *skip) {
  int n = 0;
  if ((p[0] == '-' || p[0] == '*' || p[0] == '+') && p[1] == ' ') {
    *skip = 2;
    return true;
  }
  while (p[n] >= '0' && p[n] <= '9') {
    n++;
  }
  if (n > 0 && p[n] == '.' && p[n + 1] == ' ') {
    *skip = n + 2;
    return true;
  }
  *skip = 0;
  return false;
}

static bool starts_heading(const char *p, int *skip) {
  int n = 0;
  while (p[n] == '#') {
    n++;
  }
  if (n > 0 && n <= 6 && p[n] == ' ') {
    *skip = n + 1;
    return true;
  }
  *skip = 0;
  return false;
}

static int word_len(const char *p) {
  int n = 0;
  while (p[n] && p[n] != ' ' && p[n] != '\n' && p[n] != '`') {
    if (p[n] == '*' && p[n + 1] == '*') {
      break;
    }
    if (p[n] == '_' && p[n + 1] == '_') {
      break;
    }
    n++;
  }
  return n;
}

static int16_t md_layout(GContext *ctx, GRect box, const char *text, GColor color, bool draw) {
  MdRun r;
  const char *p;
  int skip;
  bool line_start;
  memset(&r, 0, sizeof(r));
  r.ctx = ctx;
  r.x0 = box.origin.x;
  r.y0 = box.origin.y;
  r.width = box.size.w;
  r.x = r.x0;
  r.y = r.y0;
  r.line_h = line_h_for_font();
  r.draw = draw;
  r.color = color;
  if (!text || !text[0] || r.width < 8) {
    return 16;
  }
  p = text;
  line_start = true;
  while (*p) {
    if (*p == '\r') {
      p++;
      continue;
    }
    if (*p == '\n') {
      if (p[1] == '\n') {
        md_newline(&r);
        r.y = (int16_t)(r.y + 4);
        while (*p == '\n' || *p == '\r') {
          p++;
        }
      } else {
        md_newline(&r);
        p++;
      }
      line_start = true;
      r.bold = false;
      r.code = false;
      continue;
    }
    if (line_start) {
      line_start = false;
      if (starts_heading(p, &skip)) {
        p += skip;
        r.bold = true;
        continue;
      }
      if (starts_list(p, &skip)) {
        p += skip;
        emit(&r, "• ", 2);
        continue;
      }
    }
    if (p[0] == '*' && p[1] == '*') {
      r.bold = !r.bold;
      p += 2;
      continue;
    }
    if (p[0] == '_' && p[1] == '_') {
      r.bold = !r.bold;
      p += 2;
      continue;
    }
    if (*p == '`') {
      r.code = !r.code;
      p++;
      continue;
    }
    if (*p == ' ') {
      int16_t sw = meas_n(run_font(&r), " ", 1);
      if (r.x > r.x0) {
        if (r.x + sw > r.x0 + r.width) {
          md_newline(&r);
        } else {
          r.x = (int16_t)(r.x + sw);
        }
      }
      p++;
      continue;
    }
    skip = word_len(p);
    if (skip <= 0) {
      emit(&r, p, 1);
      p++;
      continue;
    }
    emit(&r, p, skip);
    p += skip;
  }
  if (!r.any) {
    return 16;
  }
  if (r.x > r.x0) {
    return (int16_t)(r.y - r.y0 + r.line_h + 2);
  }
  return (int16_t)(r.y - r.y0 + 2);
}

int16_t md_text_height(const char *text, int16_t width) {
  return md_layout(NULL, GRect(0, 0, width, 4000), text, GColorWhite, false);
}

void md_draw(GContext *ctx, GRect box, const char *text, GColor color) {
  md_layout(ctx, box, text, color, true);
}
