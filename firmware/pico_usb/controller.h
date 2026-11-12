#pragma once
#include <stdbool.h>
#include <stdint.h>

// Main-loop callers mask gate interrupts. The GPIO ISR can call controller_gate directly.
void controller_boot(uint64_t id);
void controller_gate(uint64_t now, bool rising);
void controller_poll(uint64_t now, bool connected);
void controller_request(char *line, uint64_t now, bool gate_busy, char reply[640]);
