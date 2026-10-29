import pytest

from fairway_fusion.analysis.launch_angle import launch_metrics


def test_world_convention():
    metrics = launch_metrics([10, 0, 10])
    assert metrics["launch_angle_deg"] == pytest.approx(45)
    assert metrics["launch_direction_deg"] == 0
