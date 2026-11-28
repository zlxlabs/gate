"""Domain-focused tests migrated without changing assertions."""
from test_gate_aggregator import (
    AGG,
    CONV,
    IDENTITY,
    Path,
    ROOT,
    _CANARY_REPO_ID,
    _FakeResponse,
    _cli_args,
    _panel_terminal_row,
    _valid_primary_record,
    json,
    pytest,
    socket,
    subprocess,
    sys,
    threading,
    time,
    urllib,
)
from test_gate_aggregator_caller_checks import (
    _gate_rerun,
    _panel_row,
)

def _visible_scenario(tmp_path, overrides, audit_record="__default__"):
    audit_dir = tmp_path / "audit"
    if audit_record is not None:
        audit_dir.mkdir()
        record = _valid_primary_record() if audit_record == "__default__" else audit_record
        (audit_dir / "primary-review-audit.json").write_text(json.dumps(record))
    summary_path = tmp_path / "summary.md"
    return audit_dir, summary_path, _cli_args(audit_dir, summary_path, **overrides)



def test_publish_budget_stops_hanging_http_call_and_records_operations(monkeypatch):
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setenv("GATE_PUBLISH_BUDGET_SECONDS", "0.05")
    started = threading.Event()
    released = threading.Event()
    finished = threading.Event()
    calls = []

    def hanging_urlopen(request, timeout=None):
        calls.append((request.full_url, timeout))
        started.set()
        released.wait(timeout=timeout)
        raise socket.timeout("stub hung")

    monkeypatch.setattr(urllib.request, "urlopen", hanging_urlopen)
    result = []

    def invoke_publish():
        result.append(
            AGG._post_status_panel_fail_open(
                current=_panel_terminal_row(1, 1, "pass"),
                repository="zlxlabs/gate", repository_id=123, pr_number=42, identity=IDENTITY,
            )[1]
        )
        finished.set()

    worker = threading.Thread(target=invoke_publish, daemon=True)
    worker.start()
    try:
        assert started.wait(timeout=0.5)
        assert finished.wait(timeout=0.5), "publish must stop a hanging call within its budget"
    finally:
        released.set()
        worker.join(timeout=1)

    assert len(result) == 1
    assert calls and calls[0][1] <= 0.05
    receipt = result[0]
    assert receipt["reason_code"] == "publish_budget_exhausted"
    assert receipt["completed_operations"] == []
    assert receipt["pending_operations"]

def test_slow_repo_wide_history_yields_to_comment_publish(monkeypatch):
    """A large repository artifact listing must not starve the panel POST."""
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setenv(AGG.PUBLISH_BUDGET_ENV, "0.10")
    monkeypatch.setenv(AGG.HISTORY_RECONSTRUCTION_BUDGET_ENV, "0.03")
    owner = {"id": 99, "login": "workflow-bot"}
    posted_body = None
    comment_gets = 0

    def fake_urlopen(request, timeout=None):
        nonlocal posted_body, comment_gets
        url = request.full_url
        if url.endswith("/user"):
            return _FakeResponse(json.dumps(owner).encode())
        if "/issues/42/comments" in url:
            if request.get_method() == "POST":
                posted_body = json.loads(request.data.decode())["body"]
                return _FakeResponse()
            comment_gets += 1
            comments = [] if comment_gets == 1 else [{
                "id": 77,
                "created_at": "2026-08-16T00:00:00Z",
                "body": posted_body or AGG.PANEL_MARKER,
                "user": owner,
            }]
            return _FakeResponse(json.dumps(comments).encode())
        if "/actions/artifacts" in url:
            time.sleep(0.01)
            page = int(url.rsplit("page=", 1)[1])
            artifacts = [{"name": f"unrelated-{page}-{index}"} for index in range(100)] if page <= 22 else []
            return _FakeResponse(json.dumps({"artifacts": artifacts}).encode())
        raise AssertionError(f"unexpected GitHub API call: {url}")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    _, receipt = AGG._post_status_panel_fail_open(
        current=_panel_terminal_row(1, 1, "pass", "a" * 40),
        repository="zlxlabs/gate", repository_id=123, pr_number=42, identity=IDENTITY,
    )

    assert receipt["reason_code"] == "posted"
    assert receipt["history_incomplete"] is True
    assert any("history" in reason and ("budget" in reason or "share" in reason) for reason in receipt["history_incomplete_reasons"])
    assert "COMMENT_PUBLISH" in receipt["completed_operations"]
    assert "COMMENT_PUBLISH" not in receipt["pending_operations"]

