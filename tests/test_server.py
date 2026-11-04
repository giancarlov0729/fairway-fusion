"""Tests for the server foundation without cameras or a database."""

# Import the application factory we created in app.py.
from fairway_fusion.web.app import create_app


def test_health():
    """The health endpoint should return the agreed response."""

    # Create a fresh application with testing enabled.
    app = create_app({"TESTING": True})

    # A test client sends requests without opening a real network port.
    client = app.test_client()

    # Request the server's health information.
    response = client.get("/api/health")

    # HTTP 200 means the request succeeded.
    assert response.status_code == 200

    # Confirm that the response is JSON.
    assert response.is_json

    # Confirm the exact contract that other components can rely on.
    assert response.get_json() == {
        "status": "ok",
        "service": "fairway-fusion",
        "api_version": 1,
    }


def test_unknown_endpoint():
    """An unknown endpoint should return a structured 404 error."""

    # Create an independent test client.
    client = create_app({"TESTING": True}).test_client()

    # Request an endpoint that does not exist.
    response = client.get("/api/does-not-exist")

    # HTTP 404 means the requested address was not found.
    assert response.status_code == 404

    # Check the standard error code and readable message.
    error = response.get_json()["error"]
    assert error["code"] == "not_found"
    assert isinstance(error["message"], str)
    assert error["message"]


def test_wrong_method():
    """The read-only health endpoint should reject POST requests."""

    # Create an independent test client.
    client = create_app({"TESTING": True}).test_client()

    # Attempt to POST to an endpoint that only supports reading.
    response = client.post("/api/health")

    # HTTP 405 means this request method is not supported.
    assert response.status_code == 405

    # Confirm the error uses the same JSON structure as other errors.
    assert response.get_json()["error"]["code"] == "method_not_allowed"

    # Confirm that our error handler preserved the allowed methods.
    assert "GET" in response.headers["Allow"]


def test_unexpected_error():
    """Unexpected failures should produce a generic JSON error."""

    # Disable exception propagation so the test exercises the 500 handler.
    app = create_app(
        {"TESTING": True, "PROPAGATE_EXCEPTIONS": False}
    )

    # Add a deliberately broken endpoint only to this test application.
    @app.get("/test-failure")
    def fail():
        # Simulate an unexpected failure inside the server.
        raise RuntimeError("Private diagnostic details")

    # Request the deliberately broken endpoint.
    response = app.test_client().get("/test-failure")

    # HTTP 500 identifies an unexpected server failure.
    assert response.status_code == 500

    # Confirm that the response provides a stable error code.
    assert response.get_json()["error"]["code"] == "internal_server_error"

    # Confirm that internal exception details are not sent to the user.
    assert "Private diagnostic details" not in response.get_data(as_text=True)