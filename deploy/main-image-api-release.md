# 主图服务生产发布清单

> 这是一份上线前清单。实际执行生产写库、图片替换或容器切换前，需要业务方确认发布窗口。

## 0. 前置确认

- [ ] 生产图库生成图采用 **原位覆盖** `v03data/douyin_main_images/...`，旧图先归档
- [ ] 允许 `main-image-api` 对 `gallery.db` 做受控写入（手动新增型号、is_active、宽高/SHA）
- [ ] 中文字体来源和授权（建议 Noto Sans CJK，放到 `data/fonts/`）
- [ ] 单店铺试点范围（建议 `129675602`，20～50 个型号）
- [ ] 发布窗口和回滚责任人

## 1. 构建镜像

在具备 Docker 的构建机执行：

```bash
docker build -f Dockerfile.main-image-api \
  --build-arg BASE_IMAGE=<固定基础镜像> \
  -t gallery-main-image-api:<release> .
```

生产只做 `docker load` + 镜像直切，不在服务器现场构建。

## 2. 准备挂载目录

```text
/opt/gallery-mvp/main-image-data/          # 服务库、模板、缩略图
/opt/gallery-mvp/backup/main-image/        # 生成前旧图备份
/opt/gallery-mvp/data/fonts/               # NotoSansCJK-Regular.ttc
```

## 3. 合并 compose 服务

把 `deploy/main-image-api.compose.yaml` 的 `main-image-api` 服务合并进
`/opt/gallery-mvp/compose.yaml`，保持：

- `./v03data:/data/v03:rw`
- `./main-image-data:/data/main-image:rw`
- `./backup/main-image:/backup/main-image:rw`
- `./data/fonts:/data/fonts:ro`
- 网络 `default` 和 `white-gallery-internal`

## 4. 图库 active 字段迁移

先 dry-run：

```bash
python3 dev/main-image-service/migrate_gallery_active.py \
  --gallery-db /opt/gallery-mvp/v03data/gallery.db
```

确认后再执行（脚本会先备份）：

```bash
python3 dev/main-image-service/migrate_gallery_active.py \
  --gallery-db /opt/gallery-mvp/v03data/gallery.db \
  --backup-dir /opt/gallery-mvp/backup/main-image-active \
  --execute
```

## 5. 发布

```bash
deploy/release-target.sh backend-publish main-image-api \
  --expected-image sha256:<当前> --image sha256:<新镜像> \
  --release <版本号>
```

首次新服务需要 `--bootstrap`，具体按 `release.py` 的当前参数执行。

网关需要包含 `/api/v1/main-images/*` 路由；发布网关前确认其它 6 个服务健康。

## 6. 发布后检查

```bash
docker inspect --format '{{.State.Health.Status}}' gallery-mvp-main-image-api-1
```

再通过生产域名和真实登录态检查：

- 主图生成页能打开、筛选正常
- 单型号生成、预览、缩略图
- 主图库能看到生成图，旧图已归档
- 每个店铺 × 型号只有一张 active
- `verify_gallery.py` 全部通过

## 7. 回滚

```bash
deploy/release-target.sh backend-rollback main-image-api \
  --expected-image sha256:<当前> --image sha256:<上一版> \
  --release <回滚版本号>
```

图库数据库使用迁移前备份恢复；生成图文件使用 `/backup/main-image` 恢复。
