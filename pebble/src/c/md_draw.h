#pragma once

#include <pebble.h>

int16_t md_text_height(const char *text, int16_t width);
void md_draw(GContext *ctx, GRect box, const char *text, GColor color);
