# Camera-first assembly and TA demonstration

This stage uses the two InnoMaker IMX296 cameras, Pi 5 and Pico 2 W already received.
The LCD and IR emitter/driver PCB are pending. Use a laptop/HDMI display and steady
visible light. This is a real-image visibility and recording demonstration; speed,
launch angle, stereo accuracy, spin and optical timing remain unqualified.

## Assemble in this order

1. Check each camera and its packaging for the included **M12 lens and M12 lens
   seat**. InnoMaker's CAM-IMX296RAW / CAM-IMX296Mono-GS packing list includes these,
   plus a CS lens seat and two FPC cables. A separate C/CS lens is optional; start
   with the bundled M12 lens. Confirm the contents of your actual order before
   purchasing anything. Remove lens caps, fit the supplied lens/holder if needed,
   and focus on the ball plane. A bare sensor cannot form a useful scene image.
2. Shut down and unplug the Pi. Connect one camera to each Pi 5 CAM/DISP connector.
   Pi 5 uses 22-pin mini connectors; match the cable's camera end to the received
   InnoMaker board. Seat each ribbon squarely and latch it with the correct contact
   orientation for that board. Do not insert or remove CSI cables with power on.
3. Mount the cameras rigidly outside the club/ball path, looking at the same small
   region around the ball and first 0.3-0.5 m of travel. Start with a 20-30 cm baseline
   as a bench layout, then adjust for lens field of view. Both images need a clear,
   unobstructed ball. These dimensions are starting suggestions, not calibration.
4. Put a matte dark background behind a white ping-pong ball. Light the region with
   bright steady visible light; sunlight or a non-flickering lamp is useful. Avoid
   a bright white background, reflective clutter, deep shadows and saturated ball pixels.
5. Power the Pi through its normal USB-C supply, with cooling. Connect the Pico 2 W
   to Pi USB-A using a micro-USB **data** cable. Leave GP2/GP3/GP4 disconnected from
   cameras and lighting for this stage. No microphone/impact sensor is needed.
6. Use Pi desktop on an HDMI monitor, or a remote desktop session for the camera
   preview. The final LCD is unnecessary. A normal SSH tunnel to Flask does not
   forward the OpenCV camera window; the headless command below works over SSH.

## Pi setup and independent camera checks

Run from the Fairway Fusion repo root on Raspberry Pi OS with the current camera
stack. Use the received InnoMaker revision's installation/overlay instructions if
auto-detection does not find both sensors. Start in **free-running mode**; do not
enable external-trigger mode yet or the cameras may wait indefinitely for pulses.
Do not copy trigger wiring or board modifications from a different IMX296 product.

```bash
sudo apt update
sudo apt install python3-picamera2 python3-opencv python3-venv python3-serial rpicam-apps
python3 -m venv --system-site-packages .venv-bench
source .venv-bench/bin/activate
python -m pip install -e .
rpicam-hello --list-cameras
rpicam-hello --camera 0 -t 5000
rpicam-hello --camera 1 -t 5000
```

Confirm two IMX296 devices, then focus each lens on the stationary ball. Close the
camera test process before opening another program that uses the same cameras.
If either view is just a blur, check lens presence, cap, focus distance and aperture
before debugging Python. If only one camera enumerates, power down and check each
camera/cable individually, then the vendor configuration for each Pi connector.

## Real dual preview, detection and a short chip

From the repo root in a Pi desktop terminal, start the launch preview:

```bash
./start-cameras.sh
```

The launcher uses `.venv-bench/bin/python` by default. Set
`FAIRWAY_BENCH_PYTHON` to use a different environment. If you pass arguments to
the launcher, it uses those arguments in place of the launch preset. The preset
requests 60 FPS, uses a 1 ms exposure, and searches the entire image for the ball.

For a full-frame preview with explicit settings, run:

```bash
python scripts/camera_bench.py --fps 60 --exposure-us 1000 --gain 2
```

