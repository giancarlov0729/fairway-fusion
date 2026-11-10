import pytest  # Supply test fixtures and exception assertions.
from flask import Flask  # Build a small application for endpoint tests.
from fairway_fusion.hardware.pico_client import PicoClient, PicoError  # Import the real client being tested.
from fairway_fusion.web.pico_routes import register_pico_routes  # Import the real route registration function.


class FakeSerial:  # Model USB bytes without requiring a physical Pico.
    def __init__(self, **options):  # Accept the same keyword arguments as serial.Serial.
        self.buffer = bytearray()  # Store bytes waiting to be read by the host.
        self.closed = False  # Track whether the client released the port.
        self.mode = "normal"  # Allow tests to simulate different device failures.

    def reset_input_buffer(self):  # Match the pySerial startup cleanup method.
        self.buffer.clear()  # Remove pending simulated input.

    def write(self, data):  # Receive a command written by the host client.
        version, request_id, command = data.decode("ascii").split()  # Parse its three protocol fields.
        payload = PicoClient.EXPECTED[command]  # Select the firmware's normal response.
        reply = f"{version} {request_id} OK {payload}\n".encode("ascii")  # Build a matching response.
        if self.mode == "timeout":  # Simulate a device that never replies.
            reply = b""  # Leave the receive buffer empty.
        elif self.mode == "wrong_id":  # Simulate an old or unrelated reply.
            reply = f"FF1 {'0' * 32} OK {payload}\n".encode("ascii")  # Use an incorrect request identifier.
        elif self.mode == "oversized":  # Simulate excessive firmware output.
            reply = b"X" * 129  # Exceed the host's response-size limit.
        elif self.mode == "unplug":  # Simulate removal during a command.
            raise OSError("USB disconnected")  # Reproduce an operating-system I/O failure.
        self.buffer.extend(reply)  # Make the response available to subsequent reads.
        return len(data)  # Report that the complete command was written.

    def read(self, size):  # Simulate responses arriving one byte at a time.
        result = bytes(self.buffer[:size])  # Copy up to the requested number of bytes.
        del self.buffer[:size]  # Consume those bytes from the receive buffer.
        return result  # Return an empty byte string when no data is available.

    def close(self):  # Model closing the operating-system handle.
        self.closed = True  # Record cleanup for the assertions below.


@pytest.fixture  # Construct a fresh device and client for each test.
def pair():  # Expose both sides so tests can induce failures.
    device = FakeSerial()  # Create the simulated USB endpoint.
    client = PicoClient("FAKE", timeout=0.01, serial_factory=lambda **options: device)  # Inject it instead of opening hardware.
    yield client, device  # Give the test a clean client and controllable fake.
    client.close()  # Release resources even when a test fails.


def test_handshake_and_commands(pair):  # Verify normal communication with fragmented replies.
    client, device = pair  # Retrieve this test's client and device.
    assert client.connect() == "PICO2W_USB_1"  # Check firmware identity verification.
    assert device.dtr is True  # Check the Pico SDK host-connection signal.
    for command in ("PING", "STATUS", "DISARM"):  # Exercise each supported command.
        assert client.command(command) == PicoClient.EXPECTED[command]  # Check exact reply validation.


@pytest.mark.parametrize("mode", ["timeout", "wrong_id", "oversized", "unplug"])  # Cover four distinct USB failures.
def test_failure_closes_connection(pair, mode):  # Require explicit recovery after communication becomes uncertain.
    client, device = pair  # Retrieve this test's transport and device.
    client.connect()  # Complete the initial handshake normally.
    device.mode = mode  # Introduce the selected fault after connecting.
    with pytest.raises(PicoError):  # The failed command must report a controlled application error.
        client.command("STATUS")  # Attempt a live status transaction.
    assert device.closed  # Verify the faulty session was released.
    with pytest.raises(PicoError, match="disconnected"):  # Another command must require reconnecting.
        client.command("PING")  # Confirm there is no automatic retry or reconnection.


def test_unimplemented_capture_is_rejected(pair):  # Prevent this foundation from accidentally triggering hardware.
    client, device = pair  # Obtain the unopened test client.
    with pytest.raises(ValueError):  # Unsupported commands must be rejected before serial access.
        client.command("ARM")  # Capture firmware has not been implemented in this chunk.


def test_flask_lifecycle(pair):  # Verify the browser-facing connection endpoints.
    client, device = pair  # Use the same real transport with a simulated USB device.
    app = Flask(__name__)  # Create an isolated Flask application without a database.
    app.config["PICO_CLIENT"] = client  # Inject the test transport into route registration.
    register_pico_routes(app)  # Register the same endpoints used by the main application.
    browser = app.test_client()  # Make HTTP requests without starting a network server.
    assert browser.get("/api/pico/status").status_code == 503  # Disconnected hardware is reported explicitly.
    assert browser.post("/api/pico/connect").status_code == 200  # Connecting must perform a successful handshake.
    result = browser.get("/api/pico/status").get_json()  # Read the controller's reported state.
    assert result == {"connected": True, "state": "USB_ONLY", "capture_ready": False}  # Do not claim working capture.
    assert browser.post("/api/pico/disarm").status_code == 200  # Check the disarm acknowledgement route.
    assert browser.post("/api/pico/disconnect").status_code == 200  # Release the shared serial client.
    assert browser.get("/api/pico/status").status_code == 503  # Confirm subsequent requests see it disconnected.