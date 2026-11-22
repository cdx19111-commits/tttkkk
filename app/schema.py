"""V2.0 数据库结构：在原 `image_assets` 之上做**纯新增列**升级。

约束
----
1. 原有 29 列名称、类型、含义**一律不改**，保证手机数据零改动、旧代码可读。
2. 新列全部可空，老库可原地 `ALTER TABLE ADD COLUMN` 升级，不重建表。
3. 品类用英文 id 存（phone/tablet/…），中文名只在展示层。
"""
from __future__ import annotations

import sqlite3

# 原 V0.3 结构（保持不变）
_LEGACY_COLUMNS = """
  id text primary key,
  file_token text unique not null,
  filename text not null,
  source_path text not null,
  brand_dir text,
  model_dir text,
  format text,
  width integer,
  height integer,
  size_bytes integer,
  brand_id text, brand_name text,
  model_id text, model_name text,
  color text, standard_color text,
  match_level text, match_method text, match_status text,
  candidates text, note text,
  feishu_created_at text, feishu_modified_at text,
  local_path text,
  created_at text,
  display_brand text, display_filename text, asset_type text, display_color text
"""

# V2.0 新增列（全部可空，含默认值由 backfill 负责）
NEW_COLUMNS: dict[str, str] = {
    "category": "text",           # phone / tablet / watch / laptop / unknown
    "category_source": "text",    # dir=目录声明 / missing=目录缺类目层 / legacy=旧数据回填
    "source_channel": "text",     # feishu_white / douyin / jd
    "shop_id": "text",            # 抖音店铺目录（其他渠道为空）
    "asset_kind": "text",         # white / marketing / detail
    "model_source": "text",       # gaia=标准库 / folder=目录临时索引 / none
    "publish_state": "text",      # published / candidate / restricted
    "sha256": "text",             # 文件内容指纹（用于真实去重）
    "dup_group": "text",          # 同内容分组键（sha256 前 16 位）
    "spec_json": "text",          # 品类专属规格（颜色/内存/表带…）JSON
    "quality_flags": "text",      # 质量标记（low_res / content_review_pending 等，逗号分隔）
    "verify_state": "text",       # 图片身份核验：verified / failed / pending
    "disposition": "text",        # V2.1 治理处置：keep_white / reclassified_detail / human_review_hold / misfiled_hold / frozen_out_of_scope
    # --- V1.7 双配色（非手机）---
    "gaia_color": "text",         # 盖亚参考色：与商品系统一致，用于业务筛选/匹配（缺失即空，不回落到官方色）
    "official_color": "text",     # 品牌官方配色：品牌正式命名，用于展示/查询/人工核实
}

SCHEMA = f"""
create table if not exists image_assets (
{_LEGACY_COLUMNS},
{",".join(f"{k} {v}" for k, v in NEW_COLUMNS.items())}
);
create index if not exists idx_brand on image_assets(brand_name);
create index if not exists idx_model on image_assets(model_name);
create index if not exists idx_color on image_assets(color);
create index if not exists idx_status on image_assets(match_status);
create index if not exists idx_category on image_assets(category);
create index if not exists idx_kind on image_assets(asset_kind);
create index if not exists idx_sha on image_assets(sha256);
create index if not exists idx_source on image_assets(source_channel);
create index if not exists idx_gaia_color on image_assets(gaia_color);
"""

# 老库升级回填：只回填"事实类"字段（这批数据确实是白底手机、确实走盖亚匹配）。
# publish_state 刻意**不回填**：现有生产系统没有行级发布门控，
# 给旧数据填 "published" 等于凭空捏造"已审批发布"，违反发布状态继承原则。
# NULL 在访问层被解释为"沿用现状"（行为与升级前完全一致）。
DEFAULT_BACKFILL = {
    "category": "phone",
    "category_source": "legacy",
    "source_channel": "feishu_white",
    "asset_kind": "white",
    "model_source": "gaia",
}


def _columns(conn: sqlite3.Connection, table: str = "image_assets") -> set[str]:
    return {r[1] for r in conn.execute(f"pragma table_info({table})")}


def ensure_schema(conn: sqlite3.Connection, backfill: bool = True) -> list[str]:
    """建表或原地升级；返回本次实际新增的列名。"""
    exists = conn.execute(
        "select 1 from sqlite_master where type='table' and name='image_assets'"
    ).fetchone()
    if not exists:
        conn.executescript(SCHEMA)
        conn.commit()
        return list(NEW_COLUMNS)

    have = _columns(conn)
    added: list[str] = []
    for name, typ in NEW_COLUMNS.items():
        if name not in have:
            conn.execute(f"alter table image_assets add column {name} {typ}")
            added.append(name)
    if added:
        conn.commit()
    conn.executescript(SCHEMA)      # 补索引（幂等）
    if backfill and added:
        # 旧库升级：把存量行按"手机/白底/已发布"回填，保证与升级前行为一致
        sets, args = [], []
        for col, val in DEFAULT_BACKFILL.items():
            if col in added:
                sets.append(f"{col} = coalesce({col}, ?)")
                args.append(val)
        if sets:
            conn.execute(f"update image_assets set {', '.join(sets)}", args)
            conn.commit()
    return added
