from fairway_fusion.storage import SessionStateError


def arm_session_capture(app, service, session_id=None, **timing):
    if session_id is not None and (type(session_id) is not int or session_id <= 0):
        raise ValueError("session_id must be a positive integer.")
    with app.extensions["session_lock"]:
        active = app.extensions["shot_store"].active_session()
        if active is None:
            raise SessionStateError("Start a practice session before arming a capture.")
        if session_id is not None and session_id != active["id"]:
            raise SessionStateError("This session is no longer active. Refresh before arming.")
        return service.arm(session_id=active["id"], **timing)


def end_capture_session(app, session_id):
    with app.extensions["session_lock"]:
        store = app.extensions["shot_store"]
        for name in ("capture_service", "mock_capture_service"):
            service = app.extensions[name]
            key = service.active_capture_id
            if key is not None and store.get_capture(key)["session_id"] == session_id:
                raise SessionStateError("Resolve or cancel the active capture before ending this session.")
        return store.end_session(session_id)
