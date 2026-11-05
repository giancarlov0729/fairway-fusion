# Integrated server API

All request and response bodies use JSON. Errors use
`{"error": {"code": "...", "message": "..."}}`.

| Method | Path | Response |
| --- | --- | --- |
| GET | /api/health | Server status |
| POST | /api/shots/preview | Unsaved metrics; optional scenario in body |
| POST | /api/sessions | 201, {"session": {...}} |
| GET | /api/sessions/active | {"session": {...}} or null session |
| GET | /api/sessions | {"sessions": [...]} |
| POST | /api/sessions/<id>/end | {"session": {...}} |
| POST | /api/shots/simulate | 201, {"shot": {...}, "saved": true} |
| GET | /api/shots?session_id=<id> | {"shots": [...]} |
| GET | /api/shots/<id> | {"shot": {...}}, including samples |

Simulation requests require a positive integer session_id and an optional
scenario: straight, high-launch, or right-launch. Default: straight.
Only an active session accepts saved shots. Only one session may be active.
History lists return at most the latest 50 records; HTTP pagination is not yet exposed.

Shot fields include id, session_id, created_at, status, reason, speed_m_s,
speed_mph, launch_angle_deg, launch_direction_deg, spin_rpm, and source.
Spin is null for current simulations. Samples use positions_m and timestamps_s.
Source is simulation for server-generated shots. Session fields are id,
started_at, ended_at, active, and shot_count. Record timestamps use UTC.

Validation failures return 400, missing records 404, session conflicts 409,
and unexpected failures 500. Unsupported JSON content types return 415.
Invalid requests do not create a successful or rejected shot record. Storage
supports rejected capture records separately; the standalone demo exercises that.

The shared database defaults to src/instance/fairway.db in an editable install.
FAIRWAY_DB overrides it; application DATABASE_PATH configuration takes precedence.
Existing server history is preserved. Keep database files out of Git.

Run one server process. The application lock serializes session transitions
within that process; it does not coordinate separate database-writing programs.
