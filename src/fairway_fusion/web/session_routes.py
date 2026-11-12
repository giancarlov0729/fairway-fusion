import os
from threading import RLock

from flask import jsonify, request
from werkzeug.exceptions import BadRequest

from fairway_fusion.storage import ShotStore, NotFoundError, SessionStateError
from fairway_fusion.services.shot_service import save_simulated_shot
from fairway_fusion.storage.database import DEFAULT_DB_PATH
from fairway_fusion.web.capture_sessions import end_capture_session


def register_session_routes(app):
    default_path = DEFAULT_DB_PATH
    database_path = app.config.get('DATABASE_PATH')
    if database_path is None:
        database_path = os.environ.get('FAIRWAY_DB') or default_path
    store = ShotStore(database_path)
    lock = RLock()
    app.extensions['session_lock'] = lock
    app.extensions['shot_store'] = store

    @app.errorhandler(NotFoundError)
    def storage_not_found(error):
        return jsonify(error={'code': 'not_found', 'message': str(error)}), 404

    @app.errorhandler(SessionStateError)
    def session_conflict(error):
        return jsonify(error={'code': 'session_conflict', 'message': str(error)}), 409

    @app.post('/api/sessions')
    def start_session():
        with lock:
            session = store.start_session()
        return jsonify(session=session), 201

    @app.get('/api/sessions/active')
    def active_session():
        return jsonify(session=store.active_session())

    @app.get('/api/sessions')
    def list_sessions():
        return jsonify(sessions=store.list_sessions())

    @app.post('/api/sessions/<int:session_id>/end')
    def end_session(session_id):
        with lock:
            session = end_capture_session(app, session_id)
        return jsonify(session=session)

    @app.post('/api/shots/simulate')
    def simulate_shot():
        payload = request.get_json()
        if not isinstance(payload, dict):
            raise BadRequest('The request body must be a JSON object.')
        session_id = payload.get('session_id')
        if type(session_id) is not int or session_id <= 0:
            raise BadRequest('session_id must be a positive integer.')
        scenario = payload.get('scenario', 'straight')
        with lock:
            session = store.get_session(session_id)
            if not session['active']:
                raise SessionStateError('This session has ended. Start a new session.')
            try:
                shot = save_simulated_shot(store, session_id, scenario)
            except ValueError as error:
                raise BadRequest(str(error)) from error
        return jsonify(shot=shot, saved=True), 201

    @app.get('/api/shots')
    def list_shots():
        raw_id = request.args.get('session_id')
        session_id = None
        if raw_id is not None:
            try:
                session_id = int(raw_id)
            except ValueError as error:
                raise BadRequest('session_id must be a positive integer.') from error
            if session_id <= 0:
                raise BadRequest('session_id must be a positive integer.')
            store.get_session(session_id)
        return jsonify(shots=store.list_shots(session_id=session_id))

    @app.get('/api/shots/<int:shot_id>')
    def get_shot(shot_id):
        return jsonify(shot=store.get_shot(shot_id))
