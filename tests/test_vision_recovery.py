import json
import shutil
import sqlite3
from unittest.mock import patch

import pytest

from fairway_fusion.synthetic import make_stereo_fixture
from fairway_fusion.web.app import create_app
from test_vision_api import PhysicalPico


@pytest.fixture
def setup(tmp_path):
    config = {'TESTING': True, 'DATABASE_PATH': str(tmp_path / 'original' / 'fairway.db'),
              'PICO_CLIENT': PhysicalPico()}
    app = create_app(config)
    client = app.test_client()
    client.post('/api/sessions')
    return app, client, config


@pytest.mark.parametrize('case', ['clean', 'blur', 'uneven-light', 'shallow', 'clutter', 'leftward'])
def test_unprocessed_copy_replays_without_regenerating_inputs(setup, tmp_path, case):
    app, client, config = setup
    response = client.post('/api/captures/synthetic', json={'case': case})
    record, url = response.json['capture'], response.json['process_url']
    key = record['capture_id']
    original_image = client.get(f'/vision/captures/{key}/images/a').data
    store = app.extensions['shot_store']
    replay = tmp_path / 'copied'
    replay.mkdir()
    with sqlite3.connect(store.path) as source, sqlite3.connect(replay / 'fairway.db') as destination:
        source.backup(destination)
    shutil.copytree(store.images_root / 'captures', replay / 'captures')
    second = create_app({**config, 'DATABASE_PATH': str(replay / 'fairway.db')})
    with patch('fairway_fusion.services.synthetic_capture_service.make_stereo_fixture',
               side_effect=AssertionError('Replay must not regenerate images')):
        copied = second.test_client()
        result = copied.post(url)
        assert result.status_code == 200
        assert result.json['shot']['source'] == 'simulation'
        assert result.json['shot']['status'] == ('rejected' if case in ('shallow', 'clutter') else 'accepted')
        assert copied.get(f'/vision/captures/{key}/images/a').data == original_image
        assert copied.get(f'/vision/captures/{key}/overlay').status_code == 200
        assert copied.post(url).json['shot'] == result.json['shot']
    assert store.get_capture_shot(key) is None
    assert store.get_capture(key) == record


@pytest.mark.parametrize('checkpoint', ['preparing', 'processing'])
def test_actual_flask_startup_recovers_interrupted_image_work(setup, checkpoint):
    app, client, config = setup
    created = client.post('/api/captures/synthetic', json={}).json
    key = created['capture']['capture_id']
    store = app.extensions['shot_store']
    store.update_capture(key, status=checkpoint)
    second = create_app(config)
    recovered = second.extensions['shot_store'].get_capture(key)
    assert recovered['status'] == 'interrupted'
    assert recovered['session_id'] == created['capture']['session_id']
    assert recovered['calibration_id'] == created['capture']['calibration_id']
    retry = second.test_client().post(created['process_url'])
    assert retry.status_code == 200
    assert retry.json['shot']['status'] == 'accepted'
    assert len(second.extensions['shot_store'].list_shots()) == 1


def test_cleanup_removes_previews_but_preserves_saved_measurements(setup):
    app, client, _ = setup
    created = client.post('/api/captures/synthetic', json={}).json
    key = created['capture']['capture_id']
    original = client.post(created['process_url']).json['shot']
    store = app.extensions['shot_store']
    assert store.cleanup_images(keep_accepted=0, keep_other=0)['deleted'] == [key]
    assert client.get(f'/vision/captures/{key}/images/a').status_code == 404
    assert client.get(f'/vision/captures/{key}/overlay').status_code == 404
    page = client.get('/vision?capture_id=' + key)
    assert 'Saved image pair unavailable' in page.text
    assert 'Open Shot Analysis' in page.text
    with patch('fairway_fusion.services.capture_processing_service.load_synthetic_capture') as load:
        assert client.post(created['process_url']).json['shot'] == original
    load.assert_not_called()
    assert client.get(f'/results/{original["id"]}').status_code == 200
    assert len(store.list_shots()) == 1


def test_real_calibration_substitution_is_refused_by_api_and_ui(setup):
    app, client, _ = setup
    created = client.post('/api/captures/synthetic', json={}).json
    record = created['capture']
    store = app.extensions['shot_store']
    _, cameras, _ = make_stereo_fixture()
    real = store.save_calibration(*cameras, source='real')
    store.update_capture(record['capture_id'], calibration_id=real['calibration_id'])
    manifest_file = store.images_root / 'captures' / record['capture_id'] / 'manifest.json'
    manifest = json.loads(manifest_file.read_text())
    manifest['calibration_id'] = real['calibration_id']
    manifest_file.write_text(json.dumps(manifest))
    response = client.post(created['process_url'])
    assert response.status_code == 422
    assert response.json['capture']['status'] == 'failed'
    assert store.list_shots() == []
    assert client.get(f'/vision/captures/{record["capture_id"]}/images/a').status_code == 404
    assert 'Saved image pair unavailable' in client.get('/vision?capture_id=' + record['capture_id']).text
