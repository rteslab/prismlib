/*
 * led.h — PRISM LED control (Raspberry Pi GPIO via libgpiod v2 + RUN(ACT) sysfs)
 *
 * Requires libgpiod-dev (v2) and -lgpiod at link time.
 *
 * LED identifiers (GPIO pin numbers):
 *   PRISM_LED_PWR   = GPIO 21 (RUN/ACT LED line)
 *   PRISM_LED_ERR   = GPIO 20
 *   PRISM_LED_ALARM = GPIO 16
 */
#ifndef LED_H
#define LED_H

#include <gpiod.h>

#define _RUN_LED_PATH "/sys/class/leds/ACT"
#define _GPIO_CHIP    "/dev/gpiochip0"

#define PRISM_LED_PWR   21
#define PRISM_LED_ERR   20
#define PRISM_LED_ALARM 16

typedef struct LedCtx_s {
    struct gpiod_chip         *chip;
    struct gpiod_line_request *request;      /* single request for all GPIO LEDs */
    char  act_trigger_orig[64];              /* saved RUN trigger;    empty = not saved */
    char  act_brightness_orig[8];            /* saved RUN brightness; restored with trigger */
} LedCtx_t;

void led_ctx_init(LedCtx_t *ctx);
void led_ctx_open(LedCtx_t *ctx);     /* GPIO request: PWR=1 ERR=0 ALARM=0 */
void led_ctx_close(LedCtx_t *ctx);    /* GPIO release + restore RUN trigger  */

int  led_write(LedCtx_t *ctx, int led_id, int state);
int  run_led_kernel(LedCtx_t *ctx, int enable);
int  run_led_write(LedCtx_t *ctx, int state);

#endif /* LED_H */
