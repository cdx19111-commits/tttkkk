import argparse
from collections import Counter
import csv
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO, TextIOWrapper
import json
import mimetypes
from pathlib import Path
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory, TemporaryFile
import threading
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4
import webbrowser
from zipfile import ZipFile, ZIP_STORED

from openpyxl import load_workbook
from PIL import UnidentifiedImageError

from image_processing import FRONT_AND_BACK, POSSIBLE_PAIR, SINGLE_VIEW, looks_identical, prepare


ROOT = Path(__file__).resolve().parent
WEB = ROOT / "web"
DATA = ROOT / "批次"
ACTIVE = {}
LOCK = threading.RLock()
EGO = shutil.which("ego-browser") or str(Path.home() / ".local/bin/ego-browser")
LIMIT = 12 * 1024 * 1024


class ClientError(Exception):
    pass


@dataclass
class Run:
    stop: threading.Event = field(default_factory=threading.Event)
    space: int | None = None

    def browser(self, script):
        try:
            result = subprocess.run([EGO, "nodejs", "-e", script], capture_output=True, text=True,
                                    check=True, timeout=240)
        except FileNotFoundError:
            raise RuntimeError("找不到 Ego Browser，请检查本机安装") from None
        except subprocess.TimeoutExpired:
            raise RuntimeError("ZOL 处理超时，可重试该型号") from None
        except subprocess.CalledProcessError as error:
            details = (error.stderr or error.stdout or "").strip()
            raise RuntimeError(details[-800:] or "Ego Browser 请求失败") from None
        return result.stdout

    def collect(self, model, folder):
        if self.space is None:
            receipt = folder / "browser-space.json"
            self.browser(f'''
const fs=await import("node:fs/promises");
const task=await taskSpace("白底图工具采集");
await fs.writeFile({json.dumps(str(receipt), ensure_ascii=False)},JSON.stringify(task.spaceId));
''')
            if not receipt.exists():
                raise RuntimeError("无法打开 Ego Browser 采集会话")
            self.space = int(receipt.read_text(encoding="utf8"))
        output = folder / "result.json"
        payload = {"brand":model["brand"], "model":model["model"], "url":model.get("url"), "folder":str(folder)}
        self.browser(f'''
const fs=await import("node:fs/promises");
eval(await fs.readFile({json.dumps(str(ROOT / "zol.js"), ensure_ascii=False)},"utf8"));
const task=await taskSpace({self.space});
const result=await collectZol(task,{json.dumps(payload, ensure_ascii=False)});
await fs.writeFile({json.dumps(str(output), ensure_ascii=False)},JSON.stringify(result));
''')
        return json.loads(output.read_text(encoding="utf8"))

    def finish(self):
        if self.space is not None:
            try:
                self.browser(f"const t=await taskSpace({self.space}); await t.finish({{keep:[]}});")
            except RuntimeError as error:
                print(f"Ego Browser 会话关闭失败 {error}", flush=True)


def read_models(stream):
    workbook = load_workbook(stream, read_only=True, data_only=True, keep_links=False)
    try:
        sheet = workbook["缺图型号"] if "缺图型号" in workbook.sheetnames else workbook.active
        rows = iter(sheet.values)
        headings = [str(value or "").strip() for value in next(rows, [])]
        if "型号" not in headings:
            raise ClientError("表格第一行需要有「型号」列，也可提供「品牌」列")
        brand_col = headings.index("品牌") if "品牌" in headings else None
        model_col = headings.index("型号")
        unique = {}
        for row in rows:
            model = str(row[model_col] or "").strip() if len(row) > model_col else ""
            brand = str(row[brand_col] or "").strip() if brand_col is not None and len(row) > brand_col else ""
            if model:
                url = valid_url(model) if model.startswith(("http://", "https://")) else None
                unique.setdefault((brand.casefold(), model.casefold()), {"brand":brand, "model":model, "url":url})
        return list(unique.values())
    finally:
        workbook.close()