@pytest.mark.parametrize(
    "failure,expected_delivery,expected_status",
    [
        (urllib.error.HTTPError("https://api.github.com/x", 429, "rate", hdrs=None, fp=None), "not_created", 429),
        (urllib.error.URLError("Authorization: Bearer ghs_xxx https://api.github.com/path?token=query-secret"), "unknown", None),
    ],
    ids=["http-429", "network-timeout"],
)
def test_publish_only_transport_failures_are_fail_open_and_leave_receipt(monkeypatch, capsys, tmp_path, failure, expected_delivery, expected_status):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(_valid_primary_record()))
    summary_path = tmp_path / "summary.md"
    terminal_path = tmp_path / "gate-terminal.json"
    delivery_path = tmp_path / "panel-delivery.json"
    assert AGG.main(_cli_args(audit_dir, summary_path, terminal_path=str(terminal_path))) == 0

    owner = {"id": 99, "login": "workflow-bot"}
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: [])
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=[]))
    post_calls = []
    def fail_post(**kwargs):
        post_calls.append(kwargs)
        raise failure
    monkeypatch.setattr(AGG, "_post_issue_comment", fail_post)
    args = _cli_args(
        audit_dir, summary_path, terminal_path=str(terminal_path), panel_delivery_path=str(delivery_path),
    ) + ["--publish-only"]
    assert AGG.main(args) == 0
    receipt = json.loads(delivery_path.read_text(encoding="utf-8"))
    assert receipt["delivery"] == expected_delivery
    assert receipt["http_status"] == expected_status
    assert len(post_calls) == 1
    assert receipt["operation"] == "comment_create"
    assert receipt["attempts"] == 1
    assert receipt["exception_type"] == type(failure).__name__
    summary = summary_path.read_text(encoding="utf-8")
    assert "HTTP status" in summary
    assert "面板发布失败，面板可能显示旧状态" in summary
    output = capsys.readouterr().out
    assert "PANEL_PUBLISH_FAILED operation=comment_create attempts=1" in output
    assert "exc_type=" + type(failure).__name__ in output
    assert "ghs_xxx" not in output + summary + json.dumps(receipt)
    assert "query-secret" not in output + summary + json.dumps(receipt)

