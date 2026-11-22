import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from app import api_proxy, main, business_review, douyin_gallery, listing_api
from app.governance_service import app as governance_app
from app.douyin_service import app as douyin_app
from app.integration_service import app as integration_app
from app import governance_service, douyin_service


@pytest.mark.parametrize('service,path', [
    (governance_app,'/api/v1/pool-management/summary'),
    (douyin_app,'/api/v1/douyin-gallery/summary'),
    (integration_app,'/openapi/v1/listing/models'),
    (integration_app,'/openapi/v1/schema.json'),
])
def test_new_services_are_private_and_fail_closed(service,path):
    response=TestClient(service).get(path)
    assert response.status_code==401
    assert response.headers['content-type'].startswith('application/json')


def test_gateway_no_longer_registers_business_implementations_or_runs_migrations(monkeypatch):
    def forbidden(): raise AssertionError('Startup must not migrate business data')
    monkeypatch.setattr(business_review,'init_schema',forbidden)
    monkeypatch.setattr(douyin_gallery,'init_schema',forbidden)
    for route in main.app.routes:
        endpoint=getattr(route,'endpoint',None)
        assert getattr(endpoint,'__module__','') not in {'app.business_review','app.pool_management','app.douyin_gallery'}
    with TestClient(main.app): pass


def test_new_readiness_checks_only_read_existing_storage(tmp_path, monkeypatch):
    db=tmp_path/'gallery.db'
    with sqlite3.connect(db) as c:
        for name in ('image_assets','business_review_batches','business_review_items','business_review_events','douyin_gallery_items','douyin_gallery_folders'):
            c.execute(f'create table {name}(id text)')
    monkeypatch.setattr(governance_service,'ASSET_DB',db)
    monkeypatch.setattr(douyin_service,'ASSET_DB',db)
    before=db.read_bytes()
    assert TestClient(governance_app).get('/healthz').status_code==200
    assert TestClient(douyin_app).get('/healthz').status_code==200
    assert db.read_bytes()==before


@pytest.mark.parametrize('path,body',[
    ('/api/v1/business-review/uploads?model_id=42&color=黑色',b'raw-image-bytes'),
    ('/api/v1/pool-management/assets/a/pool',b'{"pool":"B","reason":"test"}'),
    ('/openapi/v1/listing/future-capability',b'{"items":[]}'),
])
def test_gateway_forwards_post_body_verified_role_and_future_namespaces(monkeypatch,path,body):
    captured={}
    class Upstream:
        status_code=200
        headers={'content-type':'application/json'}
        async def aiter_raw(self): yield b'{"ok":true}'
        async def aclose(self): pass
    class Client:
        def build_request(self,method,url,headers,content=None):
            captured.update(method=method,url=url,headers=headers,stream=content)
            return None
        async def send(self,request,stream=False):
            captured['body']=b''.join([chunk async for chunk in captured['stream']])
            return Upstream()
        async def aclose(self): pass
    monkeypatch.setattr(api_proxy.httpx,'AsyncClient',lambda **kwargs:Client())
    app=FastAPI()
    @app.middleware('http')
    async def authenticated(request:Request,call_next):
        request.state.role='business';return await call_next(request)
    app.include_router(api_proxy.router)
    response=TestClient(app).post(path,content=body,headers={'content-type':'application/octet-stream','cookie':'secret','x-gallery-role':'review'})
    assert response.status_code==200,response.text
    assert captured['body']==body
    assert captured['headers']['x-gallery-role']=='business'
    assert not any(x in captured['headers'] for x in ('cookie','Cookie','authorization'))
    assert captured['url'].endswith(path.replace('黑色','%E9%BB%91%E8%89%B2')) or 'color=' in captured['url']


def test_integration_contract_is_separate_and_validation_is_json():
    client=TestClient(integration_app,headers={'x-gallery-role':'business'})
    schema=client.get('/openapi/v1/schema.json').json()
    assert set(schema['paths'])=={'/openapi/v1/listing/models','/openapi/v1/listing/resolve','/openapi/v1/listing/precheck','/openapi/v1/listing/images/{image_id}/file'}
    bad=client.post('/openapi/v1/listing/resolve',json={})
    assert bad.status_code==400 and bad.json()['code']=='INVALID_ARGUMENT'
    assert bad.json()['request_id']
    assert client.delete('/openapi/v1/listing/models').status_code==405


def test_listing_download_preserves_bytes_etag_head_and_304(tmp_path,monkeypatch):
    content=b'original-image-bytes'
    path=tmp_path/'x.jpg';path.write_bytes(content)
    digest=hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(listing_api,'_download_row',lambda *_:({'name':'x.jpg','format':'JPEG'},path,digest,'white_library'))
    c=TestClient(integration_app,headers={'x-gallery-role':'business'})
    url='/openapi/v1/listing/images/example/file'
    response=c.get(url)
    assert response.content==content
    assert response.headers['etag']=='"'+digest+'"'
    assert int(response.headers['content-length'])==len(content)
    head=c.head(url);assert head.content==b'' and int(head.headers['content-length'])==len(content)
    assert c.get(url,headers={'if-none-match':'"'+digest+'"'}).status_code==304


def test_gateway_docs_is_only_a_redirect_to_versioned_frontend(monkeypatch,tmp_path):
    module=tmp_path/'docs/versions/9.0';module.mkdir(parents=True)
    (module/'module.json').write_text(json.dumps({'id':'docs','version':'9.0','title':'docs','subtitle':'','nav_label':'','icon':'','surface':'route'}))
    (tmp_path/'docs/current').symlink_to('versions/9.0')
    monkeypatch.setattr(main,'FRONTEND_MODULE_ROOT',tmp_path)
    assert main.api_docs().headers['location']=='/frontend-modules/docs/9.0/index.html#swagger'
    docs=Path(__file__).parents[1]/'frontend-modules/docs/versions/1.1.0'
    assert '/openapi/v1/schema.json' in (docs/'index.html').read_text()
    assert 'fetch(choice.value' in (docs/'module.js').read_text()
    assert 'SwaggerUIBundle' in (docs/'module.js').read_text()
    assert 'swagger-ui' in (docs/'index.html').read_text()
