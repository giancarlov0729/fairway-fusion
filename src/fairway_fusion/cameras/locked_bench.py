"""Interactive empty-area setup and ball-verified microphone arming."""

from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import time

import cv2
import numpy as np

from fairway_fusion.cameras.ball_lock import (
    ARM_SAMPLES, SCAN_SAMPLES, LockError, confirm_ball, scan_empty_area, track_locked_flight,
)
from fairway_fusion.cameras.chip import calibrate_plane, measure_chip
from fairway_fusion.cameras.impact import ImpactRecorder, PICO_ARM_WINDOW_S
from fairway_fusion.storage import SessionStateError


WINDOW = "Fairway Fusion ball lock"
BUTTONS = {"scan": (12, 190), "arm": (204, 320), "cancel": (334, 450)}
FOOTER_Y = 480
SAMPLE_INTERVAL_NS = 66_000_000
LOSS_GRACE_NS = 350_000_000


def preview_image(frames, regions, *, balls, hints):
    panels = []
    for label, frame in zip(("A", "B"), frames):
        image = cv2.cvtColor(frame.image, cv2.COLOR_GRAY2BGR)
        x, y, w, h = regions[frame.camera]
        cv2.rectangle(image, (x, y), (x + w - 1, y + h - 1), (255, 200, 0), 1)
        cv2.putText(image, f"Camera {label}", (12, 26), cv2.FONT_HERSHEY_SIMPLEX, .65, (255, 255, 255), 2)
        if frame.camera in hints:
            cv2.drawMarker(image, hints[frame.camera], (255, 200, 0), markerSize=12)
        if frame.camera in balls:
            ball = balls[frame.camera]
            cv2.circle(image, ball["center"], ball["radius"], (0, 255, 0), 2)
        panels.append(image)
    return np.hstack(panels)


def _show(frames, regions, images_status, message, scanned_before, *, balls, hints):
    footer = np.full((105, 1280, 3), 28, dtype=np.uint8)
    for (left, right), title in zip(BUTTONS.values(), ("Recalibrate" if scanned_before else "Calibrated", "Arm", "Cancel")):
        cv2.rectangle(footer, (left, 7), (right, 36), (65, 65, 65), -1)
        cv2.putText(footer, title, (left + 10, 28), cv2.FONT_HERSHEY_SIMPLEX, .60, (255, 255, 255), 1)
    cv2.putText(footer, images_status, (475, 28), cv2.FONT_HERSHEY_SIMPLEX, .65, (155, 255, 215), 2)
    line, y = "", 62
    for word in message.split():
        candidate = f"{line} {word}".lstrip()
        if line and cv2.getTextSize(candidate, cv2.FONT_HERSHEY_SIMPLEX, .59, 1)[0][0] > 1248:
            cv2.putText(footer, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX, .59, (235, 235, 235), 1)
            y += 23
            line = word
        else:
            line = candidate
    cv2.putText(footer, line, (12, y), cv2.FONT_HERSHEY_SIMPLEX, .59, (235, 235, 235), 1)
    cv2.imshow(WINDOW, np.vstack((preview_image(frames, regions, balls=balls, hints=hints), footer)))


