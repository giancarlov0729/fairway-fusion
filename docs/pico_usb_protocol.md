# Pi-Pico USB foundation: FF1

This page describes the legacy USB-only firmware. See [Pico capture firmware](pico_capture.md) for the current capture commands, wiring and timing report.

## Scope

USB CDC serial between a Pi 5/laptop host and Pico 2 W (RP2350). This chunk identifies the device, tests communication, reports status, and acknowledges disarm. It does not implement camera/LED/microphone control. Never interpret successful USB tests as timing or capture validation. Keep external lighting power disconnected during this firmware bring-up; untouched GPIO inputs do not establish a safe driver state.

This concrete, text-based foundation replaces the unimplemented JSON-message example in the November 6 planning PDF for this chunk. The existing cameras/synchronization.py HELLO/READY protocol is a separate legacy scaffold. Do not connect both clients to the device or use it to trigger the two cameras sequentially.

## Physical connection

Pi USB-A host port -> USB data cable -> Pico 2 W micro-USB. Pi receives its own USB-C power. No GPIO UART wiring or Wi-Fi is needed. For this bench test, USB powers the Pico; do not add a second power source without reviewing the Pico power circuit.

BOOTSEL mass-storage mode is for loading UF2 firmware, not the running application's serial connection. After flashing, Windows exposes a COM port and Linux normally a /dev/ttyACM* device. Select the observed device; do not assume its number. Use /dev/serial/by-id when available on Linux. Close Thonny, serial monitors, and other programs using the port.

## Wire framing

- ASCII, one request per newline-terminated line. Optional CR before LF is accepted.
- Request: `FF1 <id> <command>\n`
- Success: `FF1 <id> OK <payload>\n`
- Rejection: `FF1 <id> ERR UNSUPPORTED\n`
- ID: exactly 32 lowercase hexadecimal characters, generated from uuid4().hex.
- One request in flight; no unsolicited event or debug text on this port.
- Commands have exactly three fields. Replies have exactly four fields.
- Firmware input buffer: 128 bytes including its C terminator; discard oversized/invalid lines.
- Host reply maximum: 128 bytes including line terminator.
- Host response deadline: 2 seconds, serial read interval 50 ms, write timeout 2 seconds.
- Incomplete firmware input expires after one second without a received byte.
- 115200 is the host's conventional serial line-coding setting, not a GPIO UART wire rate or a timing guarantee over native USB CDC.

| Command | Exact success payload | Meaning |
| --- | --- | --- |
| HELLO | PICO2W_USB_1 | Identifies this USB-only firmware contract |
| PING | PONG | Round-trip connectivity |
| STATUS | USB_ONLY | No capture functionality exists in this firmware |
| DISARM | DISARMED | Acknowledgement only: this firmware has no armed outputs |

Malformed requests without a valid envelope are silently discarded. The host times out and closes the connection. Unsupported well-formed commands receive ERR. No failed command is automatically retried. A timeout cannot prove a command was not executed.

## Flask routes

- POST /api/pico/connect: open configured port and verify HELLO.
- GET /api/pico/status: request live STATUS; disconnected/error returns HTTP 503.
- POST /api/pico/disarm: request DISARM acknowledgement.
- POST /api/pico/disconnect: close host serial connection.

All successes report capture_ready=false. Set FAIRWAY_PICO_PORT before starting the server. The route module owns one client per Flask application. Run one server process with the reloader disabled. Do not use request teardown to close a shared serial port. These local prototype endpoints have no authentication; bind to 127.0.0.1 and use an SSH tunnel when accessing the Pi remotely.

## Next contract increment, before real capture

Agree and implement configuration validation, safe boot/off states, hardware inhibit, ARM/DISARM semantics, finite arm timeout, simultaneous camera timing, flash count and duration limits, capture IDs, Pico timestamps, completion/fault records, and reconnect/reboot recovery. Choose how the host retrieves asynchronous results before adding them. Change the firmware identity/capability contract when adding capture; do not merely remove capture_ready=false.

Actual camera/flash timing belongs in Pico PIO/hardware, not USB arrival times or Python sleep. The capture worker must ready both cameras before arming, then associate captured images with the Pico capture ID and measured flash timing. Store no synthetic measurement merely because a USB command succeeded.