def test_panel_delivery_write_failure_clears_stale_file_and_stays_fail_open(monkeypatch, capsys, tmp_path):
    path = tmp_path / "panel-delivery.json"
    path.write_text("old receipt", encoding="utf-8")
    monkeypatch.setattr(AGG, "_write_panel_delivery", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    AGG._persist_panel_delivery(str(path), {"delivery": "created"})
    assert not path.exists()
    assert "file is missing and upload will red" in capsys.readouterr().out

def test_panel_delivery_cleanup_failure_writes_invalid_marker(monkeypatch, capsys, tmp_path):
    path = tmp_path / "panel-delivery.json"
    path.write_text("old receipt", encoding="utf-8")
    monkeypatch.setattr(Path, "unlink", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("locked")))
    monkeypatch.setattr(AGG, "_write_panel_delivery", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")))
    AGG._persist_panel_delivery(str(path), {"delivery": "created"})
    assert path.read_bytes() == b"invalid gate PR-comment receipt\n"
    assert "stale receipt was destroyed; an invalid marker was written" in capsys.readouterr().out

def test_patch_request_uses_issue_comment_endpoint(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None):
        request.captured_timeout = timeout
        calls.append(request)
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    AGG._patch_issue_comment(repository="zlxlabs/gate", comment_id=77, body="new", token="tok")
    request = calls[0]
    assert request.full_url == "https://api.github.com/repos/zlxlabs/gate/issues/comments/77"
    assert request.get_method() == "PATCH"
    assert request.captured_timeout == 15
    assert json.loads(request.data.decode("utf-8")) == {"body": "new"}

def test_post_issue_comment_uses_issue_comments_endpoint(monkeypatch):
    calls = []

    def fake_urlopen(request, timeout=None):
        request.captured_timeout = timeout
        calls.append(request)
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    AGG._post_issue_comment(repository="zlxlabs/gate", pr_number=42, body="hello gate", token="tok")
    request = calls[0]
    assert request.full_url == "https://api.github.com/repos/zlxlabs/gate/issues/42/comments"
    assert request.get_method() == "POST"
    assert request.captured_timeout == 15
    assert json.loads(request.data.decode("utf-8")) == {"body": "hello gate"}

def test_panel_marker_candidates_require_own_author_and_choose_earliest(monkeypatch):
    owner = {"id": 99, "login": "workflow-bot"}
    comments = [
        {"id": 1, "created_at": "2026-08-16T00:00:00Z", "body": AGG.PANEL_MARKER, "user": {"id": 1, "login": "human"}},
        {"id": 3, "created_at": "2026-08-16T00:02:00Z", "body": AGG.PANEL_MARKER, "user": owner},
        {"id": 2, "created_at": "2026-08-16T00:01:00Z", "body": AGG.PANEL_MARKER, "user": owner},
    ]
    selected = AGG._find_panel_comments(comments, owner)
    assert [comment["id"] for comment in selected] == [2, 3]
    assert all(comment["user"]["id"] == owner["id"] for comment in selected)

def test_panel_marker_matching_prefers_id_over_login():
    owner = {"id": 99, "login": "workflow-bot"}
    comments = [
        {"id": 1, "created_at": "2026-08-16T00:00:00Z", "body": AGG.PANEL_MARKER, "user": {"id": 7, "login": "workflow-bot"}},
        {"id": 2, "created_at": "2026-08-16T00:01:00Z", "body": AGG.PANEL_MARKER, "user": {"login": "workflow-bot"}},
    ]

    selected = AGG._find_panel_comments(comments, owner)

    assert [comment["id"] for comment in selected] == [2]

def test_panel_comment_lookup_paginates_all_pages(monkeypatch):
    owner = {"id": 99, "login": "workflow-bot"}
    page_one = [{"id": i, "body": "ordinary", "user": owner} for i in range(100)]
    page_two = [{"id": 101, "created_at": "2026-08-16T00:00:00Z", "body": AGG.PANEL_MARKER, "user": owner}]
    calls = []
    def fake_json(**kwargs):
        calls.append(kwargs["url"])
        return page_one if "&page=1" in kwargs["url"] else page_two
    monkeypatch.setattr(AGG, "_github_json", fake_json)
    comments = AGG._fetch_panel_comments(token="tok", repository="zlxlabs/gate", pr_number=42)
    assert len(comments) == 101
    assert calls == [
        "https://api.github.com/repos/zlxlabs/gate/issues/42/comments?per_page=100&page=1",
        "https://api.github.com/repos/zlxlabs/gate/issues/42/comments?per_page=100&page=2",
    ]

def test_post_self_heal_deletes_duplicate_and_patches_earliest_own_panel(monkeypatch):
    current = _panel_terminal_row(5, 1, "pass", "e" * 40)
    comments = [
        [],
        [
            {"id": 20, "created_at": "2026-08-16T00:00:00Z", "body": AGG.PANEL_MARKER, "user": {"id": 99, "login": "workflow-bot"}},
            {"id": 21, "created_at": "2026-08-16T00:01:00Z", "body": AGG.PANEL_MARKER, "user": {"id": 99, "login": "workflow-bot"}},
        ],
    ]
    operations = []
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: {"id": 99, "login": "workflow-bot"})
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: comments.pop(0))
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=[]))
    monkeypatch.setattr(AGG, "_post_issue_comment", lambda **kwargs: operations.append(("POST", kwargs["body"])))
    monkeypatch.setattr(AGG, "_patch_issue_comment", lambda **kwargs: operations.append(("PATCH", kwargs["comment_id"], kwargs["body"])))
    monkeypatch.setattr(AGG, "_delete_issue_comment", lambda **kwargs: operations.append(("DELETE", kwargs["comment_id"])))

    _, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
        identity=IDENTITY,
    )
    assert operations[0][0] == "POST"
    assert ("PATCH", 20, operations[1][2]) in operations
    assert ("DELETE", 21) in operations
    assert receipt["comment_created"] is True

