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

import hmac
import html
import json
import os
import re
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, Response)
from starlette.middleware.base import BaseHTTPMiddleware

from . import auth
from . import storage_contract
from .api_proxy import router as api_proxy_router

APP_DIR = Path(__file__).resolve().parent
LOGIN_HTML = APP_DIR / "static" / "login.html"

# 页面与脚本每次都要校验，避免浏览器继续用旧版页面
NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_EXACT = {"/login", "/login/review", "/healthz", "/favicon.ico"}
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

# 这些路径即使请求 HTML 也只回 401，不跳转（接入指南、技术文档、OpenAPI）
API_LIKE_PREFIX = ("/openapi/v1/", "/api/", "/frontend-modules/", "/docs", "/api-docs", "/redoc", "/openapi.json")


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


def _safe_next(raw: str | None) -> str:
    """只允许站内相对路径，杜绝开放重定向。"""
    nxt = (raw or "").strip().replace("\n", "").replace("\r", "")
    if not nxt.startswith("/") or nxt.startswith("//") or "\\" in nxt:
        return ""
    if nxt.startswith("/login"):
        return ""
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


def render_login(status: int = 200, error: str = "", next_path: str = "",
                 username: str = "", retry_after: int = 0) -> HTMLResponse:
    page = LOGIN_HTML.read_text(encoding="utf-8")
    page = (page
            .replace("{{ERR_HIDDEN}}", "" if error else " hidden")
            .replace("{{ERR_TEXT}}", html.escape(error))
            .replace("{{NEXT}}", html.escape(next_path))
            .replace("{{USER_VALUE}}", html.escape(username))
            .replace("{{AUTOFOCUS_USER}}", "" if username else "autofocus")
            .replace("{{AUTOFOCUS_PW}}", "autofocus" if username else ""))
    headers = {"Cache-Control": "no-store", "Pragma": "no-cache",
               "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff",
               "Referrer-Policy": "same-origin"}
    if retry_after:
        headers["Retry-After"] = str(retry_after)
    return HTMLResponse(page, status_code=status, headers=headers)


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
        if path in PUBLIC_EXACT:
            return await call_next(request)

        token = request.cookies.get(auth.COOKIE_NAME, "")
        role = auth.session_role(token) if token else ""
        if role:
            request.state.role = role
            if request.method not in SAFE_METHODS and not _same_origin(request):
                return JSONResponse({"detail": "跨站请求已拒绝"}, status_code=403,
                                    headers={"Cache-Control": "no-store"})
            response = await call_next(request)
            if not path.startswith("/api/") and path not in {"/docs", "/openapi.json", "/redoc"}:
                response.headers.setdefault("X-Frame-Options", "DENY")
                response.headers.setdefault("Referrer-Policy", "same-origin")
            return response

        # 未登录：接口、图片文件、静态资源、文档一律 401；页面请求 302 到登录页
        is_api_like = path.startswith(API_LIKE_PREFIX) or path.startswith("/static/")
        if is_api_like and path.startswith("/openapi/v1/"):
            return JSONResponse({"code": "UNAUTHORIZED", "message": "需要图库授权登录", "details": {}},
                                status_code=401, headers={"Cache-Control": "no-store"})
        if is_api_like:
            return JSONResponse({"detail": "需要登录"}, status_code=401,
                                headers={"Cache-Control": "no-store"})
        # 记住完整目标（含查询串，例如 ?image=xxx 的分享深链）
        nxt = path + (f"?{request.url.query}" if request.url.query else "")
        target = "/login" + (f"?next={quote(nxt)}" if nxt != "/" else "")
        return RedirectResponse(target, status_code=302,
                                headers={"Cache-Control": "no-store"})


# /docs 与 /openapi.json 均由下面的自定义路由提供：
# openapi_url 必须开启，否则 Swagger UI 取不到 Schema；docs_url 保持 None，
# 用自托管静态资源替代 FastAPI 默认的 CDN 版本。
app = FastAPI(title="商品图片资产库", version="1.2.1-uni",
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionAuthMiddleware)
app.include_router(api_proxy_router)
auth.init_store()


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


@app.get("/login")
def login_page(request: Request, next: str = ""):
    token = request.cookies.get(auth.COOKIE_NAME, "")
    if token and auth.session_valid(token):
        return RedirectResponse(_safe_next(next) or "/", status_code=303)
    if not auth.hash_configured():
        return render_login(status=503, error="服务尚未完成初始化，请联系管理员。",
                            next_path=_safe_next(next))
    return render_login(next_path=_safe_next(next))


