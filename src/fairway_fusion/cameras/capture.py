"""Pi-only Picamera2 acquisition. Optical synchronization remains the Pico's job."""

from contextlib import ExitStack

import cv2


class StereoCapture:
    def __init__(self, camera_ids=(0, 1), size=(1456, 1088), exposure_us=6000, gain=1):
        if (
            len(camera_ids) != 2
            or camera_ids[0] == camera_ids[1]
            or exposure_us <= 0
            or gain <= 0
        ):
            raise ValueError("Require two different cameras and positive exposure/gain")
        self.ids, self.size, self.exposure, self.gain = (
            camera_ids,
            size,
            exposure_us,
            gain,
        )
        self.cameras = []
        self.stack = ExitStack()

    def __enter__(self):
        # Raspberry Pi OS supplies this package along with libcamera; not installed on desktops.
        from picamera2 import Picamera2

        try:
            for index in self.ids:
                camera = Picamera2(index)
                self.stack.callback(camera.close)
                config = camera.create_still_configuration(
                    main={"size": self.size, "format": "YUV420"},
                    buffer_count=2,
                    queue=False,
                    controls={
                        "AeEnable": False,
                        "ExposureTime": self.exposure,
                        "AnalogueGain": self.gain,
                    },
                )
                camera.configure(config)
                camera.start()
                self.stack.callback(camera.stop)
                self.cameras.append(camera)
            return self
        except BaseException:
            self.stack.close()
            raise

    def queue(self):
        return [camera.capture_request(wait=False) for camera in self.cameras]

    def save(self, jobs, directory, timeout_s=10):
        metadata = []
        for i, (camera, job) in enumerate(zip(self.cameras, jobs)):
            request = camera.wait(job, timeout=timeout_s)
            try:
                width, height = camera.camera_configuration()["main"]["size"]
                gray = request.make_array("main")[:height, :width].copy()
                if not cv2.imwrite(str(directory / f"camera{i}.png"), gray):
                    raise OSError("Cannot save camera image")
                md = request.get_metadata()
                metadata.append(
                    {
                        k: md.get(k)
                        for k in ("SensorTimestamp", "ExposureTime", "AnalogueGain")
                    }
                )
            finally:
                request.release()
        return metadata

    def __exit__(self, *exc):
        self.stack.close()
