"""Local SQLite storage for practice sessions and shot results.

This module is the only place in Fairway Fusion that reads or writes the
database. The server calls it; the UI never touches the database directly.

Why SQLite:
    SQLite is a full database stored in a single file. It ships with Python
    (the ``sqlite3`` module), so nothing extra needs installing on the Pi, and
    there is no separate database server process to start or crash.

Why one short connection per call:
    The web server will likely handle requests on several threads at once.
    A Python sqlite3 connection should not be shared between threads, so every
    method below opens its own connection, does its work, and closes it. This
    costs about a millisecond and removes a whole class of threading bugs.

Units (kept in physics-standard units; convert only for display):
    speed            metres per second (m/s)
    angles           degrees
    sample positions metres
    sample times     seconds
    record times     UTC timestamps stored as ISO-8601 text,
                     e.g. "2026-11-04T14:02:11.204+00:00"
"""


# --- Imports ---------------------------------------------------------------
# All standard library. Nothing here needs pip.
import json  # converts the raw samples dict to and from text for storage
import math  # math.isfinite() rejects NaN and infinity before they are saved
import os  # reads the optional FAIRWAY_DB environment variable
import re  # checks capture IDs are exactly 32 lowercase hex characters
import sqlite3  # the database itself
import uuid  # generates unique 32-character hex IDs for calibrations
from contextlib import closing  # guarantees a connection is closed after use
from datetime import datetime, timezone  # timestamps for sessions and shots
from pathlib import Path  # builds the data/ folder path in a cross-platform way


# --- Constants -------------------------------------------------------------

# Keep the default shared with Flask and preserve existing server history.
# This path is independent of the working directory for editable installs.
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "instance" / "fairway.db"

# Exact conversion factor: 1 m/s = 2.2369362921 mph. Used only when shots are
# read back, so the UI can show mph without the database ever storing it.
MPS_TO_MPH = 2.2369362921


# --- Schema ------------------------------------------------------------------
# The SQL that defines the original two tables. It is now run as migration 1
# (see "Schema versions and migrations" below). "IF NOT EXISTS" means a
# database that already has these tables keeps them and their data.
SCHEMA = """
-- One row per practice session, i.e. everything between Start and End.
CREATE TABLE IF NOT EXISTS sessions (
    -- Unique number assigned automatically: 1, 2, 3 ...
    -- AUTOINCREMENT means a deleted id is never handed out again.
    id          INTEGER PRIMARY KEY AUTOINCREMENT,

    -- When Start was pressed. Required for every session.
    started_at  TEXT NOT NULL,

    -- When End was pressed. Deliberately allowed to be empty (NULL).
    -- An empty ended_at is HOW THE CODE KNOWS A SESSION IS STILL ACTIVE.
    -- There is no separate "active" column to keep in sync.
    ended_at    TEXT
);

-- One row per shot, i.e. one press of Simulate Shot (later, one real swing).
CREATE TABLE IF NOT EXISTS shots (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,

    -- Which session this shot belongs to. REFERENCES makes this a foreign
    -- key: the database refuses a shot that points at a session id that
    -- does not exist. ON DELETE CASCADE means deleting a session would also
    -- delete its shots, so no orphaned shots are left behind.
    session_id            INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,

    -- When the shot was saved.
    created_at            TEXT NOT NULL,

    -- Only these two words are allowed. The CHECK rule is enforced by the
    -- database itself, so a typo like 'acepted' fails even if some code
    -- skips the Python validation in save_shot().
    status                TEXT NOT NULL CHECK (status IN ('accepted', 'rejected')),

    -- Why a shot was rejected, e.g. "timestamps not increasing".
    -- Empty for accepted shots.
    reason                TEXT,

    -- The two numbers the pre-alpha calculates. REAL means a decimal number.
    -- They may be empty because a rejected shot might not have them.
    speed_m_s             REAL,
    launch_angle_deg      REAL,

    -- Not filled in the pre-alpha. Reserved so the spin and launch-direction
    -- work can start saving these later without changing the table.
    launch_direction_deg  REAL,
    spin_rpm              REAL,

    -- Where the shot came from. Defaults to 'simulated' so fake data can
    -- always be told apart from real camera data once hardware exists.
    source                TEXT NOT NULL DEFAULT 'simulated',

    -- The raw input points (times and x/y/z positions) stored as JSON text.
    -- Lets anyone look back at exactly what went into a calculation, replay
    -- it, or debug a strange result.
    samples_json          TEXT
);

-- An index works like a book's index: it lets "all shots in session 3" be
-- found without scanning every shot. Negligible at demo scale, cheap to keep.
CREATE INDEX IF NOT EXISTS idx_shots_session ON shots(session_id, id);
"""


# --- Schema versions and migrations ------------------------------------------
# Every SQLite file has a built-in whole number called user_version, which is
# 0 in a brand-new file. We use it to record which version of the table layout
# the file is at.
#
# Each time the layout changes, a new migration function is added to the end
# of MIGRATIONS and SCHEMA_VERSION goes up by one. On startup, ShotStore runs
# only the migrations the file has not had yet, in order. An old database
# (like the one on the Pi from the pre-alpha demo) is upgraded in place and
# keeps all of its sessions and shots.
#
# Rules for adding a migration later:
#   - Never edit or reorder an existing migration. Files in the field have
#     already run it. Add a new one instead.
#   - Only add things (tables, columns, indexes). Do not delete user data.


def _run_script(conn, script):
    """Run several SQL statements one at a time on an open connection.

    sqlite3's executescript() would be simpler, but it commits any open
    transaction before running, which would break the all-or-nothing
    guarantee of _migrate. sqlite3.complete_statement() reports when the
    text gathered so far forms one full statement (it correctly ignores
    semicolons inside comments), so each statement runs on its own.
    """
    statement = ""
    for line in script.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ""


def _migration_1(conn):
    """Version 1: the original sessions and shots tables (pre-alpha layout).

    Databases made before versioning existed already have these tables, so
    IF NOT EXISTS skips them. Those files are simply stamped as version 1.
    """
    _run_script(conn, SCHEMA)


CALIBRATIONS_SCHEMA = """
-- One row per calibration version. Rows are never edited: moving, refocusing
-- or re-fitting the cameras creates a NEW row with a new ID, so every old
-- capture still points at the exact calibration that produced its numbers.
CREATE TABLE IF NOT EXISTS calibrations (
    -- 32-character lowercase hex ID, generated by save_calibration().
    -- The CHECK makes the database itself refuse any other length.
    calibration_id         TEXT PRIMARY KEY CHECK (length(calibration_id) = 32),

    -- When this calibration was saved (UTC).
    created_at             TEXT NOT NULL,

    -- 'synthetic' for made-up test geometry, 'real' for one fitted from
    -- actual target images. Kept apart so test calibrations can never be
    -- attached to real captures by accident.
    source                 TEXT NOT NULL CHECK (source IN ('synthetic', 'real')),

    -- Each camera's full OpenCV pinhole model as JSON:
    --   {"K": 3x3, "distortion": [...], "R": 3x3, "t": [x, y, z],
    --    "image_size": [width, height]}
    -- K and distortion describe the lens (intrinsics). R and t describe
    -- where the camera sits and points in the golf frame (extrinsics).
    camera_a_json          TEXT NOT NULL,
    camera_b_json          TEXT NOT NULL,

    -- Distance between the two camera centres in metres. Calculated by
    -- save_calibration() from R and t, never typed in, so it cannot
    -- disagree with the matrices.
    baseline_m             REAL NOT NULL,

    -- How well the fit matched the target images, in pixels (smaller is
    -- better). Empty for synthetic calibrations, which are exact.
    reprojection_error_px  REAL,

    -- Free text, e.g. "after refocusing camera B".
    notes                  TEXT
);

-- Lets latest_calibration() find the newest real (or synthetic) calibration
-- without scanning the whole table.
CREATE INDEX IF NOT EXISTS idx_calibrations_source
    ON calibrations(source, created_at);
"""


