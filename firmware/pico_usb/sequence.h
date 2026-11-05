#pragma once
#include "timing.h"
void sequence_init(void);
void sequence_prepare(timing_t timing);
void sequence_start(void);
void sequence_stop(void);
bool sequence_done(void);