def run_locked_bench(args, settings, regions, stereo, buffer, read_pair, select_plane, report, *,
                     recording_store=None, operator=None, client_factory=None):
    """Run the same capture workflow for desktop preview and the web operator."""
    numbers = tuple(args.cameras)
    dimensions = args.chip_plane_m
    side = args.cameras[0] if args.chip_camera is None else args.chip_camera
    references, locked, corners = None, None, None
    scanned_before = False
    hints, commands = {}, []
    scan_label = "Calibrate" if operator is not None else "Calibrated"
    mode, message = "SETUP", f"Align the unit, clear the hitting area, then click {scan_label}."
    actions = {ord("e"): "scan", ord("a"): "arm", ord("d"): "cancel", ord("c"): "plane", ord("t"): "test"}
    work, job, samples = None, None, []
    next_sample = next_preview = started_ns = 0
    missing_since = None
    lock_lost = False
    clicked_ball = None
    arm_session_id = None
    last_capture = None
    if operator is None:
        print("Ball-lock bench: E scan empty area / A arm / D cancel / Q quit. C sets optional shot scale.")
        if stereo is None:
            print("Two-view test mode uses appearance only. It does not validate physical position.")

    def on_click(event, x, y, *_):
        nonlocal clicked_ball
        if event != cv2.EVENT_LBUTTONDOWN:
            return
        if FOOTER_Y + 7 <= y <= FOOTER_Y + 36:
            commands.extend(name for name, (left, right) in BUTTONS.items() if left <= x <= right)
        elif 0 <= x < 1280 and 0 <= y < FOOTER_Y:
            clicked_ball = numbers[x // 640], (x % 640, y)

    def measure(frames, event_ns):
        tracking = track_locked_flight(frames, locked, event_ns, args.min_radius, args.max_radius)
        if not tracking[side]["flight_observations"]:
            result = {"metrics": None, "reason": "No ball departure confirmed near the microphone event. Check the view and microphone, then re-arm."}
        elif corners is None:
            result = {"metrics": None, "reason": "Flight saved. Set the side plane to estimate speed and launch angle."}
        else:
            result = measure_chip(frames, side, event_ns, corners, *dimensions)
        return {**result, "locked_flight_tracks": tracking}

    with ExitStack() as stack:
        if operator is None:
            cv2.namedWindow(WINDOW, cv2.WINDOW_AUTOSIZE)
            cv2.setMouseCallback(WINDOW, on_click)
            stack.callback(cv2.destroyAllWindows)
        executor = stack.enter_context(ThreadPoolExecutor(max_workers=1))
        client_options = {"client_factory": client_factory} if client_factory is not None else {}
        recorder = ImpactRecorder(buffer, args.output, settings, args.pico_port, measure=measure,
                                  recording_store=recording_store, **client_options)
        stack.callback(recorder.close)
        while operator is None or not operator.stopped:
            try:
                frames = read_pair()
            except (TimeoutError, RuntimeError, OSError) as error:
                if recorder.active:
                    recorder.cancel()
                message = f"Camera feed unavailable: {error}. Check both connections and restart."
                if operator is not None:
                    raise RuntimeError(message) from error
                print(message, flush=True)
                return
            now = time.monotonic_ns()
            for frame in frames:
                buffer.append(frame)

            if clicked_ball is not None:
                number, point = clicked_ball
                clicked_ball = None
                if not work and not recorder.active:
                    # Clicking only constrains Arm. It cannot override the empty scan or stereo checks.
                    x, y, w, h = regions[number]
                    if x <= point[0] < x + w and y <= point[1] < y + h:
                        hints[number] = point
                        message = "Test click set. Press Arm to validate, or R to clear test clicks."

            if work and job is None and now >= next_sample:
                samples.append(frames)
                next_sample = now + SAMPLE_INTERVAL_NS
                if work == "scan" and len(samples) == SCAN_SAMPLES:
                    job = executor.submit(scan_empty_area, tuple(samples), numbers, regions)
                elif work == "arm" and len(samples) == ARM_SAMPLES:
                    job = executor.submit(confirm_ball, tuple(samples), numbers, regions, references,
                                          args.acquire_min_radius or args.min_radius, args.max_radius,
                                          hints.copy(), stereo)
            if job is not None and job.done():
                try:
                    result = job.result()
                    if work == "scan":
                        references = result
                        mode, message = "READY", "Empty area saved. Place one ball inside the boxes, then press Arm."
                    elif all(result.present(frame) for frame in frames):
                        session = {"session_id": arm_session_id} if arm_session_id is not None else {}
                        recorder.arm(ball_lock=result.evidence(), **session)
                        locked, missing_since, lock_lost = result, None, False
                        mode, message = "ARMING PICO", "Hold the ball still while the microphone arms."
                    else:
                        raise LockError("Ball moved or disappeared during validation. Set it down again and re-arm.")
                except (LockError, cv2.error) as error:
                    mode = "SCAN FAILED" if work == "scan" else "NOT READY"
                    message = str(error) if isinstance(error, LockError) else "Image check failed. Check the cameras and retry."
                    print(message, flush=True)
                except OSError as error:
                    mode, message = "NOT READY", "Could not write the capture record. Check the recording folder and re-arm."
                    print(f"{message} {error}", flush=True)
                except SessionStateError:
                    mode, message = "NOT READY", "The practice session ended. Start a session and arm again."
                work, job = None, None
            elif work and now - started_ns > 4_000_000_000:
                if job is not None:
                    job.cancel()
                work, job = None, None
                mode, message = "NOT READY", "Validation timed out. Check both camera feeds and try again."

            visible = {}
            if recorder.active:
                try:
                    saved = recorder.tick(now)
                except (OSError, cv2.error) as error:
                    saved = None
                    if recorder.active:
                        recorder.cancel()
                    else:
                        locked = None
                    mode, message = "SAVE FAILED", "Check storage and permissions, then re-arm."
                    print(f"{message} {error}", flush=True)
                if saved:
                    result = report(*saved)
                    outcome = saved[1]["status"]
                    last_capture = saved[1].get("capture_id") or last_capture
                    mode = outcome.upper().replace("_", " ")
                    reason = saved[1].get("failure_reason")
                    message = f"{reason or result}. Place ball and re-arm."
                    if outcome == "timed_out":
                        message = "Shot window expired. Check the ball and press Arm again."
                    if outcome == "cancelled" and lock_lost:
                        mode = "LOCK LOST"
                        message = "The verified ball disappeared before a shot. Reposition it and re-arm."
                    locked = None
                    buffer.frames.clear()
                elif recorder.state == "RECORDING":
                    mode, message = "RECORDING", "Saving footage from both cameras and following the confirmed ball."
                elif lock_lost:
                    mode, message = "LOCK LOST", "Ball no longer confirmed. Disarming; place the ball and re-arm."
                elif locked is not None:
                    visible = {frame.camera: locked.balls[frame.camera] for frame in frames if locked.present(frame)}
                    missing_since = (missing_since if missing_since is not None else now) if len(visible) != 2 else None
                    if missing_since is not None and now - missing_since >= LOSS_GRACE_NS:
                        recorder.cancel()
                        lock_lost = True
                        mode, message = "LOCK LOST", "Ball no longer confirmed. Disarming; place the ball and re-arm."
                    elif recorder.state == "PICO ARMED":
                        remaining = max(0, PICO_ARM_WINDOW_S - max(0, now - recorder.armed_at_ns) / 1e9)
                        mode = f"ARM SUCCESSFUL  |  {remaining:.1f} s"
                        message = ("Hit now. Microphone is listening." if missing_since is None else
                                   "Ball temporarily hidden. Holding the original lock.")
                    else:
                        mode = recorder.state
                        if mode == "WAITING FOR QUIET MICROPHONE":
                            message = "Keep the hitting area quiet while the microphone settles."

            if operator is not None:
                operator.publish(mode=mode, message=message, calibrated=references is not None,
                                 busy=bool(work or recorder.active), measuring=corners is not None,
                                 capture_id=recorder.capture_id if recorder.active else None,
                                 last_capture=last_capture, stereo=stereo is not None)
                if now >= next_preview:
                    operator.save_preview(preview_image(frames, regions, balls=visible, hints=hints))
                    next_preview = now + 500_000_000
                submitted = operator.take_commands()
            else:
                if now >= next_preview:
                    verification = "STEREO" if stereo else "2D TEST MODE"
                    _show(frames, regions, f"{mode}  |  {verification}", message, scanned_before,
                          balls=visible, hints=hints)
                    next_preview = now + 33_333_333
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27) or cv2.getWindowProperty(WINDOW, cv2.WND_PROP_VISIBLE) < 1:
                    return
                if key in actions:
                    commands.append(actions[key])
                if key == ord("r") and not work and not recorder.active:
                    hints.clear()
                    message = "Test clicks cleared."
                submitted = [(command, None) for command in commands]
                commands.clear()
            for command, payload in submitted:
                if command == "cancel":
                    recorder.cancel()
                    if job is not None:
                        job.cancel()
                    work, job = None, None
                    mode = "CANCELLING" if recorder.active else "CANCELLED"
                    message = "Press Arm again when the ball is in position."
                elif command == "test" and recorder.active:
                    recorder.test_trigger()
                elif work or recorder.active:
                    continue
                elif command == "scan":
                    scanned_before = True
                    references = None
                    hints.clear()
                    work, samples, started_ns, next_sample = "scan", [], now, now
                    mode, message = "SCANNING", "Keep the mat empty and the unit still."
                elif command == "arm" and references is not None:
                    arm_session_id = payload
                    work, samples, started_ns, next_sample = "arm", [], now, now
                    mode, message = "VALIDATING BALL", "Hold the ball still while both views are checked."
                elif command == "arm":
                    mode, message = "NOT READY", f"Clear the hitting area and click {scan_label} before arming."
                elif command == "plane":
                    if operator is None:
                        corners = select_plane(next(frame for frame in frames if frame.camera == side), *dimensions)
                    else:
                        corners, dimensions = payload
                    message = "Side plane saved. Press Arm when the ball is in position." if corners else "Side plane setup cancelled."
                    buffer.frames.clear()