def _migration_2(conn):
    """Version 2: add the calibrations table."""
    _run_script(conn, CALIBRATIONS_SCHEMA)


# Every state a capture can be in. Kept in Python too (not only in the SQL
# CHECK below) so save errors can name the allowed values.
CAPTURE_STATUSES = (
    "preparing",    # record created, cameras and Pico being set up
    "armed",        # Pico is waiting for the impact
    "capturing",    # impact detected, triggers and flashes running
    "processing",   # images being turned into a result
    "completed",    # finished
    "cancelled",    # user disarmed before impact
    "timed_out",    # no impact within the time limit
    "failed",       # something broke; the error column says what
    "interrupted",  # server stopped mid-capture (set at the next startup)
)

# The states that mean "still in progress". If the server restarts while a
# capture is in one of these, nothing will ever finish it, so
# mark_interrupted_captures() moves it to "interrupted".
UNFINISHED_CAPTURE_STATUSES = ("preparing", "armed", "capturing", "processing")

CAPTURES_SCHEMA = f"""
-- One row per Arm press: everything about one capture attempt, including
-- failed ones. A successful capture also produces a row in shots (step 5).
CREATE TABLE IF NOT EXISTS captures (
    -- 32-character lowercase hex ID generated by the server's capture
    -- workflow (uuid4().hex) and echoed back by the Pico.
    capture_id            TEXT PRIMARY KEY CHECK (length(capture_id) = 32),

    -- Which session the capture belongs to, if any. ON DELETE SET NULL
    -- keeps the capture record even if its session is ever deleted.
    session_id            INTEGER REFERENCES sessions(id) ON DELETE SET NULL,

    -- When the record was created, and when it last changed (UTC).
    created_at            TEXT NOT NULL,
    updated_at            TEXT NOT NULL,

    -- Lifecycle state, one of CAPTURE_STATUSES.
    status                TEXT NOT NULL CHECK (status IN {CAPTURE_STATUSES}),

    -- What went wrong, in words. Empty when nothing has.
    error                 TEXT,

    -- 'live' for real hardware, 'simulation' for generated data, 'replay'
    -- for saved images run back through the pipeline on a laptop.
    source                TEXT NOT NULL DEFAULT 'live'
                          CHECK (source IN ('live', 'simulation', 'replay')),

    -- The timing the Pico was told to use (camera trigger, flash width,
    -- flash interval, first-flash delay), as JSON.
    timing_json           TEXT,

    -- Which timing the speed calculation relied on: 'commanded' (what the
    -- Pico was told) or 'measured' (verified on the oscilloscope).
    timing_source         TEXT NOT NULL DEFAULT 'commanded'
                          CHECK (timing_source IN ('commanded', 'measured')),

    -- Evan's full capture report from the Pico, stored as-is as JSON, so
    -- new firmware fields are kept without another migration.
    pico_report_json      TEXT,

    -- Oscilloscope or photodiode measurements of the real timing, as JSON.
    -- Empty until someone actually measures. Kept apart from the commanded
    -- timing, because a commanded value does not prove what really happened.
    measured_timing_json  TEXT,

    -- Which calibration version was active for this capture.
    calibration_id        TEXT REFERENCES calibrations(calibration_id),

    -- Which version of the vision code produced the result.
    processing_version    TEXT,

    -- Camera images, as paths relative to the instance folder, e.g.
    -- "captures/<capture_id>/camera_a.png". Relative so the folder can be
    -- copied to a laptop for replay and the paths still work.
    image_a_path          TEXT,
    image_b_path          TEXT,

    -- Giancarlo's per-frame ball detections (pixel centres and times), as
    -- JSON, for debugging a strange 3D reconstruction later.
    detections_json       TEXT,

    -- 1 = never delete this capture's images in cleanup (presentation
    -- evidence, important failures). 0 = normal.
    pinned                INTEGER NOT NULL DEFAULT 0 CHECK (pinned IN (0, 1)),

    -- When cleanup deliberately deleted the image files. Lets the UI say
    -- "images removed" instead of reporting them as missing.
    images_deleted_at     TEXT
);

-- Find unfinished captures quickly at startup, and a session's captures.
CREATE INDEX IF NOT EXISTS idx_captures_status ON captures(status);
CREATE INDEX IF NOT EXISTS idx_captures_session ON captures(session_id, created_at);
"""


def _migration_3(conn):
    """Version 3: add the captures table."""
    _run_script(conn, CAPTURES_SCHEMA)


SHOT_LINKS_AND_CARRY_SCHEMA = """
-- Adds columns to the existing shots table. ALTER TABLE ... ADD COLUMN keeps
-- every existing row; old shots simply get empty values in the new columns.

-- Which capture produced this shot. Empty for simulated shots, which have no
-- capture. The UNIQUE index below allows only ONE shot per capture, which is
-- what blocks duplicate saves: a retried save of the same capture is refused.
ALTER TABLE shots ADD COLUMN capture_id TEXT REFERENCES captures(capture_id);

-- Steffano's carry estimate. These are MODEL OUTPUTS, not measurements, and
-- are kept in their own columns so they can never be mistaken for measured
-- values. Yards are not stored: they are calculated from metres when read,
-- the same way mph is calculated from m/s.
ALTER TABLE shots ADD COLUMN estimated_carry_m REAL;
ALTER TABLE shots ADD COLUMN apex_height_m REAL;
ALTER TABLE shots ADD COLUMN flight_time_s REAL;

-- Which model produced the estimate, and every assumption it used (spin,
-- air density, drag and so on) as JSON, so any carry number can be explained.
ALTER TABLE shots ADD COLUMN carry_model TEXT;
ALTER TABLE shots ADD COLUMN carry_assumptions_json TEXT;

-- Why no carry could be estimated (e.g. bad launch angle). When this is set,
-- the three carry numbers above are empty.
ALTER TABLE shots ADD COLUMN carry_error TEXT;

-- One shot per capture. SQLite allows many empty (NULL) values in a UNIQUE
-- index, so simulated shots without a capture are unaffected.
CREATE UNIQUE INDEX IF NOT EXISTS idx_shots_capture ON shots(capture_id);
"""


def _migration_4(conn):
    """Version 4: link shots to captures, and add carry columns to shots."""
    _run_script(conn, SHOT_LINKS_AND_CARRY_SCHEMA)


# Position in this list = version number it upgrades TO. MIGRATIONS[0] takes
# a file to version 1, MIGRATIONS[1] to version 2, and so on.
MIGRATIONS = [_migration_1, _migration_2, _migration_3, _migration_4]

# The version this code expects. Always equal to the number of migrations.
SCHEMA_VERSION = len(MIGRATIONS)


# --- Error types -------------------------------------------------------------
# Custom exceptions let the server tell problems apart and respond properly,
# e.g. "you need to start a session first" versus "that shot does not exist".
# Both specific errors inherit from StorageError, so the server can also catch
# StorageError alone to handle every storage problem in one place.


class StorageError(RuntimeError):
    """Base class for storage errors the server should report to the user."""


class SessionStateError(StorageError):
    """The action does not fit the current session state.

    Examples: starting a session while one is already open, ending a session
    when none is open, or saving a shot with no active session.
    Suggested HTTP response from the server: 409 Conflict.
    """


class NotFoundError(StorageError):
    """A requested session or shot id does not exist.

    Suggested HTTP response from the server: 404 Not Found.
    """


class CaptureExistsError(StorageError):
    """A capture with this ID has already been saved.

    Capture IDs are unique, so a second create() with the same ID is a bug
    or a retried request. The original record is left untouched.
    Suggested HTTP response from the server: 409 Conflict.
    """


class ShotExistsError(StorageError):
    """This capture already has a saved shot.

    Each capture produces at most one shot. A second save for the same
    capture is refused rather than silently creating a duplicate in history.
    Suggested HTTP response from the server: 409 Conflict.
    """


class SchemaVersionError(StorageError):
    """The database file was written by newer code than this version.

    Happens if someone runs an older checkout against a database a newer
    checkout already upgraded. Refusing is safer than guessing at a layout
    this code does not understand. Fix: update the code (git pull).
    """


