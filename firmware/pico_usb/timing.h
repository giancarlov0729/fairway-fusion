#pragma once
#include <stdbool.h>
#include <stdint.h>

// GP2 and GP3 go to the two non-inverting camera buffers (active low).
// GP4 is the active-high flash logic input. GP5 accepts a conditioned gate.
enum { CAMERA_A_PIN = 2, FLASH_PIN = CAMERA_A_PIN + 2, MIC_PIN = 5 };
enum { PIO_TICKS_PER_US = 10, IDLE_PINS = 3, FLASH_ON = 4 };

typedef struct {
    unsigned camera_us, width_us, interval_us, delay_us;
} timing_t;

static inline bool timing_valid(timing_t t) {
    return t.camera_us <= 20000 && t.width_us >= 1 && t.width_us <= 10 &&
           t.interval_us >= 100 && t.interval_us <= 10000 &&
           t.delay_us >= 1 && t.delay_us <= 10000 &&
           t.camera_us > t.delay_us + t.interval_us + t.width_us;
}

static inline uint32_t segment_word(unsigned pins, unsigned duration_us) {
    // OUT pins to the next OUT pins costs X+5 cycles, including the next pull.
    return ((duration_us * PIO_TICKS_PER_US - 5u) << 3) | pins;
}

static inline void timing_words(timing_t t, uint32_t words[6]) {
    words[0] = segment_word(0, t.delay_us);
    words[1] = segment_word(FLASH_ON, t.width_us);
    words[2] = segment_word(0, t.interval_us - t.width_us);
    words[3] = segment_word(FLASH_ON, t.width_us);
    words[4] = segment_word(0, t.camera_us - t.delay_us - t.interval_us - t.width_us);
    words[5] = IDLE_PINS;
}
