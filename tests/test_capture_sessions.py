import re

import pytest

from fairway_fusion.storage import SessionStateError
from fairway_fusion.web.app import create_app
from test_capture_api import Pico


@pytest.fixture
def setup(tmp_path):
    pico = Pico()
    config = {"TESTING": True, "DATABASE_PATH": str(tmp_path / "sessions.db"), "PICO_CLIENT": pico}
    app = create_app(config)
    return app, app.test_client(), pico, config


def form(client, action, **values):
    page = client.get('/capture')
    token = re.search(r'name="token" value="([^"]+)"', page.text)[1]
    return client.post('/actions/' + action, data={'token': token, **values})


def test_arming_requires_session_without_contacting_pico(setup):
    app, client, pico, _ = setup
    assert client.post('/api/captures/arm', json={}).status_code == 409
    page = client.get('/capture')
    assert 'Start a practice session before arming' in page.text
    assert form(client, 'capture-arm').status_code == 400
    assert not pico.calls
    assert app.extensions['shot_store'].list_captures() == []


@pytest.mark.parametrize('payload', [{}, {'session_id': 1}])
def test_api_capture_is_linked_and_prevents_session_end(setup, payload):
    app, client, pico, _ = setup
    session = client.post('/api/sessions').json['session']['id']
    response = client.post('/api/captures/arm', json=payload)
    assert response.status_code == 201
    record = response.json['capture']
    assert record['session_id'] == session
    assert client.post(f'/api/sessions/{session}/end').status_code == 409
    assert form(client, 'end', session_id=session).status_code == 400
    assert app.extensions['shot_store'].active_session()['id'] == session
    assert client.post(f'/api/captures/{record["capture_id"]}/cancel').status_code == 200
    assert client.post(f'/api/sessions/{session}/end').status_code == 200
    assert app.extensions['shot_store'].get_capture(record['capture_id'])['session_id'] == session


@pytest.mark.parametrize('state', ['completed', 'cancelled', 'timed_out', 'failed'])
def test_terminal_capture_retains_session_and_allows_end(setup, state):
    app, client, pico, _ = setup
    session = client.post('/api/sessions').json['session']['id']
    key = client.post('/api/captures/arm', json={}).json['capture']['capture_id']
    pico.state = state
    record = client.get(f'/api/captures/{key}').json['capture']
    assert record['session_id'] == session
    assert record['status'] == state
    assert client.post(f'/api/sessions/{session}/end').status_code == 200


def test_unresolved_usb_failure_blocks_end_until_cancel(setup):
    app, client, pico, _ = setup
    session = client.post('/api/sessions').json['session']['id']
    pico.fail = 'ARM'
    response = client.post('/api/captures/arm', json={})
    assert response.status_code == 503
    key = response.json['active_capture_id']
    assert app.extensions['shot_store'].get_capture(key)['session_id'] == session
    assert client.post(f'/api/sessions/{session}/end').status_code == 409
    pico.fail = None
    client.post(f'/api/captures/{key}/cancel')
    assert client.post(f'/api/sessions/{session}/end').status_code == 200


def test_stale_and_invalid_session_ids_do_not_arm(setup):
    app, client, pico, _ = setup
    first = client.post('/api/sessions').json['session']['id']
    client.post(f'/api/sessions/{first}/end')
    client.post('/api/sessions')
    assert client.post('/api/captures/arm', json={'session_id': first}).status_code == 409
    assert form(client, 'capture-arm', session_id=first).status_code == 400
    for value in [None, True, '2', 0, -1]:
        assert client.post('/api/captures/arm', json={'session_id': value}).status_code == 400
    assert not pico.calls
    assert app.extensions['shot_store'].list_captures() == []


def test_mock_capture_links_session_and_survives_restart(setup):
    app, client, _, config = setup
    sid = client.post('/api/sessions').json['session']['id']
    assert form(client, 'capture-arm', session_id=sid).status_code == 303
    record = app.extensions['shot_store'].list_captures()[0]
    assert record['session_id'] == sid
    assert f'#{sid}' in client.get('/capture').text
    assert client.post(f'/api/sessions/{sid}/end').status_code == 409
    restarted = create_app(config)
    store = restarted.extensions['shot_store']
    recovered = store.get_capture(record['capture_id'])
    assert recovered['session_id'] == sid
    assert recovered['status'] == 'interrupted'
    assert restarted.test_client().post(f'/api/sessions/{sid}/end').status_code == 200


@pytest.mark.parametrize('state', ['preparing', 'armed', 'capturing', 'processing'])
def test_database_refuses_ending_unfinished_session(setup, state):
    app, _, _, _ = setup
    store = app.extensions['shot_store']
    sid = store.start_session()['id']
    store.create_capture({'capture_id': 'a' * 32, 'status': state, 'session_id': sid})
    with pytest.raises(SessionStateError):
        store.end_session(sid)
    store.update_capture('a' * 32, status='cancelled')
    store.end_session(sid)
    with pytest.raises(SessionStateError):
        store.create_capture({'capture_id': 'b' * 32, 'status': 'preparing', 'session_id': sid})
