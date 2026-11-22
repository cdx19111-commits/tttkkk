from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.requests import Request as StarletteRequest
import asyncio

from app import api_proxy, main
from app.white_gallery_service import app as white_app
from app.coverage_service import app as coverage_app


class FakeUpstream:
    status_code = 206
    headers = {"content-type": "image/webp", "cache-control": "private, no-cache",
               "content-range": "bytes 0-3/4", "content-length": "4"}

    async def aiter_raw(self):
        yield b"webp"

    async def aclose(self):
        pass


class FakeClient:
    def __init__(self, *args, **kwargs):
        pass

    def build_request(self, method, url, headers):
        self.request = (method, url, headers)
        return self.request

    async def send(self, request, stream=False):
        self.request = request
        return FakeUpstream()

    async def aclose(self):
        pass


def test_gateway_proxy_preserves_image_stream_and_forwards_verified_role(monkeypatch):
    fake = FakeClient()
    monkeypatch.setattr(api_proxy.httpx, "AsyncClient", lambda **kwargs: fake)
    app = FastAPI()

    @app.middleware("http")
    async def verified_session(request: Request, call_next):
        request.state.role = "business"
        return await call_next(request)

    app.include_router(api_proxy.router)
    with TestClient(app) as client:
        response = client.get("/api/v1/images/img-1/file?w=320",
                              headers={"X-Gallery-Role": "review", "Cookie": "session=not-forwarded"})
    assert response.status_code == 206
    assert response.content == b"webp"
    assert response.headers["content-range"] == "bytes 0-3/4"
    assert fake.request[1] == "http://white-gallery-api:8000/api/v1/images/img-1/file?w=320"
    assert fake.request[2]["x-gallery-role"] == "business"
    assert "cookie" not in fake.request[2]


def test_private_services_reject_requests_without_gateway_role():
    for service in (white_app, coverage_app):
        with TestClient(service) as client:
            response = client.get("/api/v1/me" if service is white_app else "/api/v1/asset-pools")
        assert response.status_code == 401


def test_gateway_proxy_does_not_capture_unrelated_api_routes():
    app = FastAPI()
    app.include_router(api_proxy.router)
    with TestClient(app) as client:
        response = client.get("/api/v1/business-review")
    assert response.status_code == 404


def test_openapi_aggregates_contracts_from_both_private_modules(monkeypatch):
    class SchemaResponse:
        def __init__(self, schema):
            self.schema = schema

        def raise_for_status(self):
            pass

        def json(self):
            return self.schema

    class SchemaClient:
        async def get(self, url, headers):
            if "white-gallery-api" in url:
                return SchemaResponse({"paths": {"/api/v1/images": {}},
                                       "components": {"schemas": {"Image": {"type": "object"}}},
                                       "tags": [{"name": "images"}]})
            return SchemaResponse({"paths": {"/api/v1/coverage-monitor": {}},
                                   "components": {"schemas": {"Coverage": {"type": "object"}}},
                                   "tags": [{"name": "coverage-monitor"}]})

    monkeypatch.setattr(main.app.state, "api_proxy_client", SchemaClient(), raising=False)
    scope = {"type": "http", "method": "GET", "path": "/openapi.json",
             "headers": [], "query_string": b"", "server": ("test", 80),
             "client": ("127.0.0.1", 1), "scheme": "http"}
    request = StarletteRequest(scope)
    request.state.role = "business"
    response = asyncio.run(main.aggregate_openapi(request))
    import json
    schema = json.loads(response.body)
    assert "/api/v1/images" in schema["paths"]
    assert "/api/v1/coverage-monitor" in schema["paths"]
    assert "Image" in schema["components"]["schemas"]
    assert "Coverage" in schema["components"]["schemas"]
