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

#include "controller.h"

static void gate_edge(uint gpio, uint32_t events) {
    controller_gate(time_us_64(), (events & GPIO_IRQ_EDGE_RISE) && gpio_get(gpio));
}

static void handle_line(char *line) {
    char reply[640];
    uint32_t flags = save_and_disable_interrupts();
    bool gate_busy = gpio_get(MIC_PIN) || gpio_get_irq_event_mask(MIC_PIN);
    controller_request(line, time_us_64(), gate_busy, reply);
    restore_interrupts(flags);
    if (reply[0]) {
        fputs(reply, stdout);
        fflush(stdout);
    }
}

int main(void) {
    sequence_init();
    gpio_init(MIC_PIN);
    gpio_pull_down(MIC_PIN);
    gpio_set_irq_enabled_with_callback(MIC_PIN, GPIO_IRQ_EDGE_RISE | GPIO_IRQ_EDGE_FALL, true, gate_edge);
    controller_boot(get_rand_64());
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
        controller_poll(time_us_64(), connected);
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