@app.post("/login")
async def login_submit(request: Request):
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    next_path = _safe_next(str(form.get("next", "")))
    ip = _client_ip(request)

    # 闸门：正常情况下直接返回 0（放行）；硬闸期间只限制"每隔几秒校验一次"，
    # 全局限速（分布式撞库熔断）期间才完全不校验——见 auth.gate_decision 的说明。
    wait = auth.gate_decision(ip)
    if wait:
        return render_login(status=429, next_path=next_path, username=username,
                            error=f"尝试过于频繁，请 {wait} 秒后再试。", retry_after=wait)

    # hmac.compare_digest 不支持非 ASCII str，这里按 UTF-8 字节比较，仍是恒定时间
    user_ok = hmac.compare_digest(username.encode("utf-8"), auth.LOGIN_USER.encode("utf-8"))
    pass_ok = auth.verify_password(password, auth.PASSWORD_HASH)
    if user_ok and pass_ok:
        # 重新生成会话标识：先撤销浏览器带来的旧会话，避免会话固定
        auth.revoke_session(request.cookies.get(auth.COOKIE_NAME, ""))
        token = auth.create_session(ip, request.headers.get("user-agent", ""))
        auth.clear_failures(ip)      # 正确登录即清零，不受他人错误尝试影响
        resp = RedirectResponse(next_path or "/", status_code=303)
        resp.set_cookie(
            key=auth.COOKIE_NAME, value=token,
            max_age=auth.SESSION_TTL, path="/",
            httponly=True, secure=auth.COOKIE_SECURE, samesite="lax")
        resp.headers["Cache-Control"] = "no-store"
        return resp

    auth.record_failure(ip)
    delay = auth.soft_delay(ip)
    if delay:
        # 软限速：口令确实错了，只是让这次重试多等十几秒，而不是锁死十几分钟
        return render_login(status=429, next_path=next_path, username=username,
                            error=f"密码错误次数较多，请 {delay} 秒后重试。", retry_after=delay)
    return render_login(status=401, error="账号或密码不正确，请重新输入。",
                        next_path=next_path, username=username)


REVIEW_LOGIN_HTML = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>图库 · 复核登录</title><meta name="viewport" content="width=device-width,initial-scale=1">
<style>body{font-family:system-ui;background:#151a21;color:#e8eef6;display:grid;place-items:center;height:100vh;margin:0}
form{background:#1c232c;padding:28px 32px;border-radius:12px;min-width:300px}
h1{font-size:16px;margin:0 0 16px;font-weight:600}label{display:block;font-size:12px;color:#9fb0c4;margin:10px 0 4px}
input{width:100%;padding:9px 10px;border-radius:8px;border:1px solid #33414f;background:#0f141a;color:#e8eef6}
button{margin-top:16px;width:100%;padding:10px;border:0;border-radius:8px;background:#3b82f6;color:#fff;font-weight:600}
p{font-size:12px;color:#8b9bb0;margin-top:12px}</style></head><body>
<form method="post" action="/login/review">
<h1>复核视图登录</h1>
<label>复核账号</label><input name="username" autocomplete="username">
<label>复核口令</label><input name="password" type="password" autocomplete="current-password">
<input type="hidden" name="next" value="{next}">
<button type="submit">进入复核视图</button>
<p>复核视图可查看待确认（B）与已停用（X）素材，仅限授权人员。</p>
</form></body></html>"""


@app.get("/login/review", include_in_schema=False)
def review_login_page(request: Request, next: str = ""):
    if not auth.reviewer_configured():
        return HTMLResponse("<h1>复核入口未启用</h1>", status_code=503)
    token = request.cookies.get(auth.COOKIE_NAME, "")
    if auth.session_role(token) == auth.ROLE_REVIEW:
        return RedirectResponse(_safe_next(next) or "/", status_code=303)
    return HTMLResponse(REVIEW_LOGIN_HTML.replace("{next}", html.escape(_safe_next(next) or "")))


@app.post("/login/review", include_in_schema=False)
async def review_login_submit(request: Request):
    if not auth.reviewer_configured():
        return HTMLResponse("<h1>复核入口未启用</h1>", status_code=503)
    form = await request.form()
    username = str(form.get("username", "")).strip()
    password = str(form.get("password", ""))
    next_path = _safe_next(str(form.get("next", "")))
    ip = _client_ip(request)
    wait = auth.gate_decision(ip)
    if wait:
        return HTMLResponse(f"<p>尝试过于频繁，请 {wait} 秒后再试。</p>", status_code=429)
    user_ok = hmac.compare_digest(username.encode("utf-8"),
                                  auth.REVIEWER_USER.encode("utf-8"))
    pass_ok = auth.verify_password(password, auth.REVIEWER_HASH)
    if user_ok and pass_ok:
        auth.revoke_session(request.cookies.get(auth.COOKIE_NAME, ""))
        token = auth.create_session(ip, request.headers.get("user-agent", ""),
                                    role=auth.ROLE_REVIEW)
        auth.clear_failures(ip)
        resp = RedirectResponse(next_path or "/?view=review", status_code=303)
        resp.set_cookie(key=auth.COOKIE_NAME, value=token, max_age=auth.SESSION_TTL,
                        path="/", httponly=True, secure=auth.COOKIE_SECURE, samesite="lax")
        resp.headers["Cache-Control"] = "no-store"
        return resp
    auth.record_failure(ip)
    return HTMLResponse("<p>复核账号或口令不正确。<a href='/login/review'>重试</a></p>",
                        status_code=401)


@app.post("/logout")
def logout(request: Request):
    auth.revoke_session(request.cookies.get(auth.COOKIE_NAME, ""))
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie(auth.COOKIE_NAME, path="/")
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
