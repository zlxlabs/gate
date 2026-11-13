"""Versioned public contract for behavior_replay counterevidence syntax.

Consumers pinned to this gate commit may load ``behavior_counterevidence_reason``
and the matching JSON vectors beside this file. Validation is syntactic only;
this module never executes Git, reads the repository, applies a patch, or
runs tests.
"""

from __future__ import annotations

import hashlib
import re


# Increment when accepted payload shapes or rejection results change.
BEHAVIOR_COUNTEREVIDENCE_CONTRACT_VERSION = 1

PATCH_MAX_BYTES = 8192
LINE_WINDOW_TOLERANCE = 4
PATCH_MAX_NET_CHANGED_LINES = 12

_HEAD_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_GIT_DIFF_RE = re.compile(r"^diff --git a/(.+) b/(.+)$")
_FORBIDDEN_HEADER_RE = re.compile(
    r"^(?:rename from |rename to |copy from |copy to |"
    r"old mode |new mode |deleted file mode |new file mode |"
    r"similarity index |dissimilarity index |"
    r"GIT binary patch|Binary files )"
)


def behavior_counterevidence_reason(
    payload: object,
    finding_id: str,
    primary_run_id: str,
    primary_run_attempt: str,
    head_sha: str,
    finding: object,
) -> str | None:
    """Return a stable reason_code unless the author payload is syntactically legal.

    ``finding`` is the caller-supplied identity mapping with ``file`` / ``line``.
    None means the payload is compliant. This function performs no I/O.
    """
    if not isinstance(payload, dict):
        return "behavior_payload_not_object"
    if payload.get("kind") != "behavior_replay":
        return "behavior_kind_invalid"
    if payload.get("result") != "refuted":
        return "behavior_result_not_refuted"
    pointer = payload.get("pointer")
    if not isinstance(pointer, str) or not pointer:
        return "behavior_pointer_empty"
    if payload.get("finding_id") != finding_id:
        return "behavior_finding_id_mismatch"
    if payload.get("primary_run_id") != primary_run_id:
        return "behavior_primary_run_id_mismatch"
    if payload.get("primary_run_attempt") != primary_run_attempt:
        return "behavior_primary_run_attempt_mismatch"
    payload_sha = payload.get("head_sha")
    if not isinstance(payload_sha, str) or _HEAD_SHA_RE.fullmatch(payload_sha) is None:
        return "behavior_head_sha_invalid"
    if payload_sha != head_sha:
        return "behavior_head_sha_mismatch"
    if payload.get("runner") != "pytest":
        return "behavior_runner_unsupported"
    nodeid = payload.get("nodeid")
    if not isinstance(nodeid, str) or not nodeid or "::" not in nodeid:
        return "behavior_nodeid_invalid"
    if "gate_replay" in payload:
        return "behavior_replay_author_forged_rerun"
    return _patch_reason(payload.get("patch"), finding)


def _patch_reason(patch: object, finding: object) -> str | None:
    if not isinstance(patch, dict):
        return "behavior_patch_missing"
    unified_diff = patch.get("unified_diff")
    if not isinstance(unified_diff, str) or "\0" in unified_diff:
        return "behavior_patch_not_text"
    try:
        raw = unified_diff.encode("utf-8")
    except UnicodeEncodeError:
        return "behavior_patch_not_text"
    claimed_len = patch.get("byte_len")
    if len(raw) > PATCH_MAX_BYTES:
        return "behavior_patch_too_large"
    if not isinstance(claimed_len, int) or isinstance(claimed_len, bool) or claimed_len != len(raw):
        return "behavior_patch_byte_len_mismatch"
    claimed_hash = patch.get("sha256")
    digest = hashlib.sha256(raw).hexdigest()
    if not isinstance(claimed_hash, str) or _SHA256_RE.fullmatch(claimed_hash) is None or claimed_hash != digest:
        return "behavior_patch_hash_mismatch"
    finding_file, finding_line, ask_human = _finding_identity(finding)
    if ask_human:
        return "behavior_finding_ask_human"
    parsed, parse_reason = _parse_unified_diff(unified_diff)
    if parse_reason is not None:
        return parse_reason
    return _patch_text_rules(parsed, finding_file, finding_line)


