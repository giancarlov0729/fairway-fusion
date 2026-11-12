from secrets import token_urlsafe
from threading import Lock
from flask import redirect, render_template, request, url_for
from fairway_fusion.services.shot_service import save_simulated_shot
from fairway_fusion.hardware.pico_client import PicoError
from fairway_fusion.storage import NotFoundError, SessionStateError


def register_page_routes(app):
    store = app.extensions['shot_store']
    lock = Lock()
    tokens = {}
    device_status = {"message": "Not checked", "connected": False}

    def page(view='session', shot=None, error=None, status=200):
        with lock:
            token = token_urlsafe(32)
            tokens[token] = True
            if len(tokens) > 512:
                del tokens[next(iter(tokens))]
        selected = request.args.get('session_id', type=int)
        capture = None
        if view == 'capture':
            key = request.args.get('capture_id') or app.extensions['mock_capture_service'].active_capture_id
            if key:
                try:
                    capture = store.get_capture(key)
                except NotFoundError:
                    error, status = 'Capture not found.', 404
        return render_template('index.html', view=view, shot=shot, error=error,
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

    @app.post('/actions/<action>')
    def page_action(action):
        with lock:
            if tokens.pop(request.form.get('token', ''), None) is None:
                return redirect(url_for('index'), code=303)
            try:
                if action.startswith('capture-'):
                    service = app.extensions['mock_capture_service']
                    if action == 'capture-arm':
                        record = service.arm()
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
                    store.end_session(int(request.form['session_id']))
                elif action == 'simulate':
                    shot = save_simulated_shot(store, int(request.form['session_id']), request.form.get('scenario', 'straight'))
                    return redirect(url_for('result_detail', shot_id=shot['id']), code=303)
                else:
                    return redirect(url_for('index'), code=303)
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
        return page(view='capture' if action.startswith('capture-') else 'session', error=message, status=400)

    @app.after_request
    def no_scripts(response):
        response.headers['Content-Security-Policy'] = "script-src 'none'; object-src 'none'; base-uri 'self'; form-action 'self'"
        if response.mimetype == 'text/html':
            response.headers['Cache-Control'] = 'no-store'
        return response

    app.extensions['render_page'] = page
