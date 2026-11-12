import json
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from fairway_fusion.hardware.pico_client import PicoClient, PicoError
from fairway_fusion.services.capture_service import CaptureService
from fairway_fusion.storage import ShotStore
from fairway_fusion.web.app import create_app
from test_pico_usb import FakeSerial


class CaptureFirmware(FakeSerial):
    def __init__(self):
        super().__init__()
        self.key = None
        self.state = 'idle'
        self.boot = 1
        self.commands = []
        self.overrides = {}
        self.fail_command = None
        self.fail_mode = None

    def reboot(self):
        self.boot += 1
        self.key = None
        self.state = 'idle'

    def write(self, data):
        version, request_id, command, *args = data.decode('ascii').split()
        self.commands.append(command)
        if command == self.fail_command:
            if self.fail_mode == 'unplug':
                raise OSError('USB unplugged during recovery')
            if self.fail_mode == 'timeout':
                return len(data)
        result = 'OK'
        if command in self.overrides:
            result, payload = self.overrides[command]
        elif command == 'HELLO':
            payload = PicoClient.CAPTURE_FIRMWARE
        elif command == 'PING':
            payload = 'PONG'
        elif command == 'CONFIG':
            self.key, self.state = args[0], 'configured'
            payload = 'CONFIGURED'
        elif command == 'ARM':
            self.state = 'armed'
            payload = 'ARMED'
        elif command == 'DISARM':
            if self.state in {'configured', 'armed', 'capturing'}:
                self.state = 'cancelled'
            payload = 'DISARMED'
        elif command == 'STATUS':
            payload = self.state
        elif command == 'REPORT':
            if args[0] != self.key:
                result, payload = 'ERR', 'UNKNOWN_CAPTURE'
            else:
                payload = json.dumps({'capture_id': self.key, 'status': self.state,
                    'firmware_version': PicoClient.CAPTURE_FIRMWARE,
                    'boot_id': f'{self.boot:016x}', 'error_code': 'none'}, separators=(',', ':'))
        self.buffer.extend(f'{version} {request_id} {result} {payload}\n'.encode('ascii'))
        return len(data)


@pytest.fixture
def setup(tmp_path):
    device = CaptureFirmware()

    def open_device(**kwargs):
        device.closed = False
        device.buffer.clear()
        return device

    pico = PicoClient('FAKE', timeout=.005, serial_factory=open_device)
    pico.connect()
    store = ShotStore(tmp_path / 'recovery.db')
    store.start_session()
    service = CaptureService(pico, store.captures)
    yield service, pico, device, store
    pico.close()


def arm(setup):
    service, _, _, store = setup
    return service.arm(session_id=store.active_session()['id'])['capture_id']


def test_reboot_recovery_preserves_evidence_and_allows_next_capture(setup):
    service, _, device, store = setup
    key = arm(setup)
    before = service.poll(key)
    device.reboot()
    with pytest.raises(PicoError, match='UNKNOWN_CAPTURE'):
        service.poll(key)
    assert service.active_capture_id == key
    result = service.recover(key)
    assert device.commands[-3:] == ['DISARM', 'STATUS', 'REPORT']
    assert result['status'] == 'interrupted'
    assert 'DISARMED acknowledged and STATUS=idle verified' in result['error']
    assert result['session_id'] == before['session_id']
    assert result['timing'] == before['timing']
    assert result['pico_report'] == before['pico_report']
    assert service.active_capture_id is None
    commands = list(device.commands)
    assert service.recover(key) == result
    assert device.commands == commands
    assert arm(setup) != key
    assert ShotStore(store.path).get_capture(key)['status'] == 'interrupted'


def test_cancel_recovers_unknown_capture_instead_of_getting_stuck(setup):
    service, _, device, _ = setup
    key = arm(setup)
    device.reboot()
    assert service.cancel(key)['status'] == 'interrupted'
    assert service.active_capture_id is None
    assert arm(setup) != key


