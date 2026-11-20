"""Read-only access to camera recordings and their original frames."""

from flask import abort, jsonify, request, send_file, url_for

from fairway_fusion.cameras.recording_store import read_manifest, recording_frame_path


def register_recording_routes(app):
    store = app.extensions["shot_store"]

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
        _, manifest = read_manifest(store, row)
        if manifest is not None:
            for frame in manifest["frames"]:
                frame["url"] = url_for("get_recording_frame", capture_id=capture_id, filename=frame["file"])
        return jsonify(capture=row, manifest=manifest)

    @app.get("/api/recordings/<capture_id>/frames/<filename>")
    def get_recording_frame(capture_id, filename):
        path, manifest = read_manifest(store, recording(capture_id))
        if manifest is None or not any(frame["file"] == filename for frame in manifest["frames"]):
            abort(404, description="Recording frame is unavailable.")
        frame_path = recording_frame_path(path, filename)
        if frame_path is None:
            abort(404, description="Recording frame is unavailable.")
        try:
            return send_file(frame_path, mimetype="image/png")
        except FileNotFoundError:
            abort(404, description="Recording frame is unavailable.")
