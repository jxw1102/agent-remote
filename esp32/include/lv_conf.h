/* LVGL 9 configuration — only overrides; lv_conf_internal.h supplies the
 * rest. Values follow TrailMate's T-LoRa Pager setup (16-bit color, 64 KB
 * pool, 33 ms refresh, Montserrat fonts). */
#ifndef LV_CONF_H
#define LV_CONF_H

#define LV_COLOR_DEPTH 16

#define LV_MEM_SIZE (256 * 1024U)
/* Pool lives in PSRAM: 60+ session rows × (button + label + badge glyph)
 * exhausted a 64 KB internal pool and crashed on NULL lv_malloc. */
#define LV_MEM_POOL_INCLUDE <esp32-hal-psram.h>
#define LV_MEM_POOL_ALLOC ps_malloc

#define LV_DEF_REFR_PERIOD 33
#define LV_DPI_DEF 130

#define LV_USE_LOG 0
#define LV_USE_ASSERT_NULL 0
#define LV_USE_ASSERT_MALLOC 0

#define LV_FONT_MONTSERRAT_12 1
#define LV_FONT_MONTSERRAT_14 1
#define LV_FONT_MONTSERRAT_18 1
#define LV_FONT_MONTSERRAT_24 1
#define LV_FONT_UNSCII_8 1  /* monospace, for the Live TUI pane */
#define LV_FONT_DEFAULT &lv_font_montserrat_14

#define LV_USE_THEME_DEFAULT 1
#define LV_USE_SNAPSHOT 1

/* Password fields: newest char stays readable for 1 s, then masks. */
#define LV_TEXTAREA_DEF_PWD_SHOW_TIME 1000

#endif /* LV_CONF_H */
