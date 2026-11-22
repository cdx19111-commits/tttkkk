# Gaia × 白底图库覆盖监控部署说明

- Gaia 池快照：`/opt/gallery-mvp/app/gaia_runtime/gaia_model_pool.json`
- 精确 Gaia ID 上市年份映射：`/opt/gallery-mvp/app/gaia_runtime/gaia_launch_years.json`
- 每日刷新由公司 Mac LaunchAgent `com.aihuishou.gaia-pool-refresh` 在本地时间 12:00 执行，使用用户目录下权限 `0600` 的凭据文件，经京东 VPN 调用盖亚。
- 刷新器调用现有全量分页脚本；型号 ID、型号数和型号×颜色数校验通过后，通过 SSH 原子替换服务器快照，并同步更新本地快照。服务器保留最近 14 份回滚快照。
- 如果 Mac 未登录/未运行、京东 VPN 不通、接口失败或校验未通过，旧快照继续使用；服务器旧的 03:20 systemd 定时器已停用。更新数据文件不需要重启容器，覆盖 API 每次请求都会重新读取快照。
- 凭据不存入服务器、容器或代码仓库；此同步仅更新 Gaia 快照文件，不写生产图库数据库。
- 监控页每次请求只读查询生产白底图库；抖音主图从独立分图库表单独统计。

只更新图库服务时，保持 pool 挂载配置：

```bash
docker compose -p gallery-mvp -f compose.yaml -f app/deploy/coverage-pool.override.yaml up -d --no-deps gallery
```

如同时更新代码，再追加 `--build`。不要把 Gaia 凭据加入 Compose 或镜像。
