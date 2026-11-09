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
import os
from pathlib import Path
from urllib.parse import quote, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse,
                               RedirectResponse, Response)
from starlette.middleware.base import BaseHTTPMiddleware

from . import auth
from .assets_api import router as images_router

APP_DIR = Path(__file__).resolve().parent
LOGIN_HTML = APP_DIR / "static" / "login.html"

# 页面与脚本每次都要校验，避免浏览器继续用旧版页面
NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
PUBLIC_EXACT = {"/login", "/healthz", "/favicon.ico"}
STATIC_ALLOW = {"app.css", "app.js"}
# 这些路径即使请求 HTML 也只回 401，不跳转（接口文档、OpenAPI）
API_LIKE_PREFIX = ("/api/", "/docs", "/redoc", "/openapi.json")


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
        if path in PUBLIC_EXACT:
            return await call_next(request)

        token = request.cookies.get(auth.COOKIE_NAME, "")
        if token and auth.session_valid(token):
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
        if is_api_like:
            return JSONResponse({"detail": "需要登录"}, status_code=401,
                                headers={"Cache-Control": "no-store"})
        # 记住完整目标（含查询串，例如 ?image=xxx 的分享深链）
        nxt = path + (f"?{request.url.query}" if request.url.query else "")
        target = "/login" + (f"?next={quote(nxt)}" if nxt != "/" else "")
        return RedirectResponse(target, status_code=302,
                                headers={"Cache-Control": "no-store"})


app = FastAPI(title="商品图片资产库", version="0.3.4",
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(SessionAuthMiddleware)
app.include_router(images_router)
auth.init_store()


@app.get("/healthz")
def healthz():
    """健康检查：不暴露版本、账号、数据规模等任何敏感信息。"""
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

    wait = auth.rate_limited(ip)
    if wait:
        return render_login(status=429, next_path=next_path, username=username,
                            error=f"尝试次数过多，请在 {wait} 秒后重试。", retry_after=wait)

    # hmac.compare_digest 不支持非 ASCII str，这里按 UTF-8 字节比较，仍是恒定时间
    user_ok = hmac.compare_digest(username.encode("utf-8"), auth.LOGIN_USER.encode("utf-8"))
    pass_ok = auth.verify_password(password, auth.PASSWORD_HASH)
    if user_ok and pass_ok:
        # 重新生成会话标识：先撤销浏览器带来的旧会话，避免会话固定
        auth.revoke_session(request.cookies.get(auth.COOKIE_NAME, ""))
        token = auth.create_session(ip, request.headers.get("user-agent", ""))
        auth.record_attempt(ip, True)
        resp = RedirectResponse(next_path or "/", status_code=303)
        resp.set_cookie(
            key=auth.COOKIE_NAME, value=token,
            max_age=auth.SESSION_TTL, path="/",
            httponly=True, secure=auth.COOKIE_SECURE, samesite="lax")
        resp.headers["Cache-Control"] = "no-store"
        return resp

    auth.record_attempt(ip, False)
    return render_login(status=401, error="账号或密码不正确，请重新输入。",
                        next_path=next_path, username=username)


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


@app.get("/static/{filename}")
def static_file(filename: str):
    if filename not in STATIC_ALLOW:
        raise HTTPException(404, "Not found")
    return FileResponse(APP_DIR / "static" / filename, headers=NO_CACHE)
