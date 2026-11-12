from secrets import token_urlsafe
from threading import Lock
from flask import redirect, render_template, request, url_for
from fairway_fusion.services.shot_service import save_simulated_shot
from fairway_fusion.hardware.pico_client import PicoError
from fairway_fusion.storage import NotFoundError, SessionStateError
from fairway_fusion.web.capture_sessions import arm_session_capture, end_capture_session
from fairway_fusion.web.capture_sessions import require_capture_session
from fairway_fusion.services.synthetic_capture_service import CaptureAssetError, create_synthetic_capture, load_synthetic_capture
from fairway_fusion.services.capture_processing_service import CaptureProcessingError, process_saved_capture


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
            active=store.active_session(), sessions=store.list_sessions(),
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

    @app.post('/actions/<action>')
    def page_action(action):
        capture_key = request.form.get('capture_id')
        error_status = 400
        with lock:
            if tokens.pop(request.form.get('token', ''), None) is None:
                return redirect(url_for('index'), code=303)
            try:
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
        view = 'vision' if action.startswith('vision-') else 'capture' if action.startswith('capture-') else 'session'
        return page(view=view, error=message, status=error_status, capture_id=capture_key)

    @app.after_request
    def no_scripts(response):
        response.headers['Content-Security-Policy'] = "script-src 'none'; object-src 'none'; base-uri 'self'; form-action 'self'"
        if response.mimetype == 'text/html':
            response.headers['Cache-Control'] = 'no-store'
        return response

    app.extensions['render_page'] = page