def valid_url(value):
    parsed = urlsplit(value.strip())
    if parsed.scheme not in ("https", "http") or parsed.netloc != "detail.zol.com.cn" or not (
        re.fullmatch(r"/series/\d+/(?:\d+_\d+\.html|\d+/pic_[\d_]+\.html)", parsed.path)
        or re.fullmatch(r"/\d+/\d+/pic(?:_\d+_\d+)?\.shtml", parsed.path)
        or re.fullmatch(r"/cell_phone/index\d+\.shtml", parsed.path)
    ):
        raise ClientError("请使用 detail.zol.com.cn 的手机产品或图库链接")
    return parsed._replace(scheme="https", fragment="", query="").geturl()


def read_paste(text):
    items = {}
    for line in text.splitlines():
        brand, _, model = line.strip().rpartition("\t")
        if model and (brand, model) not in items:
            url = valid_url(model) if model.startswith(("http://", "https://")) else None
            items[brand, model] = {"brand":brand.strip(), "model":model.strip(), "url":url}
    return list(items.values())


def sampled(items, limit):
    if not limit or limit >= len(items):
        return items
    if limit == 1:
        return items[:1]
    return [items[round(i*(len(items)-1)/(limit-1))] for i in range(limit)]


def batch_file(batch):
    if not re.fullmatch("[a-f0-9]{12}", batch):
        raise ClientError("无效批次")
    return DATA / batch / "state.json"


def load(batch):
    path = batch_file(batch)
    if not path.exists():
        raise ClientError("批次不存在")
    return json.loads(path.read_text(encoding="utf8"))


