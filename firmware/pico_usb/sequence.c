#include "sequence.h"
#include "capture.pio.h"
#include "hardware/clocks.h"

static PIO const pio = pio0;
static uint sm, offset;
static pio_sm_config config;

void sequence_stop(void) {
    pio_sm_set_enabled(pio, sm, false);
    pio_sm_set_pins_with_mask(pio, sm, IDLE_PINS << CAMERA_A_PIN, 7u << CAMERA_A_PIN);
    pio_sm_clear_fifos(pio, sm);
    pio_interrupt_clear(pio, 0);
}

void sequence_init(void) {
    sm = pio_claim_unused_sm(pio, true);
    offset = pio_add_program(pio, &capture_program);
    config = capture_program_get_default_config(offset);
    sm_config_set_out_pins(&config, CAMERA_A_PIN, 3);
    sm_config_set_out_shift(&config, true, false, 32);
    sm_config_set_fifo_join(&config, PIO_FIFO_JOIN_TX);
    sm_config_set_clkdiv(&config, (float)clock_get_hz(clk_sys) / (PIO_TICKS_PER_US * 1e6f));
    pio_sm_init(pio, sm, offset, &config);
    sequence_stop();
    pio_sm_set_consecutive_pindirs(pio, sm, CAMERA_A_PIN, 3, true);
    for (uint pin = CAMERA_A_PIN; pin <= FLASH_PIN; ++pin) {
        pio_gpio_init(pio, pin);
    }
}

void sequence_prepare(timing_t timing) {
    uint32_t words[6];
    timing_words(timing, words);
    sequence_stop();
    pio_sm_init(pio, sm, offset, &config);
    for (unsigned i = 0; i < 6; ++i) {
        pio_sm_put(pio, sm, words[i]);
    }
}

void sequence_start(void) {
    pio_sm_set_enabled(pio, sm, true);
}

bool sequence_done(void) {
    return pio_interrupt_get(pio, 0);
}
