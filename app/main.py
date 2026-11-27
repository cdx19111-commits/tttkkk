"""商品图片资产库服务（V0.3.4：登录页 + 服务端 Session）。

对外能力不变：
- 页面：/ 图片资产库（/assets 兼容跳转到 /）、/login 登录页
- 接口：/api/v1/images* 系列，由 assets_api.py 提供

认证（V0.3.4 起）：
- 全部业务资源（页面 / API / 图片文件 / 下载 / 接口文档）默认拒绝，
  未登录的页面请求 302 到 /login，接口与文件请求返回 401。
- 仅 /login、/healthz、/favicon.ico 公开；/healthz 不返回任何敏感信息。
- 旧 HTTP Basic Auth 已移除，改为服务端 Session + HttpOnly/Secure/SameSite=Lax Cookie。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (FileResponse, JSONResponse,
                               RedirectResponse, Response)
from starlette.middleware.base import BaseHTTPMiddleware

from . import api_keys, storage_contract
from .api_proxy import router as api_proxy_router

APP_DIR = Path(__file__).resolve().parent

# 页面与脚本每次都要校验，避免浏览器继续用旧版页面
NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
STATIC_ALLOW = {"app.css", "app.js", "guide.css", "guide.js", "model_params.json"}
FRONTEND_MODULE_ROOT = Path(os.getenv("FRONTEND_MODULES_DIR", APP_DIR.parent / "frontend-modules"))
MODULE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
MODULE_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
MODULE_FILES = {"index.html": "text/html; charset=utf-8", "module.js": "text/javascript; charset=utf-8", "module.css": "text/css; charset=utf-8"}


def _module_current_dir(module_id: str):
    if not MODULE_ID_RE.fullmatch(module_id):
        return None
    root = FRONTEND_MODULE_ROOT.resolve()
    module_dir = (root / module_id).resolve()
    try:
        module_dir.relative_to(root)
    except ValueError:
        return None
    current = root / module_id / "current"
    if not current.exists():
        return None
    current_dir = current.resolve()
    try:
        current_dir.relative_to(module_dir)
    except ValueError:
        return None
    try:
        metadata = json.loads((current_dir / "module.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(metadata, dict) or metadata.get("id") != module_id:
        return None
    version = str(metadata.get("version", ""))
    if not MODULE_VERSION_RE.fullmatch(version) or not isinstance(metadata.get("order", 100), int):
        return None
    if any(not isinstance(metadata.get(key), str) or len(metadata[key]) > 120 for key in ("title", "subtitle", "nav_label", "icon")):
        return None
    return current_dir, metadata


def _client_ip(request: Request) -> str:
    """取真实客户端 IP。

    优先 X-Real-IP（本机 nginx 用 $remote_addr 覆盖写入，客户端无法伪造）；
    退回 X-Forwarded-For 的**最后一跳**——nginx 用 $proxy_add_x_forwarded_for 追加，
    首段可能被客户端伪造，取最后一段才是本机反代写入的真实地址。
    """
    real = request.headers.get("x-real-ip", "").strip()
    if real:
        return real[:64]
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[-1].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def _safe_target(raw: str | None) -> str:
    """只允许站内相对路径，避免旧登录链接造成开放重定向。"""
    nxt = (raw or "").strip().replace("\n", "").replace("\r", "")
    if not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt or nxt.startswith("/login"):
        return "/"
    return nxt


def _same_origin(request: Request) -> bool:
    """写操作的 Origin/Referer 校验；无 Origin 的服务端调用（curl）放行。"""
    host = (request.headers.get("host") or "").lower()
    origin = request.headers.get("origin")
    if origin:
        return urlsplit(origin).netloc.lower() == host
    referer = request.headers.get("referer")
    if referer:
        return urlsplit(referer).netloc.lower() == host
    return True


class SessionAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        # 0) fail-closed 守卫：库未完成质量分层迁移时，除健康检查外一律 503。
        #    这一层在鉴权之前，避免"能登录就等于能看到图"。
        if path != "/healthz":
            ok, _msg = storage_contract.quality_schema_ok()
            if not ok:
                return JSONResponse(
                    {"detail": "图片服务已进入安全保护：数据库未完成质量分层迁移",
                     "code": "SCHEMA_GUARD"},
                    status_code=503, headers={"Cache-Control": "no-store"})
        # 2026-11-27 起入口账号密码鉴权取消：所有请求按复核（review）权限放行。
        # 保留 fail-closed 的 schema 守卫，以及写操作的同源校验（防浏览器跨站误触）。
        request.state.role = "review"
        # 对外接口（/openapi/*）只认 X-API-Key；页面自用的 /api/* 不受影响。
        denied = api_keys.check(request)
        if denied is not None:
            return denied
        if request.method not in SAFE_METHODS and not _same_origin(request):
            return JSONResponse({"detail": "跨站请求已拒绝"}, status_code=403,
                                headers={"Cache-Control": "no-store"})
        response = await call_next(request)
        if not path.startswith("/api/") and path not in {"/docs", "/openapi.json", "/redoc"}:
            response.headers.setdefault("X-Frame-Options", "DENY")
            response.headers.setdefault("Referrer-Policy", "same-origin")
        return response


# /docs 与 /openapi.json 均由下面的自定义路由提供：
# openapi_url 必须开启，否则 Swagger UI 取不到 Schema；docs_url 保持 None，
# 用自托管静态资源替代 FastAPI 默认的 CDN 版本。
app = FastAPI(title="商品图片资产库", version="1.2.1-uni",
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionAuthMiddleware)
app.include_router(api_proxy_router)


@app.on_event("startup")
async def start_api_proxy_client():
    import httpx
    app.state.api_proxy_client = httpx.AsyncClient(
        timeout=httpx.Timeout(60.0, connect=5.0), trust_env=False,
        limits=httpx.Limits(max_connections=100, max_keepalive_connections=20))


@app.on_event("shutdown")
async def stop_api_proxy_client():
    client = getattr(app.state, "api_proxy_client", None)
    if client is not None:
        await client.aclose()




@app.get("/docs", include_in_schema=False)
def api_guide():
    """接口文档作为独立路由模块发布；缺模块时回退兼容指南。"""
    current = _module_current_dir("docs")
    if current is not None:
        current_dir, metadata = current
        if metadata.get("surface") == "route":
            return RedirectResponse(f"/frontend-modules/docs/{metadata['version']}/index.html", status_code=302, headers=NO_CACHE)
    return FileResponse(APP_DIR / "static" / "guide.html",
                        media_type="text/html", headers=NO_CACHE)


@app.get("/api-docs", include_in_schema=False)
def api_docs():
    """原生 Swagger UI（技术参考 / 在线试调）。"""
    current = _module_current_dir("docs")
    if current is None:
        raise HTTPException(503, "接口文档模块尚未发布")
    return RedirectResponse(f"/frontend-modules/docs/{current[1]['version']}/index.html#swagger", status_code=302, headers=NO_CACHE)


@app.get("/openapi.json", include_in_schema=False)
async def aggregate_openapi(request: Request):
    """Preserve the public contract while merging schemas from private API modules."""
    schema = app.openapi()
    role = getattr(request.state, "role", "business")
    client = getattr(app.state, "api_proxy_client", None)
    if client is None:
        import httpx
        client = httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=5.0), trust_env=False)
        owned_client = True
    else:
        owned_client = False
    try:
        for service in ("http://white-gallery-api:8000", "http://coverage-api:8000",
                        "http://governance-api:8000", "http://douyin-api:8000",
                        "http://integration-api:8000", "http://main-image-api:8000"):
            try:
                response = await client.get(f"{service}/openapi.json",
                                            headers={"x-gallery-role": role})
                response.raise_for_status()
                module_schema = response.json()
            except Exception:
                return JSONResponse({"detail": "API 模块文档暂不可用"}, status_code=503,
                                    headers={"Cache-Control": "no-store"})
            schema.setdefault("paths", {}).update(module_schema.get("paths", {}))
            components = module_schema.get("components", {}).get("schemas", {})
            schema.setdefault("components", {}).setdefault("schemas", {}).update(components)
            known_tags = {tag.get("name") for tag in schema.get("tags", [])}
            schema.setdefault("tags", []).extend(
                tag for tag in module_schema.get("tags", []) if tag.get("name") not in known_tags)
        return JSONResponse(schema, headers={"Cache-Control": "no-cache, must-revalidate"})
    finally:
        if owned_client:
            await client.aclose()


@app.get("/healthz")
def healthz():
    """健康检查：不暴露版本、账号、数据规模等任何敏感信息。

    V1.0 加固：库未完成质量分层迁移时返回 503，让编排层/监控直接判定为不健康，
    而不是"容器 healthy 但把 B/X 一起放出去"。
    """
    ok, msg = storage_contract.quality_schema_ok()
    if not ok:
        return JSONResponse({"ok": False, "guard": "schema", "reason": msg},
                            status_code=503)
    return {"ok": True}


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.get("/login", include_in_schema=False)
def login_redirect(request: Request, next: str = ""):
    """入口鉴权已取消，旧登录链接直接回首页。"""
    return RedirectResponse(_safe_target(next), status_code=303)


@app.post("/login", include_in_schema=False)
async def login_submit_redirect(request: Request):
    return RedirectResponse("/", status_code=303)


@app.get("/login/review", include_in_schema=False)
def review_login_redirect(request: Request, next: str = ""):
    return RedirectResponse(_safe_target(next), status_code=303)


@app.post("/login/review", include_in_schema=False)
async def review_login_submit_redirect(request: Request):
    return RedirectResponse("/", status_code=303)


@app.post("/logout")
def logout():
    """入口鉴权已取消，退出登录仅回首页。"""
    resp = RedirectResponse("/", status_code=303)
    resp.headers["Cache-Control"] = "no-store"
    return resp


@app.get("/")
def root():
    """站点主入口：正式图库系统。"""
    return FileResponse(APP_DIR / "static" / "index.html", media_type="text/html", headers=NO_CACHE)


@app.get("/assets")
def legacy_assets_entry():
    """兼容旧链接：/assets 统一回到首页，不再保留第二套页面。"""
    return RedirectResponse(url="/", status_code=302)


# Swagger UI 资源本地化（随镜像分发，不走公网 CDN）；
# 白名单只放两个文件，且同样在 Session 认证之后才可访问。
SWAGGER_ASSETS = {
    "swagger-ui-bundle.js": "application/javascript; charset=utf-8",
    "swagger-ui.css": "text/css; charset=utf-8",
}


@app.get("/static/swagger/{filename}")
def swagger_asset(filename: str):
    mime = SWAGGER_ASSETS.get(filename)
    if not mime:
        raise HTTPException(404, "Not found")
    return FileResponse(APP_DIR / "static" / "swagger" / filename,
                        media_type=mime, headers=NO_CACHE)


@app.get("/static/{filename}")
def static_file(filename: str):
    if filename not in STATIC_ALLOW:
        raise HTTPException(404, "Not found")
    return FileResponse(APP_DIR / "static" / filename, headers=NO_CACHE)


@app.get("/frontend-modules/manifest.json")
def frontend_module_manifest():
    modules = []
    if FRONTEND_MODULE_ROOT.is_dir():
        for module_dir in sorted(FRONTEND_MODULE_ROOT.iterdir(), key=lambda item: item.name):
            current = _module_current_dir(module_dir.name)
            if current is None:
                continue
            current_dir, metadata = current
            if metadata.get("surface") == "route":
                continue
            if not all((current_dir / filename).is_file() for filename in MODULE_FILES):
                continue
            module_id, version = module_dir.name, metadata["version"]
            base = f"/frontend-modules/{module_id}/{version}"
            modules.append({"id": module_id, "title": metadata["title"], "subtitle": metadata["subtitle"],
                "nav_label": metadata["nav_label"], "icon": metadata["icon"], "order": int(metadata.get("order", 100)),
                "html_url": f"{base}/index.html", "script_url": f"{base}/module.js", "style_url": f"{base}/module.css"})
    modules.sort(key=lambda item: (item["order"], item["id"]))
    return JSONResponse({"modules": modules}, headers=NO_CACHE)


@app.get("/frontend-modules/{module_id}/{version}/{filename}")
def frontend_module_asset(module_id: str, version: str, filename: str):
    if not MODULE_ID_RE.fullmatch(module_id) or not MODULE_VERSION_RE.fullmatch(version) or filename not in MODULE_FILES:
        raise HTTPException(404, "Not found")
    root = FRONTEND_MODULE_ROOT.resolve()
    module_root = (root / module_id).resolve()
    target = root / module_id / "versions" / version / filename
    try:
        module_root.relative_to(root)
        resolved = target.resolve(strict=True)
        resolved.relative_to(module_root)
    except (OSError, ValueError):
        raise HTTPException(404, "Not found")
    try:
        metadata = json.loads((resolved.parent / "module.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise HTTPException(404, "Not found")
    if not isinstance(metadata, dict) or metadata.get("id") != module_id or metadata.get("version") != version or not resolved.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(resolved, media_type=MODULE_FILES[filename],
        headers={"Cache-Control": "private, max-age=31536000, immutable", "X-Content-Type-Options": "nosniff"})
