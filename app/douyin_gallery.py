"""Authenticated, directory-faithful Douyin main-image sub-library."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse

from .storage_contract import ASSET_DB, ASSET_ROOT

router = APIRouter(prefix="/api/v1/douyin-gallery", tags=["douyin gallery"])
ASSET_DIR = ASSET_ROOT / "douyin_main_images"
ROOT_TOKEN = "X6lXfkJ8Xlkmdode2HVcbmSInrf"
ROOT_ID = "dyf_" + hashlib.sha256(ROOT_TOKEN.encode()).hexdigest()[:24]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    c = (sqlite3.connect(f"file:{ASSET_DB}?mode=ro", uri=True, timeout=30, isolation_level=None)
         if os.getenv("DOUYIN_READ_ONLY") == "1"
         else sqlite3.connect(ASSET_DB, timeout=30, isolation_level=None))
    c.row_factory = sqlite3.Row
    return c


def _active_expr(c: sqlite3.Connection, alias: str = "") -> str:
    """Backward-compatible active-row filter (older DBs have no is_active)."""
    cols = {row[1] for row in c.execute("pragma table_info(douyin_gallery_items)")}
    if "is_active" not in cols:
        return "1=1"
    return f"coalesce({alias + '.' if alias else ''}is_active,1)=1"


def _require_login(request: Request) -> None:
    if getattr(request.state, "role", "") not in ("business", "review"):
        raise HTTPException(401, "需要图库授权登录")


def init_schema() -> None:
    with closing(_conn()) as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS douyin_gallery_folders (
          folder_id TEXT PRIMARY KEY, parent_id TEXT NOT NULL,
          name TEXT NOT NULL, path TEXT NOT NULL,
          source_token TEXT NOT NULL UNIQUE
        );
        CREATE INDEX IF NOT EXISTS idx_douyin_folders_parent
          ON douyin_gallery_folders(parent_id, name);
        CREATE TABLE IF NOT EXISTS douyin_gallery_items (
          item_id TEXT PRIMARY KEY, source_token TEXT NOT NULL UNIQUE,
          folder_id TEXT NOT NULL, source_path TEXT NOT NULL,
          name TEXT NOT NULL, extension TEXT NOT NULL,
          local_path TEXT NOT NULL UNIQUE, sha256 TEXT NOT NULL,
          size_bytes INTEGER NOT NULL, width INTEGER, height INTEGER,
          source_url TEXT NOT NULL DEFAULT '', imported_at TEXT NOT NULL,
          is_active INTEGER NOT NULL DEFAULT 1,
          replaced_by TEXT, archive_batch TEXT, archived_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_douyin_items_folder
          ON douyin_gallery_items(folder_id, name);
        CREATE INDEX IF NOT EXISTS idx_douyin_items_sha
          ON douyin_gallery_items(sha256);
        """)


def import_manifest(manifest_path: str) -> dict:
    """Idempotently import a prepared manifest after originals are on ASSET_DIR."""
    init_schema()
    with open(manifest_path, "r", encoding="utf-8") as f:
        manifest = json.load(f)
    root_name = manifest.get("root_name", "抖音主图")
    folders = manifest.get("folders", [])
    items = manifest.get("items", [])
    now = _now()
    with closing(_conn()) as c:
        c.execute("BEGIN IMMEDIATE")
        c.execute("""INSERT INTO douyin_gallery_folders
          (folder_id,parent_id,name,path,source_token) VALUES(?,?,?,?,?)
          ON CONFLICT(source_token) DO UPDATE SET
          parent_id=excluded.parent_id,name=excluded.name,path=excluded.path""",
          (ROOT_ID, "", root_name, "", ROOT_TOKEN))
        folder_ids = {"": ROOT_ID}
        for folder in folders:
            folder_ids[folder["path"]] = folder["folder_id"]
        for folder in folders:
            parent_id = folder_ids.get(folder["parent_path"], ROOT_ID)
            c.execute("""INSERT INTO douyin_gallery_folders
              (folder_id,parent_id,name,path,source_token) VALUES(?,?,?,?,?)
              ON CONFLICT(source_token) DO UPDATE SET
              parent_id=excluded.parent_id,name=excluded.name,path=excluded.path""",
              (folder["folder_id"], parent_id, folder["name"], folder["path"], folder["source_token"]))
        present = 0
        for item in items:
            path = (ASSET_ROOT / item["local_path"]).resolve()
            if ASSET_ROOT.resolve() not in path.parents or not path.is_file():
                raise RuntimeError("原图未同步或路径越界：" + item["local_path"])
            digest = hashlib.sha256()
            with open(path, "rb") as image:
                for block in iter(lambda: image.read(1024 * 1024), b""):
                    digest.update(block)
            if digest.hexdigest() != item["sha256"]:
                raise RuntimeError("原图校验失败：" + item["source_token"])
            c.execute("""INSERT INTO douyin_gallery_items
              (item_id,source_token,folder_id,source_path,name,extension,local_path,
               sha256,size_bytes,width,height,source_url,imported_at)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(source_token) DO UPDATE SET
               folder_id=excluded.folder_id,source_path=excluded.source_path,
               name=excluded.name,extension=excluded.extension,local_path=excluded.local_path,
               sha256=excluded.sha256,size_bytes=excluded.size_bytes,width=excluded.width,
               height=excluded.height,source_url=excluded.source_url""",
              (item["item_id"], item["source_token"], folder_ids[item["folder_path"]],
               item["source_path"], item["name"], item["extension"], item["local_path"],
               item["sha256"], item["size_bytes"], item.get("width"), item.get("height"),
               item.get("source_url", ""), now))
            present += 1
        c.commit()
    return {"folders": len(folders) + 1, "images": present}


