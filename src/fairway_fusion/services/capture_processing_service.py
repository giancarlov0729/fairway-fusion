"""Process stored synthetic images into one durable accepted or rejected shot."""

from fairway_fusion.services.synthetic_capture_service import load_synthetic_capture
from fairway_fusion.services.vision_service import analyze_image_pair


PROCESSING_VERSION = "stereo-vision-v1"


class CaptureProcessingError(RuntimeError):
    def __init__(self, capture_id, message):
        self.capture_id = capture_id
        super().__init__(message)


def process_saved_capture(store, capture_id):
    """Return the saved shot; repeated successful requests return that same shot.

    This increment accepts only synthetic image captures. No hardware commands
    or automatic file cleanup are performed.
    """
    existing = store.claim_capture_processing(
        capture_id, source="simulation", processing_version=PROCESSING_VERSION,
    )
    if existing is not None:
        return existing
    result = None
    try:
        snapshot = store.get_capture(capture_id)
        inputs = load_synthetic_capture(store, capture_id)
        result = analyze_image_pair(*inputs.images, *inputs.cameras, inputs.flash_interval_s,
                                    source=inputs.source, timing_source=inputs.timing_source)
        launch = result["launch"] or {}
        evidence = {
            **result["diagnostics"], "vision_status": result["status"],
            "vision_reason": result["reason"],
            "reprojection_errors_px": result["reprojection_errors_px"],
        }
        return store.save_shot(
            session_id=inputs.session_id, capture_id=capture_id,
            status=result["status"], reason=result["reason"],
            speed_m_s=launch.get("speed_m_s"), launch_angle_deg=launch.get("launch_angle_deg"),
            launch_direction_deg=launch.get("launch_direction_deg"),
            source=inputs.source, samples=result["samples"], carry=result["carry"],
            finalize_capture=True, detections=evidence, expected_capture=snapshot,
        )
    except Exception as error:
        changes = {"status": "failed", "error": f"{type(error).__name__}: {error}"}
        if result is not None:
            changes["detections"] = {**result["diagnostics"], "vision_status": result["status"],
                                     "vision_reason": result["reason"],
                                     "reprojection_errors_px": result["reprojection_errors_px"]}
        store.update_capture(capture_id, **changes)
        raise CaptureProcessingError(capture_id, f"Capture processing failed: {error}") from error
