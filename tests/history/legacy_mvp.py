"""本地测试：只访问隔离的临时数据库和合成数据。"""
import os
import io
from PIL import Image
import tempfile
from pathlib import Path

tmp = tempfile.TemporaryDirectory()
os.environ["DATA_DIR"] = tmp.name
os.environ["DATABASE_URL"] = f"sqlite:///{Path(tmp.name) / 'test.db'}"
os.environ["GALLERY_USER"] = "tester"
os.environ["GALLERY_PASSWORD"] = "test-long-password-not-for-production"
os.environ["DEMO_SEED"] = "true"

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402


def test_minimal_flow():
    with TestClient(app) as client:
        auth = ("tester", "test-long-password-not-for-production")
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/v1/models").status_code == 401
        assert client.get("/api/v1/stats", auth=auth).json()["assets"] == 6
        r = client.get("/api/v1/lookup", params={"name":"demophonex","channel":"抖音"}, auth=auth)
        assert r.status_code == 200
        match = r.json()
        assert match["matched"] == 1
        assert len(match["candidates"][0]["assets"]) == 1
        m = client.post("/api/v1/models", auth=auth, json={"category":"手机","brand":"测试", "model_name":"Test Phone 1", "aliases":["testphone1"]})
        assert m.status_code == 201
        assert client.get("/api/v1/lookup", auth=auth, params={"name":"testphone1"}).json()["matched"] == 1
        asset_id = match["candidates"][0]["assets"][0]["id"]
        assert client.get(f"/api/v1/assets/{asset_id}/file", auth=auth).status_code == 200
        assert client.patch(f"/api/v1/assets/{asset_id}", auth=auth, json={"status":"已停用"}).status_code == 200
        assert len(client.get("/api/v1/lookup", auth=auth, params={"name":"demophonex","channel":"抖音"}).json()["candidates"][0]["assets"]) == 0
        image = io.BytesIO()
        Image.new("RGB", (40, 50), "#ABCDEF").save(image, format="PNG")
        u = client.post("/api/v1/assets", auth=auth, data={"model_id":m.json()["id"],"channel":"抖音","asset_type":"主图"},
                        files={"file":("demo.jpg",image.getvalue(),"image/jpeg")})
        assert u.status_code == 201, u.text
        assert u.json()["mime_type"] == "image/png"  # 不信任扩展名或上传 MIME
        assert u.json()["width"] == 40
        assert u.json()["status"] == "待审核"
        assert client.patch(f"/api/v1/assets/{u.json()['id']}", auth=auth, json={"status":"已审核"}).status_code == 200
        looked_up = client.get("/api/v1/lookup", auth=auth, params={"name":"testphone1","channel":"抖音"}).json()
        assert len(looked_up["candidates"][0]["assets"]) == 1
        blocked = client.post("/api/v1/models",auth=auth,headers={"Origin":"https://malicious.example"},json={"category":"a","brand":"b","model_name":"c"})
        assert blocked.status_code == 403
