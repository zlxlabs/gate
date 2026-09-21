#!/usr/bin/env bash
set -euo pipefail

if [ -z "${RUNNER_TEMP:-}" ]; then
  echo "::error::RUNNER_TEMP 未传入，无法隔离 Silo 客户端工作目录" >&2
  exit 1
fi
if [ -z "${SILO_STORE:-}" ]; then
  echo "::error::SILO_STORE 未传入，无法启动 Silo 客户端" >&2
  exit 1
fi
command -v uv >/dev/null || { echo "::error::uv 不可用，无法临时安装 boto3（Silo 客户端）" >&2; exit 1; }

cd "$RUNNER_TEMP"
exec uv run --no-project --python 3.12 --with boto3 -- python3 "$SILO_STORE" "$@"
