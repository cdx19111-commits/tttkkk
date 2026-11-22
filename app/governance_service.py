"""Existing governance routes in their own runtime; no startup database migration."""
import sqlite3
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from . import business_review, pool_management
from .storage_contract import ASSET_DB
from .service_auth import GatewayRoleMiddleware

app = FastAPI(title="Image Governance API", docs_url=None, redoc_url=None)
app.add_middleware(GatewayRoleMiddleware)
app.include_router(business_review.router)
app.include_router(pool_management.router)

@app.get('/healthz', include_in_schema=False)
def healthz():
    try:
        with sqlite3.connect(f'file:{ASSET_DB}?mode=ro&immutable=1', uri=True) as c:
            tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
            if not {'image_assets', 'business_review_batches', 'business_review_items', 'business_review_events'} <= tables:
                raise ValueError('Required existing governance tables missing; migrations are separate')
        return {'ok': True}
    except Exception:
        return JSONResponse({'ok': False, 'reason': 'Existing governance storage unavailable'}, status_code=503)