# --- Small helper functions ----------------------------------------------------
# Names starting with an underscore are internal: other modules should not
# need to call them.


def _now():
    """Current time in UTC as ISO-8601 text with millisecond precision.

    UTC avoids daylight-saving confusion. This text format also sorts
    correctly alphabetically, which matters because SQLite has no real date
    type and compares these values as plain text.
    """
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _finite_or_none(name, value):
    """Return ``value`` as a float, keep None as None, reject anything else.

    Used to validate every number before it reaches the database.
    ``name`` is only used to make the error message say which field was bad.
    """
    # None is allowed: it means "no value", stored as NULL, shown as null.
    if value is None:
        return None
    # float() accepts ints, floats and numeric strings like "50.2".
    # Anything else, such as "fast" or a list, raises here.
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number or None") from exc
    # NaN ("not a number") and infinity are technically floats, but they
    # break averages, charts and JSON output, so they are refused outright.
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


# --- Calibration checks ------------------------------------------------------------
# These turn one camera's calibration into plain lists and refuse anything that
# is the wrong shape or not a real number, BEFORE it reaches the database.
# They accept numpy arrays or plain lists, and either a dict with the five
# keys or Giancarlo's CameraCalibration object, so callers do not need to
# convert anything first. Only the standard library is used: storage must not
# depend on numpy or OpenCV.

# Distortion vector lengths OpenCV can produce, depending on the lens model.
_DISTORTION_LENGTHS = (4, 5, 8, 12, 14)


def _plain(value):
    """numpy array -> nested Python lists. Plain lists pass through."""
    return value.tolist() if hasattr(value, "tolist") else value


def _flatten(value):
    """Turn [[a], [b], [c]] or [[a, b, c]] into [a, b, c].

    OpenCV often returns vectors as one-column or one-row grids, e.g. t with
    shape (3, 1). This accepts any of those shapes.
    """
    flat = []
    for item in value:
        if isinstance(item, (list, tuple)):
            flat.extend(item)
        else:
            flat.append(item)
    return flat


def _number_list(name, value, length=None):
    """A flat list of finite floats, optionally of an exact length."""
    try:
        numbers = [_finite_or_none(name, v) for v in _flatten(value)]
    except TypeError as exc:  # value was not a list at all
        raise ValueError(f"{name} must be a list of numbers") from exc
    if None in numbers:
        raise ValueError(f"{name} cannot contain empty values")
    if length is not None and len(numbers) != length:
        raise ValueError(f"{name} must have {length} numbers, got {len(numbers)}")
    return numbers


def _matrix_3x3(name, value):
    """A 3x3 grid of finite floats."""
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{name} must be a 3x3 matrix")
    return [_number_list(f"{name} row {i}", row, 3) for i, row in enumerate(value)]


def _check_rotation(name, R):
    """Refuse an R that is not a proper rotation.

    A rotation matrix multiplied by its own transpose gives the identity, and
    its determinant is +1. A transposed, scaled or garbled R fails this, and
    would otherwise silently put every reconstructed ball in the wrong place.
    """
    for i in range(3):
        for j in range(3):
            dot = sum(R[i][k] * R[j][k] for k in range(3))
            if abs(dot - (1.0 if i == j else 0.0)) > 1e-6:
                raise ValueError(f"{name} is not a rotation matrix")
    det = (
        R[0][0] * (R[1][1] * R[2][2] - R[1][2] * R[2][1])
        - R[0][1] * (R[1][0] * R[2][2] - R[1][2] * R[2][0])
        + R[0][2] * (R[1][0] * R[2][1] - R[1][1] * R[2][0])
    )
    if abs(det - 1.0) > 1e-6:
        raise ValueError(f"{name} is a reflection, not a rotation (determinant -1)")


def _camera_dict(name, camera):
    """Validate one camera and return it as plain, JSON-ready Python data."""

    def field(key):
        # Works for a dict ({"K": ...}) and for an object (camera.K).
        try:
            value = camera[key] if isinstance(camera, dict) else getattr(camera, key)
        except (KeyError, AttributeError) as exc:
            raise ValueError(f"{name} is missing {key}") from exc
        return _plain(value)

    K = _matrix_3x3(f"{name} K", field("K"))
    if K[0][0] <= 0 or K[1][1] <= 0:
        raise ValueError(f"{name} K must have positive focal lengths")

    distortion = _number_list(f"{name} distortion", field("distortion"))
    if len(distortion) not in _DISTORTION_LENGTHS:
        raise ValueError(
            f"{name} distortion must have one of {_DISTORTION_LENGTHS} numbers"
        )

    R = _matrix_3x3(f"{name} R", field("R"))
    _check_rotation(f"{name} R", R)

    t = _number_list(f"{name} t", field("t"), 3)

    size = _number_list(f"{name} image_size", field("image_size"), 2)
    if any(v <= 0 or v != int(v) for v in size):
        raise ValueError(f"{name} image_size must be two positive whole numbers")

    return {
        "K": K,
        "distortion": distortion,
        "R": R,
        "t": t,
        "image_size": [int(v) for v in size],
    }


def _camera_centre(camera):
    """Where the camera sits in the golf frame, in metres.

    R and t map a world point to camera coordinates: x_cam = R x_world + t.
    The camera centre is the world point that maps to (0, 0, 0), which works
    out to C = -R^T t (R^T is R transposed).
    """
    R, t = camera["R"], camera["t"]
    return [-sum(R[k][i] * t[k] for k in range(3)) for i in range(3)]


# --- Capture checks ----------------------------------------------------------------
# A capture record arrives as a dict, the same shape Ethan's CaptureService
# already builds. Each key it may contain is listed in _CAPTURE_FIELDS with the
# column it is stored in and the check that turns it into a database value.
# Keys not in this list are refused, so a typo like "statsu" raises an error
# instead of being silently ignored.


def _check_capture_id(capture_id):
    """Exactly 32 lowercase hex characters, the format of uuid4().hex."""
    if not isinstance(capture_id, str) or not re.fullmatch(r"[0-9a-f]{32}", capture_id):
        raise ValueError("capture_id must be 32 lowercase hex characters")
    return capture_id


def _choice(allowed):
    """Make a check that only accepts one of the allowed words."""

    def check(key, value):
        if value not in allowed:
            raise ValueError(f"{key} must be one of {allowed}, got {value!r}")
        return value

    return check


def _json_or_none(key, value):
    """A dict (or list) stored as JSON text. None stays empty.

    allow_nan=False refuses NaN and infinity, which are not valid JSON and
    would break the browser later.
    """
    if value is None:
        return None
    if not isinstance(value, (dict, list)):
        raise ValueError(f"{key} must be a dict, a list or None")
    try:
        return json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} contains a value that cannot be saved: {exc}") from exc


def _text_or_none(key, value):
    if value is not None and not isinstance(value, str):
        raise ValueError(f"{key} must be text or None")
    return value


def _int_or_none(key, value):
    # bool is a kind of int in Python, so it is refused explicitly:
    # session_id=True is a bug, not session 1.
    if value is not None and (type(value) is not int):
        raise ValueError(f"{key} must be a whole number or None")
    return value


def _relative_path(key, value):
    """A path inside the instance folder, written with forward slashes.

    Refuses absolute paths (/home/..., C:\\...) and ".." so a stored path
    can never point outside the instance folder, and the folder can be
    copied to another computer with every path still valid.
    """
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{key} must be a relative path or None")
    if "\\" in value:
        raise ValueError(f"{key} must use forward slashes")
    if value.startswith("/") or re.match(r"[A-Za-z]:", value):
        raise ValueError(f"{key} must be relative to the instance folder")
    if ".." in value.split("/"):
        raise ValueError(f"{key} cannot contain '..'")
    return value


def _flag(key, value):
    """True/False stored as 1/0, since SQLite has no true/false type."""
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be True or False")
    return int(value)


