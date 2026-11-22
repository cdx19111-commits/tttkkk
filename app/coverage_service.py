from fastapi import FastAPI
from fastapi.responses import JSONResponse

from .coverage_monitor import _phone_rows, _pool, router
from .service_auth import GatewayRoleMiddleware

app = FastAPI(title="Coverage Monitor API", docs_url=None, redoc_url=None, openapi_url="/openapi.json")
app.add_middleware(GatewayRoleMiddleware)
app.include_router(router)


@app.get("/healthz", include_in_schema=False)
def healthz():
    try:
        _pool()
        _phone_rows()
        return {"ok": True}
    except Exception as exc:
        return JSONResponse({"ok": False, "reason": str(exc)}, status_code=503)
