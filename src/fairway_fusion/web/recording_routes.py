"""Read-only access to camera recordings and their original frames."""

from io import BytesIO
from threading import Lock

import cv2
from flask import abort, jsonify, request, send_file, url_for

from fairway_fusion.cameras.recording_store import read_manifest, recording_frame_path
from fairway_fusion.services.recording_review import annotated_png, render_webm, review_frames, review_measurement


def register_recording_routes(app):
    store = app.extensions["shot_store"]
    encoder_lock = Lock()

    def query_int(name, default, minimum):
        raw = request.args.get(name)
        if raw is None:
            return default
        value = request.args.get(name, type=int)
        if value is None or not minimum <= value <= 2**63 - 1:
            abort(400, description=f"{name} must be an integer in range.")
        return value

    def recording(capture_id):
        row = store.get_capture(capture_id)
        if row["recording_manifest_path"] is None:
            abort(404, description="This capture has no camera recording.")
        return row

    @app.get("/api/recordings")
    def list_recordings():
        rows = store.list_captures(session_id=query_int("session_id", None, 1),
                                   limit=query_int("limit", 50, 1), offset=query_int("offset", 0, 0),
                                   recordings_only=True)
        return jsonify(recordings=rows)

    @app.get("/api/recordings/<capture_id>")
    def get_recording(capture_id):
        row = recording(capture_id)
        path, manifest = read_manifest(store, row)
        if manifest is not None:
            for frame in manifest["frames"]:
                frame["url"] = url_for("get_recording_frame", capture_id=capture_id, filename=frame["file"])
        return jsonify(capture=row, manifest=manifest, measurement=review_measurement(path, manifest) if manifest else None)

    def saved_frame(capture_id, filename, *, annotated=False):
        path, manifest = read_manifest(store, recording(capture_id))
        frames = review_frames(manifest, review_measurement(path, manifest)) if annotated and manifest else (manifest or {}).get("frames", [])
        frame = next((frame for frame in frames if frame["file"] == filename), None) if manifest else None
        if frame is None:
            abort(404, description="Recording frame is unavailable.")
        frame_path = recording_frame_path(path, filename)
        if frame_path is None:
            abort(404, description="Recording frame is unavailable.")
        return frame_path, frame

    @app.get("/api/recordings/<capture_id>/frames/<filename>")
    def get_recording_frame(capture_id, filename):
        frame_path, _ = saved_frame(capture_id, filename)
        try:
            return send_file(frame_path, mimetype="image/png")
        except FileNotFoundError:
            abort(404, description="Recording frame is unavailable.")

    @app.get("/api/recordings/<capture_id>/frames/<filename>/detection")
    def get_recording_detection(capture_id, filename):
        path, frame = saved_frame(capture_id, filename, annotated=True)
        data = annotated_png(path, frame)
        if data is None:
            abort(404, description="Recording frame could not be decoded.")
        return send_file(BytesIO(data), mimetype="image/png")

    @app.get("/api/recordings/<capture_id>/preview.webm")
    def get_recording_video(capture_id):
        path, manifest = read_manifest(store, recording(capture_id))
        if manifest:
            try:
                with encoder_lock:
                    video = render_webm(path, manifest)
                if video is not None:
                    return send_file(video, mimetype="video/webm", conditional=True)
            except (OSError, cv2.error):
                app.logger.exception("Could not render recording preview %s", capture_id)
        abort(404, description="Video preview is unavailable. Open the individual recording frames.")
