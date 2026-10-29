# IMX296 + Pi 5 + Pico bench bring-up

The project draft and Parts.xlsx identify two InnoMaker CAM-IMX296Mono-GS CSI cameras,
Raspberry Pi 5, Pico H/RP2040 and a conditioned microphone trigger. Lens, IR driver,
trigger interface and pin assignments remain provisional in those documents.
The [vendor](https://www.inno-maker.com/product/cam-mipi296raw-trigger/) specifies
1456x1088 at 60 FPS with external-trigger/strobe support.

## Firmware and interfaces

firmware/pico/main.py is MicroPython bench firmware. A 1 MHz PIO program waits for a
fresh low-to-high impact edge, asserts a logical exposure gate, delays, emits THREE
8 us flashes, then releases the gate. USB/Python do not schedule individual flashes.
A 30-second impact timeout disarms it. Provisional pin mapping:

| GPIO | Function | Logic |
| --- | --- | --- |
| 2 | Exposure gate to BOTH camera interface circuits | Active high |
| 3 | IR driver gate | Active high |
| 4 | Conditioned impact input | Rising edge; return low before rearming |

GPIO 2/3 must be consecutive because PIO sets both together. These are logical
interface signals, NOT verified direct connections to camera XTR or LED power.
Match voltage, polarity, exposure gating and driver timing using the actual interface
circuit and camera revision. The script does not change boot overlays or kernel
trigger parameters. Enable external triggering for both sensors using vendor guidance.

First flash onset: delay_us after logical gate assertion. Later onsets: spacing_us
apart. Reported centres add 4 us. Gate release follows a full final pulse period after
the third onset. Actual camera integration may have a sensor-specific offset and
must be measured. Reported times are commanded DIGITAL edges, not measured light.
Use a photodiode and scope to qualify pulse width, latency and jitter. Verify both
sensors expose across the same train and both images contain the same three flashes.

## Pi setup and capture

Install Raspberry Pi OS camera/libcamera packages and python3-picamera2 through the
OS package manager. Use a system-site-packages venv for libcamera. Both cameras must
already operate in external-trigger mode with fixed focus, resolution, exposure and
gain. Check interface signals on the bench before connecting illumination.

```sh
python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -m pip install -e ".[dev,hardware]"
# Install firmware/pico/main.py as main.py on a MicroPython RP2040 board.
python scripts/capture_shot.py data/recordings/shot001 \
  --port /dev/ttyACM0 --calibration data/calibration/stereo.json \
  --spacing-us 1000 --delay-us 1000 --exposure-us 6000
```

The helper disables automatic exposure, queues both Picamera2 requests before arming
the Pico and saves the luma planes as lossless PNGs. Picamera2 imports only on the Pi.
The output folder must be new. Corrupt/stale controller packets or timeout abort the
capture; a disarm command is sent and cameras are closed on exit.

Free-running cameras can return unrelated frames. Metadata alone cannot prove frame
association. Every manifest starts with timing.verified=false, uncertainty_us=null and
empty exposure windows. Populate them from a bench-qualified optical/exposure timing
profile AND check shot/frame association before analysis. Commanded timing is never
silently promoted to verified timing. These development tools retain raw images for
validation; production retention/deletion and the practice UI are not implemented.

## USB protocol

USB CDC at 115200 baud. One ASCII line:

`JSON<TAB>eight-hex-digit-CRC32<NEWLINE>`

CRC covers the exact UTF-8 JSON bytes. Commands: arm (sequence, delay_us, spacing_us)
and disarm (sequence). Events: armed, complete, disarmed, error, all carrying sequence.
Completion includes commanded pulse centre/width arrays. Host validates CRC and
sequence, rejects old packets and does not derive timing from USB receipt. One shot
per process; reconnect and explicitly rearm after failure. No automatic exposure retry.

## Qualification still required

Firmware has not been flashed or scope-tested here. External-trigger camera startup,
request completion, pulse shape, gate polarity, exposure latency, trigger fanout skew,
and Pi processing latency require physical validation. Image-separation and spin
alias constraints are documented in [spin_tracking.md](spin_tracking.md).

Sources:

- [Project parts list](https://docs.google.com/spreadsheets/d/1z49bqfvMxks-m0q-MEvzPa9QOexFmw4A/edit)
- [Picamera2 manual](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf)
- [MicroPython PIO](https://docs.micropython.org/en/latest/library/rp2.html)
- [Raspberry Pi external trigger documentation](https://www.raspberrypi.com/documentation/accessories/global-shutter-camera.html)
