> 本文保留 V0.3 历史说明。当前版本、协作开发与发布/回滚操作以 [项目日志](项目日志.md) 和 AGENTS.md 为准；不要直接使用历史全量部署命令覆盖生产。

# 商品图库管理平台 · MVP V0.1

> **仅供技术验证。** 默认模拟商品和人工合成图片，不包含、也不自动拉取公司飞书、拍拍盖亚或任何企业业务数据。**个人云服务器严禁未经授权同步或存放企业图库和业务资料。**

## 目标与实现范围

最小闭环：**标准商品 → 型号别名 → 图片关联 → 前端搜索/管理 → 授权 API 查询**。

已实现：
- 商品标准名称、品牌、品类、别名建档；保守字符串归一化（全半角/空格/大小写），不自动合并近似型号。
- PNG/JPEG/WebP 演示图片上传，校验真实编码与宽高，图片本地存储；资料和图片分开。
- 按型号、别名、渠道、类型、审核状态检索；图片审核状态修改。
- 受 Basic Auth 保护的前端、图片访问和 `/api/v1/*` 接口；FastAPI OpenAPI 文档 `/docs`。
- 一键生成 3 个模拟型号和 6 张标注 DEMO 的合成测试图片。

尚未实现：真实飞书同步、拍拍盖亚对接、SKU/店铺精细映射、复杂 RBAC、审批日志、数据库迁移、队列、对象存储、高可用。不要把它用于真实生产。

## 本地 Docker 快速启动

```bash
cp .env.example .env
# 修改 .env 的 GALLERY_PASSWORD（务必使用长随机密码）
mkdir -p runtime
docker compose up -d --build
docker compose ps
curl http://127.0.0.1:18080/healthz
```

默认仅监听 **服务器本机** `127.0.0.1:18080`，不会直接对公网开放。

开发电脑 SSH 转发演示：

```bash
ssh -L 18080:127.0.0.1:18080 <服务器用户名>@<服务器IP>
```

然后在电脑浏览器访问 `http://127.0.0.1:18080`，输入 `.env` 中的用户和密码。

如需要固定域名供团队访问：先在服务器已有 Nginx/Caddy 上配置 HTTPS，再反代 `127.0.0.1:18080`，参考 `deploy/nginx.conf.example`。**不要使用明文 HTTP 传输登录密码，也不要公网暴露 18080 端口。**

## API 使用示例

```bash
# 查询模拟商品：
curl -u 'admin:YOUR_PASSWORD' 'http://127.0.0.1:18080/api/v1/models?q=DemoPhone'

# 按别名精确解析模型 + 已审核图片（不会把相似但不同的型号合并）：
curl -u 'admin:YOUR_PASSWORD' 'http://127.0.0.1:18080/api/v1/lookup?name=demophonex&channel=抖音'

# 查询图片资产列表：
curl -u 'admin:YOUR_PASSWORD' 'http://127.0.0.1:18080/api/v1/assets?channel=通用&asset_type=白底图'
```

### 业务端约定（暂定）

- `model_id` 是平台内稳定 ID；不要用文件名或原始路径作主键。
- `channel`、`asset_type`、`status` 分开管理。
- `/api/v1/lookup` 对候选同名模型返回 `ambiguous: true`，禁止自动猜测。
- 图片链接是受登录保护的**相对路径**；不是飞书分享链接，也不是永久公开 CDN 链接。

## 结构

```
app/main.py             后端 API、模型与模拟数据
app/static/index.html   前端
app/static/app.js       前端交互
app/static/style.css    页面样式
compose.yaml            Docker 启动
Dockerfile              容器镜像
runtime/                持久化 SQLite 与演示图片（不提交）
```

## 迁移路线

1. 本版：SQLite + 本地图片 + 合成数据，跑通用户操作。
2. 经企业批准后：明确数据边界、内网部署方式和访问授权；增加正式数据适配器（飞书只读索引、盖亚主数据映射）。
3. 正式部署：PostgreSQL + 公司批准的对象存储/图片代理 + 企业 SSO/RBAC + 审计 + 备份 + 定时同步；代码作为基础迁移。

不建议从个人云服务器导出带有企业数据的数据库或资产；届时应重新从**经批准的数据源**在正式环境内初始化与校验。

## 验收清单

- [ ] 首页出现 3 个模拟型号、6 张模拟图片。
- [ ] 搜索 `DemoPhone X` 可找到商品；搜索 `demophonex` 也能匹配。
- [ ] 渠道筛选 `抖音` 正确缩小范围。
- [ ] 新建模拟商品、上传自己制作的图片，默认待审核。
- [ ] 修改图片状态为已审核，`lookup` 接口会返回该图片。
- [ ] 没有密码不能读取图片或 API。
- [ ] 重启容器后模拟数据与用户新增数据仍在。
