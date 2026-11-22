"""Independently released internal listing contract, behind the existing session gateway."""
import uuid
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from . import listing_api
from .service_auth import GatewayRoleMiddleware

app = FastAPI(title='图库业务对接 API', version='1.0.0', docs_url=None, redoc_url=None)
app.add_middleware(GatewayRoleMiddleware)
app.include_router(listing_api.router)

@app.middleware('http')
async def request_id(request: Request, call_next):
    request.state.request_id = uuid.uuid4().hex
    response = await call_next(request)
    response.headers['X-Request-ID'] = request.state.request_id
    return response

@app.exception_handler(listing_api.ListingAPIError)
async def listing_error(request: Request, exc: listing_api.ListingAPIError):
    return JSONResponse({'code':exc.code,'message':exc.message,
                         'request_id':getattr(request.state,'request_id',''),'details':exc.details},
                        status_code=exc.status_code,headers={'Cache-Control':'no-store'})

@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    details = [{'location':[str(x) for x in e.get('loc',[])], 'message':e.get('msg','参数无效'),
                'type':e.get('type','value_error')} for e in exc.errors()]
    return JSONResponse({'code':'INVALID_ARGUMENT','message':'请求参数无效',
                         'request_id':getattr(request.state,'request_id',''),'details':{'errors':details}},
                        status_code=400,headers={'Cache-Control':'no-store'})

@app.get('/openapi/v1/schema.json', include_in_schema=False)
def contract_schema():
    return app.openapi()

@app.get('/healthz', include_in_schema=False)
def healthz():
    try:
        listing_api._gaia_pool()
        with listing_api.assets_api.db() as c:
            tables={r[0] for r in c.execute("select name from sqlite_master where type='table'")}
            if not {'image_assets','douyin_gallery_items'} <= tables: raise ValueError('Missing read stores')
        return {'ok':True}
    except Exception:
        return JSONResponse({'ok':False,'reason':'Listing read stores unavailable'},status_code=503)
