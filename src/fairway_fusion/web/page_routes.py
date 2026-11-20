from io import BytesIO
from secrets import token_urlsafe
from threading import Lock
from flask import abort, redirect, render_template, request, send_file, url_for
from fairway_fusion.cameras.recording_store import read_manifest
from fairway_fusion.services.shot_service import save_simulated_shot
from fairway_fusion.hardware.pico_client import PicoError
from fairway_fusion.storage import NotFoundError, SessionStateError
from fairway_fusion.web.capture_sessions import arm_session_capture, end_capture_session
from fairway_fusion.web.capture_sessions import require_capture_session
from fairway_fusion.services.synthetic_capture_service import CaptureAssetError, create_synthetic_capture, load_synthetic_capture
from fairway_fusion.services.capture_processing_service import CaptureProcessingError, process_saved_capture
from fairway_fusion.services.recording_review import frame_labels, recording_measurement, review_frames


def recording_review(store, capture, *, browse):
    _, manifest = read_manifest(store, capture)
    review = {"capture": capture, "manifest": manifest, "measurement": {}, "frames": []}
    if manifest is None:
        return review
    measurement = recording_measurement(manifest)
    frames = review_frames(manifest, measurement)
    review.update(measurement=measurement, frames=frames, camera_labels=frame_labels(manifest))
    if not browse:
        review["frame"] = min(frames, key=lambda frame: (not frame["used"], not frame["detection"],
                                                         abs(frame["time_s"])), default=None)
        return review
    options = {"used": "Used in calculation", "detected": "Detected ball", "all": "All frames"}
    default = "used" if any(frame["used"] for frame in frames) else "detected" if any(
        frame["detection"] for frame in frames) else "all"
    choose = request.args.get("frames", default)
    if choose not in options:
        choose = default
    camera_filter = request.args.get("camera", "")
    choices = sorted({frame["label"] for frame in frames})
    selected = [frame for frame in frames if (not camera_filter or frame["label"] == camera_filter) and (
        choose == "all" or (frame["used"] if choose == "used" else frame["detection"]))]
    index = min(max(request.args.get("frame", 1, type=int), 1), max(1, len(selected)))
    review.update(frame=selected[index - 1] if selected else None, options=options, choose=choose,
                  camera_filter=camera_filter, camera_choices=choices, index=index, count=len(selected))
    return review


