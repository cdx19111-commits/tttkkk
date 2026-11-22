"""Read-only Douyin directory/image API. Import and migrations stay outside HTTP startup."""
import sqlite3
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from . import douyin_gallery
from .storage_contract import ASSET_DB
from .service_auth import GatewayRoleMiddleware

app = FastAPI(title='Douyin Gallery API', docs_url=None, redoc_url=None)
app.add_middleware(GatewayRoleMiddleware)
app.include_router(douyin_gallery.router)

@app.get('/healthz', include_in_schema=False)
def healthz():
    try:
        with sqlite3.connect(f'file:{ASSET_DB}?mode=ro&immutable=1', uri=True) as c:
            tables = {r[0] for r in c.execute("select name from sqlite_master where type='table'")}
            if not {'douyin_gallery_items','douyin_gallery_folders'} <= tables:
                raise ValueError('Existing directory tables missing')
        return {'ok': True}
    except Exception:
        return JSONResponse({'ok':False,'reason':'Existing Douyin storage unavailable'},status_code=503)
