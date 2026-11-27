# 对外接口 API Key 台账

> 本文件由 `tools/api_keys.py` 自动生成，**不要手改**。最后更新：2026-11-27 18:50
> 明文钥匙只在签发人本机的 `config/api_keys.local.json`（gitignore，不入库）；
> 服务器只存 sha256 指纹，位置 `/opt/gallery-mvp/runtime/api_keys.json`。

用法：`/openapi/*` 请求头带 `X-API-Key: <钥匙>`；`只读` 只能调 GET/HEAD，`读写` 才允许写方法。

| 姓名 | 钥匙ID | 权限 | 状态 | 签发时间 | 用途 |
|---|---|---|---|---|---|
| — | — | — | — | — | 尚未签发任何钥匙 |
