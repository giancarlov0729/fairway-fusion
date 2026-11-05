from html.parser import HTMLParser

from fairway_fusion.web.app import create_app


class Assets(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'script':
            self.urls.append(attrs['src'])
        if tag == 'link' and attrs.get('rel') == 'stylesheet':
            self.urls.append(attrs['href'])


def test_ui_and_assets_are_served(tmp_path):
    client = create_app({'TESTING': True, 'DATABASE_PATH': tmp_path / 'shots.db'}).test_client()
    response = client.get('/')
    assert response.status_code == 200
    assert response.mimetype == 'text/html'
    parser = Assets()
    parser.feed(response.get_data(as_text=True))
    assert set(parser.urls) == {'/static/app.js', '/static/api.js', '/static/styles.css'}
    for url in parser.urls:
        asset = client.get(url)
        assert asset.status_code == 200
        assert len(asset.data) > 100


def test_ui_history_survives_server_restart(tmp_path):
    config = {'TESTING': True, 'DATABASE_PATH': tmp_path / 'shots.db'}
    client = create_app(config).test_client()
    session = client.post('/api/sessions').json['session']
    shot = client.post('/api/shots/simulate', json={
        'session_id': session['id'], 'scenario': 'right-launch',
    }).json['shot']
    client = create_app(config).test_client()
    active = client.get('/api/sessions/active').json['session']
    assert active['id'] == session['id']
    assert active['shot_count'] == 1
    assert client.post(f"/api/sessions/{session['id']}/end").status_code == 200
    client = create_app(config).test_client()
    assert client.get('/api/sessions/active').json['session'] is None
    assert client.get('/api/sessions').json['sessions'][0]['shot_count'] == 1
    assert client.get(f"/api/shots?session_id={session['id']}").json['shots'][0]['id'] == shot['id']
    saved = client.get(f"/api/shots/{shot['id']}").json['shot']
    assert saved['launch_direction_deg'] < 0
    assert len(saved['samples']['timestamps_s']) == 6
