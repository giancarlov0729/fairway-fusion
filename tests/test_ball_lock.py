import concurrent.futures
import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from fairway_fusion.cameras import ball_lock, locked_bench
from fairway_fusion.cameras.ball_lock import (
    LockError, StereoZone, confirm_ball, scan_empty_area, track_locked_flight,
)
from fairway_fusion.cameras.bench import Frame, FrameBuffer


CAMERAS = (0, 1)
REGIONS = dict.fromkeys(CAMERAS, (100, 205, 370, 215))
TEE = ((320, 304), (213, 304))


def make_pair(ns, positions=(None, None), *, extra=False, backdrop=42):
    frames = []
    for number, point in zip(CAMERAS, positions):
        image = np.full((480, 640), backdrop, np.uint8)
        # A round distractor is within both hitting boxes in every frame.
        cv2.circle(image, (402, 343), 22, 230, -1)
        if point is not None:
            cv2.circle(image, point, 22, 220, -1)
        if extra:
            cv2.circle(image, (145, 264), 21, 221, -1)
        t = ns + number * 1_000_000
        frames.append(Frame(number, t, image, {"SensorTimestamp": t}, None))
    return tuple(frames)


def sequence(positions, n=5, **kwargs):
    return [make_pair(i * 67_000_000, positions, **kwargs) for i in range(1, n + 1)]


@pytest.fixture
def references():
    return scan_empty_area(sequence((None, None), n=7), CAMERAS, REGIONS)


def confirm(pairs, references, *, stereo=None, hints=None):
    return confirm_ball(pairs, CAMERAS, REGIONS, references, 17, 29, hints or {}, stereo)


def test_scan_rejects_lighting_flicker_or_stalled_camera():
    empty = sequence((None, None), n=7)
    empty[-1] = make_pair(469_000_000, backdrop=85)
    with pytest.raises(LockError, match="movement or flicker"):
        scan_empty_area(empty, CAMERAS, REGIONS)
    with pytest.raises(LockError, match="stalled"):
        scan_empty_area(empty[:1] * 7, CAMERAS, REGIONS)


def test_static_distractor_cannot_arm_and_one_view_is_insufficient(references):
    with pytest.raises(LockError, match="both cameras"):
        confirm(sequence((None, None)), references)
    with pytest.raises(LockError, match="Camera B"):
        confirm(sequence((TEE[0], None)), references)
    with pytest.raises(LockError, match="lighting changed"):
        confirm(sequence(TEE, backdrop=112), references)


def test_ambiguous_or_unstable_candidates_require_rearming(references):
    with pytest.raises(LockError, match="Multiple possible balls"):
        confirm(sequence(TEE, extra=True), references)
    moving = sequence(TEE)
    moving[-1] = make_pair(335_000_000, ((340, 304), TEE[1]))
    with pytest.raises(LockError, match="unstable"):
        confirm(moving, references)
    lock = confirm(sequence(TEE, extra=True), references, hints=dict(zip(CAMERAS, TEE)))
    assert lock.balls[0]["center"] == TEE[0]


def test_stationary_lock_rejects_textured_round_shoe_detail(references):
    pairs = sequence(TEE)
    for pair in pairs:
        for frame in pair:
            # The outer edge is deliberately ball-shaped. Interior details
            # represent the seam/laces that showed up in the lab recordings.
            cv2.circle(frame.image, (145, 264), 21, 221, -1)
            for y in range(252, 277, 4):
                cv2.line(frame.image, (133, y), (157, y), 148, 1)
    lock = confirm(pairs, references)
    for number, center in zip(CAMERAS, TEE):
        assert lock.balls[number]["center"] == pytest.approx(center, abs=2)
    for pair in pairs:
        for frame, center in zip(pair, TEE):
            cv2.circle(frame.image, center, 22, 42, -1)
    with pytest.raises(LockError, match="No new ball"):
        confirm(pairs, references)


def test_lock_ignores_background_changes_but_detects_removal(references):
    lock = confirm(sequence(TEE), references)
    changed = make_pair(1_000_000_000, TEE, extra=True, backdrop=125)
    assert all(lock.present(frame) for frame in changed)
    assert not any(lock.present(frame) for frame in make_pair(1_100_000_000, extra=True))
    # A flat bright patch can match the core after removal, but has no rim.
    glare = make_pair(1_200_000_000, backdrop=220)
    assert not any(lock.present(frame) for frame in glare)


