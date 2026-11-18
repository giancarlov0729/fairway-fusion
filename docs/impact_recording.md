# Automatic impact recording (free-running bench)

This connects the existing microphone/Pico controller to the two-camera bench.
It records evidence clips, not synchronized stereo measurements or calculated shots.
SQLite capture storage is connected to this bench. The Flask capture service does
not run this bench, and no shot/measurement is created. Stop Flask while running
the bench: Flask startup recovery must not interrupt a bench capture in progress.

## Before running

Use capture firmware with microphone GATE on GP7. Check the wiring and quiet gate
as described in `pico_capture.md`. Leave GP2, GP3, and GP4 disconnected from cameras
and lighting for this bench: ARM and TEST_TRIGGER run the physical timing outputs.
Stop Flask and other serial tests before running; one process must own the USB port.
No firmware changes or new dependencies are required.

From the project checkout on the Pi, activate its existing environment, then run:

```bash
source ~/camera-bringup-check-20261118/.venv-lab/bin/activate
export PYTHONPATH="$PWD/src"
python scripts/camera_bench.py --fps 30 --exposure-us 3000 --gain 2 --min-radius 20 --max-radius 100 --pico-port /dev/serial/by-id/usb-Raspberry_Pi_Pico_EDA9201712679AFF-if00 --outputs-disconnected
```

Use `ls /dev/serial/by-id/` if the controller has a different device identity.

- Wait until both camera views appear. Let the buffer fill for about two seconds.
- Press **A**. The preview shows ARMING, then PICO ARMED after USB confirmation.
- Make one sound. A completed Pico report initiates another second of recording,
  then saves the rolling image buffer and report automatically. Space is unused.
- Press **T** while armed to test the same path without a microphone event. The
  resulting recording explicitly identifies a software test command.
- Press **D** to cancel/disarm. Evidence and the terminal report are still saved.
- Press **Q** or Escape to quit. Pending work is cancelled, the controller is
  disarmed where reachable, partial evidence is saved, and camera resources close.
- Press **A** again after saving for a new unique attempt. There is no auto-rearm.

Without `--pico-port`, the previous manual A / Space / D mode still works.

## Evidence and limitations

Each arm creates `<database-folder>/captures/bench/<32-hex-capture-id>/attempt.json` before
sending ARM. PNG images and `manifest.json` are added when the attempt finishes.
The manifest is written last; if storage fails, attempt.json remains and the error
is raised. Completed, timed-out, cancelled, failed, and interrupted outcomes retain
evidence. Failed USB recovery records whether disarm could be confirmed. Stop and
check hardware if it could not be confirmed; do not assume outputs were stopped.

The full Pico report retains capture ID, boot ID, counter, timing fields, and clock
values. Actual per-frame ExposureTime, AnalogueGain, SensorTimestamp, and FrameDuration
are saved separately from requested settings. PNGs contain the unannotated luma image.

The ring buffer is bounded by frame count (nominally four seconds at the requested
rate). The retained time span depends on achieved acquisition/processing rate and
USB reporting delay. Cancellation or disconnection preserves the available buffer.
Report polling is approximately 50 ms plus USB latency. Host event timestamps refer
to observation of a report or successful test command, not the actual impact edge.
Pico boot time and camera timestamps are separate clocks; no subtraction between
those clock domains is used. Both cameras remain free-running and unsynchronized.
No real speed/launch metrics are generated from these clips.

A process kill/power loss can leave only attempt.json; this bench does not recover
RAM image buffers after restart. SQLite retains unfinished records after a process kill. The existing Flask startup
recovery marks unfinished captures interrupted when the bench has stopped. Save or copy recordings before cleanup.

## Validation

```bash
python -m pytest tests/test_impact_recording.py tests/test_camera_bench.py tests/test_pico_capture_usb.py -q
```

Automated tests use simulated serial/camera devices and real PNG/manifest writes.
Physical verification: confirm A reaches PICO ARMED, a sound creates one folder,
T produces a labelled test capture, no sound yields timed_out, and D cancels.
Unplug USB while armed and verify interrupted evidence. Check both cameras and
matching report IDs in every manifest. Confirm actual microphone detection and
all disarm behavior on the assembled hardware before relying on it in the demo.

Reference: [official Picamera2 manual](https://datasheets.raspberrypi.com/camera/picamera2-manual.pdf).
Camera requests are copied and released promptly; USB polling runs on a separate
thread so serial timeouts do not block camera acquisition or keyboard handling.


## SQLite integration

Pico mode opens the same ShotStore as Flask, using `--database PATH` if supplied,
then FAIRWAY_DB, then the application's default. The path is printed at startup.
Harshil's schema migration 5 runs automatically and preserves existing records.
Back up an important database before first running this upgraded branch.

Before CONFIG/ARM, a capture row is committed as preparing, source live, purpose
camera_test. The active session is attached at that moment if present; an absent
session is allowed for bench tests. The session association is not reassigned later.
ARM acknowledgement updates the row to armed. Terminal reports are persisted while
footage finishes saving. The database status becomes completed, cancelled,
timed_out, failed, or interrupted after saving. A completed camera_test means
successful evidence recording, not a measured golf shot. Camera-test captures are
refused by save_shot so they cannot be turned into misleading measurement history.

The database contains file paths and metadata, not PNG bytes. image_a_path and
image_b_path reference one representative frame per camera nearest to the host
observation time. detections.recording_manifest points to the manifest listing
ALL clip frames. camera_metadata contains actual saved image dimensions, R8 luma
pixel format, reported exposure/gain/timestamp and camera identity when available.
No connector identity is guessed. Requested YUV420 stream settings are separate.

These multi-frame recordings are pinned by default so the existing two-image
cleanup routine cannot partly delete a clip. Keep them pinned; delete/archive the
whole clip deliberately until clip-aware retention is implemented. Disk usage can
grow, so copy useful evidence and manage recordings between lab sessions.

Use --output only for a directory under the database's folder. The default path is
captures/bench beneath that folder. Use the same --database/FAIRWAY_DB value as the
Flask app if you want them to share records. The existing shot-history page does
not yet list these camera-test clips; UI integration is a separate step.
