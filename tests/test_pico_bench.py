import json
from pathlib import Path
import runpy
import sys

import pytest

from fairway_fusion.hardware import pico_client


@pytest.mark.parametrize("trigger", [False, True])
def test_logic_bench_arms_saves_terminal_report_and_disarms(tmp_path, monkeypatch, trigger):
    commands = []

    class Pico:
        firmware = None

        def __init__(self, port):
            assert port == "fake"

        def connect(self):
            self.firmware = "PICO2W_CAPTURE_1"
            return self.firmware

        def command(self, command):
            commands.append(command)
            return "armed" if command == "STATUS" else "DISARMED"

        def capture_command(self, command, capture_id, *timing):
            commands.append(command)
            if command == "REPORT":
                return {"capture_id": capture_id, "status": "completed" if trigger else "timed_out",
                        "timing_source": "commanded"}
            return {"CONFIG": "CONFIGURED", "ARM": "ARMED", "TEST_TRIGGER": "TRIGGERED"}[command]

        def close(self):
            commands.append("CLOSE")

    monkeypatch.setattr(pico_client, "PicoClient", Pico)
    monkeypatch.setattr("time.sleep", lambda seconds: None)
    arguments = ["pico_bench", "--port", "fake", "--outputs-disconnected", "--output", str(tmp_path)]
    if trigger:
        arguments.append("--test-trigger")
    monkeypatch.setattr(sys, "argv", arguments)
    runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/pico_bench.py"), run_name="__main__")
    assert commands[:4] == ["DISARM", "CONFIG", "ARM", "STATUS"]
    assert commands[-2:] == ["DISARM", "CLOSE"]
    assert ("TEST_TRIGGER" in commands) == trigger
    report = json.loads(next(tmp_path.rglob("report.json")).read_text())
    assert report["status"] == ("completed" if trigger else "timed_out")