def register_page_routes(app):
    store = app.extensions['shot_store']
    lock = Lock()
    tokens = {}
    device_status = {"message": "Not checked", "connected": False}

    def page(view='session', shot=None, error=None, status=200, capture_id=None):
        with lock:
            token = token_urlsafe(32)
            tokens[token] = True
            if len(tokens) > 512:
                del tokens[next(iter(tokens))]
        selected = request.args.get('session_id', type=int)
        active = store.active_session()
        live_worker = app.extensions['live_capture']
        live = live_worker.snapshot() if view == 'live' else None
        review, groups = None, {}
        offset = max(0, min(2**63 - 52, request.args.get('offset', 0, type=int)))
        next_offset = None
        if view == 'live':
            # Once a different session starts, only show that session's result.
            last_id = live['last_capture']
            last = store.get_capture(last_id) if last_id else None
            if active and (last is None or last['session_id'] != active['id']):
                last = next((row for row in store.list_captures(active['id'], recordings_only=True)
                             if row['status'] in {'completed', 'timed_out', 'failed', 'cancelled', 'interrupted'}), None)
            if last:
                review = recording_review(store, last, browse=False)
        elif view == 'recordings' and capture_id:
            try:
                row = store.get_capture(capture_id)
                if row["recording_manifest_path"] is None:
                    raise NotFoundError("Recording not found.")
                review = recording_review(store, row, browse=True)
            except NotFoundError:
                error, status = "Recording not found.", 404
        elif view == 'recordings':
            if selected is not None and selected <= 0:
                selected = None
            rows = store.list_captures(session_id=selected, limit=51, offset=offset, recordings_only=True)
            for row in rows[:50]:
                groups.setdefault(row["session_id"], []).append(row)
            next_offset = offset + 50 if len(rows) > 50 else None
        capture = None
        vision_shot, assets_error, vision_inputs = None, None, None
        if view in ('capture', 'vision'):
            key = capture_id or request.args.get('capture_id')
            if view == 'capture':
                key = key or app.extensions['mock_capture_service'].active_capture_id
            if key:
                try:
                    capture = store.get_capture(key)
                except NotFoundError:
                    error, status = 'Capture not found.', 404
        if view == 'vision' and capture:
            if capture['source'] != 'simulation':
                capture, error, status = None, 'The vision demo only accepts synthetic captures.', 409
            else:
                vision_shot = store.get_capture_shot(capture['capture_id'])
                try:
                    vision_inputs = load_synthetic_capture(store, capture['capture_id'])
                except CaptureAssetError as problem:
                    assets_error = str(problem)
        vision_captures = [record for record in store.list_captures(limit=200)
                           if record['source'] == 'simulation' and isinstance(record['timing'], dict)
                           and 'fixture_case' in record['timing']][:12] if view == 'vision' else []
        return render_template('index.html', view=view, shot=shot, error=error,
            vision_shot=vision_shot, vision_inputs=vision_inputs, assets_error=assets_error,
            vision_captures=vision_captures,
            capture=capture, captures=store.list_captures(limit=12),
            mock_active=app.extensions['mock_capture_service'].active_capture_id,
            pico_active=app.extensions['capture_service'].active_capture_id,
            active=active, sessions=store.list_sessions(), live=live, live_enabled=live_worker.enabled,
            review=review, recording_groups=groups, offset=offset, next_offset=next_offset,
            shots=store.list_shots(session_id=selected), selected=selected, token=token, device=device_status.copy(), latest=store.latest_shot()), status

    @app.get('/results')
    def results():
        active = store.active_session()
        return page('results', store.latest_shot(session_id=active['id'] if active else None))

    @app.get('/results/<int:shot_id>')
    def result_detail(shot_id):
        try:
            return page('results', store.get_shot(shot_id))
        except NotFoundError as error:
            return page(error=str(error), status=404)

    @app.get('/history')
    def history():
        return page('history')

    @app.get('/devices')
    def devices():
        return page('devices')

    @app.get('/setup')
    def setup():
        return page('setup')

    @app.get('/capture')
    def capture_page():
        return page('capture')

    @app.get('/vision')
    def vision_page():
        return page('vision')

    @app.get('/live')
    def live_page():
        return page('live')

    @app.get('/live/preview.jpg')
    def live_preview():
        data = app.extensions['live_capture'].preview()
        if data is None:
            abort(404)
        result = send_file(BytesIO(data), mimetype='image/jpeg')
        result.headers['Cache-Control'] = 'no-store'
        return result

    @app.get('/recordings')
    @app.get('/recordings/<capture_id>')
    def recordings_page(capture_id=None):
        return page('recordings', capture_id=capture_id)

    @app.post('/actions/<action>')
    def page_action(action):
        capture_key = request.form.get('capture_id')
        error_status = 400
        with lock:
            if tokens.pop(request.form.get('token', ''), None) is None:
                return redirect(url_for('index'), code=303)
            try:
                if action.startswith('live-'):
                    command = action.removeprefix('live-')
                    worker = app.extensions['live_capture']
                    raw_session = request.form.get('session_id')
                    if command == 'start':
                        with app.extensions['session_lock']:
                            store.start_session()
                    elif command == 'end':
                        if not raw_session:
                            raise ValueError("Choose the practice session to end.")
                        end_capture_session(app, int(raw_session))
                    elif command == 'arm':
                        with app.extensions['session_lock']:
                            worker.submit('arm', require_capture_session(app, int(raw_session) if raw_session else None))
                    elif command == 'plane':
                        corners = [(int(request.form[f'x{i}']), int(request.form[f'y{i}'])) for i in range(4)]
                        dimensions = float(request.form['width_m']), float(request.form['height_m'])
                        worker.submit('plane', (corners, dimensions))
                    elif command in ('preview', 'scan', 'cancel'):
                        worker.submit(command)
                    elif command != 'refresh':
                        raise ValueError("Unknown live capture action.")
                    return redirect(url_for('live_page'), code=303)
                if app.config['LIVE_CAPTURE_UI'] and action in ('connect', 'status', 'disarm', 'disconnect', 'recover-capture'):
                    device_status.update(message="Use Live Capture to operate the Pico in camera bench mode.",
                                         connected=False)
                    return redirect(url_for('devices'), code=303)
                if action == 'recover-capture':
                    record = app.extensions['capture_service'].recover(capture_key or '')
                    device_status.update(message=f"Capture {record['capture_id']}: {record['status']}",
                                         connected=getattr(app.extensions['pico'], 'firmware', None) is not None)
                    return redirect(url_for('devices'), code=303)
                if action == 'vision-create':
                    raw_session = request.form.get('session_id')
                    with app.extensions['session_lock']:
                        sid = require_capture_session(app, int(raw_session) if raw_session else None)
                        record = create_synthetic_capture(store, sid, case=request.form.get('case', 'clean'))
                    return redirect(url_for('vision_page', capture_id=record['capture_id']), code=303)
                if action == 'vision-process':
                    process_saved_capture(store, capture_key or '')
                    return redirect(url_for('vision_page', capture_id=capture_key), code=303)
                if action.startswith('capture-'):
                    service = app.extensions['mock_capture_service']
                    if action == 'capture-arm':
                        raw_session = request.form.get('session_id')
                        record = arm_session_capture(app, service, session_id=int(raw_session) if raw_session else None)
                    else:
                        key = request.form.get('capture_id', '')
                        record = store.get_capture(key)
                        if record['source'] != 'simulation':
                            raise ValueError('These controls operate on mock captures only.')
                        if action == 'capture-impact':
                            service.pico.simulate_impact(key)
                            record = service.poll(key)
                        elif action == 'capture-refresh':
                            record = service.poll(key)
                        elif action == 'capture-cancel':
                            record = service.cancel(key)
                        else:
                            raise ValueError('Unknown capture action.')
                    return redirect(url_for('capture_page', capture_id=record['capture_id']), code=303)
                if action in ('connect', 'status', 'disarm', 'disconnect'):
                    pico = app.extensions['pico']
                    if action == 'connect':
                        message = pico.connect()
                    elif action == 'disconnect':
                        pico.close()
                        message = 'Disconnected'
                    else:
                        message = pico.command(action.upper())
                    device_status.update(message=message, connected=action != 'disconnect')
                    return redirect(url_for('devices'), code=303)
                if action == 'start':
                    store.start_session()
                elif action == 'end':
                    end_capture_session(app, int(request.form['session_id']))
                elif action == 'simulate':
                    shot = save_simulated_shot(store, int(request.form['session_id']), request.form.get('scenario', 'straight'))
                    return redirect(url_for('result_detail', shot_id=shot['id']), code=303)
                else:
                    return redirect(url_for('index'), code=303)
            except (CaptureAssetError, CaptureProcessingError) as error:
                capture_key = error.capture_id
                message = str(error)
                error_status = 422 if isinstance(error, CaptureProcessingError) and isinstance(error.__cause__, CaptureAssetError) else 500
                if error_status == 500:
                    app.logger.exception('Vision form action failed for capture %s', capture_key)
            except PicoError as error:
                if action.startswith('capture-'):
                    message = str(error)
                else:
                    device_status.update(message=str(error), connected=False)
                    return redirect(url_for('devices'), code=303)
            except (ValueError, KeyError, NotFoundError, SessionStateError) as error:
                message = str(error)
            else:
                return redirect(url_for('index'), code=303)
        view = ('live' if action.startswith('live-') else 'vision' if action.startswith('vision-') else
                'capture' if action.startswith('capture-') else 'session')
        return page(view=view, error=message, status=error_status, capture_id=capture_key)

    @app.after_request
    def no_scripts(response):
        response.headers['Content-Security-Policy'] = "script-src 'none'; object-src 'none'; base-uri 'self'; form-action 'self'"
        if response.mimetype == 'text/html':
            response.headers['Cache-Control'] = 'no-store'
        return response

    app.extensions['render_page'] = page
