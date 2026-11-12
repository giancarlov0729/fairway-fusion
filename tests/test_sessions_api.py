import pytest
from fairway_fusion.web.app import create_app


@pytest.fixture
def app(tmp_path):
    return create_app({'TESTING': True, 'DATABASE_PATH': str(tmp_path / 'shots.db')})


def test_complete_session_workflow(app):
    client = app.test_client()
    assert client.get('/api/sessions/active').get_json()['session'] is None
    response = client.post('/api/sessions')
    assert response.status_code == 201
    session_id = response.get_json()['session']['id']
    response = client.post('/api/shots/simulate', json={'session_id': session_id, 'scenario': 'straight'})
    assert response.status_code == 201
    result = response.get_json()
    assert result['saved'] is True
    shot = result['shot']
    assert shot['speed_m_s'] == pytest.approx(50)
    assert shot['launch_angle_deg'] == pytest.approx(15)
    assert shot['source'] == 'simulation'
    assert shot['carry']['estimated_carry_m'] > 0
    assert shot['carry']['carry_is_estimate'] is True
    assert shot['carry']['carry_assumptions']['backspin_rpm'] == 2500
    assert len(shot['samples']['positions_m']) == 6
    assert client.get(f"/api/shots/{shot['id']}").get_json()['shot'] == shot
    history = client.get(f'/api/shots?session_id={session_id}')
    assert history.status_code == 200
    assert len(history.get_json()['shots']) == 1
    assert history.get_json()['shots'][0]['carry'] == shot['carry']
    assert client.get('/api/sessions').get_json()['sessions'][0]['shot_count'] == 1
    response = client.post(f'/api/sessions/{session_id}/end')
    assert response.status_code == 200
    assert response.get_json()['session']['active'] is False
    assert client.get('/api/sessions/active').get_json()['session'] is None
    response = client.post('/api/shots/simulate', json={'session_id': session_id})
    assert response.status_code == 409
    assert len(client.get('/api/shots').get_json()['shots']) == 1


def test_second_active_session_is_rejected(app):
    client = app.test_client()
    assert client.post('/api/sessions').status_code == 201
    assert client.post('/api/sessions').status_code == 409


def test_invalid_scenario_is_not_saved(app):
    client = app.test_client()
    session_id = client.post('/api/sessions').get_json()['session']['id']
    response = client.post('/api/shots/simulate', json={'session_id': session_id, 'scenario': 'unknown'})
    assert response.status_code == 400
    assert client.get('/api/shots').get_json()['shots'] == []


def test_missing_session_is_rejected(app):
    client = app.test_client()
    response = client.post('/api/shots/simulate', json={'session_id': 999})
    assert response.status_code == 404


@pytest.mark.parametrize('session_id', [None, True, '1', 0, -1])
def test_invalid_session_ids(app, session_id):
    response = app.test_client().post('/api/shots/simulate', json={'session_id': session_id})
    assert response.status_code == 400


@pytest.mark.parametrize('query,status', [('abc', 400), ('0', 400), ('999', 404)])
def test_invalid_history_filter(app, query, status):
    assert app.test_client().get(f'/api/shots?session_id={query}').status_code == status


def test_preview_does_not_save(app):
    client = app.test_client()
    assert client.post('/api/shots/preview', json={}).status_code == 200
    assert client.get('/api/shots').get_json()['shots'] == []


def test_data_survives_new_application(tmp_path):
    config = {'TESTING': True, 'DATABASE_PATH': str(tmp_path / 'persistent.db')}
    first = create_app(config).test_client()
    session_id = first.post('/api/sessions').get_json()['session']['id']
    response = first.post('/api/shots/simulate', json={'session_id': session_id, 'scenario': 'high-launch'})
    assert response.status_code == 201
    second = create_app(config).test_client()
    shots = second.get('/api/shots').get_json()['shots']
    assert len(shots) == 1
    assert shots[0]['launch_angle_deg'] == pytest.approx(35)
    assert second.get('/api/sessions/active').get_json()['session']['id'] == session_id
