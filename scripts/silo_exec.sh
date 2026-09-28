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

# The trusted workflow selects managed credentials only for Gate itself. An
# unset selector preserves standalone and legacy callers; an empty/unknown
# value fails closed instead of silently changing credential sources.
credential_source=${SILO_CREDENTIAL_SOURCE-legacy-env}
case "$credential_source" in
  managed-profile)
    unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
    silo_args=(--managed-profile "$@")
    ;;
  legacy-env)
    silo_args=("$@")
    ;;
  *)
    echo "::error::Unsupported SILO_CREDENTIAL_SOURCE: $credential_source" >&2
    exit 1
    ;;
esac

# Silo 客户端是纯标准库 Python（SigV4 自签），无需 uv、无需 PyPI。
cd "$RUNNER_TEMP"
exec python3 "$SILO_STORE" "${silo_args[@]}"