def test_a_missing_circle_fit_can_be_rechecked_at_the_same_position(monkeypatch, references):
    pairs = sequence(TEE)
    original_detector = ball_lock.detect_ball_candidates

    def drop_fits_in_marked_frames(image, *args, **kwargs):
        if image[0, 0] == 43:
            return []
        return original_detector(image, *args, **kwargs)

    monkeypatch.setattr(ball_lock, "detect_ball_candidates", drop_fits_in_marked_frames)
    for pair in pairs[2:]:
        pair[1].image[REGIONS[1][1], REGIONS[1][0]] = 43
    lock = confirm(pairs, references)
    assert lock.balls[1]["center"] == pytest.approx(TEE[1], abs=2)

    for pair in pairs[2:]:
        cv2.circle(pair[1].image, TEE[1], 22, 42, -1)
    with pytest.raises(LockError, match="Camera B"):
        confirm(pairs, references)


def test_stereo_requires_agreement_inside_volume_and_at_expected_size(tmp_path, references):
    path = tmp_path / "stereo.json"
    cameras = {str(number): {"K": [[800, 0, 320], [0, 800, 240], [0, 0, 1]],
                             "R": np.eye(3).tolist(), "t": [-.1 * number, 0, 0],
                             "distortion": [0, 0, 0, 0, 0], "image_size": [640, 480]}
               for number in CAMERAS}
    path.write_text(json.dumps({"cameras": cameras, "center_min_m": [-.02, .04, .65],
                                "center_max_m": [.02, .08, .85]}))
    stereo = StereoZone.load(path, CAMERAS)
    for number, (x, y) in zip(CAMERAS, TEE):
        left, top, width, height = stereo.image_region(number)
        assert left + 22 <= x < left + width - 22 and top + 22 <= y < top + height - 22
    lock = confirm(sequence(TEE), references, stereo=stereo)
    assert lock.position_m == pytest.approx([0, .06, .75], abs=.01)
    for wrong_b in ((213, 328), (243, 304)):
        with pytest.raises(LockError, match="Views disagree"):
            confirm(sequence((TEE[0], wrong_b)), references, stereo=stereo)
    a, b = (lock.balls[number] for number in CAMERAS)
    assert stereo.matches(CAMERAS, {**a, "radius": 42}, b) is None


def test_saved_flight_starts_at_verified_ball_and_leaves_static_distractor_untracked(references):
    lock = confirm(sequence(TEE), references)
    pairs = [make_pair(ns, TEE) for ns in range(300_000_000, 1_001_000_000, 20_000_000)]
    for i in range(1, 5):
        positions = tuple((x + 28 * i, y - 12 * i) for x, y in TEE)
        pairs.append(make_pair(1_000_000_000 + i * 20_000_000, positions))
    pairs.extend(make_pair(ns) for ns in range(1_100_000_000, 1_200_000_000, 20_000_000))
    frames = [frame for pair in pairs for frame in pair]
    tracked = track_locked_flight(frames, lock, 1_000_000_000, 6, 56)
    assert all(value["flight_observations"] >= 3 for value in tracked.values())
    assert all(frame.ball is None for frame in frames if frame.host_ns >= 1_100_000_000)
    assert all(frame.ball is None or frame.ball["center"] != (402, 343) for frame in frames)


def test_flight_survives_a_view_missing_its_first_frame_but_rejects_a_stale_impact(references):
    lock = confirm(sequence(TEE), references)

    def shot_frames():
        pairs = [make_pair(ns, TEE) for ns in range(700_000_000, 984_000_001, 16_000_000)]
        pairs.append(make_pair(1_000_000_000, ((405, 249), None)))
        pairs.append(make_pair(1_016_000_000, ((504, 184), (400, 188))))
        pairs.append(make_pair(1_032_000_000, ((602, 119), (493, 129))))
        return [frame for pair in pairs for frame in pair]

    frames = shot_frames()
    tracked = track_locked_flight(frames, lock, 1_020_000_000, 6, 56)
    assert [tracked[number]["flight_observations"] for number in CAMERAS] == [3, 2]
    assert frames[-2].ball["center"] == pytest.approx((602, 119), abs=2)
    assert frames[-1].ball["center"] == pytest.approx((493, 129), abs=2)

    stale = track_locked_flight(shot_frames(), lock, 3_200_000_000, 6, 56)
    assert all(view["status"] == "no_departure_confirmed" for view in stale.values())


def test_flight_recovers_a_ball_darker_than_the_background(references):
    lock = confirm(sequence(TEE), references)
    frames = []
    for i in range(-6, 4):
        positions = TEE if i <= 0 else (None, None)
        pair = make_pair(1_000_000_000 + i * 16_000_000, positions)
        for frame, (x, y) in zip(pair, TEE):
            frame.image[90:300, x + 40:] = 205
            if i > 0:
                cv2.circle(frame.image, (x + 85 * i, y - 32 * i), 22, 135, -1)
        frames.extend(pair)

    tracked = track_locked_flight(frames, lock, 1_000_000_000, 6, 56)

    assert [view["flight_observations"] for view in tracked.values()] == [3, 3]
    assert frames[-2].ball["center"] == pytest.approx((575, 208), abs=3)


