#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "hardware/sync.h"
#include "hardware/watchdog.h"
#include "pico/rand.h"
#include "pico/stdio_usb.h"
#include "pico/stdlib.h"
#include "sequence.h"

#define FIRMWARE "PICO2W_CAPTURE_1"
enum phase { IDLE, CONFIGURED, ARMED, RUNNING, COMPLETED, CANCELLED, TIMED_OUT, FAILED };
static const char *const names[] = {
    "idle", "configured", "armed", "capturing", "completed", "cancelled", "timed_out", "failed"
};
static volatile enum phase state;
static char capture_id[33];
static uint64_t boot_id, deadline, impact_us, observed_done_us, last_gate_us;
static unsigned trigger_counter;
static timing_t timing;
static const char *reason = "none";

static bool valid_id(const char *s) {
    return s && strlen(s) == 32 && strspn(s, "0123456789abcdef") == 32;
}

// Called with GPIO interrupts masked, or from the gate ISR.
static bool trigger(uint64_t now) {
    if (state != ARMED || now >= deadline) return false;
    impact_us = now;
    ++trigger_counter;
    state = RUNNING;
    sequence_start();
    return true;
}

static void gate_edge(uint gpio, uint32_t events) {
    uint64_t now = time_us_64();
    if ((events & GPIO_IRQ_EDGE_RISE) && gpio_get(gpio)) trigger(now);
    last_gate_us = now;
}

static void refresh(uint64_t now) {
    if (state == RUNNING && sequence_done()) {
        sequence_stop();
        observed_done_us = now;
        state = COMPLETED;
    } else if (state == RUNNING && now - impact_us > timing.camera_us + 5000) {
        sequence_stop();
        state = FAILED;
        reason = "pio_timeout";
    } else if (state == ARMED && now >= deadline) {
        sequence_stop();
        state = TIMED_OUT;
        reason = "arm_timeout";
    }
}

static void cancel(const char *why) {
    if (state == ARMED || state == CONFIGURED || state == RUNNING) {
        sequence_stop();
        state = CANCELLED;
        reason = why;
    }
}

static bool number(const char *s, unsigned *out) {
    if (!s || !*s || strlen(s) > 5 || strspn(s, "0123456789") != strlen(s)) return false;
    *out = (unsigned)strtoul(s, NULL, 10);
    return true;
}

// REPORT is polled by capture ID. All timestamps are local observations since boot.
static void report(char *out, size_t n) {
    snprintf(out, n, "{\"capture_id\":\"%s\",\"boot_id\":\"%016" PRIx64
        "\",\"trigger_counter\":%u,\"status\":\"%s\",\"error_code\":\"%s\","
        "\"firmware_version\":\"" FIRMWARE "\",\"timing_source\":\"commanded\","
        "\"camera_trigger_us\":%u,\"flash_width_us\":%u,\"flash_interval_us\":%u,"
        "\"first_flash_delay_us\":%u,\"flash_count\":2,"
        "\"impact_observed_us\":%" PRIu64 ",\"sequence_done_observed_us\":%" PRIu64 "}",
        capture_id, boot_id, impact_us ? trigger_counter : 0, names[state], reason,
        timing.camera_us, timing.width_us, timing.interval_us, timing.delay_us,
        impact_us, observed_done_us);
}