# record key -> (database column, check). JSON fields get a _json column.
_CAPTURE_FIELDS = {
    "session_id": ("session_id", _int_or_none),
    "status": ("status", _choice(CAPTURE_STATUSES)),
    "error": ("error", _text_or_none),
    "source": ("source", _choice(("live", "simulation", "replay"))),
    "timing": ("timing_json", _json_or_none),
    "timing_source": ("timing_source", _choice(("commanded", "measured"))),
    "pico_report": ("pico_report_json", _json_or_none),
    "measured_timing": ("measured_timing_json", _json_or_none),
    "calibration_id": ("calibration_id", _text_or_none),
    "processing_version": ("processing_version", _text_or_none),
    "image_a_path": ("image_a_path", _relative_path),
    "image_b_path": ("image_b_path", _relative_path),
    "detections": ("detections_json", _json_or_none),
    "pinned": ("pinned", _flag),
    "images_deleted_at": ("images_deleted_at", _text_or_none),
}


def _capture_columns(fields):
    """Check every field and return {column: database value}.

    Raises ValueError for an unknown key or a bad value, before anything
    is written.
    """
    unknown = set(fields) - set(_CAPTURE_FIELDS)
    if unknown:
        raise ValueError(f"Unknown capture fields: {sorted(unknown)}")
    columns = {}
    for key, value in fields.items():
        column, check = _CAPTURE_FIELDS[key]
        columns[column] = check(key, value)
    return columns


# --- Carry checks ------------------------------------------------------------------
# Steffano's carry_metrics() returns exactly these keys. Anything else is
# refused, so a renamed or misspelled key fails loudly instead of being lost.
_CARRY_KEYS = {
    "estimated_carry_m", "estimated_carry_yd", "apex_height_m", "flight_time_s",
    "carry_model", "carry_assumptions", "carry_is_estimate", "carry_error",
}

# Metres to yards, for reading carry back. 1 yard is exactly 0.9144 m.
M_TO_YD = 1 / 0.9144


def _carry_columns(carry):
    """Check Steffano's carry dict and return {column: database value}.

    carry=None means no carry was calculated, and every carry column stays
    empty. estimated_carry_yd and carry_is_estimate are accepted but not
    stored: yards are recalculated from metres when read, and every stored
    carry is an estimate by definition.
    """
    empty = {
        "estimated_carry_m": None, "apex_height_m": None, "flight_time_s": None,
        "carry_model": None, "carry_assumptions_json": None, "carry_error": None,
    }
    if carry is None:
        return empty
    if not isinstance(carry, dict):
        raise ValueError("carry must be the dict carry_metrics() returns, or None")
    unknown = set(carry) - _CARRY_KEYS
    if unknown:
        raise ValueError(f"Unknown carry fields: {sorted(unknown)}")

    numbers = {
        key: _finite_or_none(key, carry.get(key))
        for key in ("estimated_carry_m", "apex_height_m", "flight_time_s")
    }
    error = _text_or_none("carry_error", carry.get("carry_error"))
    if error:
        # Steffano's rule: when there is an error, there are no numbers.
        if any(value is not None for value in numbers.values()):
            raise ValueError("carry has an error, so its numbers must be empty")
    else:
        # No error means a real estimate: all three numbers must be there,
        # and none of them can be negative.
        if any(value is None for value in numbers.values()):
            raise ValueError("carry without an error needs carry, apex and flight time")
        if any(value < 0 for value in numbers.values()):
            raise ValueError("carry, apex and flight time cannot be negative")

    return {
        **numbers,
        "carry_model": _text_or_none("carry_model", carry.get("carry_model")),
        "carry_assumptions_json": _json_or_none(
            "carry_assumptions", carry.get("carry_assumptions")
        ),
        "carry_error": error,
    }


# --- The store -----------------------------------------------------------------


