from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import pytest

from fairway_fusion.web.app import create_app


class PhysicalPico:
    def close(self):
        pass

    def connect(self):
        raise AssertionError("Synthetic image endpoints must not connect to USB")

    def capture_command(self, *args):
        raise AssertionError("Synthetic image endpoints must not trigger hardware")

    def command(self, *args):
        raise AssertionError("Synthetic image endpoints must not send USB commands")


@pytest.fixture
def setup(tmp_path):
    config = {"TESTING": True, "DATABASE_PATH": str(tmp_path / "vision-api.db"),
              "PICO_CLIENT": PhysicalPico()}
    app = create_app(config)
    return app, app.test_client(), config


def start_and_create(client, **options):
    sid = client.post('/api/sessions').json['session']['id']
    response = client.post('/api/captures/synthetic', json={'session_id': sid, **options})
    assert response.status_code == 201
    return sid, response


@pytest.mark.parametrize('case,status', [('clean', 'accepted'), ('leftward', 'accepted'),
    ('blur', 'accepted'), ('uneven-light', 'accepted'), ('shallow', 'rejected'), ('clutter', 'rejected')])
def test_complete_synthetic_api_workflow(setup, case, status):
    app, client, _ = setup
    sid, created = start_and_create(client, case=case)
    capture = created.json['capture']
    assert capture['source'] == 'simulation'
    assert capture['session_id'] == sid
    assert capture['timing']['flash_interval_us'] == 2000
    assert created.headers['Location'] == created.json['status_url']
    assert client.get(created.json['status_url']).json['capture'] == capture
    assert app.extensions['shot_store'].list_shots() == []
    response = client.post(created.json['process_url'])
    assert response.status_code == 200
    shot = response.json['shot']
    assert shot['status'] == status
    assert shot['source'] == 'simulation'
    assert shot['session_id'] == sid
    assert shot['capture_id'] == capture['capture_id']
    assert response.json['saved'] is True
    assert client.get(response.json['result_url']).status_code == 200
    assert response.json['capture']['detections']['vision_status'] == status
    if status == 'accepted':
        assert shot['speed_m_s'] > 0
        assert shot['carry']['estimated_carry_yd'] > 0
    else:
        assert shot['reason']
        assert shot['speed_m_s'] is shot['carry'] is None
    repeated = client.post(created.json['process_url'], json={})
    assert repeated.status_code == 200
    assert repeated.json['shot'] == shot
    assert len(app.extensions['shot_store'].list_shots()) == 1


def test_session_required_and_stale_session_refused(setup):
    app, client, _ = setup
    assert client.post('/api/captures/synthetic', json={}).status_code == 409
    first = client.post('/api/sessions').json['session']['id']
    client.post(f'/api/sessions/{first}/end')
    client.post('/api/sessions')
    assert client.post('/api/captures/synthetic', json={'session_id': first}).status_code == 409
    assert app.extensions['shot_store'].list_captures() == []
    response = client.post('/api/captures/synthetic', json={})
    assert response.status_code == 201
    assert response.json['capture']['session_id'] != first


@pytest.mark.parametrize('payload', [[], None, {'session_id': True}, {'session_id': None},
    {'session_id': '1'}, {'session_id': 0}, {'case': 'unknown'}, {'case': None},
    {'flash_interval_us': True}, {'flash_interval_us': 0}, {'source': 'live'},
    {'camera_a': '../image.png'}, {'calibration_id': 'a' * 32}])
def test_invalid_create_requests_do_not_save_attempts(setup, payload):
    import json
    app, client, _ = setup
    client.post('/api/sessions')
    response = client.post('/api/captures/synthetic', data=json.dumps(payload), content_type='application/json')
    assert response.status_code == 400
    assert app.extensions['shot_store'].list_captures() == []


def test_malformed_json_and_wrong_content_type(setup):
    _, client, _ = setup
    assert client.post('/api/captures/synthetic', data='{', content_type='application/json').status_code == 400
    assert client.post('/api/captures/synthetic', data='hello', content_type='text/plain').status_code == 415
    assert client.post('/api/captures/invalid/process').status_code == 400
    assert client.post('/api/captures/' + 'a' * 32 + '/process').status_code == 404


