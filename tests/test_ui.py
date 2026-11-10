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
    assert set(parser.urls) == {'/static/styles.css'}
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


def test_html_workflow_without_javascript(tmp_path):
    import re
    config = {'TESTING': True, 'DATABASE_PATH': tmp_path / 'forms.db'}
    app = create_app(config)
    client = app.test_client()
    def token():
        response = client.get('/')
        assert "script-src 'none'" in response.headers['Content-Security-Policy']
        assert '<script' not in response.text
        return re.search(r'name="token" value="([^"]+)"', response.text)[1]
    assert client.post('/actions/start', data={'token': token()}).status_code == 303
    sid = app.extensions['shot_store'].active_session()['id']
    data = {'token': token(), 'session_id': sid, 'scenario': 'right-launch'}
    response = client.post('/actions/simulate', data=data)
    assert response.status_code == 303
    result = client.get(response.headers['Location'])
    assert 'View input samples (6)' in result.text
    assert 'Saved in SQLite' in result.text
    client.post('/actions/simulate', data=data)
    assert len(app.extensions['shot_store'].list_shots()) == 1
    assert client.get('/results').status_code == 200
    assert 'Shot #1' in create_app(config).test_client().get('/results').text
    assert '#1' in client.get('/history?session_id=' + str(sid)).text
    assert client.get('/static/app.js').status_code == 404
    assert client.get('/static/api.js').status_code == 404
    assert client.post('/actions/end', data={'token': token(), 'session_id': sid}).status_code == 303
    assert app.extensions['shot_store'].active_session() is None


def test_form_errors_and_missing_token_do_not_save(tmp_path):
    import re
    app = create_app({'TESTING': True, 'DATABASE_PATH': tmp_path / 'errors.db'})
    c = app.test_client()
    c.post('/actions/start')
    assert app.extensions['shot_store'].active_session() is None
    sid = app.extensions['shot_store'].start_session()['id']
    token = re.search(r'name="token" value="([^"]+)"', c.get('/').text)[1]
    response = c.post('/actions/simulate', data={'token':token, 'session_id':sid, 'scenario':'invalid'})
    assert response.status_code == 400
    assert 'role="alert"' in response.text
    assert not app.extensions['shot_store'].list_shots()


def test_product_pages_and_pico_controls(tmp_path):
    import re
    from fairway_fusion.hardware.pico_client import PicoError
    class Device:
        def connect(self): return 'PICO2W_USB_1'
        def command(self, command):
            if command == 'STATUS': raise PicoError('USB disconnected')
            return 'DISARMED'
        def close(self): pass
    app = create_app({'TESTING':True, 'DATABASE_PATH':tmp_path/'devices.db', 'PICO_CLIENT':Device()})
    c=app.test_client()
    for path in ('/', '/results', '/history', '/devices', '/setup'):
        response=c.get(path)
        assert response.status_code == 200
        assert '<script' not in response.text
        assert 'SIMULATION MODE' in response.text
    def action(name):
        token=re.search(r'name="token" value="([^"]+)"',c.get('/devices').text)[1]
        return c.post('/actions/'+name,data={'token':token},follow_redirects=True)
    assert 'PICO2W_USB_1' in action('connect').text
    assert 'USB disconnected' in action('status').text
    assert 'DISARMED' in action('disarm').text
    assert 'Disconnected' in action('disconnect').text
    assert 'Coming soon' in c.get('/setup').text