def test_existing_panel_cache_is_parsed_and_incomplete_history_is_explicit():
    body = "\n".join([
        AGG.PANEL_MARKER,
        "| Run | Attempt | Head | 状态 | 收件人动作 |",
        "| ---: | ---: | :--- | :--- | :--- |",
        "| [7](https://github.com/zlxlabs/gate/actions/runs/7) | 1 | `abcdef1` | `fail` | 要修代码 |",
    ])
    cached = AGG._parse_panel_history(body)
    assert cached[0]["run_id"] == 7
    rendered = AGG.render_status_panel(
        [_panel_terminal_row(8, 1, "pass", "b" * 40), *cached],
        history_warning="expired artifact gate-terminal-v1-123-old",
    )
    assert "历史可能不完整" in rendered
    assert "| [7]" in rendered and "| [8]" in rendered

def test_existing_panel_cache_is_unioned_when_artifact_history_is_incomplete(monkeypatch):
    current = _panel_terminal_row(8, 1, "pass", "b" * 40)
    owner = {"id": 99, "login": "workflow-bot"}
    cached_body = AGG.render_status_panel([_panel_terminal_row(7, 1, "fail", "a" * 40)])
    comments = [{"id": 7, "created_at": "2026-08-16T00:00:00Z", "body": cached_body, "user": owner}]
    operations = []
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: comments)
    monkeypatch.setattr(
        AGG, "_fetch_terminal_history",
        lambda **kwargs: AGG.HistoryLoad(
            rows=[current],
            skipped_records=[{"name": "gate-terminal-v1-123-old", "reason": "expired_artifact"}],
            incomplete_reasons=["expired artifact gate-terminal-v1-123-old"],
        ),
    )
    monkeypatch.setattr(AGG, "_patch_issue_comment", lambda **kwargs: operations.append(kwargs["body"]))
    body, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
        identity=IDENTITY,
    )
    assert receipt["history_incomplete"] is True
    assert receipt["history_skipped_count"] == 1
    assert "历史可能不完整" in body
    assert "| [7]" in body and "| [8]" in body
    assert operations == [body]

def test_existing_panel_cache_is_incomplete_when_artifact_history_is_empty(monkeypatch):
    current = _panel_terminal_row(8, 1, "pass", "b" * 40)
    owner = {"id": 99, "login": "workflow-bot"}
    cached_body = AGG.render_status_panel([_panel_terminal_row(7, 1, "fail", "a" * 40)])
    comments = [{"id": 7, "created_at": "2026-08-16T00:00:00Z", "body": cached_body, "user": owner}]
    operations = []
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: comments)
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=[]))
    monkeypatch.setattr(AGG, "_patch_issue_comment", lambda **kwargs: operations.append(kwargs["body"]))

    body, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
        identity=IDENTITY,
    )

    assert receipt["history_incomplete"] is True
    assert "历史可能不完整" in body
    assert "1 个制品历史缺少面板缓存行" in body
    assert receipt["history_incomplete_reasons"] == ["artifact history does not contain cached rows: 7/1"]
    assert operations == [body]