static const char *dispatch(char **v, unsigned count, char *out, size_t n) {
    const char *cmd = v[2];
    uint64_t now = time_us_64();
    refresh(now);
    const char *reply;
    if (!strcmp(cmd, "HELLO") && count == 3) reply = FIRMWARE;
    else if (!strcmp(cmd, "PING") && count == 3) reply = "PONG";
    else if (!strcmp(cmd, "STATUS") && count == 3) reply = names[state];
    else if (!strcmp(cmd, "DISARM") && count == 3) {
        cancel("disarmed");
        reply = "DISARMED";
    } else if (!strcmp(cmd, "CONFIG")) {
        timing_t t;
        if (count != 8 || !valid_id(v[3]) || !number(v[4], &t.camera_us) ||
            !number(v[5], &t.width_us) || !number(v[6], &t.interval_us) ||
            !number(v[7], &t.delay_us) || !timing_valid(t)) return "BAD_CONFIG";
        if (state == CONFIGURED || state == ARMED || state == RUNNING) return "BUSY";
        if (!strcmp(capture_id, v[3])) return "USED_CAPTURE";
        if (impact_us && now - impact_us < 250000) return "COOLDOWN";
        timing = t;
        strcpy(capture_id, v[3]);
        sequence_prepare(t);
        impact_us = observed_done_us = 0;
        reason = "none";
        state = CONFIGURED;
        reply = "CONFIGURED";
    } else if (!strcmp(cmd, "ARM") || !strcmp(cmd, "TEST_TRIGGER") || !strcmp(cmd, "REPORT")) {
        if (count != 4 || !valid_id(v[3]) || strcmp(v[3], capture_id)) return "UNKNOWN_CAPTURE";
        if (!strcmp(cmd, "ARM")) {
            if (state != CONFIGURED) return "BAD_STATE";
            if (gpio_get(MIC_PIN) || gpio_get_irq_event_mask(MIC_PIN) ||
                now - last_gate_us < 50000) return "GATE_NOT_QUIET";
            deadline = now + 10000000;
            state = ARMED;
            reply = "ARMED";
        } else if (!strcmp(cmd, "TEST_TRIGGER")) {
            if (!trigger(now)) return "BAD_STATE";
            reply = "TRIGGERED";
        } else {
            report(out, n);
            return NULL;
        }
    } else return "UNSUPPORTED";
    snprintf(out, n, "%s", reply);
    return NULL;
}

static void handle_line(char *line) {
    char *v[9];
    unsigned count = 0;
    for (char *s = strtok(line, " "); s && count < 9; s = strtok(NULL, " ")) v[count++] = s;
    if (count < 3 || strcmp(v[0], "FF1") || !valid_id(v[1])) return;
    char out[512];
    uint32_t flags = save_and_disable_interrupts();
    const char *error = dispatch(v, count, out, sizeof(out));
    restore_interrupts(flags);
    printf("FF1 %s %s %s\n", v[1], error ? "ERR" : "OK", error ? error : out);
    fflush(stdout);
}

int main(void) {
    sequence_init();
    gpio_init(MIC_PIN);
    gpio_pull_down(MIC_PIN);
    gpio_set_irq_enabled_with_callback(MIC_PIN, GPIO_IRQ_EDGE_RISE | GPIO_IRQ_EDGE_FALL, true, gate_edge);
    boot_id = get_rand_64();
    stdio_init_all();
    watchdog_enable(2000, true);
    char line[128];
    size_t used = 0;
    bool dropping = false;
    uint64_t last_byte = 0;
    while (true) {
        watchdog_update();
        bool connected = stdio_usb_connected();
        uint32_t flags = save_and_disable_interrupts();
        refresh(time_us_64());
        if (!connected) cancel("usb_disconnect");
        restore_interrupts(flags);
        if (!connected) {
            used = 0;
            dropping = false;
            sleep_ms(1);
            continue;
        }
        if (time_us_64() - last_byte > 1000000) {
            used = 0;
            dropping = false;
        }
        int ch = getchar_timeout_us(100);
        if (ch < 0) continue;
        last_byte = time_us_64();
        if (ch == '\n') {
            if (used && line[used-1] == '\r') --used;
            if (!dropping && used) {
                line[used] = '\0';
                handle_line(line);
            }
            used = 0;
            dropping = false;
        } else if ((used && line[used-1] == '\r') || (ch < 32 && ch != '\r') ||
                   ch > 126 || used == sizeof(line)-1) {
            dropping = true;
        } else if (!dropping) line[used++] = (char)ch;
    }
}
