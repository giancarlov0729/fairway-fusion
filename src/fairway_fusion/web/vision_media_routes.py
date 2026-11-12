"""Serve verified synthetic inputs and overlays without exposing file paths."""

import re
from io import BytesIO

import cv2
from flask import abort, send_file

from fairway_fusion.services.synthetic_capture_service import CaptureAssetError, load_synthetic_capture
from fairway_fusion.services.vision_overlay import render_overlay


def register_vision_media_routes(app):
    store = app.extensions["shot_store"]

    def inputs(capture_id):
        if not re.fullmatch(r"[0-9a-f]{32}", capture_id):
            abort(404)
        try:
            return load_synthetic_capture(store, capture_id)
        except CaptureAssetError:
            abort(404, description="Saved synthetic image pair is unavailable.")

    def png(image, filename):
        ok, data = cv2.imencode(".png", image)
        if not ok:
            abort(500)
        response = send_file(BytesIO(data.tobytes()), mimetype="image/png", download_name=filename)
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.get("/vision/captures/<capture_id>/images/<camera>")
    def vision_image(capture_id, camera):
        if camera not in ("a", "b"):
            abort(404)
        pair = inputs(capture_id)
        return png(pair.images[0 if camera == "a" else 1], f"{capture_id}-camera-{camera}.png")

    @app.get("/vision/captures/<capture_id>/overlay")
    def vision_overlay(capture_id):
        pair = inputs(capture_id)
        record = store.get_capture(capture_id)
        shot = store.get_capture_shot(capture_id)
        if shot is None or not record["detections"]:
            abort(404, description="Process the saved images before viewing their overlay.")
        result = {"status": shot["status"]}
        if shot["reason"]:
            result["reason"] = shot["reason"]
        image = render_overlay(pair.images, pair.cameras, record["detections"], result)
        return png(image, f"{capture_id}-overlay.png")
