"""Tests for fairway_fusion.storage (the SQLite shot database).

Run from the repository root:

    python -m pytest tests/test_storage.py

Every test gets its own brand-new database file in a temporary folder, so
tests never touch data/fairway.db and never affect each other.
"""

import json  # used to check that results can be turned into JSON
import sqlite3  # used once to poke the database directly, bypassing ShotStore
import threading  # used to simulate several server threads saving at once

import pytest

from fairway_fusion.storage import (
    CaptureExistsError,
    NotFoundError,
    SchemaVersionError,
    SessionStateError,
    ShotStore,
)

# The module itself, so migration tests can read SCHEMA and swap in fake
# migrations. Normal tests only need the names imported above.
from fairway_fusion.storage import database

# A realistic simulated trajectory, shaped exactly like what the server will
# pass in: three timestamps (1, 2, 3 ms) and the ball's x/y/z position in
# metres at each one. Shared by several tests below.
SAMPLES = {
    "timestamps_s": [0.001, 0.002, 0.003],
    "positions_m": [[0.05, 0.0, 0.043], [0.10, 0.0, 0.056], [0.15, 0.0, 0.069]],
}


# A "fixture" is setup code pytest runs automatically. Any test that lists
# "store" as a parameter gets a fresh ShotStore before it runs.
# tmp_path is pytest's own fixture: a new empty temporary folder per test,
# deleted automatically afterwards.
@pytest.fixture
def store(tmp_path):
    return ShotStore(tmp_path / "fairway.db")


# --- Sessions ------------------------------------------------------------------


def test_session_lifecycle(store):
    """The full Start -> End flow works, and "active" tracks it correctly."""
    # A brand-new database has no open session.
    assert store.active_session() is None

    # Pressing Start creates a session that is active and has no shots yet.
    session = store.start_session()
    assert session["active"] and session["shot_count"] == 0

    # active_session() finds that same session.
    assert store.active_session()["id"] == session["id"]

    # Pressing End fills in ended_at and the session is no longer active.
    ended = store.end_session()
    assert ended["ended_at"] is not None and not ended["active"]
    assert store.active_session() is None


def test_only_one_active_session(store):
    """Pressing Start twice without End is refused."""
    store.start_session()
    # pytest.raises means: the code inside this block MUST raise this error.
    # If it raises nothing, or a different error, the test fails.
    with pytest.raises(SessionStateError):
        store.start_session()


def test_end_without_active_session(store):
    """Pressing End when nothing is running is refused."""
    with pytest.raises(SessionStateError):
        store.end_session()


def test_end_session_twice(store):
    """Ending an already-ended session is refused, so its real end time
    can never be overwritten."""
    session = store.start_session()
    store.end_session()
    with pytest.raises(SessionStateError):
        store.end_session(session["id"])


# --- Saving shots --------------------------------------------------------------


def test_save_and_read_back_shot(store):
    """A saved shot comes back with exactly what was stored, plus the
    fields the database fills in (id, session, timestamp)."""
    session = store.start_session()
    shot = store.save_shot(50.0, 15.2, samples=SAMPLES)

    # The shot was attached to the open session automatically.
    assert shot["session_id"] == session["id"]
    assert shot["status"] == "accepted"

    # 50 m/s converts to 111.85 mph. pytest.approx allows a tiny tolerance,
    # because decimal math on computers is rarely exact to the last digit.
    assert shot["speed_mph"] == pytest.approx(111.85, abs=0.01)

    # Spin was not given, so it is stored and returned as None.
    assert shot["spin_rpm"] is None

    # The raw samples survive the round trip to JSON text and back unchanged.
    assert shot["samples"] == SAMPLES

    # Reading the shot back later gives the identical dict.
    assert store.get_shot(shot["id"]) == shot


def test_shot_requires_active_session(store):
    """Simulate Shot before Start is refused."""
    with pytest.raises(SessionStateError):
        store.save_shot(50.0, 15.0)


def test_shot_rejected_for_ended_session(store):
    """Shots cannot be added to a session that is already over."""
    session = store.start_session()
    store.end_session()
    with pytest.raises(SessionStateError):
        store.save_shot(50.0, 15.0, session_id=session["id"])


