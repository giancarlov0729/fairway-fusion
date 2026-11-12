# Shot storage

Local SQLite database for sessions, shots, captures and calibrations.
Standard library only, so nothing extra to install on the Pi. Data lives in
`src/instance/fairway.db` at the repository root, no matter which folder the
server is started from, and survives restarts. The file is git-ignored.

## Using it from the server

Create one store when the server starts and reuse it for every request.
It opens a short connection per call, so it is safe from worker threads.

```python
from fairway_fusion.storage import ShotStore, StorageError

store = ShotStore()                      # or ShotStore("path/to.db")

store.start_session()                    # Start button
shot = store.save_shot(speed_m_s, launch_angle_deg, samples=samples,
                       carry=result["carry"])
store.latest_shot()                      # Results screen
store.list_shots(limit=50)               # History screen, newest first
store.end_session()                      # End button
```

`samples` is optional. Passing the simulated input keeps each shot
replayable:

```python
{"timestamps_s": [0.001, 0.002], "positions_m": [[0.05, 0, 0.043], [0.10, 0, 0.056]]}
```

`carry` is optional. Pass the dict `carry_metrics()` returns, unchanged. It is
stored apart from the measured values and comes back in the same shape.

If the calculation raises `ValueError` on bad input, store the attempt
as rejected so it still shows in history:

```python
store.save_shot(status="rejected", reason=str(exc), samples=samples)
```

## Captures

One capture record per Arm press, including failed and cancelled ones.

`CaptureService` already works with the database. Replace the temporary
in-memory store with `store.captures`:

```python
CaptureService(pico, MemoryCaptureStore())   # before: lost on restart
CaptureService(pico, store.captures)         # after: saved in SQLite
```

`store.captures` provides the same `create(record)`, `get(capture_id)` and
`update(capture_id, **changes)` the service already calls. All twelve
`test_capture_service.py` tests pass against it with only the fixture changed.

Two calls belong in the server's lifecycle:

```python
store.mark_interrupted_captures()   # once at startup, before serving requests
store.cleanup_images()              # after a capture finishes, or at startup
```

`mark_interrupted_captures()` is not run automatically by `ShotStore()`,
because scripts and tests also create a store and would wrongly interrupt a
capture the running server is still working on.

When a capture produces a result, save the shot with its capture ID. Each
capture can have at most one shot, so a retried save is refused instead of
duplicated:

```python
store.save_shot(speed, angle, capture_id=capture_id, source="live", carry=carry)
```

Capture states: `preparing`, `armed`, `capturing`, `processing`, `completed`,
`cancelled`, `timed_out`, `failed`, `interrupted`. The first four are
unfinished. Keep the Pico's own status inside `pico_report`.

Fields a capture record may contain (unknown keys are refused):

| Key | Contents |
| --- | --- |
| `capture_id` | 32 lowercase hex characters, `uuid4().hex`. Required, never changes |
| `status` | One of the states above. Required |
| `session_id` | Session it belongs to, or `None` |
| `error` | What went wrong, in words |
| `source` | `live` (default), `simulation` or `replay` |
| `timing` | Commanded timing dict |
| `timing_source` | `commanded` (default) or `measured` |
| `pico_report` | Evan's capture report, stored whole |
| `measured_timing` | Oscilloscope or photodiode measurements, once taken |
| `calibration_id` | Calibration active for this capture |
| `processing_version` | Version of the vision code that produced the result |
| `image_a_path`, `image_b_path` | Relative to `src/instance/`, forward slashes, e.g. `captures/<capture_id>/camera_a.png` |
| `detections` | Per-frame ball detections, for debugging |
| `pinned` | `True` to keep the images through cleanup |
| `images_deleted_at` | Set by cleanup |

## Images

Save each capture's images as lossless PNGs in `src/instance/captures/<capture_id>/`
and store the relative paths in the capture record.

`store.image_files(capture_id)["state"]` reports `present`, `missing` (paths
saved but a file is gone), `deleted` (removed by cleanup on purpose) or
`none` (the capture never had images).

`store.cleanup_images(keep_accepted=200, keep_other=100)` keeps images for the
newest 200 captures with an accepted shot and the newest 100 others (rejected,
failed, timed out). Pinned captures are never touched and do not count toward
either limit. Records and shots are always kept; only files are removed, and
`images_deleted_at` is set. It does nothing while a capture is in progress.
Pin a capture with `store.update_capture(capture_id, pinned=True)`.

## Calibrations

Each calibration version is saved once and never edited. Recalibrating
creates a new one, so old captures keep pointing at the calibration that
produced them.

```python
calibration = store.save_calibration(camera_a, camera_b, source="real",
                                     reprojection_error_px=0.21)
store.latest_calibration()               # newest real calibration, or None
store.get_calibration(calibration_id)
```

`camera_a` and `camera_b` can be `CameraCalibration` objects or dicts with
`K`, `distortion`, `R`, `t` and `image_size`; numpy arrays are fine. `source`
must be given: `real` or `synthetic`, so test geometry is never attached to
real captures. The baseline is calculated from R and t. The cameras come back
as plain lists; rebuild a `CameraCalibration` with `np.array(...)` on each field.

## Calls

