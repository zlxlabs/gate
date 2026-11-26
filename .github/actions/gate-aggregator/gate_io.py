"""Focused gate io implementation for gate aggregate."""
from __future__ import annotations

import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Optional

GATE_ROOT = Path(__file__).resolve().parents[3]

from gate_aggregate_models import (
    ACTIONS_BOT_ID, ACTIONS_BOT_LOGIN, DISPOSITION_ARTIFACT_PREFIX,
    GITHUB_API_TIMEOUT_SECONDS, MAX_REPO_WIDE_HISTORY_PAGES,
    SILO_CLI_CALL_TIMEOUT_SECONDS, SILO_DISPOSITION_TIER,
    _ACTIVE_PUBLISH_BUDGET, _PublishBudgetExhausted, _is_strict_int,
)

_MODELS_NAME = "gate_aggregate_models"
_MODELS = sys.modules.get(_MODELS_NAME)
if _MODELS is None:
    _MODELS_SPEC = importlib.util.spec_from_file_location(
        _MODELS_NAME, Path(__file__).with_name("aggregate_models.py")
    )
    assert _MODELS_SPEC and _MODELS_SPEC.loader
    _MODELS = importlib.util.module_from_spec(_MODELS_SPEC)
    sys.modules[_MODELS_NAME] = _MODELS
    _MODELS_SPEC.loader.exec_module(_MODELS)

_CONVERGENCE_NAME = "gate_convergence_from_aggregate"
_CONVERGENCE = sys.modules.get(_CONVERGENCE_NAME)
if _CONVERGENCE is None:
    _CONVERGENCE_SPEC = importlib.util.spec_from_file_location(
        _CONVERGENCE_NAME, Path(__file__).with_name("convergence.py")
    )
    assert _CONVERGENCE_SPEC and _CONVERGENCE_SPEC.loader
    _CONVERGENCE = importlib.util.module_from_spec(_CONVERGENCE_SPEC)
    sys.modules[_CONVERGENCE_NAME] = _CONVERGENCE
    _CONVERGENCE_SPEC.loader.exec_module(_CONVERGENCE)


def _download_terminal_zip(*, token: str, url: str, _ports: Any = None) -> bytes:
    """Download one terminal zip while keeping GitHub auth off signed blobs."""
    opener = urllib.request.build_opener(_ports._NoRedirectHandler())
    budget = _ACTIVE_PUBLISH_BUDGET.get()
    api_headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "gate-aggregator",
    }

    def open_once(request: urllib.request.Request) -> bytes:
        timeout = budget.timeout() if budget is not None else GITHUB_API_TIMEOUT_SECONDS
        try:
            with opener.open(request, timeout=timeout) as response:
                raw = response.read()
        except (socket.timeout, TimeoutError) as exc:
            if budget is not None:
                try:
                    budget.timeout()
                except _PublishBudgetExhausted as budget_exc:
                    raise budget_exc from exc
            raise
        if budget is not None:
            budget.timeout()
        return raw

    request = urllib.request.Request(url, headers=api_headers, method="GET")
    try:
        return open_once(request)
    except urllib.error.HTTPError as exc:
        if exc.code != 302:
            raise
        location = exc.headers.get("Location") if exc.headers is not None else None
        if not isinstance(location, str) or not location:
            raise ValueError("artifact zip redirect did not include a Location header")
        if urllib.parse.urlsplit(location).hostname is None:
            raise ValueError("artifact zip redirect Location is not an absolute URL")
        redirect_headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": "gate-aggregator",
        }
        if urllib.parse.urlsplit(location).hostname.lower() == "api.github.com":
            redirect_headers.update(api_headers)
        return open_once(urllib.request.Request(location, headers=redirect_headers, method="GET"))

def _github_json(*, token: str, url: str, _ports: Any = None) -> Any:
    raw = _ports._github_request(token=token, url=url)
    return json.loads(raw) if raw else None

def _github_identity(token: str, _ports: Any = None) -> dict[str, Any]:
    try:
        payload = _ports._github_json(token=token, url="https://api.github.com/user")
    except urllib.error.HTTPError as exc:
        if exc.code not in (403, 404):
            raise
        return {"id": ACTIONS_BOT_ID, "login": ACTIONS_BOT_LOGIN, "identity_source": "actions_bot_fallback"}
    if not isinstance(payload, dict) or not _is_strict_int(payload.get("id")) or not isinstance(payload.get("login"), str) or not payload["login"]:
        raise ValueError("GitHub identity response is missing a strict numeric id or login")
    return {"id": payload["id"], "login": payload["login"], "identity_source": "user_api"}