@pytest.mark.parametrize('state', ['armed', 'completed', 'cancelled', 'timed_out', 'failed'])
def test_known_capture_preserves_its_terminal_outcome(setup, state):
    service, _, device, _ = setup
    key = arm(setup)
    device.state = state
    result = service.recover(key)
    assert result['status'] == ('cancelled' if state == 'armed' else state)
    assert result['pico_report']['capture_id'] == key
    assert service.active_capture_id is None


def test_changed_boot_cannot_create_a_false_completed_result(setup):
    service, _, device, _ = setup
    key = arm(setup)
    before = service.poll(key)
    device.boot += 1
    device.state = 'completed'
    result = service.recover(key)
    assert result['status'] == 'interrupted'
    assert 'boot changed' in result['error']
    assert result['pico_report'] == before['pico_report']


@pytest.mark.parametrize('command', ['DISARM', 'STATUS', 'REPORT'])
@pytest.mark.parametrize('mode', ['timeout', 'unplug'])
def test_transport_failure_during_recovery_never_releases_capture(setup, command, mode):
    service, pico, device, store = setup
    key = arm(setup)
    device.reboot()
    device.fail_command, device.fail_mode = command, mode
    with pytest.raises(PicoError):
        service.recover(key)
    assert service.active_capture_id == key
    assert store.get_capture(key)['status'] == 'failed'
    device.fail_command = None
    pico.connect()
    assert service.recover(key)['status'] == 'interrupted'


@pytest.mark.parametrize('command,result,payload', [
    ('DISARM', 'OK', 'WRONG_ACK'), ('DISARM', 'ERR', 'BUSY'),
    ('STATUS', 'OK', 'armed'), ('STATUS', 'OK', 'configured'),
    ('STATUS', 'OK', 'capturing'), ('STATUS', 'OK', 'garbage'),
    ('REPORT', 'ERR', 'BAD_STATE'),
])
def test_unverified_controller_does_not_clear_reservation(setup, command, result, payload):
    service, _, device, _ = setup
    key = arm(setup)
    device.reboot()
    device.overrides[command] = (result, payload)
    with pytest.raises(PicoError):
        service.recover(key)
    assert service.active_capture_id == key


def test_recovery_requires_reconnection(setup):
    service, pico, _, _ = setup
    key = arm(setup)
    pico.close()
    with pytest.raises(PicoError, match='Reconnect'):
        service.recover(key)
    assert service.active_capture_id == key
    pico.connect()
    assert service.recover(key)['status'] == 'cancelled'


def test_database_failure_keeps_reservation_until_interruption_is_saved(setup, monkeypatch):
    service, _, device, store = setup
    key = arm(setup)
    device.reboot()
    with monkeypatch.context() as m:
        m.setattr(service.store, 'update', lambda *args, **kwargs: (_ for _ in ()).throw(OSError('Database unavailable')))
        with pytest.raises(OSError):
            service.recover(key)
    assert service.active_capture_id == key
    assert store.get_capture(key)['status'] == 'armed'
    assert service.recover(key)['status'] == 'interrupted'


def test_usb_commands_cannot_interleave_recovery_steps(setup, monkeypatch):
    service, pico, device, _ = setup
    key = arm(setup)
    device.reboot()
    entered, release, ping_started = Event(), Event(), Event()
    original = pico._exchange

    def exchange(command, **kwargs):
        result = original(command, **kwargs)
        if command == 'DISARM':
            entered.set()
            assert release.wait(5)
        return result

    def ping():
        ping_started.set()
        return pico.command('PING')

    monkeypatch.setattr(pico, '_exchange', exchange)
    with ThreadPoolExecutor(max_workers=2) as pool:
        recovery = pool.submit(service.recover, key)
        try:
            assert entered.wait(5)
            pending = pool.submit(ping)
            assert ping_started.wait(5)
            assert not pending.done()
        finally:
            release.set()
        assert recovery.result(timeout=5)['status'] == 'interrupted'
        assert pending.result(timeout=5) == 'PONG'
    assert device.commands[-4:] == ['DISARM', 'STATUS', 'REPORT', 'PING']


