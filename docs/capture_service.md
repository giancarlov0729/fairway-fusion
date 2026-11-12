# Capture coordinator foundation

`CaptureService(pico, store)` provides `arm()`, `poll(capture_id)` and
`cancel(capture_id)`. Inject an already-connected PicoClient or a fake for tests.
Create exactly one service per controller in a single server process.

The store interface is `create(record)`, `get(capture_id)`, and
`update(capture_id, **changes)`. Use `CaptureService(pico, shot_store.captures)`
with Harshil's SQLite adapter. It commits create before returning: CONFIG and
ARM are sent only after that call succeeds.
MemoryCaptureStore is temporary, loses records on restart, and returns copies.
The coordinator tests run against both memory and SQLite storage.

Flask initializes the coordinator as `app.extensions["capture_service"]`, using
the existing `app.extensions["pico"]` client and `shot_store.captures` adapter.
Application startup marks unfinished database captures as interrupted before
creating the coordinator. Startup does not connect to or send commands to the
Pico; an interrupted database record does not confirm the hardware is disarmed.
Run one server process without automatic reloading. Creating another app against
the same database while a capture is active would mark that capture interrupted.

The service reserves one attempt and records preparing before CONFIG and ARM.
Polling follows Pico states. Here completed means the Pico waveform completed,
not that camera images were acquired, processed, or saved as a shot.
Transport errors retain the reservation because hardware outcome is uncertain.
After reconnecting the same controller, poll or cancel the original ID.
Cancellation checks REPORT after DISARM so an already completed waveform is
not incorrectly labelled cancelled. Unknown captures after a Pico reboot require
future explicit recovery handling; the service intentionally remains blocked.

Flask exposes `POST /api/captures/arm` (JSON object, `{}` uses default timings),
`GET /api/captures/<capture_id>` (poll active hardware or read a stored record),
and `POST /api/captures/<capture_id>/cancel` (DISARM followed by REPORT).
Arm accepts `camera_us`, `width_us`, `interval_us`, `delay_us`, and optional
`session_id`. Flask requires an active practice session and saves its ID before
CONFIG/ARM. An explicitly supplied session must match the active session; a stale
session returns 409 and invalid ID types return 400. Mock forms use the same rule.
Session ending is blocked while either coordinator reserves a capture for that
session, including unresolved USB failures. SQLite also rejects ending a session
with preparing/armed/capturing/processing captures and refuses new captures linked
to ended sessions. A terminal report releases the coordinator's reservation;
cancelled, timed-out, failed, completed, and interrupted records retain their ID.
Successful arming returns HTTP 201; status and cancel return HTTP 200, all with
a `capture` object. Invalid input returns 400, unknown IDs 404, an active-capture
conflict or firmware rejection 409, and USB failures 503. Hardware errors include
`active_capture_id` when an attempt remains reserved for recovery. Arming requires
an existing USB connection running capture firmware; it does not auto-connect.
There is no background worker, camera preparation, image storage,
or LED current configuration in this chunk. Use fake hardware for this stage.
Default timings are example commands, not measured or approved lighting settings.
Before live camera integration, camera readiness must precede ARM. SQLite storage
and startup interruption recovery are connected; image processing remains pending.

Run `python -m pytest tests/test_capture_service.py -q`.

## Capture control page

`/capture` provides server-rendered mock Arm, Refresh status, Cancel/disarm,
and Simulate impact forms, using the existing single-use form tokens.
The separate mock controller never accesses USB or GPIO. Its coordinator saves
records with source `simulation`; completed means mock timing finished, with no
images or measured shot generated. Camera preview panels are illustrations.
The page shows persistent capture IDs, states, errors, timing settings and a
recent-attempt log. Physical arming is disabled in the UI until acquisition and
lighting are ready. Existing JSON capture endpoints still control the real Pico.
