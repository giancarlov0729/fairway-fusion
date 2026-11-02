import json
import threading
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import pytest
from fairway_fusion.demo.server import DemoHandler


@pytest.fixture(scope="module")
def endpoint():
    server = ThreadingHTTPServer(("127.0.0.1", 0), DemoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()
    thread.join()


def test_real_http_replay_and_static_assets(endpoint):
    with urlopen(endpoint + "/api/fixtures/driver") as response:
        fixture = json.load(response)
    request = Request(
        endpoint + "/api/replay",
        data=json.dumps({"capture": fixture["capture"]}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urlopen(request) as response:
        result = json.load(response)
        assert result["status"] == "accepted"
        assert result["spin"]["rpm"] == pytest.approx(2600, abs=5)
    for path in ("/", "/app.js", "/style.css"):
        with urlopen(endpoint + path) as response:
            assert response.status == 200
            assert response.read()


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"[]",
        b'{"capture": null}',
        b'{"conditions":{"air_density_kg_m3":-1}}',
    ],
)
def test_malformed_http_requests_return_errors(endpoint, payload):
    with pytest.raises(HTTPError) as caught:
        urlopen(
            Request(
                endpoint + "/api/replay",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
        )
    assert caught.value.code == 400
    assert "error" in json.load(caught.value)


def test_static_path_is_not_a_file_server(endpoint):
    with pytest.raises(HTTPError) as caught:
        urlopen(endpoint + "/../../pyproject.toml")
    assert caught.value.code == 404