def save(state):
    path = batch_file(state["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False), encoding="utf8")
    temporary.replace(path)


def create(items, title, sample=0):
    if not items:
        raise ClientError("清单里没有可导入的型号")
    original_total = len(items)
    rows = [dict(item, id=i, status="排队中") for i, item in enumerate(sampled(items, sample), start=1)]
    state = {"id":uuid4().hex[:12], "title":title[:120], "created":datetime.now().isoformat(timespec="seconds"),
             "count":len(rows), "originalTotal":original_total, "rows":rows}
    with LOCK:
        save(state)
    return state["id"]


def process_picture_set(batch, row, data):
    if data["status"] != "已匹配":
        return data
    folder = batch_file(batch).parent / "images" / str(row["id"]) / uuid4().hex[:8]
    folder.mkdir(parents=True, exist_ok=True)

    def prepare_candidate(image, *, require_product=False):
        destination = folder / (image["id"] + ".jpg")
        try:
            result = prepare(Path(image["local"]), destination, require_product=require_product)
        except (OSError, ValueError, UnidentifiedImageError):
            return None
        if not result:
            return None
        metadata, signature = result
        relative = destination.relative_to(batch_file(batch).parent).as_posix()
        return dict(metadata, id=image["id"], source=image["url"], file=relative), signature

    priority = {FRONT_AND_BACK: 0, POSSIBLE_PAIR: 1, SINGLE_VIEW: 2}

    def candidate_rank(item):
        return (priority.get(item["view"], 3), item["review"],
                item["tone"] >= 65 if item["view"] == SINGLE_VIEW else False, -item["pixels"])

    color_counts = Counter(image["id"] for color in data["colors"] for image in color["images"])
    signatures = []
    for color in data["colors"]:
        images = color.pop("images")
        candidates = []
        for image in images:
            if color_counts[image["id"]] > 1:
                continue
            result = prepare_candidate(image)
            if result:
                candidate, signature = result
                for other_color, other, other_signature in signatures:
                    if other_color != color["name"] and looks_identical(signature, other_signature):
                        for item in (candidate, other):
                            item["review"] = True
                            if "多个颜色" not in item["note"]:
                                item["note"] = "；".join(filter(None, (item["note"], "多个颜色的图像近似，请核对颜色")))
                signatures.append((color["name"], candidate, signature))
                candidates.append(candidate)
        color["candidates"] = candidates
        color["hadImages"] = bool(images)
    for color in data["colors"]:
        candidates = color["candidates"]
        candidates.sort(key=candidate_rank)
        color["candidates"] = candidates[:5]
        if len(candidates) > 5 and all(item["review"] for item in color["candidates"]):
            fallback = next((item for item in candidates[5:] if not item["review"]), None)
            if fallback:
                color["candidates"][-1] = fallback
        kept = {item["id"] for item in color["candidates"]}
        for item in candidates:
            if item["id"] not in kept:
                (batch_file(batch).parent / item["file"]).unlink(missing_ok=True)
        recommended = candidates[0] if candidates else None
        color["selected"] = recommended["id"] if recommended and not recommended["review"] else None
        if recommended:
            color["status"] = "待复核" if recommended["review"] else "已选图"
            color["reason"] = recommended["note"]
        else:
            color["status"] = "缺图" if color["hadImages"] or "没有" in color.get("reason", "") else "待复核"
            color["reason"] = color.get("reason") or "没有颜色明确、背景合格的外观大图"
        del color["hadImages"]

    unlabeled = []
    seen = set()
    for image in data.pop("unlabeled", []):
        if image["id"] in seen or color_counts[image["id"]] == 1:
            continue
        seen.add(image["id"])
        result = prepare_candidate(image, require_product=True)
        if result:
            unlabeled.append(result[0])
    for number, candidate in enumerate(sorted(unlabeled, key=candidate_rank), 1):
        # Unknown-color photos are saved as candidates even when the view is unclear.
        needs_review = candidate["watermarkReview"]
        data["colors"].append({"name":"", "key":"image:"+candidate["id"], "number":number,
                               "status":"待复核" if needs_review else "已选图", "reason":candidate["note"],
                               "candidates":[candidate], "selected":None if needs_review else candidate["id"]})
    if not data["colors"] and not data.get("reason"):
        data["reason"] = "图库没有通过白底筛选的外观图"
    data["status"] = model_status(data["colors"])
    return data


def model_status(colors):
    statuses = {color["status"] for color in colors}
    if statuses == {"已选图"}:
        return "已完成"
    if "待复核" in statuses:
        return "待复核"
    if "已选图" in statuses:
        return "部分缺图"
    return "缺图"


def work(batch, runner):
    consecutive_failures = 0
    try:
        while not runner.stop.is_set():
            with LOCK:
                state = load(batch)
                pending = next((row for row in state["rows"] if row["status"] in ("排队中", "处理中")), None)
                if not pending:
                    break
                pending["status"] = "处理中"
                save(state)
            try:
                with TemporaryDirectory(prefix="zol-phone-") as tmp:
                    raw = runner.collect(pending, Path(tmp))
                    result = process_picture_set(batch, pending, raw)
                consecutive_failures = 0
            except Exception as error:
                print(pending["model"], repr(error), flush=True)
                result = {"status":"采集失败", "reason":str(error)}
                consecutive_failures += 1
            with LOCK:
                latest = load(batch)
                row = latest["rows"][pending["id"]-1]
                row.update(result)
                latest["pauseReason"] = "连续三次采集失败，已暂停。检查连接后可重试失败项。" if consecutive_failures >= 3 else ""
                save(latest)
            if consecutive_failures >= 3:
                break
            runner.stop.wait(.35)
    finally:
        runner.finish()
        with LOCK:
            ACTIVE.pop(batch, None)


def start(batch, retry=False):
    with LOCK:
        if ACTIVE:
            raise ClientError("已有一个批次在运行")
        state = load(batch)
        if retry:
            for row in state["rows"]:
                if row["status"] == "采集失败":
                    row.update(status="排队中", reason="")
        if not any(row["status"] in ("排队中", "处理中") for row in state["rows"]):
            raise ClientError("当前批次没有待处理型号")
        state["pauseReason"] = ""
        save(state)
        runner = Run()
        ACTIVE[batch] = runner
        threading.Thread(target=work, args=(batch, runner), daemon=True).start()


def safe_name(name):
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).strip(" .")[:100] or "未命名"


def sheet_value(value):
    value = str(value or "")
    return "'" + value if value.startswith(("=", "+", "-", "@")) else value


def export_zip(state, output):
    listing = BytesIO()
    text = TextIOWrapper(listing, encoding="utf-8-sig", newline="")
    writer = csv.writer(text)
    writer.writerow(("品牌", "型号", "颜色", "状态", "视角", "来源", "文件", "备注"))
    with ZipFile(output, "w", compression=ZIP_STORED) as archive:
        for row in state["rows"]:
            for color in row.get("colors") or [{}]:
                candidates = color.get("candidates", [])
                picture = next((item for item in candidates if item["id"] == color.get("selected")), None)
                pending = picture is None and color.get("status") == "待复核" and bool(candidates)
                if pending:
                    picture = candidates[0]
                name = ""
                if picture:
                    model = row["model"] if not row["model"].startswith("https://") else row.get("name") or row["model"]
                    filename = (f"未标色/{color['number']:02d}.jpg" if "number" in color
                                else f"{safe_name(color['name'])}.jpg")
                    if pending:
                        filename = "待复核/" + filename
                    name = f"{row['id']:04d}_{safe_name(model)}/{filename}"
                    archive.write(batch_file(state["id"]).parent / picture["file"], name)
                writer.writerow(map(sheet_value, (row["brand"], row["model"], color.get("name"),
                    color.get("status", row["status"]), picture and picture["view"],
                    picture["source"] if picture else row.get("galleryUrl"), name,
                    color.get("reason", row.get("reason")))))
        text.flush()
        archive.writestr("采集结果.csv", listing.getvalue())


class Handler(BaseHTTPRequestHandler):
    def send_content(self, data, kind, status=200, headers=()):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        for name, value in headers:
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(data)

    def json(self, data, status=200):
        self.send_content(json.dumps(data, ensure_ascii=False).encode(), "application/json; charset=utf-8", status)

    def check_origin(self):
        host = self.headers.get("Host") or ""
        if host not in (f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"):
            raise ClientError("请通过本机工具地址访问")
        origin = self.headers.get("Origin")
        if origin and origin != "http://" + host:
            raise ClientError("请求来源无效")

    def do_GET(self):
        self.handle_request(self.get)

    def do_POST(self):
        self.handle_request(self.post)

    def handle_request(self, method):
        try:
            self.check_origin()
            url = urlsplit(self.path)
            method(url.path, parse_qs(url.query))
        except ClientError as error:
            self.json({"error":str(error)}, 400)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            print(repr(error), flush=True)
            self.json({"error":"操作失败，请检查文件或本地运行日志"}, 500)

    def get(self, path, query):
        if path in ("/", "/ui.js", "/style.css"):
            file = WEB / ("index.html" if path == "/" else path[1:])
            self.send_content(file.read_bytes(), mimetypes.guess_type(file)[0] + "; charset=utf-8")
            return
        if path == "/api/list":
            with LOCK:
                batches = [load(file.parent.name) for file in DATA.glob("*/state.json")]
            self.json([header(state) for state in sorted(batches, key=lambda s:s["created"], reverse=True)])
            return
        api = re.fullmatch(r"/api/batch/([a-f0-9]{12})(/zip)?", path)
        if api:
            state = load(api[1])
            if api[2]:
                with TemporaryFile() as output:
                    export_zip(state, output)
                    self.send_response(HTTPStatus.OK)
                    self.send_header("Content-Type", "application/zip")
                    self.send_header("Content-Disposition", f'attachment; filename="zol-{state["id"]}.zip"')
                    self.send_header("Content-Length", str(output.tell()))
                    self.end_headers()
                    output.seek(0)
                    shutil.copyfileobj(output, self.wfile)
                return
            status = query.get("status", [""])[0]
            term = query.get("q", [""])[0].casefold()
            rows = [row for row in state["rows"] if (not status or row["status"] == status) and
                    (not term or term in (row["brand"] + " " + row["model"]).casefold())]
            position = max(0, int(query.get("offset", ["0"])[0]))
            self.json(dict(header(state), totals=Counter(row["status"] for row in state["rows"]),
                           rows=rows[position:position+24], filtered=len(rows),
                           pauseReason=state.get("pauseReason","")))
            return
        if re.fullmatch(r"/image/[a-f0-9]{12}/images/\d+/[a-f0-9]{8}/\d+\.jpg", path):
            file = DATA / path.removeprefix("/image/")
            if file.is_file():
                self.send_content(file.read_bytes(), "image/jpeg")
                return
        self.json({"error":"页面或文件不存在"}, 404)

    def post(self, path, query):
        length = int(self.headers.get("Content-Length") or "0")
        if not 0 <= length <= LIMIT:
            raise ClientError("上传文件不能超过 12 MB")
        file_data = self.rfile.read(length)
        if path == "/api/import":
            trial = min(100, max(0, int(query.get("sample", ["0"])[0])))
            if self.headers.get_content_type() == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
                try:
                    models = read_models(BytesIO(file_data))
                except ClientError:
                    raise
                except Exception:
                    raise ClientError("无法读取 Excel，请上传有效的 .xlsx 文件") from None
                title = query.get("name", ["Excel 批次"])[0]
            else:
                body = json.loads(file_data)
                models, title = read_paste(body.get("text","")), "粘贴清单"
            self.json({"id":create(models,title,trial)})
            return
        action = re.fullmatch(r"/api/batch/([a-f0-9]{12})/(start|retry|pause|choose|link)", path)
        if not action:
            self.json({"error":"操作不存在"}, 404)
            return
        batch, command = action.groups()
        if command in ("start", "retry"):
            start(batch, command == "retry")
        elif command == "pause":
            with LOCK:
                if runner := ACTIVE.get(batch):
                    runner.stop.set()
        else:
            body = json.loads(file_data)
            with LOCK:
                state = load(batch)
                row_id = int(body["row"])
                if row_id < 1 or row_id > state["count"]:
                    raise ClientError("型号码无效")
                row = state["rows"][row_id-1]
                if command == "link":
                    if row["status"] in ("处理中", "排队中"):
                        raise ClientError("请等该型号处理结束")
                    row.update(url=valid_url(body["url"]), status="排队中", reason="", choices=[], colors=[])
                else:
                    color = next((entry for entry in row.get("colors", [])
                                  if entry.get("key", entry["name"]) == body["color"]), None)
                    if not color:
                        raise ClientError("找不到该颜色")
                    candidate = next((item for item in color["candidates"] if item["id"] == body["id"]), None)
                    if not candidate:
                        raise ClientError("找不到所选图片")
                    color.update(selected=candidate["id"], status="已选图", reason="已人工确认")
                    row["status"] = model_status(row["colors"])
                save(state)
        self.json({"ok":True})


def header(state):
    runner = ACTIVE.get(state["id"])
    return {key:state[key] for key in ("id", "title", "count", "originalTotal", "created")} | {
        "running":runner is not None,
        "pausing":bool(runner and runner.stop.is_set())}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="本地 ZOL 白底图批量工具")
    parser.add_argument("--port", default=8765, type=int)
    parser.add_argument("--open", action="store_true", help="在默认浏览器打开工具")
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        address = f"http://127.0.0.1:{server.server_port}"
        print(f"白底图工具  {address}", flush=True)
        if args.open:
            threading.Thread(target=webbrowser.open, args=(address,), daemon=True).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            with LOCK:
                for active in ACTIVE.values():
                    active.stop.set()
            print("批次进度已经保存，可重新打开后继续。")
