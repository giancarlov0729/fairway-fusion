import unittest
from unittest.mock import Mock

from fairway_fusion.cameras.synchronization import (
    PicoSynchronizer,
    STROBE_PULSE_US,
    SynchronizationError,
    TriggerEvent,
)


class PicoSynchronizerTests(unittest.TestCase):
    def setUp(self):
        self.serial = Mock()
        self.synchronizer = PicoSynchronizer(self.serial)

    def test_initialize_completes_ready_handshake(self):
        self.serial.readline.return_value = b"READY\n"

        self.synchronizer.initialize()

        self.serial.write.assert_called_once_with(b"HELLO\n")
        self.serial.flush.assert_called_once_with()

    def test_initialize_rejects_unexpected_response(self):
        self.serial.readline.return_value = b"BUSY\n"

        with self.assertRaises(SynchronizationError):
            self.synchronizer.initialize()

    def test_initialize_rejects_timeout(self):
        self.serial.readline.return_value = b""

        with self.assertRaisesRegex(SynchronizationError, "timed out"):
            self.synchronizer.initialize()

    def test_trigger_sends_camera_and_10_microsecond_pulse(self):
        self.synchronizer._initialized = True
        self.serial.readline.return_value = b"TRIGGERED,cam0,123456,10\n"

        event = self.synchronizer.trigger_camera("cam0")

        self.serial.write.assert_called_once_with(
            f"TRIGGER,cam0,{STROBE_PULSE_US}\n".encode("ascii")
        )
        self.assertEqual(event, TriggerEvent("cam0", 123456, 10))

    def test_trigger_requires_handshake(self):
        with self.assertRaisesRegex(SynchronizationError, "initialize"):
            self.synchronizer.trigger_camera("cam0")
        self.serial.write.assert_not_called()

    def test_trigger_rejects_wrong_pulse_width(self):
        self.synchronizer._initialized = True
        self.serial.readline.return_value = b"TRIGGERED,cam0,123456,11\n"

        with self.assertRaisesRegex(SynchronizationError, "expected 10 us"):
            self.synchronizer.trigger_camera("cam0")

    def test_trigger_rejects_mismatched_camera_acknowledgement(self):
        self.synchronizer._initialized = True
        self.serial.readline.return_value = b"TRIGGERED,cam1,123456,10\n"

        with self.assertRaisesRegex(SynchronizationError, "expected 'cam0'"):
            self.synchronizer.trigger_camera("cam0")

    def test_trigger_rejects_malformed_timestamp(self):
        self.synchronizer._initialized = True
        self.serial.readline.return_value = b"TRIGGERED,cam0,not-a-time,10\n"

        with self.assertRaisesRegex(SynchronizationError, "numeric"):
            self.synchronizer.trigger_camera("cam0")


if __name__ == "__main__":
    unittest.main()