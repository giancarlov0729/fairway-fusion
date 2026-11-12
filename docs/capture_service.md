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

The service reserves one attempt and records preparing before CONFIG and ARM.
Polling follows Pico states. Here completed means the Pico waveform completed,
not that camera images were acquired, processed, or saved as a shot.
Transport errors retain the reservation because hardware outcome is uncertain.
After reconnecting the same controller, poll or cancel the original ID.
Cancellation checks REPORT after DISARM so an already completed waveform is
not incorrectly labelled cancelled. Unknown captures after a Pico reboot require
future explicit recovery handling; the service intentionally remains blocked.

There are no Flask routes, background worker, camera preparation, image storage,
or LED current configuration in this chunk. Use fake hardware for this stage.
Default timings are example commands, not measured or approved lighting settings.
Before live integration, camera readiness must precede ARM and persistent storage
must replace memory. Application lifecycle states will add processing/interrupted.

Run `python -m pytest tests/test_capture_service.py -q`.
