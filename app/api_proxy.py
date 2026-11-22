"""Authenticated same-origin proxy from the stable gallery gateway to private APIs."""
from __future__ import annotations

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

router = APIRouter()
WHITE_SERVICE = "http://white-gallery-api:8000"
COVERAGE_SERVICE = "http://coverage-api:8000"
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
              "te", "trailers", "transfer-encoding", "upgrade", "host"}


async def _proxy(request: Request, base: str):
    role = getattr(request.state, "role", "")
    if role not in {"business", "review"}:
        raise HTTPException(401, "需要登录")
    path = request.url.path
    if base == WHITE_SERVICE:
        allowed = (
            path in {"/api/v1/images", "/api/v1/images-facets", "/api/v1/brand-tree",
                     "/api/v1/images-overview", "/api/v1/me", "/api/v1/catalog"}
            or path.startswith("/api/v1/images/")
            or path.startswith("/api/v1/coverage/")
        )
    else:
        allowed = path in {"/api/v1/coverage-monitor", "/api/v1/asset-pools",
                           "/api/v1/coverage-upload-context"}
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
        upstream_request = client.build_request(request.method, upstream_url, headers=headers)
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
                             name=f"proxy_{'white' if base == WHITE_SERVICE else 'coverage'}_{index}",
                             include_in_schema=False)


_register(("/api/v1/images", "/api/v1/images-facets", "/api/v1/brand-tree",
           "/api/v1/images-overview", "/api/v1/me", "/api/v1/catalog",
           "/api/v1/images/{image_id}", "/api/v1/images/{image_id}/file",
           "/api/v1/images/{image_id}/download", "/api/v1/coverage/{model_id}"), WHITE_SERVICE)
_register(("/api/v1/coverage-monitor", "/api/v1/asset-pools",
           "/api/v1/coverage-upload-context"), COVERAGE_SERVICE)
