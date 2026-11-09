# Swagger UI 静态资源

来源：`swagger-ui-dist@5.17.14`（npm / jsDelivr 官方包）
- `swagger-ui-bundle.js` — sha256 `c2e4a9ef08144839ff47c14202063ecfe4e59e70a4e7154a26bd50d880c88ba1`
- `swagger-ui.css` — sha256 `40170f0ee859d17f92131ba707329a88a070e4f66874d11365e9a77d232f6117`
- 许可：Apache License 2.0（见 `LICENSE`、`swagger-ui-bundle.js.LICENSE.txt`）

为什么随镜像分发而不是走 CDN：
1. 内网工具的接口文档不应依赖公网可用性；
2. 避免在已登录页面里执行第三方 CDN 脚本。

这两个文件由 `app/main.py` 的 `/static/swagger/{filename}` 白名单路由提供，
和文档页本身一样受 Session 认证保护（未登录 401）。升级时替换文件并同步更新
`app/main.py` 里的 `SWAGGER_ASSETS` 白名单即可。
