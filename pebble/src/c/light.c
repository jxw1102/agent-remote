#include "app.h"

void light_attention(void) {
  light_set_color(GColorOrange);
  light_enable(true);
}

void light_leave(void) {
  light_enable(false);
  light_set_system_color();
}
