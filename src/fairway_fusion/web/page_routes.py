from secrets import token_urlsafe
from threading import Lock
from flask import redirect, render_template, request, url_for
from fairway_fusion.services.shot_service import save_simulated_shot
from fairway_fusion.storage import NotFoundError, SessionStateError


def register_page_routes(app):
    store = app.extensions['shot_store']
    lock = Lock()
    tokens = {}

    def page(view='session', shot=None, error=None, status=200):
        with lock:
            token = token_urlsafe(32)
            tokens[token] = True
            if len(tokens) > 512:
                del tokens[next(iter(tokens))]
        selected = request.args.get('session_id', type=int)
        return render_template('index.html', view=view, shot=shot, error=error,
            active=store.active_session(), sessions=store.list_sessions(),
            shots=store.list_shots(session_id=selected), selected=selected, token=token), status

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

    @app.post('/actions/<action>')
    def page_action(action):
        with lock:
            if tokens.pop(request.form.get('token', ''), None) is None:
                return redirect(url_for('index'), code=303)
            try:
                if action == 'start':
                    store.start_session()
                elif action == 'end':
                    store.end_session(int(request.form['session_id']))
                elif action == 'simulate':
                    shot = save_simulated_shot(store, int(request.form['session_id']), request.form.get('scenario', 'straight'))
                    return redirect(url_for('result_detail', shot_id=shot['id']), code=303)
                else:
                    return redirect(url_for('index'), code=303)
            except (ValueError, KeyError, NotFoundError, SessionStateError) as error:
                message = str(error)
            else:
                return redirect(url_for('index'), code=303)
        return page(error=message, status=400)

    @app.after_request
    def no_scripts(response):
        response.headers['Content-Security-Policy'] = "script-src 'none'; object-src 'none'; base-uri 'self'; form-action 'self'"
        if response.mimetype == 'text/html':
            response.headers['Cache-Control'] = 'no-store'
        return response

    app.extensions['render_page'] = page
