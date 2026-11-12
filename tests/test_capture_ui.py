import re

import pytest

from fairway_fusion.web.app import create_app


class PhysicalPico:
    def close(self):
        pass

    def capture_command(self, *args):
        raise AssertionError("Mock UI must not contact physical hardware")

    def command(self, *args):
        raise AssertionError("Mock UI must not contact physical hardware")


@pytest.fixture
def setup(tmp_path):
    config = {"TESTING": True, "DATABASE_PATH": str(tmp_path / "shots.db"),
              "PICO_CLIENT": PhysicalPico()}
    app = create_app(config)
    return app, app.test_client(), config


def action(client, name, key=None):
    page = client.get('/capture')
    token = re.search(r'name="token" value="([^"]+)"', page.text)[1]
    data = {'token': token}
    if key:
        data['capture_id'] = key
    return client.post('/actions/capture-' + name, data=data)


def test_mock_capture_form_workflow(setup):
    app, client, _ = setup
    response = client.get('/capture')
    assert response.status_code == 200
    assert 'MOCK HARDWARE' in response.text
    assert '<script' not in response.text
    assert "script-src 'none'" in response.headers['Content-Security-Policy']
    response = action(client, 'arm')
    assert response.status_code == 303
    store = app.extensions['shot_store']
    record = store.list_captures()[0]
    key = record['capture_id']
    assert record['source'] == 'simulation'
    assert record['status'] == 'armed'
    assert key in client.get(response.location).text
    assert action(client, 'refresh', key).status_code == 303
    assert store.get_capture(key)['status'] == 'armed'
    assert action(client, 'impact', key).status_code == 303
    assert store.get_capture(key)['status'] == 'completed'
    assert store.get_capture(key)['pico_report']['source'] == 'simulation'
    assert store.list_shots() == []
    page = client.get('/capture?capture_id=' + key)
    assert 'No camera images or measured shot were produced' in page.text
    assert 'Illustrations only' in page.text
    assert store.get_capture(key)['image_a_path'] is None


def test_cancel_duplicate_arm_and_refresh(setup):
    app, client, _ = setup
    action(client, 'arm')
    store = app.extensions['shot_store']
    key = store.list_captures()[0]['capture_id']
    response = action(client, 'arm')
    assert response.status_code == 400
    assert 'already active' in response.text
    assert len(store.list_captures()) == 1
    assert action(client, 'cancel', key).status_code == 303
    assert store.get_capture(key)['status'] == 'cancelled'
    assert client.get('/capture?capture_id=' + key).status_code == 200
    assert action(client, 'arm').status_code == 303


def test_mock_restart_retains_record_and_recovers(setup):
    app, client, config = setup
    action(client, 'arm')
    key = app.extensions['shot_store'].list_captures()[0]['capture_id']
    restarted = create_app(config)
    second = restarted.test_client()
    page = second.get('/capture?capture_id=' + key)
    assert 'Interrupted' in page.text
    assert 'Server restarted' in page.text
    assert action(second, 'arm').status_code == 303


def test_mock_forms_require_token_and_reject_live_capture(setup):
    app, client, _ = setup
    assert client.post('/actions/capture-arm').status_code == 303
    assert app.extensions['shot_store'].list_captures() == []
    key = 'a' * 32
    app.extensions['shot_store'].create_capture({'capture_id': key, 'status': 'armed'})
    response = action(client, 'cancel', key)
    assert response.status_code == 400
    assert 'mock captures only' in response.text
    assert app.extensions['shot_store'].get_capture(key)['status'] == 'armed'