def test_rejected_shot_stays_visible(store):
    """A failed calculation is still saved, with its reason, so it shows up
    in history. The design draft requires rejected captures stay visible."""
    store.start_session()
    shot = store.save_shot(status="rejected", reason="timestamps not increasing")

    # No speed was given, so both speed fields are empty instead of crashing.
    assert shot["speed_m_s"] is None and shot["speed_mph"] is None

    # It is the latest shot, and the reason is preserved.
    assert store.latest_shot()["reason"] == "timestamps not increasing"


# parametrize runs the SAME test once per line in the list, each time with a
# different bad input. That gives 8 separate tests from one function, and
# pytest reports each one individually.
# Each line is (positional arguments, keyword arguments) for save_shot.
@pytest.mark.parametrize(
    "args, kwargs",
    [
        ((None, 15.0), {}),  # accepted shot missing its speed
        ((50.0, None), {}),  # accepted shot missing its launch angle
        ((float("nan"), 15.0), {}),  # speed is NaN ("not a number")
        ((50.0, float("inf")), {}),  # angle is infinity
        ((-1.0, 15.0), {}),  # negative speed
        (("fast", 15.0), {}),  # speed is not a number at all
        ((50.0, 15.0), {"status": "maybe"}),  # status that is not allowed
        ((), {"status": "rejected"}),  # rejected shot with no reason
    ],
)
def test_invalid_shots_are_refused(store, args, kwargs):
    """Every kind of bad input is refused, and NOTHING is saved."""
    store.start_session()
    # *args and **kwargs unpack the tuple and dict into save_shot's arguments,
    # e.g. ((None, 15.0), {}) becomes save_shot(None, 15.0).
    with pytest.raises(ValueError):
        store.save_shot(*args, **kwargs)
    # The important half: the bad shot did not sneak into the database.
    assert store.latest_shot() is None


# --- History -------------------------------------------------------------------


def test_history_newest_first_and_filtered(store):
    """The history screen gets shots newest first, can filter by session,
    and session listings carry correct shot counts."""
    # Build a small history: session 1 with two shots, session 2 with one.
    first = store.start_session()
    store.save_shot(40.0, 10.0)
    store.save_shot(45.0, 12.0)
    store.end_session()
    second = store.start_session()
    store.save_shot(60.0, 20.0)

    # All shots, newest first. The speeds are used to tell shots apart.
    assert [s["speed_m_s"] for s in store.list_shots()] == [60.0, 45.0, 40.0]

    # Only session 1's shots, still newest first.
    assert [s["speed_m_s"] for s in store.list_shots(first["id"])] == [45.0, 40.0]

    # latest_shot feeds the results screen: newest overall, or newest in
    # one session.
    assert store.latest_shot()["speed_m_s"] == 60.0
    assert store.latest_shot(first["id"])["speed_m_s"] == 45.0

    # History lists leave out the raw samples to keep responses small.
    assert "samples" not in store.list_shots()[0]

    # Sessions come back newest first, each with the right number of shots.
    sessions = store.list_sessions()
    assert [s["id"] for s in sessions] == [second["id"], first["id"]]
    assert [s["shot_count"] for s in sessions] == [1, 2]


def test_pagination(store):
    """limit and offset return the right slice, for paging through history."""
    store.start_session()
    # Save 10 shots with speeds 0.0 through 9.0, so each one is identifiable.
    for speed in range(10):
        store.save_shot(float(speed), 10.0)

    # Newest first is 9, 8, 7, 6, 5, 4 ... so skipping 3 and taking 3
    # gives 6, 5, 4. This is "page 2" with a page size of 3.
    page = store.list_shots(limit=3, offset=3)
    assert [s["speed_m_s"] for s in page] == [6.0, 5.0, 4.0]


# --- Persistence ---------------------------------------------------------------


def test_data_survives_restart(tmp_path):
    """Data saved before a "reboot" is all still there afterwards.

    This test does not use the store fixture, because it needs TWO stores
    pointing at the same file.
    """
    path = tmp_path / "fairway.db"

    # First "boot": start a session and save a shot.
    before = ShotStore(path)
    session = before.start_session()
    shot = before.save_shot(55.0, 14.0, samples=SAMPLES)

    # Second "boot": a completely new ShotStore on the same file, like the
    # server starting up again after the Pi restarts. Nothing is carried
    # over in memory; everything has to come from the file on disk.
    after = ShotStore(path)

    # The session is still open, so the server can resume it.
    assert after.active_session()["id"] == session["id"]

    # The shot is identical, samples included.
    assert after.get_shot(shot["id"]) == shot


