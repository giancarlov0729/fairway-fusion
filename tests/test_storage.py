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
    NotFoundError,
    SessionStateError,
    ShotStore,
)

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