def test_existing_panel_cache_is_complete_when_artifact_history_covers_it(monkeypatch):
    current = _panel_terminal_row(8, 1, "pass", "b" * 40)
    cached = _panel_terminal_row(7, 1, "fail", "a" * 40)
    owner = {"id": 99, "login": "workflow-bot"}
    cached_body = AGG.render_status_panel([cached])
    comments = [{"id": 7, "created_at": "2026-08-16T00:00:00Z", "body": cached_body, "user": owner}]
    operations = []
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: comments)
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=[cached, current]))
    monkeypatch.setattr(AGG, "_patch_issue_comment", lambda **kwargs: operations.append(kwargs["body"]))

    body, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
        identity=IDENTITY,
    )

    assert receipt["history_incomplete"] is False
    assert "历史可能不完整" not in body
    assert receipt["reason_code"] == "patched"
    assert operations == [body]

def test_history_api_failure_preserves_existing_panel_body_and_records_diagnostic(monkeypatch):
    current = _panel_terminal_row(8, 1, "pass", "b" * 40)
    owner = {"id": 99, "login": "workflow-bot"}
    old_body = AGG.render_status_panel([_panel_terminal_row(7, 1, "fail", "a" * 40)])
    comments = [{"id": 7, "created_at": "2026-08-16T00:00:00Z", "body": old_body, "user": owner}]
    error = urllib.error.HTTPError("https://api.github.com/actions/artifacts", 503, "down", hdrs=None, fp=None)
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: comments)
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: (_ for _ in ()).throw(error))
    monkeypatch.setattr(AGG, "_patch_issue_comment", lambda **kwargs: (_ for _ in ()).throw(AssertionError("must preserve old body")))
    body, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42, identity=IDENTITY,
    )
    assert body == old_body
    assert receipt["delivery"] == "not_created"
    assert receipt["reason_code"] == "history_unavailable"
    assert receipt["http_status"] == 503
    assert receipt["history_error"]

def test_history_skip_count_is_written_to_step_summary(tmp_path, capsys):
    summary = tmp_path / "summary.md"
    receipt = AGG._build_panel_delivery(
        body="panel", repository="zlxlabs/gate", pr_number=42, identity=IDENTITY,
        delivery="updated", reason_code="patched",
        history_skipped_records=[{"name": "old", "reason": "expired_artifact"}],
    )
    AGG._append_panel_diagnostic(str(summary), receipt)
    assert "Skipped history records: `1`" in summary.read_text(encoding="utf-8")
    assert "`old`: `expired_artifact`" in capsys.readouterr().out

def test_status_panel_aggregates_repeated_http_reasons_and_keeps_receipt_detail():
    reasons = [
        f"artifact gate-terminal-v1-123-{index}: HTTPError: HTTP Error 401: signed blob rejected"
        for index in range(21)
    ]
    body = AGG.render_status_panel(
        [_panel_row(1, 1, "pass")], history_reasons=reasons,
    )
    warning_line = next(line for line in body.splitlines() if line.startswith("> 历史可能不完整："))
    receipt = AGG._build_panel_delivery(
        body=body, repository="zlxlabs/gate", pr_number=42, identity=IDENTITY,
        delivery="updated", reason_code="history_incomplete",
        history_incomplete_reasons=reasons,
    )

    assert "21 个制品下载失败：HTTP 401" in body
    assert body.count("制品下载失败：HTTP 401") == 1
    assert len(warning_line) <= AGG.MAX_HISTORY_WARNING_CHARS
    assert receipt["history_incomplete_reasons"] == reasons
    assert len(receipt["history_incomplete_reasons"]) == 21

    long_body = AGG.render_status_panel(
        [_panel_row(1, 1, "pass")],
        history_reasons=[f"unclassified reason {index}: {'x' * 40}" for index in range(21)],
    )
    long_warning_line = next(
        line for line in long_body.splitlines() if line.startswith("> 历史可能不完整：")
    )
    assert len(long_warning_line) <= AGG.MAX_HISTORY_WARNING_CHARS
    assert "完整明细见 delivery 诊断制品" in long_warning_line