The program shows actual monochrome images side by side, candidate ball outlines,
sensor-timestamp capture cadence and reported exposure. The display refreshes at
up to 30 FPS while the cameras continue acquiring. The live detector samples
frames independently, so the outline can update more slowly than the video.
Detection can mistake other round objects for balls and can miss blurred, dim or
partially hidden balls. Inspect the image as well as the outline.
The 640x480 luma output keeps memory and processing modest; it is not raw sensor data.
Requested 60 FPS is not a guarantee of achieved capture rate. Each camera's successive
saved SensorTimestamp differences show the acquired cadence and gaps.

1. Put the stationary ball in both views. Verify fresh images by moving a hand or ball.
2. Press **a**. Both cameras have delivered images; the window says **SOFTWARE ARMED**.
   The bench prioritizes recording at sensor cadence and defers ball detection
   until after capture. The Pico remains separate.
3. Wait about two seconds to fill the rolling buffer. Gently chip the ping-pong ball
   roughly 1-2 yards toward a net/soft backstop, with everyone outside the swing path.
   Only the initial travel needs to be in view; do not widen the shot to cover the
   entire landing distance at the expense of ball size.
4. Have a teammate press **space immediately after impact**. The buffer retains recent
   frames and adds one second afterward. It analyzes the frames and saves PNGs and
   `manifest.json` under `data/recordings/bench/<UTC timestamp>/`. Keep the first attempt
   slow: roll or toss the ball through the same region before trying a club.
5. Press **a** to rearm for another attempt, **d** to cancel/disarm, or **q** to exit.
   Acquisition pauses while files save. Quit/disarm during recording discards that
   pending clip. A save failure exits with an error; no manifest means an incomplete save.
6. Open saved PNGs to show the ball before and after departure. The saved images have
   no outlines burned in; candidate centers and per-frame camera metadata are in JSON.
   On this uncalibrated setup do not turn those pixels into launch measurements.

For an SSH-only acquisition check, coordinate a gentle roll while this runs:

```bash
python scripts/camera_bench.py --headless-seconds 3 --exposure-us 1000
```

This saves one timed clip and exits; there is no interactive arming or impact trigger.
The headless run analyzes ball candidates after capture so detection does not
delay frame acquisition. The detector looks for a fresh, round silhouette in
recent frame differences and checks that the current image supports it. The
single-frame search still handles stationary balls and ambiguous motion.
The buffer is bounded by frame count (about 3 seconds at the requested preview rate;
longer elapsed history if acquisition is slower). Requests are queued for both
independently free-running cameras, each with a two-second result timeout. There is
no guarantee that camera A frame N and camera B frame N share an exposure time.
Host receipt timestamps and sensor timestamps are stored separately. Never interpret
host arrival/keypress times as impact or optical timing. Data stays outside git via
the existing recordings ignore rule. Copy useful evidence to the presentation folder.

### Exposure feasibility

At an illustrative 3 m/s, a 1000 us exposure smears 3 mm of travel; 500 us smears
1.5 mm. A 40 mm ball crossing a 0.5 m field spans about 51 pixels in a 640-pixel-wide
image. At 60 FPS it travels about 50 mm per frame. That is plausible for showing a
gentle ball departure, given enough light. It does not guarantee the club-contact
instant is sampled. At that speed, crossing 0.3 m takes 0.1 s, about six frames at
60 FPS. Recheck the actual cadence in saved metadata.

At 50 m/s, the ball travels about 0.83 m between 60 FPS frames. The bench's
0.3–0.5 m field can miss an entire airborne pass at that speed. The launch metrics
pipeline requires calibrated stereo images of two timed flashes. The free-running
bench records candidate positions and per-camera timestamps for detection checks.

If motion blurs, try `--exposure-us 500` and more light. If dark, improve illumination
or aperture first; extra gain increases noise. For stationary focusing only, use
`--fps 30 --exposure-us 5000`; shorten exposure again before a chip. If the detector
misses a clear ball, adjust `--min-radius`/`--max-radius` to the visible pixel radius.
Use `--roi CAMERA X Y WIDTH HEIGHT` to limit detection to the ball's expected
path in a cluttered view. Repeat the option to set a region for the other camera.
Leave enough room for the full ball throughout the visible path. Coordinates in
the manifest refer to the full 640×480 image.

## Separate Pico arming proof with no IR board

