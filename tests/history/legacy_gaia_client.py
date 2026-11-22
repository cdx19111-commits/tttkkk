import json
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[3] / "tools"
sys.path.insert(0, str(TOOLS))

from gaia.client import GaiaClient
from gaia.config import GaiaConfig
import gaia.client as client_module


class FakeHeaders:
    def get_content_charset(self):
        return "utf-8"


class FakeResponse:
    status = 200
    headers = FakeHeaders()
    def __init__(self, payload): self.payload = json.dumps(payload).encode()
    def read(self): return self.payload
    def __enter__(self): return self
    def __exit__(self, *args): return False


class FakeOpener:
    def __init__(self, responses): self.responses = list(responses); self.calls = 0
    def open(self, *args, **kwargs):
        self.calls += 1
        return FakeResponse(self.responses.pop(0))


def test_gateway_transient_backend_status_is_retried(monkeypatch):
    monkeypatch.setattr(client_module.time, "sleep", lambda _seconds: None)
    opener = FakeOpener([
        {"code": 200, "success": True, "backendResponse": {"status": 503, "content": []}},
        {"code": 200, "success": True, "backendResponse": {"status": 200, "content": [{"ok": True}]}},
    ])
    config = GaiaConfig(api_name="unit", auth_token="unit", user="unit@example.com", min_interval=0)
    client = GaiaClient(config, opener=opener)
    assert client.fetch_page(0, page_size=1) == [{"ok": True}]
    assert opener.calls == 2

