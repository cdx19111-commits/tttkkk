#!/usr/bin/env bash
# 新机（47.95.20.94）发布入口。
#
# 2026-11-24 起 compose.yaml 已是包含全部 6 个服务的唯一编排（网关绑 0.0.0.0:18090，
# 使用生产实际镜像标签），release.py 默认只读这一份，不再叠加 deploy/*.compose.yaml。
# 因此本脚本不再需要设置编译期环境变量，仅是给新机一个语义清晰的入口名。
#
# 用法：
#   镜像直切（推荐，无需 Dockerfile / 基础镜像）：
#     deploy/release-target.sh backend-publish <module> \
#       --expected-image sha256:<目标当前镜像> --image sha256:<已 load 的新镜像> \
#       --release <唯一版本号>
#   构建发布（服务器缺 Dockerfile 副本与基础镜像；仓库里 6 个 Dockerfile 齐全，补齐后可用）：
#     deploy/release-target.sh backend-publish <module> \
#       --source <源码包目录> --base-image sha256:<固定基础镜像> --offline ...
#   回滚：
#     deploy/release-target.sh backend-rollback <module> \
#       --expected-image sha256:<当前镜像> --image sha256:<要回退到的镜像> --release <唯一回滚号>
set -euo pipefail
exec python3 "$(dirname "$0")/release.py" "$@"