# --- Deleting ------------------------------------------------------------------


def test_delete_shot(store):
    """A deleted shot is gone, and deleting it again is reported, not ignored."""
    store.start_session()
    shot = store.save_shot(50.0, 15.0)
    store.delete_shot(shot["id"])

    # Looking it up now fails.
    with pytest.raises(NotFoundError):
        store.get_shot(shot["id"])

    # Deleting something that no longer exists is an error, so a UI bug
    # that deletes the wrong id does not fail silently.
    with pytest.raises(NotFoundError):
        store.delete_shot(shot["id"])


def test_missing_session(store):
    """Asking for a session id that was never created raises NotFoundError."""
    with pytest.raises(NotFoundError):
        store.get_session(999)



# --- Edge cases ----------------------------------------------------------------


def test_results_are_json_serializable(store):
    """Everything the store returns can be sent straight to the browser.

    The server will turn these dicts into JSON. If any value were a type JSON
    cannot handle (a database row object, a datetime, NaN), the server would
    crash on that request. allow_nan=False makes json.dumps strict about NaN.
    """
    store.start_session()
    store.save_shot(50.0, 15.0, samples=SAMPLES)
    # No assert needed: if json.dumps raises an error, the test fails.
    json.dumps(
        {
            "shots": store.list_shots(include_samples=True),
            "sessions": store.list_sessions(),
        },
        allow_nan=False,
    )


def test_concurrent_saves_from_threads(store):
    """Four threads saving at the same time lose no shots and raise no errors.

    This simulates the web server handling several requests at once, and is
    the reason every ShotStore method opens its own connection.
    """
    store.start_session()
    errors = []  # any error from a thread is collected here

    def worker():
        # Each thread saves 20 shots as fast as it can.
        try:
            for _ in range(20):
                store.save_shot(50.0, 15.0)
        except Exception as exc:
            # An error inside a thread would not fail the test by itself,
            # so it is recorded and checked after all threads finish.
            errors.append(exc)

    # Start 4 threads, then wait for all of them to finish (join).
    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # No thread hit an error, and all 4 x 20 = 80 shots were saved.
    assert not errors
    assert store.active_session()["shot_count"] == 80


def test_database_rejects_bad_status_directly(store):
    """The CHECK rule in the schema works on its own, without the Python checks.

    This writes to the file with plain sqlite3, skipping ShotStore and all of
    save_shot's validation, to prove the database itself is a second line of
    defence against an invalid status.
    """
    store.start_session()
    # IntegrityError is sqlite3's error for "this breaks a table rule".
    with pytest.raises(sqlite3.IntegrityError):
        with sqlite3.connect(store.path) as conn:
            conn.execute(
                "INSERT INTO shots (session_id, created_at, status) "
                "VALUES (1, 'now', 'bogus')"
            )


def test_memory_path_refused():
    """":memory:" is refused with a clear error instead of silently losing
    every table after the first call."""
    with pytest.raises(ValueError):
        ShotStore(":memory:")



# --- Schema versions and migrations --------------------------------------------


def _version(path):
    """Read a database file's schema version straight from SQLite."""
    with sqlite3.connect(path) as conn:
        return conn.execute("PRAGMA user_version").fetchone()[0]


def _backups(path):
    """List the backup files ShotStore made next to a database."""
    return sorted(path.parent.glob(f"{path.stem}.backup-*.db"))


def _make_pre_versioning_db(path):
    """Build a database exactly like the one on the Pi from the pre-alpha demo.

    Original two tables, one session, one shot, and user_version left at 0,
    because versioning did not exist yet when it was created.
    """
    with sqlite3.connect(path) as conn:
        conn.executescript(database.SCHEMA)
        conn.execute(
            "INSERT INTO sessions (started_at) VALUES ('2026-11-05T15:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO shots (session_id, created_at, status, speed_m_s, "
            "launch_angle_deg, source) VALUES "
            "(1, '2026-11-05T15:01:00+00:00', 'accepted', 55.0, 14.0, 'simulation')"
        )


def test_new_database_gets_current_version(tmp_path):
    """A brand-new file is created at the current version, with no backup,
    because there is no existing data to protect."""
    path = tmp_path / "fairway.db"
    ShotStore(path)
    assert _version(path) == database.SCHEMA_VERSION
    assert _backups(path) == []