| Call | Returns | Raises |
| --- | --- | --- |
| `start_session()` | session | `SessionStateError` if one is already active |
| `end_session(session_id=None)` | session | `SessionStateError` if none active or already ended |
| `active_session()` | session or `None` | |
| `get_session(id)` | session | `NotFoundError` |
| `list_sessions(limit=50, offset=0)` | list of sessions, newest first | |
| `save_shot(speed_m_s, launch_angle_deg, ..., carry=None, capture_id=None)` | shot with samples | `SessionStateError` if no active session, `ValueError` for bad values, `NotFoundError` for an unknown capture, `ShotExistsError` if the capture already has a shot |
| `get_shot(id, include_samples=True)` | shot | `NotFoundError` |
| `latest_shot(session_id=None)` | shot or `None` | |
| `list_shots(session_id=None, limit=50, offset=0, include_samples=False)` | list of shots, newest first | |
| `delete_shot(id)` | `None` | `NotFoundError` |
| `create_capture(record)` | capture | `CaptureExistsError`, `ValueError`, `NotFoundError` for an unknown session or calibration |
| `get_capture(capture_id)` | capture | `NotFoundError` |
| `update_capture(capture_id, **changes)` | capture | `NotFoundError`, `ValueError` |
| `list_captures(session_id=None, limit=50, offset=0)` | list of captures, newest first | |
| `mark_interrupted_captures()` | number marked | |
| `image_files(capture_id)` | image state and paths | `NotFoundError` |
| `cleanup_images(keep_accepted=200, keep_other=100)` | `{"deleted": [...], "skipped": None or reason}` | `ValueError` for bad limits |
| `save_calibration(camera_a, camera_b, *, source, ...)` | calibration | `ValueError` |
| `get_calibration(calibration_id)` | calibration | `NotFoundError` |
| `latest_calibration(source="real")` | calibration or `None` | |

`SessionStateError`, `NotFoundError`, `CaptureExistsError`, `ShotExistsError`
and `SchemaVersionError` all subclass `StorageError`.
The integrated server uses 409 for `SessionStateError`, 404 for
`NotFoundError` and 400 for input `ValueError`. 409 also fits
`CaptureExistsError` and `ShotExistsError`.

Every return value is a plain dict that `json.dumps` accepts directly.

Session:

```json
{"id": 1, "started_at": "2026-11-04T14:02:11.204+00:00", "ended_at": null,
 "active": true, "shot_count": 3}
```

Shot:

```json
{"id": 7, "session_id": 1, "created_at": "2026-11-04T14:03:40.918+00:00",
 "status": "accepted", "reason": null, "speed_m_s": 50.1, "speed_mph": 112.07,
 "launch_angle_deg": 15.0, "launch_direction_deg": null, "spin_rpm": null,
 "source": "simulation", "capture_id": null,
 "carry": {"estimated_carry_m": 182.3, "estimated_carry_yd": 199.4,
           "apex_height_m": 24.1, "flight_time_s": 6.2, "carry_model": "...",
           "carry_assumptions": {"backspin_rpm": 2500}, "carry_is_estimate": true,
           "carry_error": null},
 "samples": {"timestamps_s": [], "positions_m": []}}
```

`samples` appears only when requested. `carry` is `null` when no carry was
saved. Missing values are `null`, matching the draft API. `speed_mph` and
`estimated_carry_yd` are derived from metric values and are not stored.

## Rules

- Only one session can be open at a time. After a Pi restart, call
  `active_session()` to resume the open one or end it.
- Accepted shots require finite speed and launch angle. Speed cannot be negative.
- Rejected shots require a reason and are kept, as the design draft requires.
- Shots cannot be added to an ended session.
- One shot per capture. A second save for the same capture raises `ShotExistsError`.
- Carry is an estimate. It is stored in its own columns, never in measured fields.
- Commanded timing and measured timing are stored separately.

## Schema versions

The database records which layout it is at (`PRAGMA user_version`). On
startup, `ShotStore()` backs up an existing database next to itself
(`fairway.backup-v<old>-<time>.db`, also git-ignored) and runs any missing
migrations in one transaction. A failed upgrade changes nothing. A database
from newer code raises `SchemaVersionError`.

| Version | Change |
| --- | --- |
| 1 | `sessions` and `shots` |
| 2 | `calibrations` |
| 3 | `captures` |
| 4 | `capture_id` and carry columns on `shots` |

Never edit a migration that has run on a real database. Add a new one to the
end of `MIGRATIONS` instead.

## Try it

```sh
python scripts/storage_demo.py
python scripts/storage_demo.py --history   # run again to confirm data persisted
python -m pytest tests/test_storage.py
```

Set `FAIRWAY_DB=/some/path.db` to use a different file. Delete the file to
start fresh.

The standalone demo uses the shared velocity and speed calculations. It refuses
to reuse an active session; use `--db` for isolated experiments. The server uses
`source="simulation"`. Legacy standalone records may use `simulated`.

The storage API supports rejected captures. Currently the HTTP simulation endpoint
rejects malformed requests without saving a record; it does not accept raw captures.
Session transition locking in the server covers one process only. Do not run
multiple server processes or write with the standalone demo during a live session.
