"""Authenticated same-origin proxy from the stable gallery gateway to private APIs."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter()
WHITE_SERVICE = "http://white-gallery-api:8000"
COVERAGE_SERVICE = "http://coverage-api:8000"
GOVERNANCE_SERVICE = "http://governance-api:8000"
DOUYIN_SERVICE = "http://douyin-api:8000"
INTEGRATION_SERVICE = "http://integration-api:8000"
MAIN_IMAGE_SERVICE = "http://main-image-api:8000"
# Explicit contracts only: the gateway never forwards arbitrary paths or methods.
CONTRACTS = {
    GOVERNANCE_SERVICE: {
        "/api/v1/business-review/{rest:path}": {"GET", "HEAD", "POST"},
        "/api/v1/pool-management/{rest:path}": {"GET", "HEAD", "POST"},
    },
    DOUYIN_SERVICE: {"/api/v1/douyin-gallery/{rest:path}": {"GET", "HEAD"}},
    INTEGRATION_SERVICE: {"/openapi/v1/{rest:path}": {"GET", "HEAD", "POST"}},
    MAIN_IMAGE_SERVICE: {"/api/v1/main-images/{rest:path}": {"GET", "HEAD", "POST"}},
}

HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailers", "transfer-encoding", "upgrade", "host"}


async def _proxy(request: Request, base: str):
    role = getattr(request.state, "role", "")
    if role not in {"business", "review"}:
        raise HTTPException(401, "需要登录")
    path = request.url.path
    contract = request.scope.get("route")
    template = getattr(contract, "path", "")
    namespace_allowed = request.method in CONTRACTS.get(base, {}).get(template, set())
    if namespace_allowed:
        allowed = True
    elif base == WHITE_SERVICE:
        allowed = (
            path in {"/api/v1/images", "/api/v1/images-facets", "/api/v1/brand-tree",
                     "/api/v1/images-overview", "/api/v1/me", "/api/v1/catalog"}
            or path.startswith("/api/v1/images/")
            or path.startswith("/api/v1/coverage/")
        )
    elif base == COVERAGE_SERVICE:
        allowed = path in {"/api/v1/coverage-monitor", "/api/v1/asset-pools",
                           "/api/v1/coverage-upload-context"}
    else:
        contract = request.scope.get("route")
        template = getattr(contract, "path", "")
        allowed = request.method in CONTRACTS.get(base, {}).get(template, set())
    if not allowed:
        raise HTTPException(404, "接口不存在")

    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP | {"cookie", "authorization", "x-gallery-role"}}
    headers["x-gallery-role"] = role
    upstream_url = f"{base}{path}"
    if request.url.query:
        upstream_url += f"?{request.url.query}"
    client = getattr(request.app.state, "api_proxy_client", None)
    owned_client = client is None
    if owned_client:
        client = httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0), trust_env=False)
    try:
        kwargs = {"headers": headers}
        if request.method not in {"GET", "HEAD"}:
            kwargs["content"] = request.stream()
        upstream_request = client.build_request(request.method, upstream_url, **kwargs)
        upstream = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        if owned_client:
            await client.aclose()
        raise HTTPException(503, "后端模块暂不可用") from exc

    response_headers = {k: v for k, v in upstream.headers.items() if k.lower() not in HOP_BY_HOP}

    async def stream_body():
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            if owned_client:
                await client.aclose()

    return StreamingResponse(stream_body(), status_code=upstream.status_code,
                             headers=response_headers, media_type=None)


def _register(paths: tuple[str, ...], base: str, methods=("GET", "HEAD")):
    async def endpoint(request: Request):
        return await _proxy(request, base)
    for index, path in enumerate(paths):
        router.add_api_route(path, endpoint, methods=list(methods),
                             name=f"proxy_{base.split('//')[1].split(':')[0]}_{index}",
                             include_in_schema=False)


_register(("/api/v1/images", "/api/v1/images-facets", "/api/v1/brand-tree",
           "/api/v1/images-overview", "/api/v1/me", "/api/v1/catalog",
           "/api/v1/images/{image_id}", "/api/v1/images/{image_id}/file",
           "/api/v1/images/{image_id}/download", "/api/v1/coverage/{model_id}"), WHITE_SERVICE)
_register(("/api/v1/coverage-monitor", "/api/v1/asset-pools",
           "/api/v1/coverage-upload-context"), COVERAGE_SERVICE)

for service, paths in CONTRACTS.items():
    for path, methods in paths.items():
        _register((path,), service, methods=sorted(methods))

# Stable namespaces for future additions without redeploying the gateway.
CONTRACTS[WHITE_SERVICE] = {"/api/v1/white-gallery/{rest:path}": {"GET", "HEAD"}}
CONTRACTS[COVERAGE_SERVICE] = {"/api/v1/coverage-module/{rest:path}": {"GET", "HEAD"}}
_register(("/api/v1/white-gallery/{rest:path}",), WHITE_SERVICE)
_register(("/api/v1/coverage-module/{rest:path}",), COVERAGE_SERVICE)