class ShotStore:
    """Everything the server needs for sessions and shots.

    Create ONE of these when the server starts, then reuse it for every
    request. It holds no open connection, only the file path, so sharing it
    across threads is safe.

    ``path`` defaults to the FAIRWAY_DB environment variable, then
    ``data/fairway.db``. Use a file path, not ``":memory:"``: every call
    opens a new connection, and each in-memory connection starts empty.
    """

    def __init__(self, path=None):
        # Choose the database file. The first option that is set wins:
        #   1. a path passed in directly (the tests do this, so they write to
        #      a throwaway file and never touch real data)
        #   2. the FAIRWAY_DB environment variable
        #   3. the default, data/fairway.db
        self.path = str(path or os.environ.get("FAIRWAY_DB") or DEFAULT_DB_PATH)

        # An in-memory database disappears the moment its connection closes.
        # This class closes its connection after every call, so the tables
        # would vanish immediately. Fail loudly instead of failing silently.
        if self.path == ":memory:":
            raise ValueError("ShotStore needs a file path; use a temp file for tests")

        # Make sure the folder exists (e.g. data/ on a fresh clone). SQLite
        # creates the file itself but will crash if the folder is missing.
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)

        # Create the tables, or upgrade an older database to the current
        # layout. Safe on every startup: a file already at SCHEMA_VERSION
        # is left untouched.
        self._migrate()

    def _migrate(self):
        """Bring the database file up to SCHEMA_VERSION.

        Steps:
          1. If an existing database is about to change, back it up first.
          2. Take the write lock and start ONE transaction for the rest, so
             a failure in any later step undoes everything.
          3. Refuse a file newer than this code (SchemaVersionError).
          4. Run each missing migration in order.
          5. Record the new version and commit.
        """
        # isolation_level=None turns off sqlite3's automatic transaction
        # handling, so this method can control BEGIN, COMMIT and ROLLBACK
        # itself. That is needed because some layout changes (CREATE TABLE,
        # ALTER TABLE) would otherwise not be covered by the transaction.
        conn = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        with closing(conn):
            # Back up BEFORE taking the write lock. SQLite's backup cannot
            # copy a database while this same connection holds its write
            # lock; it waits forever. Version 0 with no tables is a brand-new
            # empty file, so there is nothing to back up. Anything else
            # that is about to be upgraded is real data.
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            has_tables = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' LIMIT 1"
            ).fetchone()
            if version < SCHEMA_VERSION and has_tables:
                self._backup(conn, version)

            # BEGIN IMMEDIATE takes the write lock right away. If two server
            # processes start at the same moment, the second one waits here,
            # then sees the version the first one already wrote, and does
            # nothing. Without it, both could try to upgrade at once.
            conn.execute("BEGIN IMMEDIATE")
            try:
                # Read the version again now that we hold the lock, in case
                # another process upgraded the file in the meantime.
                version = conn.execute("PRAGMA user_version").fetchone()[0]

                if version > SCHEMA_VERSION:
                    raise SchemaVersionError(
                        f"Database is at schema version {version}, but this code "
                        f"only understands up to {SCHEMA_VERSION}. Update the code."
                    )

                if version < SCHEMA_VERSION:
                    # Run every migration the file has not had yet, in order.
                    # MIGRATIONS[version] is the one that upgrades FROM it.
                    for migration in MIGRATIONS[version:]:
                        migration(conn)

                    # PRAGMA statements cannot use "?" placeholders, so the
                    # number is put into the text directly. That is safe here
                    # because SCHEMA_VERSION is a whole number set in this
                    # file, never user input.
                    conn.execute(f"PRAGMA user_version = {int(SCHEMA_VERSION)}")

                conn.execute("COMMIT")
            except BaseException:
                # Anything went wrong: undo every change, including the
                # version number, then let the error continue upward.
                conn.execute("ROLLBACK")
                raise

    def _backup(self, conn, version):
        """Copy the database next to itself before it is upgraded.

        Named like fairway.backup-v1-20261111T021500Z.db so backups sort by
        time and show which version they hold. The name ends in .db, so the
        "*.db" line in .gitignore keeps backups out of git.

        Uses SQLite's own backup feature rather than copying the file, so the
        copy is consistent even if the database is in use.
        """
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        original = Path(self.path)
        target = original.with_name(f"{original.stem}.backup-v{version}-{stamp}.db")
        with closing(sqlite3.connect(target)) as copy:
            conn.backup(copy)

    def _connect(self):
        """Open a fresh connection configured the way every method expects."""
        # timeout=5: if another thread is mid-write, wait up to 5 seconds for
        # it to finish instead of failing immediately with "database locked".
        conn = sqlite3.connect(self.path, timeout=5)

        # Lets code read results by column name, row["speed_m_s"], instead of
        # by position, row[5], which silently breaks if columns are reordered.
        conn.row_factory = sqlite3.Row

        # SQLite IGNORES foreign keys by default, and the setting has to be
        # switched on for every new connection. Without this line the
        # "REFERENCES sessions(id)" rule in the schema would do nothing.
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # About the pattern used in the methods below:
    #
    #     with closing(self._connect()) as conn, conn:
    #
    # This does two separate jobs.
    #   closing(...) as conn  -> the connection is always closed when the block
    #                            ends, even if an error is raised inside it.
    #   the second "conn"     -> wraps the block in a TRANSACTION. If the block
    #                            finishes normally, all changes are saved
    #                            (committed). If anything raises an error part
    #                            way through, every change in the block is
    #                            undone (rolled back). There is never a
    #                            half-saved shot or half-ended session.
    #
    # Methods that only read data use just "with closing(...) as conn:"
    # because there is nothing to save or undo.

    # Sessions -------------------------------------------------------------

    def start_session(self):
        """Open a new session and return it. Only one session may be open.

        Called when the user presses Start.
        Returns the new session as a dict (see _session_dict for the fields).
        Raises SessionStateError if a session is already active.
        """
        with closing(self._connect()) as conn, conn:
            # Refuse to start a second session while one is open. Two open
            # sessions would make it unclear which one new shots belong to.
            if self._active(conn) is not None:
                raise SessionStateError("A session is already active; end it first")

            # Insert a row with only the start time. The id is assigned
            # automatically and ended_at stays empty, which marks it active.
            # The "?" is a placeholder: sqlite3 fills it in safely, which
            # prevents SQL injection and quoting bugs. Never build SQL by
            # pasting values into the string.
            cursor = conn.execute(
                "INSERT INTO sessions (started_at) VALUES (?)", (_now(),)
            )

            # cursor.lastrowid is the id SQLite just assigned. Read the full
            # row back so the caller gets exactly what was stored.
            return self._session(conn, cursor.lastrowid)

    def end_session(self, session_id=None):
        """Close the given session, or the active one if no id is given.

        Called when the user presses End. Normally called with no arguments.
        Raises SessionStateError if there is nothing to end or it already ended.
        Raises NotFoundError if a given session_id does not exist.
        """
        with closing(self._connect()) as conn, conn:
            # No id given: find the active session ourselves.
            if session_id is None:
                active = self._active(conn)
                if active is None:
                    raise SessionStateError("No active session to end")
                session_id = active["id"]

            # Look the session up (raises NotFoundError if the id is bad) and
            # refuse to end it twice, which would overwrite the real end time.
            session = self._session(conn, session_id)
            if session["ended_at"] is not None:
                raise SessionStateError(f"Session {session_id} has already ended")

            # Filling in ended_at is what makes the session no longer active.
            conn.execute(
                "UPDATE sessions SET ended_at = ? WHERE id = ?", (_now(), session_id)
            )
            return self._session(conn, session_id)

    def active_session(self):
        """Return the open session, or None if no session is open.

        Useful when the server starts: if the Pi rebooted mid-session, the
        session is still open in the database, and the UI can show it as in
        progress (or the server can end it) instead of getting stuck.
        """
        with closing(self._connect()) as conn:
            active = self._active(conn)
            return None if active is None else self._session(conn, active["id"])

    def get_session(self, session_id):
        """Return one session by id. Raises NotFoundError if it does not exist."""
        with closing(self._connect()) as conn:
            return self._session(conn, session_id)

    def list_sessions(self, limit=50, offset=0):
        """Return sessions newest first, each with its shot count.

        ``limit`` is how many to return (capped at 500 by _bounded).
        ``offset`` skips that many first, for paging: offset=50 gives the
        next 50 after the first page.
        """
        with closing(self._connect()) as conn:
            # LEFT JOIN pairs each session with its shots. LEFT means sessions
            # with zero shots are still included. GROUP BY s.id collapses the
            # result back to one row per session, and COUNT(sh.id) counts how
            # many shots were paired with it. ORDER BY id DESC puts the newest
            # first, since ids only ever increase.
            rows = conn.execute(
                """SELECT s.*, COUNT(sh.id) AS shot_count
                   FROM sessions s LEFT JOIN shots sh ON sh.session_id = s.id
                   GROUP BY s.id ORDER BY s.id DESC LIMIT ? OFFSET ?""",
                (_bounded(limit), max(int(offset), 0)),
            ).fetchall()
            # Convert raw database rows into plain dicts for the server.
            return [_session_dict(row) for row in rows]

    # Shots ------------------------------------------------------------------

    def save_shot(
        self,
        speed_m_s=None,
        launch_angle_deg=None,
        *,  # every argument after this must be given by name, e.g. status="rejected"
        status="accepted",
        reason=None,
        launch_direction_deg=None,
        spin_rpm=None,
        source="simulation",
        samples=None,
        session_id=None,
        carry=None,
        capture_id=None,
    ):
        """Store one shot in the active session (or ``session_id``).

        Called after each Simulate Shot press (or, with real hardware, after a
        capture is processed), once the launch angle and ball speed have been
        calculated.

        Typical accepted shot:
            store.save_shot(50.1, 15.0, samples=samples, carry=result["carry"])

        Typical rejected shot (calculation failed on bad input):
            store.save_shot(status="rejected", reason=str(error), samples=samples)

        Shot from a real capture (at most one shot per capture):
            store.save_shot(..., capture_id=capture_id, carry=carry)

        Accepted shots need speed and launch angle. Rejected shots need a
        reason and keep whatever values exist, so they stay visible in history.
        ``samples`` is optional raw input, e.g. {"timestamps_s": [...],
        "positions_m": [[x, y, z], ...]}, kept for replay and debugging.
        ``carry`` is the dict Steffano's carry_metrics() returns, saved as-is.

        Returns the saved shot as a dict, including its samples.
        Raises ValueError for bad values, SessionStateError if no session is
        open, NotFoundError for an unknown capture, and ShotExistsError if the
        capture already has a shot.
        """
        # Step 1: validate everything BEFORE opening the database, so bad
        # input never gets anywhere near the stored data.

        if status not in ("accepted", "rejected"):
            raise ValueError("status must be 'accepted' or 'rejected'")

        # Run every number through the same check: float or None, never
        # NaN or infinity.
        values = {
            "speed_m_s": _finite_or_none("speed_m_s", speed_m_s),
            "launch_angle_deg": _finite_or_none("launch_angle_deg", launch_angle_deg),
            "launch_direction_deg": _finite_or_none(
                "launch_direction_deg", launch_direction_deg
            ),
            "spin_rpm": _finite_or_none("spin_rpm", spin_rpm),
        }

        if status == "accepted":
            # An accepted shot without its two core numbers makes no sense.
            if values["speed_m_s"] is None or values["launch_angle_deg"] is None:
                raise ValueError("accepted shots need speed_m_s and launch_angle_deg")
            # Speed is a magnitude, so a negative value means a bug upstream.
            # Launch angle CAN be negative (a shot going downward), so it is
            # not checked the same way.
            if values["speed_m_s"] < 0:
                raise ValueError("speed_m_s cannot be negative")
        elif not reason:
            # A rejected shot must say why, otherwise history shows a
            # rejection nobody can explain.
            raise ValueError("rejected shots need a reason")

        # Turn the samples dict into JSON text. allow_nan=False makes this
        # raise an error on NaN or infinity instead of writing invalid JSON.
        samples_json = (
            None if samples is None else json.dumps(samples, allow_nan=False)
        )

        carry_columns = _carry_columns(carry)
        if capture_id is not None:
            _check_capture_id(capture_id)

        # Step 2: write to the database inside one transaction.
        with closing(self._connect()) as conn, conn:
            if session_id is None:
                # Normal case: attach the shot to whichever session is open.
                active = self._active(conn)
                if active is None:
                    raise SessionStateError("Start a session before saving shots")
                session_id = active["id"]
            elif self._session(conn, session_id)["ended_at"] is not None:
                # A specific session was named, but it is already over.
                # (_session also raises NotFoundError if the id is bad.)
                raise SessionStateError(f"Session {session_id} has ended")

            if capture_id is not None:
                # The capture must exist, and must not already have a shot.
                # The UNIQUE index would also refuse a duplicate, but this
                # check gives a clear message that names the existing shot.
                self._capture(conn, capture_id)  # raises NotFoundError
                existing = conn.execute(
                    "SELECT id FROM shots WHERE capture_id = ?", (capture_id,)
                ).fetchone()
                if existing:
                    raise ShotExistsError(
                        f"Capture {capture_id} already has shot {existing['id']}"
                    )

            columns = {
                "session_id": session_id,
                "created_at": _now(),
                "status": status,
                "reason": reason,
                **values,
                "source": source,
                "samples_json": samples_json,
                "capture_id": capture_id,
                **carry_columns,
            }
            # Column names come from the fixed dict above, never from the
            # caller, so building them into the SQL text is safe. Every value
            # still goes through "?" placeholders.
            cursor = conn.execute(
                f"INSERT INTO shots ({', '.join(columns)}) "
                f"VALUES ({', '.join('?' for _ in columns)})",
                list(columns.values()),
            )
            # Read the saved row back so the server can show it immediately
            # on the results screen, with the id and timestamp the database set.
            return self._shot(conn, cursor.lastrowid, include_samples=True)

    def get_shot(self, shot_id, include_samples=True):
        """Return one shot by id. Raises NotFoundError if it does not exist.

        Includes the raw samples by default, since this is for a detail view.
        """
        with closing(self._connect()) as conn:
            return self._shot(conn, shot_id, include_samples)

    def latest_shot(self, session_id=None):
        """Most recent shot overall, or in one session. None if there are none.

        Feeds the results screen. It reuses list_shots with a limit of one
        rather than duplicating the query.
        """
        shots = self.list_shots(session_id=session_id, limit=1)
        return shots[0] if shots else None

    def list_shots(self, session_id=None, limit=50, offset=0, include_samples=False):
        """Return shots newest first. Missing values are None (null in JSON).

        Feeds the history screen.
        ``session_id``: only that session's shots; None means all sessions.
        ``limit`` and ``offset``: paging, same as list_sessions.
        ``include_samples``: off by default, because history lists only need
        the headline numbers and the samples would make responses much larger.
        """
        # The query is built in pieces because the WHERE clause is optional.
        # Only fixed SQL text is ever added to the string; every value goes
        # into params and is filled in through "?" placeholders.
        query = "SELECT * FROM shots"
        params = []
        if session_id is not None:
            query += " WHERE session_id = ?"
            params.append(session_id)
        query += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params += [_bounded(limit), max(int(offset), 0)]

        with closing(self._connect()) as conn:
            rows = conn.execute(query, params).fetchall()
            return [_shot_dict(row, include_samples) for row in rows]

    def delete_shot(self, shot_id):
        """Permanently delete one shot. Raises NotFoundError if it does not exist.

        Matches DELETE /shots/{id} in the design draft. The UI should ask the
        user to confirm before calling this.
        """
        with closing(self._connect()) as conn, conn:
            # rowcount is how many rows the DELETE removed. Zero means the id
            # did not exist, which is reported instead of silently ignored.
            if conn.execute("DELETE FROM shots WHERE id = ?", (shot_id,)).rowcount == 0:
                raise NotFoundError(f"Shot {shot_id} not found")

    # Calibrations -------------------------------------------------------------

    def save_calibration(
        self, camera_a, camera_b, *, source, reprojection_error_px=None, notes=None
    ):
        """Save a new calibration version and return it with its new ID.

        ``camera_a`` and ``camera_b`` can each be a dict with the keys K,
        distortion, R, t and image_size, or a CameraCalibration object from
        fairway_fusion.calibration. numpy arrays are fine.

        ``source`` has no default on purpose: the caller must say whether this
        is a 'synthetic' or 'real' calibration, so test geometry is never
        labelled real by accident.

        Raises ValueError if anything is the wrong shape, not a finite number,
        or physically impossible (R not a rotation, both cameras in the same
        spot).
        """
        # Step 1: validate everything before opening the database.
        if source not in ("synthetic", "real"):
            raise ValueError("source must be 'synthetic' or 'real'")
        a = _camera_dict("camera_a", camera_a)
        b = _camera_dict("camera_b", camera_b)
        error_px = _finite_or_none("reprojection_error_px", reprojection_error_px)
        if error_px is not None and error_px < 0:
            raise ValueError("reprojection_error_px cannot be negative")

        # The baseline is the straight-line distance between the two camera
        # centres. Zero means both cameras are in the same spot, which gives
        # no depth information, so it is refused.
        centre_a, centre_b = _camera_centre(a), _camera_centre(b)
        baseline = math.dist(centre_a, centre_b)
        if baseline < 1e-6:
            raise ValueError("camera_a and camera_b cannot be in the same position")

        # Step 2: save. uuid4().hex is 32 random lowercase hex characters, the
        # same style as the capture IDs Ethan's server generates.
        calibration_id = uuid.uuid4().hex
        with closing(self._connect()) as conn, conn:
            conn.execute(
                """INSERT INTO calibrations (calibration_id, created_at, source,
                       camera_a_json, camera_b_json, baseline_m,
                       reprojection_error_px, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    calibration_id,
                    _now(),
                    source,
                    json.dumps(a),
                    json.dumps(b),
                    baseline,
                    error_px,
                    notes,
                ),
            )
            return self._calibration(conn, calibration_id)

    def get_calibration(self, calibration_id):
        """Return one calibration. Raises NotFoundError if it does not exist."""
        with closing(self._connect()) as conn:
            return self._calibration(conn, calibration_id)

    def latest_calibration(self, source="real"):
        """Return the newest calibration of this source, or None if there is none.

        Ethan's capture workflow calls this when arming, to know which
        calibration to attach to the new capture. ``source=None`` means
        newest of any source.
        """
        query = "SELECT * FROM calibrations"
        params = []
        if source is not None:
            query += " WHERE source = ?"
            params.append(source)
        # created_at can tie if two are saved in the same millisecond, so
        # rowid (SQLite's hidden insertion counter) breaks the tie.
        query += " ORDER BY created_at DESC, rowid DESC LIMIT 1"
        with closing(self._connect()) as conn:
            row = conn.execute(query, params).fetchone()
            return None if row is None else _calibration_dict(row)

    # Captures -----------------------------------------------------------------

    @property
    def captures(self):
        """The three-method interface Ethan's CaptureService expects.

        Use it as: CaptureService(pico, store.captures)
        It provides create(record), get(capture_id) and
        update(capture_id, **changes), backed by the captures table instead
        of the temporary in-memory store.
        """
        return CaptureRecords(self)

    def create_capture(self, record):
        """Save a new capture record and return it as stored.

        ``record`` is a dict that must contain capture_id and status, and
        may contain any other key in _CAPTURE_FIELDS (timing, pico_report,
        image paths and so on). Missing optional keys get their defaults.

        The record is committed before this returns, which Ethan's service
        relies on: it only sends CONFIG and ARM to the Pico afterwards.

        Raises CaptureExistsError if the ID is already saved, ValueError for
        bad values, and NotFoundError for a session or calibration that does
        not exist.
        """
        record = dict(record)  # copy, so the caller's dict is never changed
        if "capture_id" not in record or "status" not in record:
            raise ValueError("A capture record needs capture_id and status")
        capture_id = _check_capture_id(record.pop("capture_id"))
        columns = _capture_columns(record)

        now = _now()
        columns = {"capture_id": capture_id, "created_at": now, "updated_at": now,
                   **columns}
        names = ", ".join(columns)
        marks = ", ".join("?" for _ in columns)

        with closing(self._connect()) as conn, conn:
            if conn.execute(
                "SELECT 1 FROM captures WHERE capture_id = ?", (capture_id,)
            ).fetchone():
                raise CaptureExistsError(f"Capture {capture_id} already exists")
            self._check_links(conn, columns)
            # Column names come only from _CAPTURE_FIELDS (fixed in this
            # file), never from the caller, so building them into the SQL
            # text is safe. Every value still goes through "?" placeholders.
            conn.execute(
                f"INSERT INTO captures ({names}) VALUES ({marks})",
                list(columns.values()),
            )
            return self._capture(conn, capture_id)

    def get_capture(self, capture_id):
        """Return one capture. Raises NotFoundError if it does not exist."""
        with closing(self._connect()) as conn:
            return self._capture(conn, capture_id)

    def update_capture(self, capture_id, /, **changes):
        """Change some fields of a capture and return the updated record.

        e.g. update_capture(capture_id, status="armed")
             update_capture(capture_id, status="failed", error="Pico timed out")

        The capture_id itself can never change. Raises NotFoundError if the
        capture does not exist, ValueError for unknown fields or bad values.

        The "/" in the signature makes capture_id positional-only. Without
        it, update_capture(x, capture_id=y) would crash with a confusing
        TypeError before the clear "cannot change" check below could run.
        """
        if "capture_id" in changes:
            raise ValueError("Capture ID cannot change.")
        columns = _capture_columns(changes)
        columns["updated_at"] = _now()
        assignments = ", ".join(f"{column} = ?" for column in columns)

        with closing(self._connect()) as conn, conn:
            self._capture(conn, capture_id)  # raises NotFoundError if missing
            self._check_links(conn, columns)
            conn.execute(
                f"UPDATE captures SET {assignments} WHERE capture_id = ?",
                [*columns.values(), capture_id],
            )
            return self._capture(conn, capture_id)

    def mark_interrupted_captures(self):
        """Mark every unfinished capture as interrupted. Returns how many.

        Call this ONCE when the server starts, before accepting requests. A
        capture left in preparing, armed, capturing or processing means the
        server stopped mid-capture, and nothing will ever finish it.

        It is deliberately not run automatically by ShotStore(): scripts and
        tests also create a ShotStore, and doing it there would wrongly
        interrupt a capture the running server is still working on.
        """
        placeholders = ", ".join("?" for _ in UNFINISHED_CAPTURE_STATUSES)
        with closing(self._connect()) as conn, conn:
            cursor = conn.execute(
                f"""UPDATE captures
                    SET status = 'interrupted',
                        error = COALESCE(error, 'Server restarted before the capture finished'),
                        updated_at = ?
                    WHERE status IN ({placeholders})""",
                (_now(), *UNFINISHED_CAPTURE_STATUSES),
            )
            # COALESCE keeps an existing error message if there was one.
            return cursor.rowcount

    def list_captures(self, session_id=None, limit=50, offset=0):
        """Return captures newest first, optionally only one session's.

        ``limit`` and ``offset`` page through them, like list_shots.
        """
        query = "SELECT * FROM captures"
        params = []
        if session_id is not None:
            query += " WHERE session_id = ?"
            params.append(session_id)
        # created_at can tie within a millisecond; rowid breaks the tie.
        query += " ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?"
        params += [_bounded(limit), max(int(offset), 0)]
        with closing(self._connect()) as conn:
            rows = conn.execute(query, params).fetchall()
            return [_capture_dict(row) for row in rows]

    # Capture images -----------------------------------------------------------
    # Image paths are stored relative to the folder the database file lives in
    # (src/instance/ by default), e.g. "captures/<capture_id>/camera_a.png".

    @property
    def images_root(self):
        """The folder image paths are relative to: the database's own folder."""
        return Path(self.path).parent

    def image_files(self, capture_id):
        """Report where a capture's images are and whether they still exist.

        Returns {"state": ..., "camera_a": {...}, "camera_b": {...}} where
        state is one of:
          "none"     the capture never had images (e.g. it was cancelled)
          "deleted"  cleanup removed them on purpose (images_deleted_at set)
          "missing"  paths were saved but a file is gone. Something is wrong.
          "present"  every saved image file exists
        Each camera entry has the relative "path" and whether it "exists".
        Raises NotFoundError if the capture does not exist.
        """
        capture = self.get_capture(capture_id)
        cameras = {}
        for name in ("camera_a", "camera_b"):
            relative = capture[f"image_{name[-1]}_path"]
            exists = relative is not None and (self.images_root / relative).is_file()
            cameras[name] = {"path": relative, "exists": exists}

        saved = [c for c in cameras.values() if c["path"] is not None]
        if not saved:
            state = "none"
        elif capture["images_deleted_at"] is not None:
            state = "deleted"
        elif all(c["exists"] for c in saved):
            state = "present"
        else:
            state = "missing"
        return {"state": state, **cameras}

    def cleanup_images(self, keep_accepted=200, keep_other=100):
        """Delete old capture images, keeping the newest ones and every record.

        The rule the team agreed on:
          - keep images for the newest ``keep_accepted`` captures whose shot
            was accepted
          - keep images for the newest ``keep_other`` remaining captures
            (rejected shots, failures, timeouts), as failure evidence
          - never touch pinned captures; they do not count toward either limit
          - keep every database record; only the image files are removed,
            and images_deleted_at records when

        Call it after a capture finishes processing, or at server startup.
        It does nothing while any capture is in progress, so it can never
        delete images a running capture is about to use.

        Returns {"deleted": [capture ids whose images were removed],
                 "skipped": None, or the reason nothing was done}.
        """
        for name, value in (("keep_accepted", keep_accepted), ("keep_other", keep_other)):
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a whole number, 0 or more")

        placeholders = ", ".join("?" for _ in UNFINISHED_CAPTURE_STATUSES)
        with closing(self._connect()) as conn, conn:
            if conn.execute(
                f"SELECT 1 FROM captures WHERE status IN ({placeholders}) LIMIT 1",
                UNFINISHED_CAPTURE_STATUSES,
            ).fetchone():
                return {"deleted": [], "skipped": "A capture is in progress"}

            # Every capture that still has images and is not pinned, newest
            # first. LEFT JOIN brings in its shot (if any) so captures can be
            # split into "accepted" and "other".
            rows = conn.execute(
                """SELECT c.capture_id, c.image_a_path, c.image_b_path,
                          s.status AS shot_status
                   FROM captures c
                   LEFT JOIN shots s ON s.capture_id = c.capture_id
                   WHERE c.images_deleted_at IS NULL
                     AND c.pinned = 0
                     AND (c.image_a_path IS NOT NULL OR c.image_b_path IS NOT NULL)
                   ORDER BY c.created_at DESC, c.rowid DESC"""
            ).fetchall()

            kept = {"accepted": 0, "other": 0}
            limit = {"accepted": keep_accepted, "other": keep_other}
            doomed = []
            for row in rows:
                group = "accepted" if row["shot_status"] == "accepted" else "other"
                if kept[group] < limit[group]:
                    kept[group] += 1
                else:
                    doomed.append(row)

            # Step 1: mark the records first, inside the transaction. If the
            # file deletion below were to fail part way, the worst case is a
            # few leftover files on disk, which is harmless. The other order
            # could leave records saying images exist when they are gone.
            now = _now()
            conn.executemany(
                "UPDATE captures SET images_deleted_at = ?, updated_at = ? "
                "WHERE capture_id = ?",
                [(now, now, row["capture_id"]) for row in doomed],
            )
        # The "with" block has committed by this point.

        # Step 2: delete the files. missing_ok=True means a file that is
        # already gone is not an error. Each capture's folder is removed too
        # if it is now empty.
        for row in doomed:
            for relative in (row["image_a_path"], row["image_b_path"]):
                if relative is None:
                    continue
                file = self.images_root / relative
                file.unlink(missing_ok=True)
                folder = file.parent
                if folder != self.images_root:
                    try:
                        folder.rmdir()  # only succeeds if the folder is empty
                    except OSError:
                        pass
        return {"deleted": [row["capture_id"] for row in doomed], "skipped": None}

    # Internals ----------------------------------------------------------------
    # These take an already-open connection, so a public method can do several
    # steps inside ONE transaction. @staticmethod means they do not use self.

    @staticmethod
    def _active(conn):
        """Return the id row of the open session, or None.

        "Open" means ended_at is empty. start_session prevents two open
        sessions, but ORDER BY id DESC LIMIT 1 guarantees a single, newest
        answer even if the data were ever inconsistent.
        """
        return conn.execute(
            "SELECT id FROM sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
        ).fetchone()

    @staticmethod
    def _session(conn, session_id):
        """Return one session as a dict, with its shot count.

        Same JOIN and COUNT as list_sessions, filtered to one id.
        Raises NotFoundError if the id does not exist.
        """
        row = conn.execute(
            """SELECT s.*, COUNT(sh.id) AS shot_count
               FROM sessions s LEFT JOIN shots sh ON sh.session_id = s.id
               WHERE s.id = ? GROUP BY s.id""",
            (session_id,),
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Session {session_id} not found")
        return _session_dict(row)

    @staticmethod
    def _shot(conn, shot_id, include_samples):
        """Return one shot as a dict. Raises NotFoundError if it does not exist."""
        row = conn.execute("SELECT * FROM shots WHERE id = ?", (shot_id,)).fetchone()
        if row is None:
            raise NotFoundError(f"Shot {shot_id} not found")
        return _shot_dict(row, include_samples)

    @staticmethod
    def _calibration(conn, calibration_id):
        """Return one calibration as a dict. Raises NotFoundError if missing."""
        row = conn.execute(
            "SELECT * FROM calibrations WHERE calibration_id = ?", (calibration_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Calibration {calibration_id} not found")
        return _calibration_dict(row)

    @staticmethod
    def _capture(conn, capture_id):
        """Return one capture as a dict. Raises NotFoundError if missing."""
        row = conn.execute(
            "SELECT * FROM captures WHERE capture_id = ?", (capture_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"Capture {capture_id} not found")
        return _capture_dict(row)

    @staticmethod
    def _check_links(conn, columns):
        """Make sure a session_id or calibration_id points at a real row.

        The foreign keys would also refuse a bad ID, but only with a vague
        "FOREIGN KEY constraint failed". Checking first gives a message that
        says which ID was wrong.
        """
        links = (
            ("session_id", "sessions", "id", "Session"),
            ("calibration_id", "calibrations", "calibration_id", "Calibration"),
        )
        for column, table, key, label in links:
            value = columns.get(column)
            if value is not None and not conn.execute(
                f"SELECT 1 FROM {table} WHERE {key} = ?", (value,)
            ).fetchone():
                raise NotFoundError(f"{label} {value} not found")


class CaptureRecords:
    """Adapter giving Ethan's CaptureService the exact method names it uses.

    His service calls store.create(record), store.get(capture_id) and
    store.update(capture_id, **changes). ShotStore names these
    create_capture, get_capture and update_capture, because a plain
    "create" on ShotStore would be unclear (create a session? a shot?).
    This small class just forwards the calls.
    """

    def __init__(self, store):
        self._store = store

    def create(self, record):
        return self._store.create_capture(record)

    def get(self, capture_id):
        return self._store.get_capture(capture_id)

    def update(self, capture_id, /, **changes):
        return self._store.update_capture(capture_id, **changes)


# --- Output formatting -------------------------------------------------------
# Database rows are converted to plain dicts so the server can return them as
# JSON directly, and so no other module depends on how the tables are laid out.


def _bounded(limit):
    """Keep page sizes between 1 and 500.

    Stops a request like limit=1000000 from loading the whole database into
    memory on the Pi.
    """
    return min(max(int(limit), 1), 500)


def _session_dict(row):
    """Convert a session row into the dict the server returns."""
    return {
        "id": row["id"],
        "started_at": row["started_at"],
        "ended_at": row["ended_at"],  # None while the session is running
        # Derived for convenience, so the UI does not have to check ended_at.
        "active": row["ended_at"] is None,
        "shot_count": row["shot_count"],
    }


def _shot_dict(row, include_samples):
    """Convert a shot row into the dict the server returns."""
    speed = row["speed_m_s"]
    shot = {
        "id": row["id"],
        "session_id": row["session_id"],
        "created_at": row["created_at"],
        "status": row["status"],  # "accepted" or "rejected"
        "reason": row["reason"],  # None unless rejected
        "speed_m_s": speed,
        # mph is calculated here on the way out, never stored. The database
        # keeps one source of truth in physics-standard units.
        "speed_mph": None if speed is None else speed * MPS_TO_MPH,
        "launch_angle_deg": row["launch_angle_deg"],
        "launch_direction_deg": row["launch_direction_deg"],  # None for now
        "spin_rpm": row["spin_rpm"],  # None for now
        "source": row["source"],  # "simulated" until real captures exist
        "capture_id": row["capture_id"],  # None for simulated shots
        "carry": _carry_dict(row),  # None if no carry was saved
    }
    # Samples are only added when asked for, and turned from JSON text back
    # into a normal dict of lists.
    if include_samples:
        shot["samples"] = (
            None if row["samples_json"] is None else json.loads(row["samples_json"])
        )
    return shot


def _calibration_dict(row):
    """Convert a calibration row into the dict the server returns.

    The two cameras come back as plain dicts of lists. To use one with the
    vision pipeline, convert it with numpy, e.g.
    CameraCalibration(K=np.array(cam["K"]), ...).
    """
    return {
        "calibration_id": row["calibration_id"],
        "created_at": row["created_at"],
        "source": row["source"],  # "synthetic" or "real"
        "camera_a": json.loads(row["camera_a_json"]),
        "camera_b": json.loads(row["camera_b_json"]),
        "baseline_m": row["baseline_m"],
        "reprojection_error_px": row["reprojection_error_px"],  # None if synthetic
        "notes": row["notes"],
    }


def _capture_dict(row):
    """Convert a capture row into the dict the server returns.

    Uses the same key names Ethan's CaptureService uses (timing, pico_report,
    error), so records look the same as with his temporary in-memory store.
    """

    def loaded(column):
        return None if row[column] is None else json.loads(row[column])

    return {
        "capture_id": row["capture_id"],
        "session_id": row["session_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "status": row["status"],
        "error": row["error"],
        "source": row["source"],
        "timing": loaded("timing_json"),
        "timing_source": row["timing_source"],
        "pico_report": loaded("pico_report_json"),
        "measured_timing": loaded("measured_timing_json"),
        "calibration_id": row["calibration_id"],
        "processing_version": row["processing_version"],
        "image_a_path": row["image_a_path"],
        "image_b_path": row["image_b_path"],
        "detections": loaded("detections_json"),
        "pinned": bool(row["pinned"]),
        "images_deleted_at": row["images_deleted_at"],
    }


def _carry_dict(row):
    """Rebuild Steffano's carry block from a shot row, or None if there is none.

    Same keys carry_metrics() returns, so the results screen sees the same
    shape whether it shows a fresh preview or a saved shot from history.
    """
    if row["carry_model"] is None and row["carry_error"] is None:
        return None
    carry_m = row["estimated_carry_m"]
    return {
        "estimated_carry_m": carry_m,
        "estimated_carry_yd": None if carry_m is None else carry_m * M_TO_YD,
        "apex_height_m": row["apex_height_m"],
        "flight_time_s": row["flight_time_s"],
        "carry_model": row["carry_model"],
        "carry_assumptions": (
            None if row["carry_assumptions_json"] is None
            else json.loads(row["carry_assumptions_json"])
        ),
        "carry_is_estimate": True,  # always: carry is modelled, never measured
        "carry_error": row["carry_error"],
    }