def app_client(setup):
    _, pico, device, store = setup
    app = create_app({'TESTING': True, 'DATABASE_PATH': store.path, 'PICO_CLIENT': pico})
    return app, app.test_client(), device, store


def test_api_lost_report_has_recovery_link_and_preserves_session(setup):
    app, client, device, store = app_client(setup)
    key = client.post('/api/captures/arm', json={}).json['capture']['capture_id']
    device.reboot()
    response = client.get(f'/api/captures/{key}')
    assert response.status_code == 409
    assert response.json['recovery_url'] == f'/api/captures/{key}/recover'
    assert client.post('/api/sessions/1/end').status_code == 409
    recovered = client.post(response.json['recovery_url'])
    assert recovered.status_code == 200
    assert recovered.json['capture']['status'] == 'interrupted'
    assert recovered.json['capture']['session_id'] == 1
    assert recovered.json['active_capture_id'] is None
    assert client.post('/api/captures/arm', json={}).status_code == 201
    commands = list(device.commands)
    assert client.post(f'/api/captures/{key}/recover').status_code == 409
    assert device.commands == commands
    assert store.get_capture(key)['status'] == 'interrupted'


def test_api_disconnect_requires_reconnect_before_recovery(setup):
    app, client, device, _ = app_client(setup)
    key = client.post('/api/captures/arm', json={}).json['capture']['capture_id']
    app.extensions['pico'].close()
    device.reboot()
    assert client.post(f'/api/captures/{key}/recover').status_code == 503
    assert app.extensions['capture_service'].active_capture_id == key
    assert client.post('/api/pico/connect').status_code == 200
    assert client.post(f'/api/captures/{key}/recover').json['capture']['status'] == 'interrupted'
    assert client.post('/api/sessions/1/end').status_code == 200


def test_api_cancel_recovers_unknown_record_automatically(setup):
    _, client, device, _ = app_client(setup)
    key = client.post('/api/captures/arm', json={}).json['capture']['capture_id']
    device.reboot()
    response = client.post(f'/api/captures/{key}/cancel')
    assert response.status_code == 200
    assert response.json['capture']['status'] == 'interrupted'


def test_devices_form_recovers_with_single_use_token(setup):
    app, client, device, _ = app_client(setup)
    key = client.post('/api/captures/arm', json={}).json['capture']['capture_id']
    device.reboot()
    page = client.get('/devices')
    assert 'Recover capture' in page.text
    assert '<script' not in page.text
    token = re.search(r'name="token" value="([^"]+)"', page.text)[1]
    data = {'token': token, 'capture_id': key}
    assert client.post('/actions/recover-capture', data={'capture_id': key}).status_code == 303
    assert app.extensions['capture_service'].active_capture_id == key
    result = client.post('/actions/recover-capture', data=data, follow_redirects=True)
    assert result.status_code == 200
    assert f'Capture {key}: interrupted' in result.text
    assert 'Recover capture' not in result.text
    commands = list(device.commands)
    client.post('/actions/recover-capture', data=data)
    assert device.commands == commands


def test_invalid_recovery_requests_do_not_touch_hardware(setup):
    _, client, device, _ = app_client(setup)
    commands = list(device.commands)
    assert client.post('/api/captures/invalid/recover').status_code == 400
    assert client.post('/api/captures/' + 'a' * 32 + '/recover').status_code == 404
    assert client.post('/api/captures/' + 'a' * 32 + '/recover', json={'force': True}).status_code == 400
    assert client.post('/api/captures/' + 'a' * 32 + '/recover', json=[]).status_code == 400
    assert device.commands == commands


def test_report_status_mismatch_keeps_reservation(setup):
    service, _, device, _ = setup
    key = arm(setup)
    device.overrides['REPORT'] = ('OK', json.dumps({
        'capture_id': key, 'status': 'completed', 'firmware_version': PicoClient.CAPTURE_FIRMWARE,
    }, separators=(',', ':')))
    with pytest.raises(PicoError, match='disagree'):
        service.recover(key)
    assert service.active_capture_id == key
