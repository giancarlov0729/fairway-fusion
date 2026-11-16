# Pico 2 W capture firmware

Build the existing `firmware/pico_usb` target using Pico SDK 2.2.0 with TinyUSB initialized and an Arm embedded toolchain. Set `PICO_SDK_PATH` to the SDK checkout.

```sh
cmake -S firmware/pico_usb -B build/pico -DPICO_BOARD=pico2_w
cmake --build build/pico
```

The flash image is `build/pico/fairway_pico_usb.uf2`. A native test exercises the production controller with a simulated clock and PIO completion signal.

```sh
cc -std=c11 firmware/pico_usb/controller.c firmware/pico_usb/test_controller.c -o test_controller
./test_controller
python -m pytest tests/test_pico_usb.py tests/test_pico_capture_usb.py
```

## Capture contract

Use one FF1 request at a time. Every request starts with `FF1 <request_id>` and ends in LF. Generate a fresh lowercase 32-hex request ID per command. The server creates a separate UUID hex capture ID when it creates the attempt. All capture-specific requests use that same ID. There are no unsolicited replies.

| Command after the request ID | Success payload |
| --- | --- |
| HELLO | PICO2W_CAPTURE_1 |
| PING | PONG |
| STATUS | idle, configured, armed, capturing, completed, failed, cancelled or timed_out |
| CONFIG id camera_us width_us interval_us delay_us | CONFIGURED |
| ARM id | ARMED |
| TEST_TRIGGER id | TRIGGERED |
| DISARM | DISARMED |
| REPORT id | Compact JSON record |

Use `PicoClient.capture_command(command, capture_id, *timing)` for the four capture-specific commands. Existing Flask status/connect/disarm routes support the legacy and capture firmware. The capture worker owns camera preparation and image acquisition. Prepare both cameras before ARM. TEST_TRIGGER starts the same physical output sequence as a gate edge.

CONFIG accepts integer microseconds. The camera gate is at most 20000 µs. Flash width is 1–10 µs, start-to-start interval 100–10000 µs, and first-flash delay 1–10000 µs from the camera assertion edge. The final flash must end strictly inside the gate. Each accepted sequence contains exactly two pulses. The prototype brief's example is `5000 5 1000 500`. These limits bound logic-level output and require driver characterization before use with powered lighting.

ARM requires a low gate with at least 50 ms since the last observed transition and expires after 10 s. An accepted impact consumes the arm. CONFIG requires 250 ms since the last impact and a fresh capture ID. The device retains one record until the next successful CONFIG or reboot. Use globally unique IDs on the host. During an active sequence DISARM aborts and returns pins to idle. Poll REPORT for its terminal outcome. A completed waveform stays completed even if DISARM arrives afterward. Observed USB disconnect cancels a pending attempt. A watchdog reset clears RAM and changes the boot identifier.

A successful reply is `FF1 <request_id> OK <payload>`. Rejections return `ERR` with BAD_CONFIG, BUSY, USED_CAPTURE, COOLDOWN, UNKNOWN_CAPTURE, GATE_NOT_QUIET, BAD_STATE or UNSUPPORTED. Input is limited to 127 ASCII bytes before LF. REPORT replies may use up to 640 bytes including LF. Other replies retain the 128-byte limit. After transport loss, reconnect and query REPORT with the original ID before deciding the outcome.

## Timing and storage

Match the returned `capture_id` to the two saved camera images. Save the report before configuring another capture. It carries `boot_id`, per-boot `trigger_counter` (zero before an impact), firmware version, status, error code, the four applied timing fields and commanded flash count. A cancelled attempt can have fewer emitted flashes. The Pi supplies actual camera exposure settings.

`impact_observed_us` timestamps software acceptance of the impact or test command. `sequence_done_observed_us` timestamps the CPU observing the PIO completion flag. Both use microseconds since boot, with zero for unavailable. The exact camera assertion edge is uninstrumented. The PIO independently schedules every subsequent edge. The report uses `timing_source=commanded`. Leave measured fields empty until an instrument trace is available.

## Logic-level integration

The proposed pin map is centralized in `timing.h`. GP2 and GP3 drive the camera-buffer inputs together, idle high and asserted low. GP4 is flash logic, idle low and asserted high. **GP6 (physical pin 9)** accepts the conditioned, active-high microphone gate or a 3.3 V test signal. The assembled board's GP5 is inaccessible, so the microphone input has moved from GP5 to GP6. GP7 (physical pin 10) is reserved and unused. Verify the received hardware's polarity, input voltage and external idle pulls before wiring. Keep the LED driver inhibited independently during reset and logic-only tests. The firmware does not configure lighting-board SPI or current.

### SEN-14262 wiring for the GP6 assembly

| Detector connection | Pico connection |
| --- | --- |
| GATE | Conditioned divider output to GP6, physical pin 9 |
| GND | GND, for example physical pin 8; also the divider ground |
| VCC | Recommended: USB VBUS, physical pin 40, with the divider below |
| AUDIO / ENVELOPE | Leave disconnected |

For a divider, connect GATE through 4.7 kOhm to the GP6 junction, then another
4.7 kOhm from that junction to ground (nominal 2.5 V from a 5 V GATE). The earlier
10 kOhm upper / 15 kOhm lower combination also works (nominal 3.0 V). Check the
actual loaded logic levels on the assembled board. Do not connect anything to GP7
for this digital-trigger test. Soldering a header does not require using that pin.

The existing soldered 3V3 pin (physical pin 36) does not change firmware operation.
SparkFun recommends a 3.5-5.5 V detector supply, ideally stable 5 V; operation from
3.3 V is outside that recommended range and must not be treated as qualified.
Use VBUS plus the divider for the recommended USB-powered setup, leaving the
3V3 wire unused and insulated. Firmware cannot compensate for inadequate sensor
supply or a GATE output stuck high. See the
[SparkFun guide](https://learn.sparkfun.com/tutorials/sound-detector-hookup-guide/)
and [Pico 2 W pinout](https://datasheets.raspberrypi.com/picow/pico-2-w-pinout.pdf).

Rebuild and flash the UF2 after changing pins; the previously flashed image still
listens on GP5. USB commands and timing settings are unchanged. After flashing,
verify GATE is low in quiet conditions, arm the controller, then produce a sound
and confirm a completed REPORT. This verifies the microphone input path only;
keep GP2/GP3/GP4 disconnected from camera/lighting interfaces during the bench test.

For timing verification, acquire GP2, GP3 and GP4 on the same timebase. Save the scope export, sample rate, capture ID, boot ID, firmware version and applied settings. Measure camera skew, trigger width, delay to first flash, each pulse width and rising-edge interval. Repeat across settings and record min/max interval and jitter. Exercise no-impact timeout, repeated edges, DISARM and USB loss. With validated lighting hardware, use a photodiode to measure optical pulse centres and widths separately and link the measurement to the configuration used for the image pair.
