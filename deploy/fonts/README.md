# 生产中文字体

主图服务在 Linux 容器内必须能加载中文字体，不能使用 macOS 的
`/System/Library/Fonts/Hiragino Sans GB.ttc`。

请放置一份可再分发的开源中文字体，例如：

```text
deploy/fonts/NotoSansCJK-Regular.ttc
```

并确保生产挂载：

```yaml
- ./data/fonts/NotoSansCJK-Regular.ttc:/data/fonts/NotoSansCJK-Regular.ttc:ro
```

环境变量：

```text
MAIN_IMAGE_FONT_PATH=/data/fonts/NotoSansCJK-Regular.ttc
```

当前仓库不包含字体文件；字体来源和授权需要确认后再放入生产挂载目录。
