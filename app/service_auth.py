"""Authentication boundary shared by private API services behind the gateway."""
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware


class GatewayRoleMiddleware(BaseHTTPMiddleware):
    """Services have no published ports and accept roles only from the private gateway."""
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        role = request.headers.get("x-gallery-role", "")
        if role not in {"business", "review"}:
            return JSONResponse({"detail": "需要网关认证"}, status_code=401,
                                headers={"Cache-Control": "no-store"})
        request.state.role = role
        return await call_next(request)
