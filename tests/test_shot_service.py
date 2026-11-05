"""Check the connection between the simulator, analysis, and server."""

import pytest  # Supplies parameterized tests and approximate comparisons.

from fairway_fusion.web.app import create_app  # Build the test application.


@pytest.mark.parametrize(  # Run the same test for three known trajectories.
    "scenario,speed,angle",
    [
        ("straight", 50, 15),
        ("high-launch", 35, 35),
        ("right-launch", 60, 12),
    ],
)
def test_preview_metrics(scenario, speed, angle):
    client = create_app({"TESTING": True}).test_client()  # Create an isolated client.
    response = client.post("/api/shots/preview", json={"scenario": scenario})  # Send JSON.
    assert response.status_code == 200  # Confirm successful processing.
    result = response.get_json()  # Decode the response.
    assert result["speed_m_s"] == pytest.approx(speed)  # Check the calculated speed.
    assert result["launch_angle_deg"] == pytest.approx(angle)  # Check the angle.
    assert result["source"] == "simulation"  # Check provenance.
    assert result["saved"] is False  # Confirm no persistence is claimed.


def test_unknown_scenario():
    client = create_app({"TESTING": True}).test_client()  # Create an isolated client.
    response = client.post("/api/shots/preview", json={"scenario": "unknown"})  # Bad input.
    assert response.status_code == 400  # Expect a validation error.
    assert response.get_json()["error"]["code"] == "bad_request"  # Check error format.


def test_invalid_body():
    client = create_app({"TESTING": True}).test_client()  # Create an isolated client.
    response = client.post("/api/shots/preview", json=[])  # Send a list instead of an object.
    assert response.status_code == 400  # The server should reject the request.


def test_previews_have_unique_ids():
    client = create_app({"TESTING": True}).test_client()  # Create an isolated client.
    first = client.post("/api/shots/preview", json={}).get_json()  # Default scenario.
    second = client.post("/api/shots/preview", json={}).get_json()  # Same scenario again.
    assert first["preview_id"] != second["preview_id"]  # Each request has its own ID.