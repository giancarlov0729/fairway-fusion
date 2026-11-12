"""JSON actions for the saved synthetic-image vision workflow."""

import re

from flask import jsonify, request, url_for
from werkzeug.exceptions import BadRequest

from fairway_fusion.services.capture_processing_service import CaptureProcessingError, process_saved_capture
from fairway_fusion.services.synthetic_capture_service import CaptureAssetError, create_synthetic_capture
from fairway_fusion.web.capture_sessions import require_capture_session


def register_vision_routes(app):
    store = app.extensions["shot_store"]

    def payload_fields(allowed, *, empty=False):
        payload = {} if empty and not request.get_data(cache=True) else request.get_json()
        if not isinstance(payload, dict):
            raise BadRequest("The request body must be a JSON object.")
        if set(payload) - allowed:
            raise BadRequest("Unsupported request fields.")
        return payload

    def failed_capture(error, code, message, status):
        return jsonify(error={"code": code, "message": message},
                       capture=store.get_capture(error.capture_id)), status

    @app.post("/api/captures/synthetic")
    def create_synthetic_image_capture():
        payload = payload_fields({"session_id", "case", "flash_interval_us"})
        if "session_id" in payload and (type(payload["session_id"]) is not int or payload["session_id"] <= 0):
            raise BadRequest("session_id must be a positive integer.")
        try:
            with app.extensions["session_lock"]:
                session_id = require_capture_session(app, payload.pop("session_id", None))
                capture = create_synthetic_capture(store, session_id, **payload)
        except ValueError as error:
            raise BadRequest(str(error)) from error
        except CaptureAssetError as error:
            app.logger.exception("Synthetic capture generation failed: %s", error.capture_id)
            return failed_capture(error, "capture_generation_failed",
                                  "Could not save synthetic inputs. The capture record contains the error.", 500)
        location = url_for("capture_status", capture_id=capture["capture_id"])
        return jsonify(capture=capture, status_url=location,
                       process_url=url_for("process_image_capture", capture_id=capture["capture_id"])), 201, {"Location": location}

    @app.post("/api/captures/<capture_id>/process")
    def process_image_capture(capture_id):
        if not re.fullmatch(r"[0-9a-f]{32}", capture_id):
            raise BadRequest("capture_id must be 32 lowercase hex characters.")
        payload_fields(set(), empty=True)
        try:
            shot = process_saved_capture(store, capture_id)
        except CaptureProcessingError as error:
            if isinstance(error.__cause__, CaptureAssetError):
                return failed_capture(error, "capture_inputs_unavailable",
                                      "Saved images or calibration are missing, changed, or unavailable.", 422)
            app.logger.exception("Capture processing failed: %s", capture_id)
            return failed_capture(error, "capture_processing_failed",
                                  "Image processing failed. The capture record contains the error.", 500)
        return jsonify(capture=store.get_capture(capture_id), shot=shot, saved=True,
                       result_url=url_for("result_detail", shot_id=shot["id"]))