def test_silo_objects_under_has_no_in_process_client_branch(monkeypatch):
    # Silo 控制路径零运行期装包：旧的进程内直连分支（含第三方 SDK 探测）已
    # 删除，模块不再提供 _silo_store_mod；唯一出口是纯标准库 CLI（打桩断言）。
    import subprocess

    assert not hasattr(AGG, "_silo_store_mod")
    prefix = f"d30/{_CANARY_REPO_ID}/"
    canonical_key = f"d30/{_CANARY_REPO_ID}/artifact-abc/receipt.json"
    payload = b'{"receipt": true}'
    recorded_calls = []

    def fake_cli(argv, timeout_s):
        recorded_calls.append(list(argv))
        assert "--dest" in argv
        dest_dir = Path(argv[argv.index("--dest") + 1])
        parts = canonical_key.split("/", 3)
        file_path = dest_dir / parts[2] / parts[3]
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_bytes(payload)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout=f"{canonical_key}\n", stderr="")

    monkeypatch.setattr(AGG, "_silo_cli", fake_cli)
    objects = AGG._silo_objects_under(prefix, AGG.SILO_CLI_CALL_TIMEOUT_SECONDS)
    assert objects == [(canonical_key, payload)]
    assert len(recorded_calls) == 1
    assert recorded_calls[0][0:3] == ["list", "--prefix", prefix]

def test_silo_cli_argv_uses_interpreter_without_package_manager(monkeypatch):
    # 跨进程边界断言：聚合器实际发出的 argv 必须是 [解释器, 仓内脚本, ...]，
    # 不得出现 uv/uvx/pip/npx。每次调用必须带墙钟 timeout（gate#290 教训：
    # 无界 Silo 子进程会拖死汇总 job），由调用点显式声明预算来源。
    recorded = []

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kwargs):
        recorded.append((list(argv), kwargs.copy()))
        return _Proc()

    monkeypatch.setattr(AGG.subprocess, "run", fake_run)
    monkeypatch.setenv("SILO_STORE", "/tmp/fake-silo-store.py")
    AGG._silo_cli(["list", "--prefix", "d30/1/"], 30)
    assert len(recorded) == 1
    argv, kwargs = recorded[0]
    assert kwargs["timeout"] == 30
    assert argv[0] == sys.executable
    assert argv[1] == "/tmp/fake-silo-store.py"
    assert argv[2:] == ["list", "--prefix", "d30/1/"]
    joined = " ".join(argv)
    for banned in ("uv run", "uvx", "pip install", "npx", "--with"):
        assert banned not in joined

def _string_values(value):
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [part for nested in value.values() for part in _string_values(nested)]
    if isinstance(value, list):
        return [part for nested in value for part in _string_values(nested)]
    return []

def test_render_previous_context_silo_unconfigured_has_complete_empty_contract(tmp_path, monkeypatch, capsys):
    _, _, objects = _previous_round_fixture_objects()
    rc, payload = _render_previous_round_fixture(
        tmp_path, monkeypatch, objects, silo_configured=False,
    )
    out = capsys.readouterr().out

    assert rc == 0
    assert payload == {"available": False, "detail": "silo-not-configured", "head_sha": None, "findings": [], "dispositions": [], "open_findings": []}
    assert "GATE-FINDING-RELATION-DEGRADED: source=previous-ledger detail=silo-not-configured" in out