@router.get("/summary")
def summary(request: Request):
    _require_login(request)
    with closing(_conn()) as c:
        active = _active_expr(c)
        image_count, total_bytes = c.execute(
            f"SELECT count(*),coalesce(sum(size_bytes),0) FROM douyin_gallery_items WHERE {active}").fetchone()
        folder_count = c.execute("SELECT count(*) FROM douyin_gallery_folders").fetchone()[0]
    return {"images": image_count, "folders": folder_count, "size_bytes": total_bytes,
            "root_folder_id": ROOT_ID}


@router.get("/folders")
def folders(request: Request, parent_id: str = ROOT_ID):
    _require_login(request)
    with closing(_conn()) as c:
        parent = c.execute("SELECT folder_id FROM douyin_gallery_folders WHERE folder_id=?",
                            (parent_id,)).fetchone()
        if not parent:
            raise HTTPException(404, "目录不存在")
        active = _active_expr(c, "i")
        rows = c.execute(f"""SELECT f.folder_id,f.name,f.path,
          (SELECT count(*) FROM douyin_gallery_items i WHERE {active} AND
             (i.folder_id=f.folder_id OR substr(i.source_path,1,length('抖音主图/'||f.path||'/'))='抖音主图/'||f.path||'/')) image_count,
          (SELECT count(*) FROM douyin_gallery_folders d WHERE d.parent_id=f.folder_id) child_count
          FROM douyin_gallery_folders f WHERE f.parent_id=? ORDER BY f.name COLLATE NOCASE""",
          (parent_id,)).fetchall()
    return {"items": [dict(r) for r in rows]}


@router.get("/folder-tree")
def folder_tree(request: Request):
    """Return the authenticated directory tree and descendant image counts in one request."""
    _require_login(request)
    with closing(_conn()) as c:
        folder_rows = c.execute("""SELECT folder_id,parent_id,name,path
          FROM douyin_gallery_folders ORDER BY name COLLATE NOCASE""").fetchall()
        active = _active_expr(c)
        direct_counts = dict(c.execute(f"""SELECT folder_id,count(*)
          FROM douyin_gallery_items WHERE {active} GROUP BY folder_id""").fetchall())
    nodes = {r["folder_id"]: {"folder_id": r["folder_id"], "parent_id": r["parent_id"],
            "name": r["name"], "path": r["path"], "image_count": int(direct_counts.get(r["folder_id"], 0)),
            "child_count": 0} for r in folder_rows}
    for node in nodes.values():
        if node["parent_id"] in nodes:
            nodes[node["parent_id"]]["child_count"] += 1
    # Paths are imported parent-first; reverse depth order aggregates descendants once.
    for node in sorted(nodes.values(), key=lambda n: n["path"].count("/"), reverse=True):
        parent = nodes.get(node["parent_id"])
        if parent:
            parent["image_count"] += node["image_count"]
    return {"items": [dict(n) for n in nodes.values()], "root_folder_id": ROOT_ID}


@router.get("/items")
def items(request: Request, folder_id: str = ROOT_ID, q: str = "",
          limit: int = Query(48, ge=1, le=100), offset: int = Query(0, ge=0)):
    _require_login(request)
    where = []
    args: list = []
    with closing(_conn()) as c:
        folder = c.execute("SELECT path FROM douyin_gallery_folders WHERE folder_id=?", (folder_id,)).fetchone()
        if not folder:
            raise HTTPException(404, "目录不存在")
        where.append(_active_expr(c))
        if folder_id != ROOT_ID:
            prefix = "抖音主图/" + folder["path"].rstrip("/") + "/"
            where.append("(folder_id=? OR substr(source_path,1,length(?))=?)")
            args.extend([folder_id, prefix, prefix])
        if q.strip():
            where.append("instr(lower(name),lower(?))>0")
            args.append(q.strip())
        clause = " WHERE " + " AND ".join(where) if where else ""
        total = c.execute("SELECT count(*) FROM douyin_gallery_items" + clause, args).fetchone()[0]
        rows = c.execute("""SELECT item_id,name,source_path,extension,size_bytes,width,height,sha256
          FROM douyin_gallery_items""" + clause + " ORDER BY name COLLATE NOCASE LIMIT ? OFFSET ?",
          args + [limit, offset]).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        d["image_url"] = f"/api/v1/douyin-gallery/items/{d['item_id']}/image"
        result.append(d)
    return {"total": total, "limit": limit, "offset": offset, "items": result}


@router.get("/items/{item_id}/image")
def image(item_id: str, request: Request):
    _require_login(request)
    with closing(_conn()) as c:
        active = _active_expr(c)
        row = c.execute(f"SELECT local_path,extension FROM douyin_gallery_items WHERE item_id=? AND {active}",
                         (item_id,)).fetchone()
    if not row:
        raise HTTPException(404, "图片不存在")
    path = (ASSET_ROOT / row["local_path"]).resolve()
    if ASSET_ROOT.resolve() not in path.parents or not path.is_file():
        raise HTTPException(404, "图片文件缺失")
    mime = mimetypes.types_map.get("." + row["extension"].lower(), "application/octet-stream")
    return FileResponse(path, media_type=mime,
                        headers={"Cache-Control": "private, no-cache",
                                 "X-Content-Type-Options": "nosniff"})
