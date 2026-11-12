import re

from flask import jsonify, request
from werkzeug.exceptions import BadRequest

from fairway_fusion.hardware.pico_client import PicoClient, PicoError, PicoRejected
from fairway_fusion.services.capture_service import CaptureBusyError


def register_capture_routes(app):
    service = app.extensions["capture_service"]

    def validate_id(capture_id):
        if not re.fullmatch(r"[0-9a-f]{32}", capture_id):
            raise BadRequest("capture_id must be 32 lowercase hex characters.")

    def hardware_error(error):
        rejected = isinstance(error, PicoRejected)
        return jsonify(
            error={"code": "pico_rejected" if rejected else "pico_unavailable",
                   "message": str(error)},
            active_capture_id=service.active_capture_id,
        ), 409 if rejected else 503

    @app.post("/api/captures/arm")
    def arm_capture():
        payload = request.get_json()
        if not isinstance(payload, dict):
            raise BadRequest("The request body must be a JSON object.")
        allowed = {"camera_us", "width_us", "interval_us", "delay_us"}
        if set(payload) - allowed:
            raise BadRequest("Allowed fields: camera_us, width_us, interval_us, delay_us.")
        try:
            if service.pico.firmware != PicoClient.CAPTURE_FIRMWARE:
                raise PicoError("Connect the Pico with capture firmware before arming.")
            record = service.arm(**payload)
        except CaptureBusyError as error:
            return jsonify(error={"code": "capture_busy", "message": str(error)},
                           active_capture_id=service.active_capture_id), 409
        except PicoError as error:
            return hardware_error(error)
        except ValueError as error:
            raise BadRequest(str(error)) from error
        return jsonify(capture=record), 201

    @app.get("/api/captures/<capture_id>")
    def capture_status(capture_id):
        validate_id(capture_id)
        try:
            record = service.poll(capture_id)
        except PicoError as error:
            return hardware_error(error)
        return jsonify(capture=record)

    @app.post("/api/captures/<capture_id>/cancel")
    def cancel_capture(capture_id):
        validate_id(capture_id)
        try:
            record = service.cancel(capture_id)
        except PicoError as error:
            return hardware_error(error)
        return jsonify(capture=record)
