from fairway_fusion.storage import ShotStore
from fairway_fusion.web.app import create_app


class Pico:
    def __init__(self, database_path):
        self.database_path = database_path
        self.calls = []

    def capture_command(self, command, capture_id, *timing):
        # A separate connection must see the record before hardware is armed.
        record = ShotStore(self.database_path).get_capture(capture_id)
        assert record["status"] == "preparing"
        self.calls.append(command)

    def close(self):
        pass


def test_capture_service_uses_shared_client_and_persistent_store(tmp_path):
    path = tmp_path / "shots.db"
    pico = Pico(path)
    app = create_app({"TESTING": True, "DATABASE_PATH": str(path), "PICO_CLIENT": pico})
    service = app.extensions["capture_service"]
    assert service.pico is app.extensions["pico"] is pico
    assert service.active_capture_id is None
    assert pico.calls == []

    record = service.arm()
    assert pico.calls == ["CONFIG", "ARM"]
    assert record["status"] == "armed"
    assert app.extensions["shot_store"].get_capture(record["capture_id"]) == record
    assert ShotStore(path).get_capture(record["capture_id"]) == record


def test_startup_interrupts_only_unfinished_captures(tmp_path):
    path = tmp_path / "shots.db"
    store = ShotStore(path)
    states = ["preparing", "armed", "capturing", "processing", "completed",
              "cancelled", "timed_out", "failed", "interrupted"]
    for number, state in enumerate(states):
        store.create_capture({"capture_id": f"{number:032x}", "status": state})
    pico = Pico(path)
    app = create_app({"TESTING": True, "DATABASE_PATH": str(path), "PICO_CLIENT": pico})
    for number, state in enumerate(states):
        record = store.get_capture(f"{number:032x}")
        assert record["status"] == ("interrupted" if number < 4 else state)
        if number < 4:
            assert record["error"] == "Server restarted before the capture finished"
    assert app.extensions["capture_service"].active_capture_id is None
    assert pico.calls == []

    before = store.list_captures()
    create_app({"TESTING": True, "DATABASE_PATH": str(path), "PICO_CLIENT": pico})
    assert store.list_captures() == before


def test_application_restart_preserves_capture_and_marks_it_interrupted(tmp_path):
    path = tmp_path / "shots.db"
    pico = Pico(path)
    config = {"TESTING": True, "DATABASE_PATH": str(path), "PICO_CLIENT": pico}
    first = create_app(config)
    record = first.extensions["capture_service"].arm()
    second = create_app(config)
    recovered = second.extensions["shot_store"].get_capture(record["capture_id"])
    assert recovered["status"] == "interrupted"
    assert recovered["timing"] == record["timing"]
    assert pico.calls == ["CONFIG", "ARM"]


def test_startup_without_connected_hardware(tmp_path):
    app = create_app({"TESTING": True, "DATABASE_PATH": str(tmp_path / "shots.db"),
                      "PICO_PORT": ""})
    assert app.extensions["capture_service"].pico is app.extensions["pico"]
    assert app.test_client().get("/api/health").status_code == 200
