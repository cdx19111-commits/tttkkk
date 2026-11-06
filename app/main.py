"""商品图片资产库服务（V1.0，单一主产品）。

对外只提供两类能力：
- 页面：/ 图片资产库（/assets 兼容跳转到 /）
- 接口：/api/v1/images* 系列，由 assets_api.py 提供

旧版演示工作台（商品型号 CRUD、演示图上传、lookup、stats 等）已于
V1.0 收尾阶段移除：业务代码零引用、访问日志无第三方调用、与图片资产
接口无依赖关系。历史演示数据库文件仍保留在磁盘上，仅不再被读写。
"""
from __future__ import annotations

import hmac
import os
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .assets_api import router as images_router

APP_DIR = Path(__file__).resolve().parent
ADMIN_USER = os.getenv("GALLERY_USER", "admin")
ADMIN_PASSWORD = os.getenv("GALLERY_PASSWORD", "")

# 页面与脚本每次都要校验，避免浏览器继续用旧版演示页面的缓存
NO_CACHE = {"Cache-Control": "no-cache, must-revalidate"}


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        # 生产环境必须显式设置密码；不可使用代码中的弱默认值
        if not ADMIN_PASSWORD or ADMIN_PASSWORD == "CHANGE_ME_TO_A_LONG_RANDOM_PASSWORD":
            return JSONResponse({"detail": "请先在 .env 配置 GALLERY_PASSWORD"}, status_code=503)
        from base64 import b64decode
        auth = request.headers.get("authorization", "")
        passed = False
        if auth.lower().startswith("basic "):
            try:
                decoded = b64decode(auth.split(" ", 1)[1], validate=True).decode("utf-8")
                username, password = decoded.split(":", 1)
                passed = hmac.compare_digest(username, ADMIN_USER) and hmac.compare_digest(password, ADMIN_PASSWORD)
            except (ValueError, UnicodeDecodeError):
                pass
        # 防止浏览器携带 Basic Auth 跨站提交修改请求；不限制 curl 等无 Origin 的服务端调用。
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc.lower() != request.headers.get("host", "").lower():
                return JSONResponse({"detail": "跨站写操作已拒绝"}, status_code=403)
        if not passed:
            return JSONResponse({"detail": "需要登录"}, status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="Gallery MVP"', "Cache-Control": "no-store"})
        return await call_next(request)


app = FastAPI(title="商品图片资产库", version="1.0.0")
app.add_middleware(AuthMiddleware)
app.include_router(images_router)


@app.get("/healthz")
def healthz():
    return {"ok": True}


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
    if filename not in {"app.css", "app.js"}:
        raise HTTPException(404, "Not found")
    return FileResponse(APP_DIR / "static" / filename, headers=NO_CACHE)