Use the existing **Pico 2 W / RP2350** C firmware, not the older branch's RP2040
MicroPython/three-flash firmware. Build/flash instructions are in
[pico_capture.md](pico_capture.md). Hold BOOTSEL while connecting the Pico, copy
`build/pico/fairway_pico_usb.uf2` to its mass-storage drive, then let it reboot.
The running program appears as USB serial, not the BOOTSEL drive.

```bash
ls -l /dev/serial/by-id/
# Replace the port below with the device actually enumerated on this Pi.
python scripts/pico_bench.py --port /dev/ttyACM0 --outputs-disconnected
python scripts/pico_bench.py --port /dev/ttyACM0 --outputs-disconnected --test-trigger
```

The first run prints CONFIGURED, ARMED and live status, then saves the expected
10-second no-impact timeout report. The second uses firmware TEST_TRIGGER, saves
the completed commanded timing report, and disarms on exit. Do not run Flask or a
serial monitor against the same port concurrently. A serial permission error may
require adding your Pi login to `dialout`, then logging out and back in.

GP2/GP3 are active-low camera-buffer logic, GP4 active-high flash logic, and GP7 the
conditioned active-high gate input in current firmware. **Do not connect Pico 3.3 V
GPIO directly to camera trigger pads on the basis of this pin list.** Confirm the
specific camera board's voltage, polarity and interface first. Without a validated
interface, keep the cameras free-running and demonstrate Pico arming separately.
GP7 is physical pin 10; the confirmed assembly exposes GP7 and GP8, replacing the
earlier GP5/GP6 assignments. GP8 is unused. See
[the SEN-14262 wiring table](pico_capture.md#sen-14262-wiring-for-the-gp7-assembly)
for sensor power and divider connections. Rebuild and flash the updated UF2 before
testing a real microphone trigger.
No raw microphone wiring is required: TEST_TRIGGER replaces the impact input for
the logic test. A completed report proves firmware sequence completion only. A scope
or logic analyser is needed to demonstrate actual output edges; optical timing awaits
the driver/emitter and photodiode measurements.

## Three-minute TA walkthrough and acceptance evidence

| Step | Show | Pass evidence |
| --- | --- | --- |
| 1 | Physical assembly and missing parts | Two mounted cameras, Pi 5, USB Pico; LCD replaced by monitor |
| 2 | Live camera visibility | Moving ball visible in both views; useful focus/exposure; ball candidate circles |
| 3 | Software recording readiness | Both cameras live, **SOFTWARE ARMED**, then saved real PNGs and manifest |
| 4 | Pico readiness separately | Real firmware identity, ARMED response, timeout and test-trigger reports |
| 5 | Optional gentle chip | Several real frames showing ball departure in each view, with timestamp intervals |

Suggested explanation: "Both cameras now see the ball and we can save real departure
frames. The Pico separately arms, times out, and completes a test sequence. Today the
cameras use steady visible light and independent exposures. The IR PCB is needed for
our planned flash-based capture, and we still need calibration and measured timing
before claiming launch or spin accuracy."

If a chip is unclear, demonstrate a slow roll and keep the best real frames as the
fallback. Keep synthetic UI results explicitly labelled and separate. The existing
Flask capture page still uses illustrated camera panels; this script does not connect
live images into Flask or the two-flash stereo analysis pipeline.

## References checked for this bring-up

- [InnoMaker IMX296 monochrome CSI module](https://www.inno-maker.com/product/cam-mipi296raw-trigger/): use the received revision's lens, cable, overlay and trigger instructions.
- [Raspberry Pi camera installation and software](https://www.raspberrypi.com/documentation/accessories/camera-software.html): Pi 5 connectors and `rpicam` enumeration/tests.
- [Picamera2 manual](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf): OS package installation, independent cameras, YUV arrays and request metadata.
- [Current Pico firmware contract](pico_capture.md) and [hardware references](hardware/README.md).

Desktop tests cover bounded evidence storage, timestamp preservation, detector
fixtures and fake camera/controller lifecycle. Physical focus, exposure, achieved
FPS, camera enumeration and Pico outputs must be verified on the received hardware.
