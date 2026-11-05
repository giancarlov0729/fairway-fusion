# Shot storage

Local SQLite database for sessions and shots. Standard library only, so
nothing extra to install on the Pi. Data lives in `src/instance/fairway.db` at the
repository root, no matter which folder the server is started from, and
survives restarts. The file is git-ignored.

## Using it from the server

Create one store when the server starts and reuse it for every request.
It opens a short connection per call, so it is safe from worker threads.

```python
from fairway_fusion.storage import ShotStore, StorageError

store = ShotStore()                      # or ShotStore("path/to.db")

store.start_session()                    # Start button
shot = store.save_shot(speed_m_s, launch_angle_deg, samples=samples)
store.latest_shot()                      # Results screen
store.list_shots(limit=50)               # History screen, newest first
store.end_session()                      # End button
```

`samples` is optional. Passing the simulated input keeps each shot
replayable:

```python
{"timestamps_s": [0.001, 0.002], "positions_m": [[0.05, 0, 0.043], [0.10, 0, 0.056]]}
```

If the calculation raises `ValueError` on bad input, store the attempt
as rejected so it still shows in history:

```python
store.save_shot(status="rejected", reason=str(exc), samples=samples)
```

## Calls

| Call | Returns | Raises |
| --- | --- | --- |
| `start_session()` | session | `SessionStateError` if one is already active |
| `end_session(session_id=None)` | session | `SessionStateError` if none active or already ended |
| `active_session()` | session or `None` | |
| `get_session(id)` | session | `NotFoundError` |
| `list_sessions(limit=50, offset=0)` | list of sessions, newest first | |
| `save_shot(speed_m_s, launch_angle_deg, ...)` | shot with samples | `SessionStateError` if no active session, `ValueError` for bad values |
| `get_shot(id, include_samples=True)` | shot | `NotFoundError` |
| `latest_shot(session_id=None)` | shot or `None` | |
| `list_shots(session_id=None, limit=50, offset=0, include_samples=False)` | list of shots, newest first | |
| `delete_shot(id)` | `None` | `NotFoundError` |

`SessionStateError` and `NotFoundError` both subclass `StorageError`.
The integrated server uses 409 for `SessionStateError`, 404 for
`NotFoundError` and 400 for input `ValueError`.

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
 "source": "simulated", "samples": {"timestamps_s": [], "positions_m": []}}
```

`samples` appears only when requested. Missing values are `null`, matching
the draft API. `speed_mph` is derived from `speed_m_s` and is not stored.

## Rules

- Only one session can be open at a time. After a Pi restart, call
  `active_session()` to resume the open one or end it.
- Accepted shots require finite speed and launch angle. Speed cannot be negative.
- Rejected shots require a reason and are kept, as the design draft requires.
- Shots cannot be added to an ended session.
- `spin_rpm` and `launch_direction_deg` are already in the schema as nullable
  columns, so later stages can fill them without a migration.

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