def _finding_identity(finding: object) -> tuple[str, int, bool]:
    if not isinstance(finding, dict):
        return "", 0, True
    path = finding.get("file")
    line = finding.get("line")
    file_missing = not isinstance(path, str) or path == ""
    line_invalid = not isinstance(line, int) or isinstance(line, bool) or line < 1
    return path if isinstance(path, str) else "", line if isinstance(line, int) else 0, file_missing or line_invalid


def _parse_unified_diff(text: str) -> tuple[dict | None, str | None]:
    paths: list[str] = []
    hunk_old_ranges: list[tuple[int, int]] = []
    net_changed = 0
    has_dev_null = False
    has_forbidden_header = False
    in_hunk = False
    old_left = 0
    new_left = 0

    for raw_line in text.splitlines():
        line = raw_line[:-1] if raw_line.endswith("\r") else raw_line
        if in_hunk and (old_left > 0 or new_left > 0):
            if line.startswith("\\"):
                continue
            if line.startswith("+"):
                if new_left <= 0:
                    return None, "behavior_patch_unparseable"
                new_left -= 1
                net_changed += 1
                continue
            if line.startswith("-"):
                if old_left <= 0:
                    return None, "behavior_patch_unparseable"
                old_left -= 1
                net_changed += 1
                continue
            if old_left <= 0 or new_left <= 0:
                return None, "behavior_patch_unparseable"
            old_left -= 1
            new_left -= 1
            continue
        in_hunk = False

        git_match = _GIT_DIFF_RE.match(line)
        if git_match is not None:
            left, right = git_match.group(1), git_match.group(2)
            if left != right:
                has_forbidden_header = True
            paths.append(_strip_ab_prefix("a/" + left))
            paths.append(_strip_ab_prefix("b/" + right))
            continue
        if _FORBIDDEN_HEADER_RE.match(line) is not None:
            has_forbidden_header = True
            continue
        if line.startswith("--- ") or line.startswith("+++ "):
            path = _header_path(line[4:])
            if path == "/dev/null":
                has_dev_null = True
            paths.append(path)
            continue
        hunk_match = _HUNK_RE.match(line)
        if hunk_match is not None:
            old_start = int(hunk_match.group(1))
            old_count = int(hunk_match.group(2) or "1")
            new_count = int(hunk_match.group(4) or "1")
            hunk_old_ranges.append(_old_range(old_start, old_count))
            in_hunk = True
            old_left = old_count
            new_left = new_count
            continue

    if in_hunk and (old_left != 0 or new_left != 0):
        return None, "behavior_patch_unparseable"
    if has_forbidden_header:
        return None, "behavior_patch_forbidden_header"
    if has_dev_null:
        return None, "behavior_patch_creates_or_deletes"
    if not paths or not hunk_old_ranges:
        return None, "behavior_patch_unparseable"
    return (
        {
            "paths": paths,
            "hunk_old_ranges": hunk_old_ranges,
            "net_changed": net_changed,
        },
        None,
    )


def _header_path(rest: str) -> str:
    return _strip_ab_prefix(rest.split("\t", 1)[0])


def _strip_ab_prefix(path: str) -> str:
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def _old_range(start: int, count: int) -> tuple[int, int]:
    if count == 0:
        return (start, start)
    return (start, start + count - 1)


def _patch_text_rules(parsed: dict, finding_file: str, finding_line: int) -> str | None:
    unique_paths: list[str] = []
    for path in parsed["paths"]:
        if path not in unique_paths:
            unique_paths.append(path)
    for path in unique_paths:
        if ".." in path.split("/"):
            return "behavior_patch_path_dotdot"
        if path.startswith("/"):
            return "behavior_patch_path_absolute"
    if unique_paths != [finding_file]:
        return "behavior_patch_path_mismatch"
    window_lo = finding_line - LINE_WINDOW_TOLERANCE
    window_hi = finding_line + LINE_WINDOW_TOLERANCE
    for lo, hi in parsed["hunk_old_ranges"]:
        if hi < window_lo or lo > window_hi:
            return "behavior_patch_outside_line_window"
    if parsed["net_changed"] > PATCH_MAX_NET_CHANGED_LINES:
        return "behavior_patch_too_many_lines"
    return None