def test_flight_prediction_uses_sensor_time_when_host_delivery_is_bursty(references):
    lock = confirm(sequence(TEE), references)
    impact_ns = 1_000_000_000
    frames = [frame for i in range(-6, 1) for frame in make_pair(impact_ns + i * 16_000_000, TEE)]
    for i, host_delay_ms in enumerate((16, 17, 49), 1):
        positions = tuple((x + 35 * i, y - 20 * i) for x, y in TEE)
        pair = make_pair(impact_ns + i * 16_000_000, positions)
        for frame in pair:
            frame.host_ns = impact_ns + host_delay_ms * 1_000_000 + frame.camera * 1_000_000
        frames.extend(pair)

    tracked = track_locked_flight(frames, lock, impact_ns, 6, 56)

    for number, (x, y) in zip(CAMERAS, TEE):
        assert tracked[number]["flight_centers_px"][-1] == pytest.approx((x + 105, y - 60), abs=2)
        assert tracked[number]["flight_radii_px"] == pytest.approx([22, 22, 22], abs=2)


def test_flight_reports_sampling_gap_after_only_one_airborne_frame(references):
    lock = confirm(sequence(TEE), references)
    impact_ns = 1_000_000_000
    frames = [frame for i in range(-6, 1) for frame in make_pair(impact_ns + i * 16_000_000, TEE)]
    positions = tuple((x + 60, y - 30) for x, y in TEE)
    frames.extend(make_pair(impact_ns + 16_000_000, positions))
    frames.extend(make_pair(impact_ns + 149_000_000))

    tracked = track_locked_flight(frames, lock, impact_ns, 6, 56)

    assert all(view["status"] == "sampling_gap" and view["flight_observations"] == 0
               for view in tracked.values())
    assert all(frame.ball is None for frame in frames[-4:])


def test_preview_uses_one_arm_request_and_retries_an_empty_area(monkeypatch, tmp_path):
    clock = 0
    frame_index = 0
    status = []
    commands = []
    mouse = None

    class InlineExecutor:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def submit(self, function, *args):
            future = concurrent.futures.Future()
            try:
                future.set_result(function(*args))
            except Exception as error:
                future.set_exception(error)
            return future

    class Recorder:
        active = False
        state = "READY / DISARMED"
        armed_at_ns = None

        def __init__(self, *args, **kwargs):
            pass

        def arm(self, *, ball_lock):
            commands.append((frame_index, ball_lock))
            self.active = True
            self.armed_at_ns = clock
            self.state = "PICO ARMED"

        def tick(self, now):
            if self.state == "CANCELLING":
                self.active = False
                return tmp_path, {"status": "cancelled"}

        def cancel(self):
            self.state = "CANCELLING"

        def close(self):
            pass

    def read_pair():
        nonlocal clock, frame_index
        clock += 67_000_000
        frame_index += 1
        positions = TEE if 18 <= frame_index < 28 else (None, None)
        return make_pair(clock, positions)

    def set_callback(name, callback):
        nonlocal mouse
        mouse = callback

    def keys(delay):
        if frame_index == 1:
            mouse(cv2.EVENT_LBUTTONDOWN, 40, 499, None, None)  # Calibrated
        if frame_index == 9:
            mouse(cv2.EVENT_LBUTTONDOWN, 240, 499, None, None)  # Arm
        return ord("q") if frame_index == 41 else -1

    monkeypatch.setattr(locked_bench, "ImpactRecorder", Recorder)
    monkeypatch.setattr(locked_bench, "ThreadPoolExecutor", InlineExecutor)
    monkeypatch.setattr(locked_bench.time, "monotonic_ns", lambda: clock)
    monkeypatch.setattr(locked_bench, "_show", lambda frames, regions, mode, message, *a, **k:
                        status.append((mode, message)))
    monkeypatch.setattr(cv2, "namedWindow", lambda *a: None)
    monkeypatch.setattr(cv2, "destroyAllWindows", lambda: None)
    monkeypatch.setattr(cv2, "setMouseCallback", set_callback)
    monkeypatch.setattr(cv2, "getWindowProperty", lambda *a: 1)
    monkeypatch.setattr(cv2, "waitKey", keys)
    args = SimpleNamespace(cameras=list(CAMERAS), chip_plane_m=(.6, .3), chip_camera=None,
                           output=tmp_path, pico_port="fake", min_radius=6, max_radius=29,
                           acquire_min_radius=17)
    locked_bench.run_locked_bench(args, {}, REGIONS, None, FrameBuffer(), read_pair,
                                 None, lambda *_: "Capture cancelled")
    assert len(commands) == 1 and commands[0][0] > 18
    assert commands[0][1]["verification"] == "two_view_appearance"
    assert any("No new ball" in message and "Checking again" in message for _, message in status)
    assert any("ARM SUCCESSFUL" in mode for mode, _ in status)
    assert any("LOCK LOST" in mode for mode, _ in status)