def _fetch_panel_comments(*, token: str, repository: str, pr_number: int, _ports: Any = None) -> list[dict[str, Any]]:
    comments: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = _ports._github_json(
            token=token,
            url=f"https://api.github.com/repos/{repository}/issues/{pr_number}/comments?per_page=100&page={page}",
        )
        if not isinstance(payload, list) or any(not isinstance(comment, dict) for comment in payload):
            raise ValueError("PR comments response has an invalid shape")
        comments.extend(payload)
        if len(payload) < 100:
            return comments
        page += 1

def _post_issue_comment(*, repository: str, pr_number: int, body: str, token: str, _ports: Any = None) -> None:
    _ports._github_request(
        token=token,
        url=f"https://api.github.com/repos/{repository}/issues/{pr_number}/comments",
        method="POST",
        payload={"body": body},
    )

def _patch_issue_comment(*, repository: str, comment_id: int, body: str, token: str, _ports: Any = None) -> None:
    _ports._github_request(
        token=token,
        url=f"https://api.github.com/repos/{repository}/issues/comments/{comment_id}",
        method="PATCH",
        payload={"body": body},
    )

def _delete_issue_comment(*, repository: str, comment_id: int, token: str, _ports: Any = None) -> None:
    _ports._github_request(
        token=token,
        url=f"https://api.github.com/repos/{repository}/issues/comments/{comment_id}",
        method="DELETE",
    )

def _read_audit_file(audit_dir: Optional[Path], _ports: Any = None) -> tuple[Any, Optional[str], Optional[bytes]]:
    """Read the sole downloaded audit and retain the exact producer bytes."""
    if audit_dir is None or not audit_dir.is_dir():
        return None, "audit directory not present (download step likely found no artifact)", None
    candidates = sorted(p for p in audit_dir.iterdir() if p.suffix == ".json")
    if not candidates:
        return None, f"no *.json file found under {audit_dir}", None
    if len(candidates) > 1:
        names = ", ".join(p.name for p in candidates)
        return None, f"expected exactly one audit file under {audit_dir}, found {len(candidates)}: {names}", None
    try:
        raw = candidates[0].read_bytes()
        return json.loads(raw.decode("utf-8")), None, raw
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return None, f"could not parse {candidates[0].name}: {exc}", None

def _write_convergence_receipt(path: str, receipt: Any, _ports: Any = None) -> None:
    """Write one canonical receipt payload before any terminal publication."""
    _CONVERGENCE.validate_receipt(receipt, receipt.scope)
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        receipt.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    temporary_path = target.with_name(f".{target.name}.tmp")
    temporary_path.write_bytes(payload)
    temporary_path.replace(target)

def _read_disposition_zip(raw: bytes, _ports: Any = None) -> dict[str, Any]:
    with zipfile.ZipFile(io.BytesIO(raw)) as bundle:
        names = [name for name in bundle.namelist() if not name.endswith("/")]
        if len(names) != 1:
            raise ValueError(
                f"disposition artifact must contain exactly one file, found {len(names)}"
            )
        payload = json.loads(bundle.read(names[0]))
    if not isinstance(payload, dict):
        raise ValueError("disposition receipt payload must be a JSON object")
    return payload

def _fetch_github_disposition_receipts(
    *, token: str, repository: str, repository_id: int, pr_number: int,
    _ports: Any = None) -> tuple[Any, ...]:
    """Bounded same-repo GitHub artifact scan; errors fail-open to an empty tuple."""

    artifacts: list[dict[str, Any]] = []
    page = 1
    try:
        while page <= MAX_REPO_WIDE_HISTORY_PAGES:
            payload = _ports._github_json(
                token=token,
                url=(
                    f"https://api.github.com/repos/{repository}/actions/artifacts"
                    f"?per_page=100&page={page}"
                ),
            )
            if not isinstance(payload, dict) or not isinstance(payload.get("artifacts"), list):
                print("::warning::Disposition receipt scan returned an invalid artifact list; claims were not recorded.")
                return ()
            page_artifacts = payload["artifacts"]
            artifacts.extend(
                artifact for artifact in page_artifacts
                if isinstance(artifact, dict)
                and isinstance(artifact.get("name"), str)
                and artifact["name"].startswith(DISPOSITION_ARTIFACT_PREFIX)
                and artifact.get("expired") is not True
            )
            if len(page_artifacts) < 100:
                break
            page += 1
    except Exception as exc:
        print(f"::warning::Disposition receipt scan failed; claims were not recorded ({type(exc).__name__}).")
        return ()

    receipts: list[Any] = []
    artifact_warning_reported = False
    for artifact in artifacts:
        archive_url = artifact.get("archive_download_url")
        if not isinstance(archive_url, str) or not archive_url:
            if not artifact_warning_reported:
                print("::warning::Disposition receipt artifact has no archive URL; claims were not recorded.")
                artifact_warning_reported = True
            continue
        try:
            payload = _ports._read_disposition_zip(_ports._download_terminal_zip(token=token, url=archive_url))
            receipt = _CONVERGENCE.parse_disposition_receipt(payload)
        except Exception as exc:
            if not artifact_warning_reported:
                print(f"::warning::Disposition receipt artifact could not be recorded ({type(exc).__name__}).")
                artifact_warning_reported = True
            continue
        if receipt.pr_number != pr_number or receipt.repository_id != str(repository_id):
            continue
        receipts.append(receipt)
    return tuple(receipts)

