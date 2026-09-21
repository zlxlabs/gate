"""Red-verify bounded retry for workflow_sha checkout and MagicDNS (gate#190)."""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

from scripts import gate_bounded_retry as retry

HELPER = Path(__file__).resolve().parent.parent / "scripts" / "gate_bounded_retry.py"


def _run(args: list[str], env: dict[str, str], cwd: Path) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.update(env)
    return subprocess.run(
        [sys.executable, str(HELPER), *args], cwd=cwd, env=merged,
        text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
    )


def test_worst_case_and_first_failure_absorbed_then_exhausted_stays_red(monkeypatch):
    assert retry.worst_case_secs() == 3 * 25 + 5 + 15 <= 180
    monkeypatch.setenv(retry.RETRY_ATTEMPTS_ENV, "3")
    monkeypatch.setenv(retry.RETRY_BACKOFF_ENV, "0 0")
    hits = {"n": 0}

    def flaky():
        hits["n"] += 1
        if hits["n"] == 1:
            raise RuntimeError("injected first-attempt failure")
        return "ok"

    assert retry.run_with_retries(flaky, label="probe") == "ok"
    assert hits["n"] == 2

    def boom():
        raise RuntimeError("persistent MagicDNS lookup failed: injected resolver error")

    try:
        retry.run_with_retries(boom, label="probe")
    except RuntimeError as exc:
        assert "injected resolver error" in str(exc)
    else:
        raise AssertionError("expected failure")


def test_git_http_config_keeps_token_out_of_argv(tmp_path, monkeypatch):
    monkeypatch.setenv("RUNNER_TEMP", str(tmp_path))
    token = "ghs_test_token_not_for_argv"
    path = retry.write_git_http_config(token, "https://github.com/zlxlabs/gate.git")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert "extraheader = AUTHORIZATION: basic " in path.read_text(encoding="ascii")
    joined = " ".join(retry.git_argv("fetch", "--depth", "1", "origin", "d" * 40))
    assert token not in joined and "x-access-token" not in joined


def test_checkout_retries_then_materializes_sha(tmp_path):
    origin = tmp_path / "origin.git"
    work = tmp_path / "seed"
    dest = tmp_path / "out"
    work.mkdir()
    subprocess.run(["git", "init", "--bare", str(origin)], check=True, cwd=tmp_path)
    subprocess.run(["git", "init"], check=True, cwd=work, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "gate@example.test"], check=True, cwd=work)
    subprocess.run(["git", "config", "user.name", "gate"], check=True, cwd=work)
    (work / "scripts").mkdir()
    (work / "scripts" / "silo_store.py").write_text("print('silo-at-sha')\n", encoding="utf-8")
    subprocess.run(["git", "add", "scripts/silo_store.py"], check=True, cwd=work)
    subprocess.run(["git", "commit", "-m", "seed"], check=True, cwd=work, stdout=subprocess.DEVNULL)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=work, text=True).strip()
    subprocess.run(["git", "remote", "add", "origin", str(origin)], check=True, cwd=work)
    subprocess.run(["git", "push", "origin", "HEAD:refs/heads/main"], check=True, cwd=work,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    wrap = tmp_path / "git-wrap"
    wrap.write_text(
        "#!/bin/sh\nset -eu\n"
        f'printf "%s\\n" "$*" >> "{tmp_path / "git-argv.log"}"\n'
        f'state="{tmp_path / "fetch-count"}"\n'
        'case " $* " in *" fetch "*) '
        'c=0; [ -f "$state" ] && c=$(cat "$state"); c=$((c+1)); printf "%s" "$c" > "$state"; '
        '[ "$c" -eq 1 ] && { echo "fatal: unable to access fake" >&2; exit 128; } ;; esac\n'
        'exec git "$@"\n',
        encoding="utf-8",
    )
    wrap.chmod(0o755)
    (tmp_path / "rt").mkdir()
    env = {
        retry.RETRY_ATTEMPTS_ENV: "3", retry.RETRY_BACKOFF_ENV: "0 0",
        retry.ATTEMPT_TIMEOUT_ENV: "20", retry.REPOSITORY_ENV: "zlxlabs/gate",
        retry.REF_ENV: sha, retry.PATH_ENV: str(dest),
        retry.SPARSE_ENV: "scripts/silo_store.py\n", retry.ORIGIN_URL_ENV: str(origin),
        retry.TOKEN_ENV: "ghs_should_not_appear_in_ps", "RUNNER_TEMP": str(tmp_path / "rt"),
        "GITHUB_WORKSPACE": str(tmp_path), retry.GIT_BIN_ENV: str(wrap),
    }
    completed = _run(["checkout"], env, tmp_path)
    assert completed.returncode == 0, completed.stderr
    assert (dest / "scripts" / "silo_store.py").read_text(encoding="utf-8") == "print('silo-at-sha')\n"
    assert (tmp_path / "fetch-count").read_text(encoding="utf-8") == "2"
    log = (tmp_path / "git-argv.log").read_text(encoding="utf-8")
    assert "ghs_should_not_appear_in_ps" not in log and "--filter" not in log
    assert subprocess.check_output(["git", "-C", str(dest), "rev-parse", "HEAD"], text=True).strip() == sha


def test_magicdns_cli_absorbs_then_fail_loud(tmp_path):
    store = tmp_path / "silo_store.py"
    store.write_text(
        "#!/usr/bin/env python3\nimport sys\nfrom pathlib import Path\n"
        f"p=Path({str(tmp_path / 'n')!r})\n"
        "n=int(p.read_text()) if p.exists() else 0\nn+=1\np.write_text(str(n))\n"
        "if n==1:\n"
        "    sys.stderr.write('MagicDNS lookup failed: injected resolver error\\n')\n"
        "    raise SystemExit(1)\nprint('100.64.0.8 silo.example')\n",
        encoding="utf-8",
    )
    env = {retry.RETRY_ATTEMPTS_ENV: "3", retry.RETRY_BACKOFF_ENV: "0 0", retry.ATTEMPT_TIMEOUT_ENV: "5"}
    ok = _run(["magicdns", "--silo-store", str(store), "--endpoint", "https://silo.example"], env, tmp_path)
    assert ok.returncode == 0, ok.stderr
    assert ok.stdout.strip() == "100.64.0.8 silo.example"
    store.write_text(
        "#!/usr/bin/env python3\nimport sys\n"
        "raise SystemExit(1)\n",
        encoding="utf-8",
    )
    bad = _run(["magicdns", "--silo-store", str(store), "--endpoint", "https://silo.example"], env, tmp_path)
    assert bad.returncode != 0
    assert "Silo MagicDNS lookup failed; see the preceding MagicDNS attempt N/M failed:" in bad.stderr
    assert "SILO_ENDPOINT=https://silo.example" in bad.stderr
    assert "tailnet" not in bad.stderr
    assert all(f"MagicDNS attempt {attempt}/3 failed:" in bad.stderr for attempt in range(1, 4))
