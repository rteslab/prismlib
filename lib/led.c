/*
 * led.c — PRISM LED control (Raspberry Pi GPIO via libgpiod v2 + RUN(ACT) sysfs)
 *
 * Uses libgpiod v2 C API.
 * Requires: sudo apt-get install -y libgpiod-dev
 * Link with: -lgpiod
 */
#include "led.h"
#include "../include/prismlib.h"

#include <string.h>
#include <stdio.h>

void led_ctx_init(LedCtx_t *ctx)
{
    memset(ctx, 0, sizeof(*ctx));
}

void led_ctx_open(LedCtx_t *ctx)
{
    ctx->chip = gpiod_chip_open(_GPIO_CHIP);
    if (!ctx->chip)
        return;

    struct gpiod_line_settings  *settings = gpiod_line_settings_new();
    struct gpiod_line_config    *line_cfg = gpiod_line_config_new();
    struct gpiod_request_config *req_cfg  = gpiod_request_config_new();

    if (!settings || !line_cfg || !req_cfg)
        goto cleanup;

    gpiod_line_settings_set_direction(settings, GPIOD_LINE_DIRECTION_OUTPUT);
    gpiod_line_settings_set_output_value(settings, GPIOD_LINE_VALUE_INACTIVE);

    unsigned int offsets[] = { PRISM_LED_PWR, PRISM_LED_ERR, PRISM_LED_ALARM };
    gpiod_line_config_add_line_settings(line_cfg, offsets, 3, settings);
    gpiod_request_config_set_consumer(req_cfg, "prismlib");

    ctx->request = gpiod_chip_request_lines(ctx->chip, req_cfg, line_cfg);
    if (ctx->request)
        gpiod_line_request_set_value(ctx->request, PRISM_LED_PWR,
                                     GPIOD_LINE_VALUE_ACTIVE);   /* PWR ON */

cleanup:
    if (settings) gpiod_line_settings_free(settings);
    if (line_cfg) gpiod_line_config_free(line_cfg);
    if (req_cfg)  gpiod_request_config_free(req_cfg);

    if (!ctx->request) {
        gpiod_chip_close(ctx->chip);
        ctx->chip = NULL;
    }
}

void led_ctx_close(LedCtx_t *ctx)
{
    /* Restore RUN(ACT) trigger if we took over */
    if (ctx->act_trigger_orig[0] != '\0')
        run_led_kernel(ctx, 1);

    if (ctx->request) {
        gpiod_line_request_release(ctx->request);
        ctx->request = NULL;
    }
    if (ctx->chip) {
        gpiod_chip_close(ctx->chip);
        ctx->chip = NULL;
    }
}

int led_write(LedCtx_t *ctx, int led_id, int state)
{
    if (led_id != PRISM_LED_PWR &&
        led_id != PRISM_LED_ERR &&
        led_id != PRISM_LED_ALARM)
        return RESULT_BAD_PARAMETER;
    if (!ctx->request)
        return RESULT_RESOURCE_UNAVAIL;

    enum gpiod_line_value val = state ? GPIOD_LINE_VALUE_ACTIVE
                                      : GPIOD_LINE_VALUE_INACTIVE;
    gpiod_line_request_set_value(ctx->request, (unsigned int)led_id, val);
    return RESULT_SUCCESS;
}

int run_led_kernel(LedCtx_t *ctx, int enable)
{
    FILE *f;
    if (enable) {
        /* Restore saved trigger */
        if (ctx->act_trigger_orig[0] == '\0')
            return RESULT_SUCCESS;

        /* Write brightness first (while "none" trigger is still active so the
         * write takes effect), then switch trigger.  mmc0/heartbeat etc. will
         * inherit this brightness value and the LED resumes its normal state. */
        if (ctx->act_brightness_orig[0] != '\0') {
            f = fopen(_RUN_LED_PATH "/brightness", "w");
            if (f) {
                fputs(ctx->act_brightness_orig, f);
                fclose(f);
            }
        }

        f = fopen(_RUN_LED_PATH "/trigger", "w");
        if (!f)
            return RESULT_RESOURCE_UNAVAIL;
        fprintf(f, "%s\n", ctx->act_trigger_orig);
        fclose(f);

        ctx->act_trigger_orig[0]    = '\0';
        ctx->act_brightness_orig[0] = '\0';
    } else {
        /* Take over: save current trigger + brightness, switch to manual */
        if (ctx->act_trigger_orig[0] != '\0')
            return RESULT_SUCCESS;

        /* Save current brightness */
        ctx->act_brightness_orig[0] = '\0';
        f = fopen(_RUN_LED_PATH "/brightness", "r");
        if (f) {
            size_t n = fread(ctx->act_brightness_orig,
                             1, sizeof(ctx->act_brightness_orig) - 1, f);
            fclose(f);
            ctx->act_brightness_orig[n] = '\0';
            /* Trim trailing whitespace / newline */
            while (n > 0 && (ctx->act_brightness_orig[n-1] == '\n' ||
                              ctx->act_brightness_orig[n-1] == ' '))
                ctx->act_brightness_orig[--n] = '\0';
        }

        /* Save current trigger (extract from "[name] ..." format) */
        f = fopen(_RUN_LED_PATH "/trigger", "r");
        if (!f)
            return RESULT_RESOURCE_UNAVAIL;
        char buf[256];
        size_t n = fread(buf, 1, sizeof(buf) - 1, f);
        fclose(f);
        buf[n] = '\0';

        char *start = strchr(buf, '[');
        char *end   = strchr(buf, ']');
        if (start && end && end > start) {
            size_t len = (size_t)(end - start - 1);
            if (len >= sizeof(ctx->act_trigger_orig))
                len = sizeof(ctx->act_trigger_orig) - 1;
            memcpy(ctx->act_trigger_orig, start + 1, len);
            ctx->act_trigger_orig[len] = '\0';
        } else {
            strncpy(ctx->act_trigger_orig, "none",
                    sizeof(ctx->act_trigger_orig) - 1);
        }

        /* Switch to manual control */
        f = fopen(_RUN_LED_PATH "/trigger", "w");
        if (!f) {
            ctx->act_trigger_orig[0] = '\0';
            return RESULT_RESOURCE_UNAVAIL;
        }
        fputs("none", f);
        fclose(f);
    }
    return RESULT_SUCCESS;
}

int run_led_write(LedCtx_t *ctx, int state)
{
    if (ctx->act_trigger_orig[0] == '\0')
        return RESULT_RESOURCE_UNAVAIL;  /* must call run_led_kernel(dev,0) first */

    FILE *f = fopen(_RUN_LED_PATH "/brightness", "w");
    if (!f)
        return RESULT_RESOURCE_UNAVAIL;
    /* Pi_LED_nActivity: active-low → state=1 → brightness='0' */
    fputc(state ? '0' : '1', f);
    fclose(f);
    return RESULT_SUCCESS;
}