def _silo_configured(_ports: Any = None) -> bool:
    return bool((os.environ.get("SILO_ENDPOINT") or "").strip())

def _silo_cli(argv: list[str], timeout_s: float = SILO_CLI_CALL_TIMEOUT_SECONDS, _ports: Any = None) -> subprocess.CompletedProcess:
    # Silo 客户端是纯标准库（scripts/silo_store.py 自签 SigV4）：直接用当前
    # 解释器执行，禁止经由 uv/pip 在运行期取包。SILO_STORE 允许覆盖是为了
    # 让测试打桩与 job 稀疏检出能指向同一份仓内脚本，不接受仓外路径以外的
    # 任何包管理器入口。仓内调用点必须显式传 timeout_s（deadline 剩余或
    # SILO_CLI_CALL_TIMEOUT_SECONDS）；默认值只服务于仓外无超时语义的探针
    # （tests/test_no_runtime_install.py），且本身就是有界的。
    store_path = os.environ.get("SILO_STORE") or str(GATE_ROOT / "scripts" / "silo_store.py")
    return subprocess.run(
        [sys.executable, store_path, *argv],
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_s,
    )

def _silo_objects_under(prefix: str, timeout_s: float, _ports: Any = None) -> list[tuple[str, bytes]]:
    """Return (key, body) pairs under a Silo prefix. Raises on listing/get failure."""
    import tempfile

    with tempfile.TemporaryDirectory(prefix="silo-scan-") as dest:
        proc = _ports._silo_cli(["list", "--prefix", prefix, "--dest", dest], timeout_s)
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout or "silo list failed").strip())
        keys = [line for line in proc.stdout.splitlines() if line.strip()]
        objects = []
        for key in keys:
            parts = key.split("/", 3)
            if len(parts) != 4:
                raise ValueError(f"silo key is not tier/repo_id/artifact_name/path: {key}")
            path = Path(dest) / parts[2] / parts[3]
            objects.append((key, path.read_bytes()))
        return objects

def _fetch_silo_disposition_receipts(*, repository_id: int, pr_number: int, _ports: Any = None) -> tuple[Any, ...]:
    prefix = f"{SILO_DISPOSITION_TIER}/{repository_id}/{DISPOSITION_ARTIFACT_PREFIX}"
    try:
        objects = _ports._silo_objects_under(prefix, SILO_CLI_CALL_TIMEOUT_SECONDS)
    except Exception as exc:
        print(
            f"::warning::Silo disposition receipt scan failed; "
            f"claims from Silo were not recorded ({type(exc).__name__})."
        )
        return ()
    receipts: list[Any] = []
    artifact_warning_reported = False
    for key, raw in objects:
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError("disposition receipt payload must be a JSON object")
            receipt = _CONVERGENCE.parse_disposition_receipt(payload)
        except Exception as exc:
            if not artifact_warning_reported:
                print(
                    f"::warning::Silo disposition receipt object could not be recorded ({type(exc).__name__})."
                )
                artifact_warning_reported = True
            continue
        if receipt.pr_number != pr_number or receipt.repository_id != str(repository_id):
            continue
        receipts.append(receipt)
    return tuple(receipts)

def _receipt_merge_key(receipt: Any, _ports: Any = None) -> str:
    return _CONVERGENCE.disposition_receipt_artifact_name(receipt)

def _merge_disposition_receipts(github: tuple[Any, ...], silo: tuple[Any, ...], _ports: Any = None) -> tuple[Any, ...]:
    by_name: dict[str, Any] = {}
    for receipt in github:
        by_name[_ports._receipt_merge_key(receipt)] = receipt
    for receipt in silo:
        by_name[_ports._receipt_merge_key(receipt)] = receipt
    return tuple(by_name.values())

def _fetch_disposition_receipts(
    *, token: str, repository: str, repository_id: int, pr_number: int,
    _ports: Any = None) -> tuple[Any, ...]:
    """Dual-read GitHub ∪ Silo; errors fail-open per source and merge the rest."""

    github = _ports._fetch_github_disposition_receipts(
        token=token, repository=repository, repository_id=repository_id, pr_number=pr_number,
    )
    silo: tuple[Any, ...] = ()
    if _ports._silo_configured():
        silo = _ports._fetch_silo_disposition_receipts(repository_id=repository_id, pr_number=pr_number)
    return _ports._merge_disposition_receipts(github, silo)
