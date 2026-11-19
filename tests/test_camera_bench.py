import concurrent.futures
import json
from itertools import count
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from fairway_fusion.cameras.bench import Frame, FrameBuffer
from fairway_fusion.detection.ball_detector import detect_ball


def test_buffer_bounds_and_preserves_images_and_clock_domains(tmp_path):
    buffer = FrameBuffer(max_frames=2)
    for index in range(3):
        image = np.full((24, 32), index, dtype=np.uint8)
        buffer.append(Frame(index % 2, 10_000_000_000 + index * 1_000_000_000, image,
                            {"SensorTimestamp": index * 123, "ExposureTime": 500}, None))
    manifest = buffer.save(tmp_path / "clip", trigger_ns=11_500_000_000, settings={"fps": 60})
    assert len(manifest["frames"]) == 2
    assert [f["relative_to_manual_trigger_s"] for f in manifest["frames"]] == [-.5, .5]
    assert manifest["frames"][0]["metadata"]["SensorTimestamp"] == 123
    assert not manifest["synchronized"] and not manifest["timing_verified"]
    assert manifest["metrics"] is None
    for record, expected in zip(manifest["frames"], (1, 2)):
        image = cv2.imread(str(tmp_path / "clip" / record["file"]), cv2.IMREAD_GRAYSCALE)
        assert np.all(image == expected)
    assert json.loads((tmp_path / "clip/manifest.json").read_text()) == manifest
    with pytest.raises(FileExistsError):
        buffer.save(tmp_path / "clip", trigger_ns=0, settings={})


def test_bright_ball_candidate_and_blank_background():
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    assert detect_ball(image) is None
    cv2.circle(image, (320, 240), 20, (255, 255, 255), -1)
    ball = detect_ball(image)
    assert ball is not None
    assert abs(ball["center"][0] - 320) <= 1
    assert abs(ball["center"][1] - 240) <= 1


def test_near_ball_lock_ignores_distant_object_and_follows_out_of_tee_area():
    bench = runpy.run_path(str(Path(__file__).resolve().parents[1] / "scripts/camera_bench.py"))
    background = np.full((240, 400), 60, dtype=np.uint8)
    cv2.rectangle(background, (240, 20), (390, 210), 130, -1)
    cv2.line(background, (230, 200), (390, 40), 180, 3)
    cv2.circle(background, (68, 58), 10, 250, -1)
    empty = Frame(0, 0, background, {}, None)
    assert bench["analyze_preview"](([empty],) * 3, {}, 18, 56)[0] is None

    tee = {0: (96, 85, 108, 130)}
    still = background.copy()
    cv2.circle(still, (153, 151), 23, 205, -1)
    parked = Frame(0, 16_000_000, still, {}, None)
    seed = bench["analyze_preview"](([parked],) * 3, {}, 18, 56, tee)[0]
    assert abs(seed["center"][0] - 153) <= 2

    moving = background.copy()
    cv2.circle(moving, (225, 107), 15, 205, -1)
    airborne = Frame(0, 32_000_000, moving, {}, None)
    _, cold = bench["track_preview"]([airborne], [parked], [parked], {}, 6, 56, {}, {}, 18)
    assert cold == {}
    _, balls = bench["track_preview"]([airborne], [parked], [parked], {}, 6, 56,
                                       {0: (seed, parked.host_ns)}, {}, 18, tee)
    assert abs(balls[0]["center"][0] - 225) <= 2
    assert balls[0]["radius"] < 18

    stationary = [Frame(0, i * 16_000_000, still, {}, None) for i in range(7)]
    airborne.host_ns = 7 * 16_000_000
    bench["analyze_frames"]([*stationary, airborne], {}, 6, 56, {},
                            acquire_radius=18, acquisition_regions=tee)
    assert airborne.ball == balls[0]

    clutter = still.copy()
    cv2.circle(clutter, (232, 128), 23, 255, -1)
    changed = Frame(0, 32_000_000, clutter, {}, None)
    _, balls = bench["track_preview"]([changed], [parked], [parked], {}, 6, 56,
                                       {0: (seed, parked.host_ns)}, {}, 18, tee)
    assert abs(balls[0]["center"][0] - 153) <= 3


def test_initial_lock_accepts_ball_with_dark_lower_rim():
    image = np.full((240, 400), 105, dtype=np.uint8)
    center = (205, 130)
    y, x = np.indices(image.shape)
    ball = np.hypot(x - center[0], y - center[1]) <= 28
    image[ball] = np.clip(145 - 4 * (y[ball] - center[1]), 0, 255)
    cv2.circle(image, (65, 70), 11, 250, -1)

    candidate = detect_ball(image, 18, 56, prefer_roundness=True)

    assert candidate is not None
    assert np.linalg.norm(np.subtract(candidate["center"], center)) <= 4
    assert abs(candidate["radius"] - 28) <= 4


def test_failed_save_has_no_success_manifest(tmp_path, monkeypatch):
    buffer = FrameBuffer()
    buffer.append(Frame(0, 0, np.zeros((4, 4), dtype=np.uint8), {}, None))
    monkeypatch.setattr(cv2, "imwrite", lambda *args: False)
    with pytest.raises(OSError):
        buffer.save(tmp_path / "clip", trigger_ns=0, settings={})
    assert not (tmp_path / "clip/manifest.json").exists()