def _latest_store(tmp_path, *, body):
    argv_log = tmp_path / "silo-argv.jsonl"
    body_path = tmp_path / "latest-body.jsonl"
    if body is not None:
        body_path.write_bytes(body)
    store = tmp_path / "fake_silo.py"
    store.write_text(
        "import json, pathlib, sys\n"
        f"log = pathlib.Path({str(argv_log)!r})\n"
        "with log.open('a', encoding='utf-8') as fh:\n"
        "    fh.write(json.dumps(sys.argv[1:]) + chr(10))\n"
        "args = sys.argv[1:]\n"
        "if args[0] != 'get':\n"
        "    raise SystemExit('list must not be called' if args[0] == 'list' else args[0])\n"
        f"src = pathlib.Path({str(body_path)!r})\n"
        "if not src.exists():\n"
        "    raise SystemExit(2)\n"
        "dest = pathlib.Path(args[args.index('--dest') + 1])\n"
        "dest.parent.mkdir(parents=True, exist_ok=True)\n"
        "dest.write_bytes(src.read_bytes())\n",
        encoding="utf-8",
    )
    return store, argv_log

def _assert_single_latest_get(argv_log, key):
    argvs = [json.loads(line) for line in argv_log.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(argvs) == 1
    argv = argvs[0]
    assert argv[0] == "get"
    assert argv[argv.index("--key") + 1] == key
    assert "list" not in argv

def test_previous_findings_get_latest_once_without_list(tmp_path, monkeypatch):
    ledger, _, _ = _previous_round_fixture_objects()
    repo_id = ledger["primary_identity"]["repository_id"]
    key = AGG._ledger_latest_key(repo_id, ledger["pr_number"])
    store, argv_log = _latest_store(tmp_path, body=(ROOT / "tests/fixtures/previous-round-pr1063-ledger.json").read_bytes())
    monkeypatch.setenv("SILO_STORE", str(store))
    monkeypatch.setenv("SILO_ENDPOINT", "https://silo.example.test:9000")
    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    previous = AGG.load_previous_round_findings(
        path=None, repository_id=repo_id, pr_number=ledger["pr_number"], run_id=35989919390, run_attempt=1,
    )
    assert set(previous) == {"available", "detail", "head_sha", "findings"}
    assert previous["available"] is True and previous["head_sha"] == ledger["head_sha"]
    assert any(item.get("id") == "reliability_alternates_survive_hard_kill" for item in previous["findings"])
    _assert_single_latest_get(argv_log, key)

def test_previous_findings_absent_latest_degrades_without_list(tmp_path, monkeypatch, capsys):
    ledger, _, _ = _previous_round_fixture_objects()
    repo_id = ledger["primary_identity"]["repository_id"]
    key = AGG._ledger_latest_key(repo_id, ledger["pr_number"])
    store, argv_log = _latest_store(tmp_path, body=None)
    monkeypatch.setenv("SILO_STORE", str(store))
    monkeypatch.setenv("SILO_ENDPOINT", "https://silo.example.test:9000")
    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    previous = AGG.load_previous_round_findings(
        path=None, repository_id=repo_id, pr_number=ledger["pr_number"],
        run_id=35989919390, run_attempt=1,
    )
    assert previous == {
        "available": False, "detail": "silo-ledger-latest-absent", "head_sha": None, "findings": [],
    }
    assert "GATE-FINDING-RELATION-DEGRADED: source=previous-ledger detail=silo-ledger-latest-absent" in capsys.readouterr().out
    _assert_single_latest_get(argv_log, key)

def test_previous_findings_not_prior_latest_degrades_without_list(tmp_path, monkeypatch, capsys):
    ledger, _, _ = _previous_round_fixture_objects()
    repo_id = ledger["primary_identity"]["repository_id"]
    key = AGG._ledger_latest_key(repo_id, ledger["pr_number"])
    store, argv_log = _latest_store(
        tmp_path, body=(ROOT / "tests/fixtures/previous-round-pr1063-ledger.json").read_bytes(),
    )
    monkeypatch.setenv("SILO_STORE", str(store))
    monkeypatch.setenv("SILO_ENDPOINT", "https://silo.example.test:9000")
    monkeypatch.setattr(AGG, "_silo_configured", lambda: True)
    previous = AGG.load_previous_round_findings(
        path=None, repository_id=repo_id, pr_number=ledger["pr_number"],
        run_id=ledger["run_id"], run_attempt=ledger["run_attempt"],
    )
    assert previous == {
        "available": False, "detail": "silo-ledger-latest-not-prior", "head_sha": None, "findings": [],
    }
    assert "GATE-FINDING-RELATION-DEGRADED: source=previous-ledger detail=silo-ledger-latest-not-prior" in capsys.readouterr().out
    _assert_single_latest_get(argv_log, key)

def _previous_round_fixture_objects():
    ledger_path = ROOT / "tests/fixtures/previous-round-pr1063-ledger.json"
    receipt_path = ROOT / "tests/fixtures/previous-round-pr1063-disposition.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    evidence = dict(receipt.get("counterevidence") or {})
    if evidence and "gate_rerun" not in evidence:
        evidence["gate_rerun"] = _gate_rerun(receipt["head_sha"])
        receipt["counterevidence"] = evidence
    receipt_bytes = json.dumps(receipt, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    repository_id = ledger["primary_identity"]["repository_id"]
    ledger_name = (
        f"codex-review-ledger-v2-{repository_id}-{ledger['head_sha']}-"
        f"{ledger['run_id']}-{ledger['run_attempt']}"
    )
    ledger_key = f"d30/{repository_id}/{ledger_name}/ledger.json"
    latest_key = AGG._ledger_latest_key(repository_id, ledger["pr_number"])
    receipt_name = CONV.disposition_receipt_artifact_name(CONV.parse_disposition_receipt(receipt))
    receipt_key = f"d30/{repository_id}/{receipt_name}/{receipt_name}.json"
    ledger_bytes = ledger_path.read_bytes()
    return ledger, receipt, [
        (ledger_key, ledger_bytes),
        (latest_key, ledger_bytes),
        (receipt_key, receipt_bytes),
    ]



def _render_previous_round_fixture(
    tmp_path, monkeypatch, silo_objects, *, silo_configured=True,
    run_id=35989919390, run_attempt=1,
):
    ledger, _, _ = _previous_round_fixture_objects()
    by_key = dict(silo_objects)

    def fake_silo_cli(argv, timeout_s):
        if argv[0] == "list":
            raise AssertionError("previous-round findings must not list Silo objects")
        key = argv[argv.index("--key") + 1]
        dest = argv[argv.index("--dest") + 1]
        body = by_key.get(key)
        if body is None:
            return subprocess.CompletedProcess(argv, 2, stdout="", stderr="Silo object not found")
        Path(dest).write_bytes(body)
        return subprocess.CompletedProcess(argv, 0, stdout=dest, stderr="")

    monkeypatch.setattr(AGG, "_silo_configured", lambda: silo_configured)
    monkeypatch.setattr(AGG, "_silo_cli", fake_silo_cli)
    monkeypatch.setattr(
        AGG, "_silo_objects_under",
        lambda prefix, timeout_s: [(key, raw) for key, raw in silo_objects if key.startswith(prefix)],
    )
    output = tmp_path / "previous-findings.json"
    rc = AGG.main([
        "--render-previous-context",
        "--repository-id", str(ledger["primary_identity"]["repository_id"]),
        "--pr-number", str(ledger["pr_number"]),
        "--run-id", str(run_id),
        "--run-attempt", str(run_attempt),
        "--previous-json", str(output),
    ])
    return rc, json.loads(output.read_text(encoding="utf-8"))