def test_old_database_is_upgraded_and_keeps_data(tmp_path):
    """The Pi situation: an old database is upgraded in place, its history is
    untouched, and a backup holding that history is made first."""
    path = tmp_path / "fairway.db"
    _make_pre_versioning_db(path)

    store = ShotStore(path)

    # Upgraded to the current version.
    assert _version(path) == database.SCHEMA_VERSION

    # The demo shot is still there with its original values.
    shots = store.list_shots()
    assert [(s["speed_m_s"], s["launch_angle_deg"]) for s in shots] == [(55.0, 14.0)]

    # Exactly one backup, labelled with the version it came from (0), and it
    # really contains the old shot.
    backups = _backups(path)
    assert len(backups) == 1
    assert "backup-v0-" in backups[0].name
    with sqlite3.connect(backups[0]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM shots").fetchone()[0] == 1


def test_reopening_current_database_makes_no_backup(tmp_path):
    """Restarting the server on an up-to-date file changes nothing, so the
    Pi does not fill up with a new backup on every reboot."""
    path = tmp_path / "fairway.db"
    _make_pre_versioning_db(path)
    ShotStore(path)  # upgrade: one backup
    ShotStore(path)  # already current: no new backup
    ShotStore(path)
    assert len(_backups(path)) == 1


def test_database_from_newer_code_is_refused(tmp_path):
    """Older code must not touch a file newer code already upgraded. The
    file's version is left exactly as it was."""
    path = tmp_path / "fairway.db"
    ShotStore(path)
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA user_version = 99")

    with pytest.raises(SchemaVersionError):
        ShotStore(path)
    assert _version(path) == 99


def test_failed_upgrade_changes_nothing(tmp_path, monkeypatch):
    """If any migration fails partway, every change is undone: the version
    is not bumped and no half-built tables are left behind."""

    def broken_migration(conn):
        # Make a real change, then crash, like a bug halfway through.
        conn.execute("CREATE TABLE half_done (x)")
        raise RuntimeError("simulated failure during upgrade")

    # monkeypatch swaps these values for this test only, and puts the real
    # ones back automatically when the test ends.
    monkeypatch.setattr(database, "MIGRATIONS", [*database.MIGRATIONS, broken_migration])
    monkeypatch.setattr(database, "SCHEMA_VERSION", len(database.MIGRATIONS))

    path = tmp_path / "fairway.db"
    with pytest.raises(RuntimeError):
        ShotStore(path)

    # Still version 0, and neither migration 1's tables nor the broken
    # migration's table survived.
    assert _version(path) == 0
    with sqlite3.connect(path) as conn:
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    assert tables == []
    
    


# --- Calibrations --------------------------------------------------------------


def _camera(x_m):
    """A simple valid camera, as plain lists, sitting at x = x_m metres.

    R is the identity (no rotation), so the camera centre is just -t.
    """
    return {
        "K": [[540.0, 0, 320], [0, 540.0, 240], [0, 0, 1]],
        "distortion": [0, 0, 0, 0, 0],
        "R": [[1, 0, 0], [0, 1, 0], [0, 0, 1]],
        "t": [-x_m, 0, 0],
        "image_size": [1456, 1088],
    }


def test_save_and_read_back_calibration(store):
    """A saved calibration comes back unchanged, with a generated 32-character
    hex ID and a baseline calculated from the camera positions."""
    saved = store.save_calibration(
        _camera(0.0), _camera(0.3), source="real",
        reprojection_error_px=0.21, notes="first real fit",
    )
    assert len(saved["calibration_id"]) == 32
    int(saved["calibration_id"], 16)  # raises if it is not hex
    assert saved["baseline_m"] == pytest.approx(0.3)
    assert saved["camera_a"] == _camera(0.0) | {"distortion": [0.0] * 5}
    assert saved["camera_b"]["image_size"] == [1456, 1088]
    assert saved["reprojection_error_px"] == 0.21
    assert store.get_calibration(saved["calibration_id"]) == saved


def test_stored_calibration_reproduces_the_same_measurement(store):
    """The real proof: save Giancarlo's synthetic cameras, rebuild his
    CameraCalibration objects from what was stored, and his pipeline gives
    the identical shot result. Nothing was lost or rounded in storage."""
    import numpy as np

    from fairway_fusion.calibration.stereo_calibration import CameraCalibration
    from fairway_fusion.pipeline import analyze_stereo_shot
    from fairway_fusion.synthetic import make_stereo_fixture

    images, cameras, _ = make_stereo_fixture()
    saved = store.save_calibration(*cameras, source="synthetic")

    # His cameras sit at x = -0.1 m and x = +0.1 m, so 0.2 m apart.
    assert saved["baseline_m"] == pytest.approx(0.2)

    def rebuild(cam):
        return CameraCalibration(
            np.array(cam["K"]), np.array(cam["distortion"]), np.array(cam["R"]),
            np.array(cam["t"]), tuple(cam["image_size"]),
        )

    stored = store.get_calibration(saved["calibration_id"])
    original = analyze_stereo_shot(*images, *cameras, flash_interval_s=0.002)
    from_db = analyze_stereo_shot(
        *images, rebuild(stored["camera_a"]), rebuild(stored["camera_b"]),
        flash_interval_s=0.002,
    )
    assert from_db["status"] == "accepted"
    assert from_db["launch"] == original["launch"]


def test_baseline_is_correct_for_rotated_cameras(store):
    """The baseline must use each camera's real position even when the
    cameras point in different directions.

    Camera A sits at (1, 0, 0) facing straight ahead. Camera B sits at
    (0.3, 0.4, 0) and is turned 90 degrees about the vertical axis. For a
    camera at position p with rotation R, OpenCV stores t = -R p. The true
    distance between them is sqrt(0.7^2 + 0.4^2) = 0.806 m. A wrong
    camera-centre formula gives 1.36 m here, so this test catches it.
    (The other tests use cameras facing the same way, where the wrong
    formula happens to give the right answer.)
    """
    turned = [[0, -1, 0], [1, 0, 0], [0, 0, 1]]  # 90 degrees about z
    camera_b = _camera(0) | {"R": turned, "t": [0.4, -0.3, 0]}  # t = -R (0.3, 0.4, 0)
    saved = store.save_calibration(_camera(1.0), camera_b, source="real")
    assert saved["baseline_m"] == pytest.approx((0.7**2 + 0.4**2) ** 0.5)


def test_latest_calibration_by_source(store):
    """latest_calibration returns the newest of the requested source, and
    synthetic calibrations never count as real."""
    assert store.latest_calibration() is None

    first_real = store.save_calibration(_camera(0), _camera(0.2), source="real")
    synthetic = store.save_calibration(_camera(0), _camera(0.2), source="synthetic")
    second_real = store.save_calibration(_camera(0), _camera(0.25), source="real")

    assert store.latest_calibration()["calibration_id"] == second_real["calibration_id"]
    assert store.latest_calibration("synthetic")["calibration_id"] == (
        synthetic["calibration_id"]
    )
    assert store.latest_calibration(None)["calibration_id"] == (
        second_real["calibration_id"]
    )
    assert first_real["calibration_id"] != second_real["calibration_id"]


def test_missing_calibration(store):
    """Asking for a calibration ID that was never saved raises NotFoundError."""
    with pytest.raises(NotFoundError):
        store.get_calibration("0" * 32)


def _broken(**changes):
    """A valid camera with some fields replaced by bad values."""
    return _camera(0.3) | changes


@pytest.mark.parametrize(
    "camera_b, kwargs",
    [
        (_broken(K=[[540, 0], [0, 540]]), {}),  # K is 2x2, not 3x3
        (_broken(K=[[0, 0, 320], [0, 540, 240], [0, 0, 1]]), {}),  # zero focal length
        (_broken(distortion=[0, 0, 0]), {}),  # 3 coefficients: no lens model uses that
        (_broken(R=[[2, 0, 0], [0, 1, 0], [0, 0, 1]]), {}),  # scaled, not a rotation
        (_broken(R=[[-1, 0, 0], [0, 1, 0], [0, 0, 1]]), {}),  # mirror image
        (_broken(t=[0.3, float("nan"), 0]), {}),  # NaN position
        (_broken(t=[0.3, 0]), {}),  # only 2 numbers
        (_broken(image_size=[1456, -1088]), {}),  # negative size
        (_broken(image_size=[1456.5, 1088]), {}),  # fractional pixels
        ({"K": _camera(0)["K"]}, {}),  # missing fields
        (_camera(0.0), {}),  # same position as camera A: no depth possible
        (_camera(0.3), {"source": "guess"}),  # unknown source
        (_camera(0.3), {"reprojection_error_px": -1}),  # negative error
    ],
)
def test_invalid_calibrations_are_refused(store, camera_b, kwargs):
    """Every kind of bad calibration is refused, and nothing is saved."""
    kwargs = {"source": "real"} | kwargs
    with pytest.raises(ValueError):
        store.save_calibration(_camera(0.0), camera_b, **kwargs)
    assert store.latest_calibration(None) is None


def test_old_database_gains_calibrations_table(tmp_path):
    """Upgrading the Pi's pre-alpha database adds the calibrations table and
    it works straight away, with the old shots still there."""
    path = tmp_path / "fairway.db"
    _make_pre_versioning_db(path)
    store = ShotStore(path)
    store.save_calibration(_camera(0), _camera(0.2), source="real")
    assert store.latest_calibration() is not None
    assert len(store.list_shots()) == 1
    
    


# --- Captures ------------------------------------------------------------------


def _capture_id(n=1):
    """A valid, readable capture ID: 32 hex characters ending in n."""
    return f"{n:032x}"


def _ethan_record(n=1):
    """A record exactly like the one Ethan's CaptureService.arm() creates."""
    return {
        "capture_id": _capture_id(n),
        "status": "preparing",
        "error": None,
        "timing": {"camera_trigger_us": 5000, "flash_width_us": 5,
                   "flash_interval_us": 1000, "first_flash_delay_us": 500},
        "timing_source": "commanded",
        "pico_report": None,
    }


# Evan's capture report, as the Pico sends it after a shot.
PICO_REPORT = {
    "capture_id": _capture_id(1), "boot_id": "a1b2c3d4e5f60718",
    "trigger_counter": 1, "status": "completed", "error_code": "none",
    "firmware_version": "PICO2W_CAPTURE_1", "timing_source": "commanded",
    "camera_trigger_us": 5000, "flash_width_us": 5, "flash_interval_us": 1000,
    "first_flash_delay_us": 500, "flash_count": 2,
    "impact_observed_us": 123456, "sequence_done_observed_us": 128000,
}


def test_create_and_get_capture(store):
    """Ethan's record is saved as-is, with defaults for everything he did not
    send, and comes back with the same key names he uses."""
    saved = store.create_capture(_ethan_record())
    assert saved["capture_id"] == _capture_id(1)
    assert saved["status"] == "preparing"
    assert saved["timing"]["flash_interval_us"] == 1000
    assert saved["source"] == "live"  # default
    assert saved["pinned"] is False  # default
    assert saved["created_at"] == saved["updated_at"]
    assert store.get_capture(_capture_id(1)) == saved


def test_capture_lifecycle_updates(store):
    """A capture moves through its states, the Pico report is stored whole,
    and created_at never changes while updated_at does."""
    created = store.create_capture(_ethan_record())
    for status in ("armed", "capturing", "processing"):
        store.update_capture(_capture_id(1), status=status)
    done = store.update_capture(
        _capture_id(1), status="completed", pico_report=PICO_REPORT,
        image_a_path=f"captures/{_capture_id(1)}/camera_a.png",
        image_b_path=f"captures/{_capture_id(1)}/camera_b.png",
    )
    assert done["status"] == "completed"
    assert done["pico_report"] == PICO_REPORT  # every field Evan sent, intact
    assert done["image_a_path"].endswith("camera_a.png")
    assert done["created_at"] == created["created_at"]
    assert done["updated_at"] >= created["updated_at"]


def test_duplicate_capture_is_refused(store):
    """A second create with the same ID fails and leaves the original alone."""
    store.create_capture(_ethan_record())
    store.update_capture(_capture_id(1), status="armed")
    with pytest.raises(CaptureExistsError):
        store.create_capture(_ethan_record())
    assert store.get_capture(_capture_id(1))["status"] == "armed"


def test_capture_id_cannot_change(store):
    store.create_capture(_ethan_record())
    with pytest.raises(ValueError):
        store.update_capture(_capture_id(1), capture_id=_capture_id(2))


def test_missing_capture(store):
    """Reading or updating an ID that was never created raises NotFoundError."""
    with pytest.raises(NotFoundError):
        store.get_capture(_capture_id(9))
    with pytest.raises(NotFoundError):
        store.update_capture(_capture_id(9), status="armed")


@pytest.mark.parametrize(
    "changes",
    [
        {"capture_id": "ABC"},  # too short
        {"capture_id": "F" * 32},  # uppercase: uuid4().hex is always lowercase
        {"capture_id": "g" * 32},  # not hex
        {"status": "done"},  # not one of the nine states
        {"source": "camera"},  # not live/simulation/replay
        {"timing_source": "guessed"},
        {"timing": {"flash_interval_us": float("nan")}},  # NaN in JSON
        {"timing": "1000us"},  # text instead of a dict
        {"image_a_path": "/home/pi/captures/a.png"},  # absolute path
        {"image_a_path": "C:/captures/a.png"},  # Windows drive
        {"image_a_path": "captures\\a.png"},  # backslashes
        {"image_b_path": "captures/../../etc/passwd"},  # escapes the folder
        {"pinned": 1},  # must be True/False, not a number
        {"session_id": True},  # bool is not a session number
        {"statsu": "armed"},  # typo in a field name
    ],
)
def test_invalid_captures_are_refused(store, changes):
    """Every kind of bad capture record is refused, and nothing is saved."""
    with pytest.raises(ValueError):
        store.create_capture(_ethan_record() | changes)
    with sqlite3.connect(store.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM captures").fetchone()[0] == 0


def test_capture_links_must_exist(store):
    """A capture can point at a real session and calibration, but not at
    ones that do not exist."""
    session = store.start_session()
    calibration = store.save_calibration(_camera(0), _camera(0.2), source="real")
    linked = store.create_capture(_ethan_record(1) | {
        "session_id": session["id"],
        "calibration_id": calibration["calibration_id"],
    })
    assert linked["calibration_id"] == calibration["calibration_id"]

    with pytest.raises(NotFoundError, match="Session"):
        store.create_capture(_ethan_record(2) | {"session_id": 999})
    with pytest.raises(NotFoundError, match="Calibration"):
        store.update_capture(_capture_id(1), calibration_id="0" * 32)


def test_mark_interrupted_captures(store):
    """At startup, only unfinished captures become interrupted. Finished ones
    are untouched, and an existing error message is kept."""
    statuses = {1: "preparing", 2: "armed", 3: "capturing", 4: "processing",
                5: "completed", 6: "cancelled", 7: "timed_out", 8: "failed"}
    for n, status in statuses.items():
        store.create_capture(_ethan_record(n) | {"status": status})
    store.update_capture(_capture_id(2), error="Pico stopped responding")

    assert store.mark_interrupted_captures() == 4

    after = {n: store.get_capture(_capture_id(n)) for n in statuses}
    for n in (1, 2, 3, 4):
        assert after[n]["status"] == "interrupted"
    for n in (5, 6, 7, 8):
        assert after[n]["status"] == statuses[n]
    assert after[1]["error"] == "Server restarted before the capture finished"
    assert after[2]["error"] == "Pico stopped responding"  # kept, not overwritten

    assert store.mark_interrupted_captures() == 0  # nothing left to mark


def test_captures_survive_restart(tmp_path):
    """A capture saved before a reboot is still there after it, which is what
    replaces Ethan's temporary in-memory store."""
    path = tmp_path / "fairway.db"
    ShotStore(path).create_capture(_ethan_record() | {"pico_report": PICO_REPORT})
    assert ShotStore(path).get_capture(_capture_id(1))["pico_report"] == PICO_REPORT


def test_captures_adapter_matches_ethans_interface(store):
    """store.captures offers exactly create/get/update, like his
    MemoryCaptureStore, so CaptureService(pico, store.captures) works."""
    records = store.captures
    records.create(_ethan_record())
    records.update(_capture_id(1), status="armed")
    record = records.get(_capture_id(1))
    assert record["status"] == "armed"

    # Like his store, get() returns a copy: changing it changes nothing saved.
    record["timing"]["flash_width_us"] = 999
    assert records.get(_capture_id(1))["timing"]["flash_width_us"] == 5


def test_old_database_gains_captures_table(tmp_path):
    """Upgrading the Pi's pre-alpha database adds the captures table, with
    the old shots still there."""
    path = tmp_path / "fairway.db"
    _make_pre_versioning_db(path)
    store = ShotStore(path)
    store.create_capture(_ethan_record())
    assert store.get_capture(_capture_id(1))["status"] == "preparing"
    assert len(store.list_shots()) == 1