def test_processing_cannot_override_session_source_or_inputs(setup):
    app, client, _ = setup
    _, created = start_and_create(client)
    for payload in ({'session_id': 2}, {'source': 'live'}, {'flash_interval_us': 1000}):
        assert client.post(created.json['process_url'], json=payload).status_code == 400
    assert app.extensions['shot_store'].list_shots() == []


def test_missing_images_return_actionable_failure_and_allow_repair_retry(setup):
    app, client, _ = setup
    _, created = start_and_create(client)
    record = created.json['capture']
    store = app.extensions['shot_store']
    image = store.images_root / record['image_b_path']
    data = image.read_bytes()
    image.unlink()
    response = client.post(created.json['process_url'])
    assert response.status_code == 422
    assert response.json['error']['code'] == 'capture_inputs_unavailable'
    assert response.json['capture']['status'] == 'failed'
    assert response.json['capture']['capture_id'] == record['capture_id']
    assert store.list_shots() == []
    image.write_bytes(data)
    assert client.post(created.json['process_url']).status_code == 200


def test_unexpected_processing_failure_returns_json_without_traceback(setup):
    app, client, _ = setup
    _, created = start_and_create(client)
    with patch('fairway_fusion.services.capture_processing_service.analyze_image_pair',
               side_effect=RuntimeError('Internal implementation detail')):
        response = client.post(created.json['process_url'])
    assert response.status_code == 500
    assert response.json['error']['code'] == 'capture_processing_failed'
    assert response.json['capture']['status'] == 'failed'
    assert 'Traceback' not in response.text
    assert app.extensions['shot_store'].list_shots() == []


def test_generation_failure_returns_failed_record(setup):
    app, client, _ = setup
    client.post('/api/sessions')
    with patch('fairway_fusion.services.synthetic_capture_service.cv2.imencode', return_value=(False, None)):
        response = client.post('/api/captures/synthetic', json={})
    assert response.status_code == 500
    assert response.json['error']['code'] == 'capture_generation_failed'
    assert response.json['capture']['status'] == 'failed'
    assert app.extensions['shot_store'].list_shots() == []


def test_physical_record_is_not_modified_or_processed(setup):
    app, client, _ = setup
    store = app.extensions['shot_store']
    record = store.create_capture({'capture_id': 'a' * 32, 'source': 'live', 'status': 'completed'})
    response = client.post('/api/captures/' + record['capture_id'] + '/process')
    assert response.status_code == 409
    assert store.get_capture(record['capture_id']) == record
    assert store.list_shots() == []


def test_saved_result_retry_works_after_restart_and_session_end(setup):
    _, client, config = setup
    sid, created = start_and_create(client)
    first = client.post(created.json['process_url']).json['shot']
    client.post(f'/api/sessions/{sid}/end')
    second = create_app(config).test_client().post(created.json['process_url'])
    assert second.status_code == 200
    assert second.json['shot'] == first


def test_unprocessed_capture_in_closed_session_returns_conflict(setup):
    _, client, _ = setup
    sid, created = start_and_create(client)
    client.post(f'/api/sessions/{sid}/end')
    assert client.post(created.json['process_url']).status_code == 409


def test_parallel_processing_request_returns_conflict(setup):
    app, client, _ = setup
    sid, created = start_and_create(client)
    entered, release = Event(), Event()
    from fairway_fusion.services.vision_service import analyze_image_pair

    def wait(*args, **kwargs):
        entered.set()
        assert release.wait(10)
        return analyze_image_pair(*args, **kwargs)

    def process():
        return app.test_client().post(created.json['process_url'])

    with patch('fairway_fusion.services.capture_processing_service.analyze_image_pair', wait):
        with ThreadPoolExecutor(max_workers=1) as executor:
            work = executor.submit(process)
            try:
                assert entered.wait(10)
                assert client.post(created.json['process_url']).status_code == 409
                assert client.post(f'/api/sessions/{sid}/end').status_code == 409
            finally:
                release.set()
            assert work.result(timeout=10).status_code == 200
    assert len(app.extensions['shot_store'].list_shots()) == 1