@pytest.mark.parametrize("mode", ["headless", "failure", "manual", "preview"])
def test_camera_recording_and_request_release(tmp_path, monkeypatch, mode):
    cameras, requests = [], []
    preview_jobs = []
    preview_markers = []

    class Request:
        released = False

        def __init__(self, index, camera):
            self.index = index
            self.camera = camera

        def make_array(self, name):
            if mode == "failure":
                raise RuntimeError("camera array failed")
            # Include stride padding to check only visible luma pixels are saved.
            array = np.zeros((720, 672), dtype=np.uint8)
            if mode == "manual":
                x = 180 + 25 * self.camera + self.index * 25
                y = 326 - 45 * max(self.index - 4, 0)
                cv2.circle(array, (x, y), 22, 230, -1)
                if self.index > 4:
                    array = cv2.blur(array, (9, 5))
            return array

        def get_metadata(self):
            return {"SensorTimestamp": 123, "ExposureTime": 1000}

        def release(self):
            self.released = True

    class Camera:
        stopped = closed = False

        def __init__(self, number):
            self.number = number
            self.capture_indices = count(1)
            cameras.append(self)

        def create_video_configuration(self, **kwargs):
            return kwargs

        def configure(self, config):
            assert config["queue"] is False

        def start(self):
            pass

        def capture_request(self, wait):
            assert wait is False
            request = Request(next(self.capture_indices), self.number)
            requests.append(request)
            return SimpleNamespace(get_result=lambda timeout: request)

        def stop(self):
            self.stopped = True

        def close(self):
            self.closed = True

    monkeypatch.setitem(sys.modules, "picamera2", SimpleNamespace(Picamera2=Camera))
    clock = count(0, 16_000_000 if mode == "manual" else 100_000_000)
    monkeypatch.setattr("time.monotonic_ns", lambda: next(clock))
    args = ["camera_bench", "--fps", "60", "--output", str(tmp_path)]
    if mode in ("manual", "preview"):
        keypresses = [ord("a"), -1, 32] + [-1] * 28 if mode == "manual" else [-1] * 6
        keys = iter(keypresses + [ord("q")])
        monkeypatch.setattr(cv2, "waitKey", lambda delay: next(keys))
        monkeypatch.setattr(cv2, "imshow", lambda name, image: None)
        monkeypatch.setattr(cv2, "destroyAllWindows", lambda: None)
        monkeypatch.setattr(cv2, "setNumThreads", lambda count: None)
        draw_circle = cv2.circle

        def record_marker(image, center, radius, color, thickness):
            if image.ndim == 3:
                preview_markers.append(center)
            return draw_circle(image, center, radius, color, thickness)

        monkeypatch.setattr(cv2, "circle", record_marker)
    else:
        args.extend(["--headless-seconds", ".01"])
    if mode in ("manual", "preview"):
        class TestPreviewWorker:
            def __init__(self, *, max_workers):
                assert max_workers == 1

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                pass

            def submit(self, process, *args):
                result = concurrent.futures.Future()
                if mode == "manual":
                    result.set_result(process(*args))
                # In preview mode neither worker completes. Capture must keep
                # going without waiting or queuing more than one job per worker.
                preview_jobs.append(result)
                return result

        monkeypatch.setattr(concurrent.futures, "ThreadPoolExecutor", TestPreviewWorker)
    monkeypatch.setattr(sys, "argv", args)
    script = Path(__file__).resolve().parents[1] / "scripts/camera_bench.py"
    if mode == "failure":
        with pytest.raises(RuntimeError, match="camera array failed"):
            runpy.run_path(str(script), run_name="__main__")
        assert not list(tmp_path.rglob("manifest.json"))
    elif mode == "preview":
        runpy.run_path(str(script), run_name="__main__")
        assert len(preview_jobs) == 2
        assert len(requests) == 16  # Startup pair plus seven preview pairs.
    else:
        runpy.run_path(str(script), run_name="__main__")
        manifest = json.loads(next(tmp_path.rglob("manifest.json")).read_text())
        assert {frame["camera"] for frame in manifest["frames"]} == {0, 1}
        image = cv2.imread(str(next(tmp_path.rglob("*.png"))), cv2.IMREAD_GRAYSCALE)
        assert image.shape == (480, 640)
        if mode == "manual":
            assert any(y < 240 for _, y in preview_markers)
            after_trigger = [frame for frame in manifest["frames"]
                             if frame["relative_to_manual_trigger_s"] > 0]
            for camera in (0, 1):
                airborne = [frame["ball_candidate"]["center"]
                            for frame in after_trigger if frame["camera"] == camera
                            and frame["ball_candidate"]
                            and frame["ball_candidate"]["center"][1] < 240]
                assert len(airborne) >= 2
                assert airborne[1][0] > airborne[0][0]
                assert airborne[1][1] < airborne[0][1]
    assert all(request.released for request in requests)
    assert len(cameras) == 2 and all(camera.stopped and camera.closed for camera in cameras)
