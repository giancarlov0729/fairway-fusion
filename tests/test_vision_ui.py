import re
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from fairway_fusion.web.app import create_app
from test_vision_api import PhysicalPico


@pytest.fixture
def setup(tmp_path):
    config = {'TESTING': True, 'DATABASE_PATH': str(tmp_path / 'vision-ui.db'),
              'PICO_CLIENT': PhysicalPico()}
    app = create_app(config)
    return app, app.test_client(), config


def token(client, key=None):
    page = client.get('/vision' + ('?capture_id=' + key if key else ''))
    assert '<script' not in page.text
    assert "script-src 'none'" in page.headers['Content-Security-Policy']
    return re.search(r'name="token" value="([^"]+)"', page.text)[1]


def generate(client, sid, case='clean'):
    return client.post('/actions/vision-create', data={
        'token': token(client), 'session_id': sid, 'case': case,
    })


def process(client, key):
    return client.post('/actions/vision-process', data={'token': token(client, key), 'capture_id': key})


@pytest.mark.parametrize('case,outcome', [('clean', 'accepted'), ('blur', 'accepted'),
                                        ('clutter', 'rejected'), ('shallow', 'rejected')])
def test_complete_form_workflow_with_real_saved_images_and_overlay(setup, case, outcome):
    app, client, _ = setup
    sid = client.post('/api/sessions').json['session']['id']
    response = generate(client, sid, case)
    assert response.status_code == 303
    page = client.get(response.location)
    assert 'SYNTHETIC VISION' in page.text
    assert 'Images ready' in page.text
    assert 'Process saved images' in page.text
    store = app.extensions['shot_store']
    key = store.list_captures()[0]['capture_id']
    assert store.list_shots() == []
    for label in ('a', 'b'):
        url = f'/vision/captures/{key}/images/{label}'
        assert url in page.text
        image = client.get(url)
        assert image.status_code == 200
        assert image.mimetype == 'image/png'
        decoded = cv2.imdecode(np.frombuffer(image.data, np.uint8), cv2.IMREAD_UNCHANGED)
        original = cv2.imread(str(store.images_root / store.get_capture(key)[f'image_{label}_path']),
                              cv2.IMREAD_UNCHANGED)
        assert np.array_equal(decoded, original)
    assert client.get(f'/vision/captures/{key}/overlay').status_code == 404
    response = process(client, key)
    assert response.status_code == 303
    page = client.get(response.location)
    shot = store.get_capture_shot(key)
    assert shot['status'] == outcome
    assert 'Open Shot Analysis' in page.text
    if outcome == 'accepted':
        assert 'The pair produced a launch result.' in page.text
        assert f'{shot["speed_mph"]:.1f}' in page.text
    else:
        assert 'This pair was rejected.' in page.text
        assert shot['reason'] in page.text
    overlay = client.get(f'/vision/captures/{key}/overlay')
    assert overlay.status_code == 200
    decoded = cv2.imdecode(np.frombuffer(overlay.data, np.uint8), cv2.IMREAD_UNCHANGED)
    assert decoded.shape == (556, 1280, 3)
    assert overlay.headers['Cache-Control'] == 'no-store'
    assert client.get(f'/results/{shot["id"]}').status_code == 200


def test_invalid_or_repeated_form_tokens_do_not_duplicate_work(setup):
    app, client, _ = setup
    sid = client.post('/api/sessions').json['session']['id']
    assert client.post('/actions/vision-create', data={'session_id': sid}).status_code == 303
    assert app.extensions['shot_store'].list_captures() == []
    data = {'token': token(client), 'session_id': sid, 'case': 'clean'}
    first = client.post('/actions/vision-create', data=data)
    client.post('/actions/vision-create', data=data)
    store = app.extensions['shot_store']
    assert len(store.list_captures()) == 1
    key = store.list_captures()[0]['capture_id']
    data = {'token': token(client, key), 'capture_id': key}
    client.post('/actions/vision-process', data=data)
    client.post('/actions/vision-process', data=data)
    assert len(store.list_shots()) == 1
    assert process(client, key).status_code == 303
    assert len(store.list_shots()) == 1
    assert client.get(first.location).status_code == 200


