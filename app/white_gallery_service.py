from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .assets_api import quality_schema_ok, router
from .service_auth import GatewayRoleMiddleware

app = FastAPI(title="White Gallery API", docs_url=None, redoc_url=None, openapi_url="/openapi.json")
app.add_middleware(GatewayRoleMiddleware)
app.include_router(router)


@app.get("/healthz", include_in_schema=False)
def healthz():
    ok, reason = quality_schema_ok()
    if not ok:
        return JSONResponse({"ok": False, "reason": reason}, status_code=503)
    return {"ok": True}
