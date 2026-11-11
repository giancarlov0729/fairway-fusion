// Native bench model. Link with controller.c; only the pins and PIO are substituted.
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "controller.h"
#include "sequence.h"
#define ID "1234567890abcdef1234567890abcdef"
#define ID2 "abcdef1234567890abcdef1234567890"
#define FRAME "FF1 " ID " "
static unsigned starts;
static bool done;
static uint64_t now = 100000;
static char reply[640];

void sequence_prepare(timing_t t) { assert(timing_valid(t)); done = false; }
void sequence_start(void) { ++starts; }
void sequence_stop(void) { done = false; }
bool sequence_done(void) { return done; }

static void request(const char *cmd, bool gate, const char *expected) {
    char line[128];
    snprintf(line, sizeof(line), FRAME "%s", cmd);
    controller_request(line, now, gate, reply);
    assert(strstr(reply, expected));
}

static void check_waveform(timing_t t) {
    uint32_t words[6];
    timing_words(t, words);
    unsigned edges[] = {0, t.delay_us, t.delay_us + t.width_us,
        t.delay_us + t.interval_us, t.delay_us + t.interval_us + t.width_us, t.camera_us};
    unsigned pins[] = {0, 4, 0, 4, 0, 3};
    unsigned cycle = 0;
    for (unsigned i = 0; i < 6; ++i) {
        assert((words[i] & 7) == pins[i]);
        assert(cycle == edges[i] * PIO_TICKS_PER_US);
        cycle += (words[i] >> 3) + 5;
    }
}

int main(void) {
    check_waveform((timing_t){5000,5,1000,500});
    check_waveform((timing_t){103,1,100,1});
    check_waveform((timing_t){20000,10,10000,9989});
    controller_boot(42);
    request("CONFIG " ID " 5000 11 1000 500", false, "ERR BAD_CONFIG");
    request("CONFIG " ID " 1505 5 1000 500", false, "ERR BAD_CONFIG");
    request("CONFIG " ID " 5000 5 1000 500", false, "OK CONFIGURED");
    request("ARM " ID, true, "ERR GATE_NOT_QUIET");
    controller_gate(now, false);
    request("ARM " ID, false, "ERR GATE_NOT_QUIET");
    now += 50000;
    request("ARM " ID, false, "OK ARMED");
    request("TEST_TRIGGER " ID2, false, "ERR UNKNOWN_CAPTURE");
    controller_gate(now, true);
    controller_gate(now + 1, true);
    request("TEST_TRIGGER " ID, false, "ERR BAD_STATE");
    assert(starts == 1);
    done = true;
    now += 5100;
    request("REPORT " ID, false, "\"status\":\"completed\"");
    assert(strstr(reply, "\"capture_id\":\"" ID "\""));
    assert(strstr(reply, "\"trigger_counter\":1"));
    request("CONFIG " ID " 5000 5 1000 500", false, "ERR USED_CAPTURE");
    request("CONFIG " ID2 " 5000 5 1000 500", false, "ERR COOLDOWN");
    now += 250000;
    request("CONFIG " ID2 " 5000 5 1000 500", false, "OK CONFIGURED");
    request("ARM " ID2, false, "OK ARMED");
    now += 10000000;
    controller_gate(now, true);
    request("REPORT " ID2, false, "\"error_code\":\"arm_timeout\"");
    assert(starts == 1);
    now += 50000;
    request("CONFIG " ID " 5000 5 1000 500", false, "OK CONFIGURED");
    request("ARM " ID, false, "OK ARMED");
    request("TEST_TRIGGER " ID, false, "OK TRIGGERED");
    assert(starts == 2);
    request("DISARM", false, "OK DISARMED");
    request("REPORT " ID, false, "\"status\":\"cancelled\"");
    now += 250000;
    request("CONFIG " ID2 " 5000 5 1000 500", false, "OK CONFIGURED");
    request("ARM " ID2, false, "OK ARMED");
    controller_poll(now, false);
    controller_gate(now + 10, true);
    assert(starts == 2);
    request("REPORT " ID2, false, "\"error_code\":\"usb_disconnect\"");
    puts("Capture controller and commanded waveform checks passed.");
}
