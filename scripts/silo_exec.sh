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

# Silo 客户端是纯标准库 Python（SigV4 自签），无需 uv、无需 PyPI。
cd "$RUNNER_TEMP"
exec python3 "$SILO_STORE" "$@"