def test_session_required_and_stale_session_form_rejected(setup):
    app, client, _ = setup
    assert 'Start a practice session before creating images' in client.get('/vision').text
    response = client.post('/actions/vision-create', data={'token': token(client), 'case': 'clean'})
    assert response.status_code == 400
    sid = client.post('/api/sessions').json['session']['id']
    client.post(f'/api/sessions/{sid}/end')
    client.post('/api/sessions')
    assert generate(client, sid).status_code == 400
    assert app.extensions['shot_store'].list_captures() == []


def test_missing_images_show_failure_without_substituting_fake_preview(setup):
    app, client, _ = setup
    sid = client.post('/api/sessions').json['session']['id']
    generate(client, sid)
    store = app.extensions['shot_store']
    record = store.list_captures()[0]
    key = record['capture_id']
    form_token = token(client, key)
    (store.images_root / record['image_b_path']).unlink()
    page = client.get('/vision?capture_id=' + key)
    assert 'Saved image pair unavailable' in page.text
    assert 'No replacement images have been generated' in page.text
    assert client.get(f'/vision/captures/{key}/images/a').status_code == 404
    response = client.post('/actions/vision-process', data={'token': form_token, 'capture_id': key})
    assert response.status_code == 422
    assert key in response.text
    assert store.get_capture(key)['status'] == 'failed'
    assert store.list_shots() == []


def test_page_and_media_reject_physical_records_and_path_overrides(setup):
    app, client, _ = setup
    store = app.extensions['shot_store']
    key = 'a' * 32
    store.create_capture({'capture_id': key, 'source': 'live', 'status': 'completed'})
    assert client.get('/vision?capture_id=' + key).status_code == 409
    assert client.get(f'/vision/captures/{key}/images/a').status_code == 404
    assert client.get(f'/vision/captures/{key}/overlay').status_code == 404
    assert process(client, key).status_code == 409
    assert store.get_capture(key)['source'] == 'live'
    assert client.get('/vision/captures/invalid/images/a').status_code == 404
    sid = client.post('/api/sessions').json['session']['id']
    generate(client, sid)
    record = store.list_captures()[0]
    store.update_capture(record['capture_id'], image_a_path='vision-ui.db')
    assert client.get(f'/vision/captures/{record["capture_id"]}/images/a').status_code == 404


def test_restart_refresh_and_media_do_not_rerun_vision(setup):
    app, client, config = setup
    sid = client.post('/api/sessions').json['session']['id']
    created = generate(client, sid)
    store = app.extensions['shot_store']
    key = store.list_captures()[0]['capture_id']
    process(client, key)
    shot = store.get_capture_shot(key)
    restarted = create_app(config)
    with patch('fairway_fusion.services.capture_processing_service.analyze_image_pair') as analyze:
        second = restarted.test_client()
        assert 'Open Shot Analysis' in second.get(created.location).text
        assert second.get(f'/vision/captures/{key}/images/a').status_code == 200
        assert second.get(f'/vision/captures/{key}/overlay').status_code == 200
    analyze.assert_not_called()
    assert restarted.extensions['shot_store'].get_capture_shot(key) == shot


def test_unexpected_processing_failure_stays_on_capture_page(setup):
    app, client, _ = setup
    sid = client.post('/api/sessions').json['session']['id']
    generate(client, sid)
    key = app.extensions['shot_store'].list_captures()[0]['capture_id']
    with patch('fairway_fusion.services.capture_processing_service.analyze_image_pair',
               side_effect=RuntimeError('Synthetic processing test failure')):
        response = process(client, key)
    assert response.status_code == 500
    assert 'Synthetic processing test failure' in response.text
    assert key in response.text
    assert 'Retry processing' in response.text
    assert app.extensions['shot_store'].list_shots() == []
