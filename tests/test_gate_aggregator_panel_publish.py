from test_gate_aggregator import (
    AGG,
    IDENTITY,
    MODULE_PATH,
    Path,
    ROOT,
    _FakeResponse,
    _cli_args,
    _panel_terminal_row,
    _valid_primary_record,
    ast,
    io,
    json,
    os,
    patch,
    pytest,
    socket,
    subprocess,
    sys,
    threading,
    urllib,
    zipfile,
)

def test_status_panel_publisher_creates_once_then_patches(monkeypatch):
    current = _panel_terminal_row(5, 1, "pass", "e" * 40)
    history = [_panel_terminal_row(i, 1, result, chr(96 + i) * 40) for i, result in enumerate(
        ["skipped", "fail", "fail", "unavailable"], start=1
    )]
    owner = {"id": 99, "login": "workflow-bot"}
    existing_comment = {"id": 77, "created_at": "2026-08-16T00:00:00Z", "body": AGG.PANEL_MARKER + "\nold", "user": owner}
    comments = [[], [existing_comment], [existing_comment]]
    operations = []

    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: comments.pop(0))
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=history))
    monkeypatch.setattr(
        AGG, "_post_issue_comment",
        lambda **kwargs: operations.append(("POST", kwargs["body"])),
    )
    monkeypatch.setattr(
        AGG, "_patch_issue_comment",
        lambda **kwargs: operations.append(("PATCH", kwargs["comment_id"], kwargs["body"])),
    )

    body, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
        identity=IDENTITY,
    )
    assert receipt["delivery"] == "created"
    assert operations[0][0] == "POST"
    assert body.count("| [") == 5

    body, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
        identity=IDENTITY,
    )
    assert receipt["delivery"] == "updated"
    assert operations[1][0:2] == ("PATCH", 77)
    assert body.count("| [") == 5
    assert sum(operation[0] == "POST" for operation in operations) == 1

def test_status_panel_scrub_failure_prevents_github_write(monkeypatch):
    operations = []
    monkeypatch.setattr(AGG, "scrub_for_publish", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("scrub failed")))
    monkeypatch.setattr(AGG, "_post_issue_comment", lambda **kwargs: operations.append(kwargs))

    with pytest.raises(RuntimeError, match="scrub failed"):
        AGG._post_status_panel_fail_open(
            current=_panel_terminal_row(1, 1, "pass", "a" * 40),
            repository="org/repo", repository_id=123, pr_number=7, identity=IDENTITY,
        )

    assert operations == []


def test_aggregate_step_summary_scrubs_runtime_values(monkeypatch, tmp_path):
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("RUNNER_NAME", "runner-secret")
    outcome = AGG.Outcome(
        ok=True,
        notes=["runner-secret"],
        classification="code_pass",
        reason_code="primary_pass",
        gate_result="pass",
    )

    assert AGG._finish(outcome, str(summary)) == 0
    text = summary.read_text()
    assert "runner-secret" not in text
    assert "[REDACTED:RUNNER_NAME]" in text

@pytest.mark.parametrize("status", [403, 404])
def test_installation_token_identity_403_or_404_falls_back_and_publishes(monkeypatch, status):
    current = _panel_terminal_row(5, 1, "pass", "e" * 40)
    identity_error = urllib.error.HTTPError("https://api.github.com/user", status, "forbidden", hdrs=None, fp=None)
    calls = []
    operations = []

    def fake_json(**kwargs):
        url = kwargs["url"]
        calls.append(url)
        if url == "https://api.github.com/user":
            raise identity_error
        if "/issues/42/comments" in url:
            return []
        if "/actions/artifacts" in url:
            return {"artifacts": []}
        raise AssertionError(f"unexpected GitHub API call: {url}")

    monkeypatch.setenv("GH_TOKEN", "installation-token")
    monkeypatch.setattr(AGG, "_github_json", fake_json)
    monkeypatch.setattr(AGG, "_post_issue_comment", lambda **kwargs: operations.append(kwargs["body"]))

    _, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
        identity=IDENTITY,
    )

    assert calls.count("https://api.github.com/user") == 1
    assert operations and AGG.PANEL_MARKER in operations[0]
    assert receipt["delivery"] == "created"
    assert receipt["operation"] == "POST"
    assert receipt["identity_source"] == "actions_bot_fallback"

def test_identity_http_500_is_reported_as_identity_failure(monkeypatch):
    error = urllib.error.HTTPError("https://api.github.com/user", 500, "server error", hdrs=None, fp=None)
    monkeypatch.setenv("GH_TOKEN", "installation-token")
    monkeypatch.setattr(AGG, "_github_json", lambda **kwargs: (_ for _ in ()).throw(error))

    _, receipt = AGG._post_status_panel_fail_open(
        current=_panel_terminal_row(1, 1, "pass", "a" * 40),
        repository="zlxlabs/gate", repository_id=123, pr_number=42, identity=IDENTITY,
    )

    assert receipt["operation"] == "IDENTITY"
    assert receipt["http_status"] == 500
    assert receipt["error_category"] == "server_error"

def test_status_panel_rebuild_after_comment_deletion_uses_terminal_history(monkeypatch):
    current = _panel_terminal_row(5, 1, "pass", "e" * 40)
    history = [_panel_terminal_row(i, 1, result, chr(96 + i) * 40) for i, result in enumerate(
        ["skipped", "fail", "fail", "unavailable"], start=1
    )]
    operations = []
    monkeypatch.setenv("GH_TOKEN", "tok")
    owner = {"id": 99, "login": "workflow-bot"}
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: [])
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=history))
    monkeypatch.setattr(
        AGG, "_post_issue_comment",
        lambda **kwargs: operations.append(("POST", kwargs["body"])),
    )
    body, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
        identity=IDENTITY,
    )
    assert receipt["delivery"] == "created"
    assert sum(operation[0] == "POST" for operation in operations) == 1
    assert body.count("| [") == 5
    assert "当前状态：**pass** · **可合并**" in body

def test_existing_panel_history_targets_cached_runs_without_repo_wide_scan(monkeypatch):
    current = _panel_terminal_row(10, 1, "pass", "j" * 40)
    cached = [_panel_terminal_row(run_id, 1, "fail", chr(96 + run_id) * 40) for run_id in (7, 8, 9)]
    owner = {"id": 99, "login": "workflow-bot"}
    comments = [{"id": 7, "created_at": "2026-08-16T00:00:00Z", "body": AGG.render_status_panel(cached), "user": owner}]
    calls = []
    operations = []

    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: comments)
    monkeypatch.setattr(AGG, "_github_json", lambda **kwargs: calls.append(kwargs["url"]) or {"artifacts": []})
    monkeypatch.setattr(AGG, "_patch_issue_comment", lambda **kwargs: operations.append(kwargs["body"]))

    body, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
        identity=IDENTITY,
    )

    assert calls == [
        "https://api.github.com/repos/zlxlabs/gate/actions/runs/7/artifacts",
        "https://api.github.com/repos/zlxlabs/gate/actions/runs/8/artifacts",
        "https://api.github.com/repos/zlxlabs/gate/actions/runs/9/artifacts",
    ]
    assert all("/actions/artifacts?" not in url for url in calls)
    assert receipt["history_incomplete"] is True
    assert operations == [body]

def test_targeted_history_run_count_is_bounded(monkeypatch):
    calls = []
    monkeypatch.setattr(AGG, "_github_json", lambda **kwargs: calls.append(kwargs["url"]) or {"artifacts": []})

    result = AGG._fetch_terminal_history(
        token="tok", repository="zlxlabs/gate", repository_id=123, pr_number=42,
        target_run_ids=list(range(1, AGG.MAX_TARGETED_HISTORY_RUNS + 7)),
    )

    assert len(calls) == AGG.MAX_TARGETED_HISTORY_RUNS
    assert any("bounded_scan" in reason for reason in result.incomplete_reasons)

def test_repo_wide_history_scan_stops_at_page_limit(monkeypatch):
    calls = []

    def fake_json(**kwargs):
        calls.append(kwargs["url"])
        return {"artifacts": [{"name": f"unrelated-{len(calls)}-{index}"} for index in range(100)]}

    monkeypatch.setattr(AGG, "_github_json", fake_json)
    result = AGG._fetch_terminal_history(
        token="tok", repository="zlxlabs/gate", repository_id=123, pr_number=42,
    )

    assert len(calls) == AGG.MAX_REPO_WIDE_HISTORY_PAGES
    assert calls[-1].endswith(f"page={AGG.MAX_REPO_WIDE_HISTORY_PAGES}")
    assert any("bounded_scan" in reason for reason in result.incomplete_reasons)

def test_fetch_terminal_history_consumes_a_persisted_terminal_artifact(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(_valid_primary_record()))
    terminal_path = tmp_path / "gate-terminal.json"
    summary_path = tmp_path / "summary.md"
    assert AGG.main(_cli_args(audit_dir, summary_path, terminal_path=str(terminal_path))) == 0
    terminal_bytes = terminal_path.read_bytes()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("gate-terminal.json", terminal_bytes)

    monkeypatch.setattr(
        AGG, "_github_json",
        lambda **kwargs: {"artifacts": [{
            "name": "gate-terminal-v1-123-" + "a" * 40 + "-999-1",
            "expired": False,
            "archive_download_url": "https://api.github.com/archive/1",
        }]},
    )
    monkeypatch.setattr(AGG, "_download_terminal_zip", lambda **kwargs: archive.getvalue())
    rows = AGG._fetch_terminal_history(
        token="tok", repository="zlxlabs/gate", repository_id=123, pr_number=42,
    )
    assert rows.rows[0]["run_id"] == IDENTITY.run_id
    assert rows.rows[0]["gate_result"] == "pass"

def test_terminal_artifact_redirect_drops_auth_for_blob_download(monkeypatch):
    record = {
        "schema_version": 1, "kind": "gate_terminal", "repository": "zlxlabs/gate",
        "repository_id": 123, "pr_number": 42, "run_id": 9, "run_attempt": 1,
        "head_sha": "a" * 40, "gate_result": "pass", "classification": "code_pass",
        "reason_code": "primary_pass",
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("gate-terminal.json", json.dumps(record))
    archive_url = "https://api.github.com/repos/zlxlabs/gate/actions/artifacts/9/zip"
    blob_url = "https://blob.example.test/signed-artifact.zip?sig=secret"
    calls = []

    class RedirectStub:
        def open(self, request, timeout=None):
            headers = dict(request.header_items())
            calls.append((request.full_url, headers, timeout))
            if request.full_url == archive_url:
                raise urllib.error.HTTPError(
                    archive_url, 302, "redirect", {"Location": blob_url}, None,
                )
            assert request.full_url == blob_url
            assert "Authorization" not in headers
            return _FakeResponse(archive.getvalue())

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: RedirectStub())
    monkeypatch.setattr(
        urllib.request, "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            urllib.error.HTTPError(archive_url, 302, "redirect", {"Location": blob_url}, None)
        ),
    )
    monkeypatch.setattr(
        AGG, "_github_json",
        lambda **kwargs: {"artifacts": [{
            "name": "gate-terminal-v1-123-valid", "expired": False,
            "archive_download_url": archive_url,
        }]},
    )

    result = AGG._fetch_terminal_history(
        token="tok", repository="zlxlabs/gate", repository_id=123, pr_number=42,
    )

    assert [row["run_id"] for row in result.rows] == [9]
    assert [call[0] for call in calls] == [archive_url, blob_url]
    assert calls[0][1]["Authorization"] == "Bearer tok"
    assert "Authorization" not in calls[1][1]

@pytest.mark.parametrize("existing", [False, True], ids=["POST", "PATCH"])
def test_status_panel_post_or_patch_failure_is_fail_open(monkeypatch, existing):
    error = urllib.error.HTTPError("https://api.github.com/x", 403, "forbidden", hdrs=None, fp=None)
    current = _panel_terminal_row(1, 1, "pass", "a" * 40)
    monkeypatch.setenv("GH_TOKEN", "tok")
    owner = {"id": 99, "login": "workflow-bot"}
    comments = [{"id": 7, "created_at": "2026-08-16T00:00:00Z", "body": AGG.PANEL_MARKER, "user": owner}] if existing else []
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: comments)
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=[]))
    requests = []
    if existing:
        def fail_patch(**kwargs):
            requests.append("PATCH")
            raise error
        monkeypatch.setattr(AGG, "_patch_issue_comment", fail_patch)
    else:
        def fail_post(**kwargs):
            requests.append("POST")
            raise error
        monkeypatch.setattr(AGG, "_post_issue_comment", fail_post)
    _, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
        identity=IDENTITY,
    )
    assert receipt["http_status"] == 403
    assert receipt["error_category"] == "permission_or_rate_limit"
    assert receipt["comment_created"] is False
    assert requests == ["PATCH" if existing else "POST"]
    assert receipt["attempts"] == 1
    assert receipt["exception_type"] == "HTTPError"
    assert receipt["operation"] == ("comment_patch" if existing else "comment_create")
    assert receipt["reason_code"] == "http_403"

def test_status_panel_mail_invariant_for_five_runs(monkeypatch):
    states = ["skipped", "fail", "fail", "unavailable", "pass"]
    rows = []
    comments = []
    operations = []
    monkeypatch.setenv("GH_TOKEN", "tok")
    owner = {"id": 99, "login": "workflow-bot"}

    def fake_comments(**kwargs):
        return comments

    def post(**kwargs):
        operations.append("POST")
        comments[:] = [{"id": 1, "created_at": "2026-08-16T00:00:00Z", "body": kwargs["body"], "user": owner}]

    def patch(**kwargs):
        operations.append("PATCH")
        comments[0]["body"] = kwargs["body"]

    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", fake_comments)
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=list(rows)))
    monkeypatch.setattr(AGG, "_post_issue_comment", post)
    monkeypatch.setattr(AGG, "_patch_issue_comment", patch)

    for run_id, state in enumerate(states, start=1):
        current = _panel_terminal_row(run_id, 1, state, chr(96 + run_id) * 40)
        AGG._post_status_panel_fail_open(
            current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
            identity=IDENTITY,
        )
        rows.append(current)

    assert operations.count("POST") == 1
    assert operations.count("PATCH") == 4
    assert len(comments) == 1
    assert comments[0]["body"].count("| [") == 5
    assert "当前状态：**pass** · **可合并**" in comments[0]["body"]

def test_terminal_artifact_written_by_real_aggregate_is_panel_history_producer_fixture(monkeypatch, tmp_path):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(_valid_primary_record()))
    terminal_path = tmp_path / "gate-terminal.json"
    summary_path = tmp_path / "summary.md"
    args = _cli_args(audit_dir, summary_path, terminal_path=str(terminal_path))
    assert AGG.main(args) == 0
    record = json.loads(terminal_path.read_text(encoding="utf-8"))
    row = AGG._terminal_row(record, repository="zlxlabs/gate", repository_id=123, pr_number=42)
    assert row["schema_version"] == AGG.PANEL_HISTORY_ROW_SCHEMA_VERSION
    assert AGG.PANEL_MARKER in AGG.render_status_panel([row])

def test_publish_only_consumes_the_real_terminal_producer_fixture_after_upload(monkeypatch, tmp_path):
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
    operations = []
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_panel_comments", lambda **kwargs: [])
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=[]))
    monkeypatch.setattr(AGG, "_post_issue_comment", lambda **kwargs: operations.append(kwargs["body"]))
    publish_args = _cli_args(
        audit_dir, summary_path, terminal_path=str(terminal_path), panel_delivery_path=str(delivery_path),
    ) + ["--publish-only"]
    assert AGG.main(publish_args) == 0
    receipt = json.loads(delivery_path.read_text(encoding="utf-8"))
    assert receipt["delivery"] == "created"
    assert receipt["history_incomplete"] is False
    assert receipt["completed_operations"] == [
        "IDENTITY", "COMMENT_LOOKUP", "HISTORY_RECONSTRUCTION", "COMMENT_PUBLISH", "POST_VERIFY",
    ]
    assert receipt["pending_operations"] == []
    assert operations and AGG.PANEL_MARKER in operations[0]

def test_publish_only_subprocess_writes_additive_delivery_receipt_bytes(monkeypatch, tmp_path):
    # Captured from base 85916ed's real _build_panel_delivery producer for this terminal row.
    old_receipt = {
        "comment_body_sha256": "e1eec6dfd3e6568367e09430ba0d4fd4f24924429ecfbe977fe448d6766cf370",
        "comment_created": True, "comment_expected": True,
        "completed_operations": ["IDENTITY", "COMMENT_LOOKUP", "HISTORY_RECONSTRUCTION", "COMMENT_PUBLISH", "POST_VERIFY"],
        "delivery": "created", "error_category": None, "head_sha": "a" * 40,
        "history_error": None, "history_incomplete": False, "history_incomplete_reasons": [],
        "history_skipped_count": 0, "history_skipped_records": [], "http_status": None,
        "identity_source": "user_api", "kind": "gate_v2_status_panel_delivery",
        "operation": "POST", "pending_operations": [], "pr_number": 42, "reason_code": "posted",
        "repository": "zlxlabs/gate", "repository_id": 123, "run_attempt": 1, "run_id": 999,
        "schema_version": 1, "self_heal_errors": [],
    }
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(_valid_primary_record()))
    terminal_path, summary_path = tmp_path / "terminal.json", tmp_path / "summary.md"
    assert AGG.main(_cli_args(audit_dir, summary_path, terminal_path=str(terminal_path))) == 0
    owner = {"id": 99, "login": "workflow-bot"}

    import http.server

    class Api(http.server.BaseHTTPRequestHandler):
        def respond(self, value):
            payload = json.dumps(value).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args):
            pass

        def do_GET(self):
            if self.path == "/user":
                return self.respond(owner)
            if self.path.startswith("/repos/zlxlabs/gate/issues/42/comments"):
                return self.respond(self.server.comments)
            if self.path.startswith("/repos/zlxlabs/gate/actions/artifacts"):
                return self.respond({"artifacts": []})
            self.send_error(404)

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.server.comments.append({
                "id": 1, "created_at": "2026-09-30T00:00:00Z", "body": body["body"], "user": owner,
            })
            self.respond({})

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Api)
    server.comments = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        site_dir = tmp_path / "site"
        site_dir.mkdir()
        (site_dir / "sitecustomize.py").write_text(
            "import urllib.request\n"
            "_urlopen = urllib.request.urlopen\n"
            f"_local = 'http://127.0.0.1:{server.server_port}'\n"
            "def urlopen(request, *args, **kwargs):\n"
            "    if isinstance(request, urllib.request.Request) and request.full_url.startswith('https://api.github.com'):\n"
            "        request = urllib.request.Request(request.full_url.replace('https://api.github.com', _local, 1), data=request.data, headers=dict(request.header_items()), method=request.get_method())\n"
            "    return _urlopen(request, *args, **kwargs)\n"
            "urllib.request.urlopen = urlopen\n",
            encoding="utf-8",
        )
        silo_store = tmp_path / "fake_silo.py"
        artifact_name = "gate-terminal-v1-123-" + "a" * 40 + "-999-1"
        key = f"d30/123/{artifact_name}/gate-terminal.json"
        silo_store.write_text(
            "import pathlib, sys\n"
            "args = sys.argv[1:]\n"
            f"key = {key!r}\n"
            f"body = pathlib.Path({str(terminal_path)!r}).read_bytes()\n"
            "if args[0] == 'get':\n"
            "    pathlib.Path(args[args.index('--dest') + 1]).write_bytes(body)\n"
            "elif '--dest' in args:\n"
            "    dest = pathlib.Path(args[args.index('--dest') + 1])\n"
            "    target = dest / key.split('/')[2] / key.split('/', 3)[3]\n"
            "    target.parent.mkdir(parents=True, exist_ok=True)\n"
            "    target.write_bytes(body)\n"
            "print(key)\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env.pop("GITHUB_TOKEN", None)
        env["GH_TOKEN"] = "test-token"
        env["SILO_ENDPOINT"] = "http://local-test"
        env["SILO_STORE"] = str(silo_store)
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(site_dir), env.get("PYTHONPATH", "")]))
        delivery_path = tmp_path / "delivery.json"
        args = _cli_args(
            audit_dir, summary_path, terminal_path=str(terminal_path), panel_delivery_path=str(delivery_path),
        ) + ["--publish-only"]
        result = subprocess.run(
            [sys.executable, str(MODULE_PATH), *args], cwd=ROOT, env=env,
            capture_output=True, text=True, timeout=20,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

    assert result.returncode == 0, result.stderr
    receipt_bytes = delivery_path.read_bytes()
    receipt = json.loads(receipt_bytes)
    assert receipt_bytes.endswith(b"\n")
    assert b'"attempts": null' in receipt_bytes
    assert b'"exception_type": null' in receipt_bytes
    assert receipt == {**old_receipt, "attempts": None, "exception_type": None}

@pytest.mark.parametrize(
    "status,category",
    [(403, "permission_or_rate_limit"), (500, "server_error")],
)
def test_status_panel_http_failure_is_fail_open_and_diagnostic(monkeypatch, capsys, status, category):
    monkeypatch.setenv("GH_TOKEN", "tok")
    error = urllib.error.HTTPError("https://api.github.com/x", status, "boom", hdrs=None, fp=None)
    monkeypatch.setattr(AGG, "_github_json", lambda **kwargs: (_ for _ in ()).throw(error))
    current = _panel_terminal_row(1, 1, "pass", "a" * 40)
    body, receipt = AGG._post_status_panel_fail_open(
        current=current, repository="zlxlabs/gate", repository_id=123, pr_number=42,
        identity=IDENTITY,
    )
    assert receipt["delivery"] in {"not_created", "unknown"}
    assert receipt["http_status"] == status
    assert receipt["error_category"] == category
    output = capsys.readouterr().out
    assert "HTTP status=" + str(status) in output
    assert "permission category=" + category in output

def test_status_panel_missing_token_is_fail_open_without_network(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network call")))
    _, receipt = AGG._post_status_panel_fail_open(
        current=_panel_terminal_row(1, 1, "pass", "a" * 40),
        repository="zlxlabs/gate", repository_id=123, pr_number=42, identity=IDENTITY,
    )
    assert receipt["delivery"] == "not_created"
    assert receipt["reason_code"] == "missing_token"
    assert receipt["error_category"] == "configuration"

def test_github_timeout_and_publish_budget_defaults_are_centralized(monkeypatch):
    assert AGG.GITHUB_API_TIMEOUT_SECONDS == 15
    assert AGG.DEFAULT_PUBLISH_BUDGET_SECONDS <= 120
    monkeypatch.delenv(AGG.PUBLISH_BUDGET_ENV, raising=False)
    assert AGG._PublishBudget.from_environment().seconds == 120
    monkeypatch.setenv(AGG.PUBLISH_BUDGET_ENV, "3.5")
    assert AGG._PublishBudget.from_environment().seconds == 3.5

    tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
    urlopen_calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "urlopen"
    ]
    assert len(urlopen_calls) == 1
    timeout_keywords = [keyword for keyword in urlopen_calls[0].keywords if keyword.arg == "timeout"]
    assert len(timeout_keywords) == 1

@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError("temporary"),
        urllib.error.HTTPError("https://api.github.com/x", 503, "server", hdrs=None, fp=None),
    ],
    ids=["network", "http-5xx"],
)
def test_comment_lookup_retries_transient_failure_twice(monkeypatch, failure):
    owner = {"id": 99, "login": "workflow-bot"}
    comments = [{"id": 77, "created_at": "2026-08-16T00:00:00Z", "body": AGG.PANEL_MARKER, "user": owner}]
    requests = []
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=[]))
    monkeypatch.setattr(AGG.time, "sleep", lambda seconds: None)

    def fake_urlopen(request, timeout=None):
        requests.append(request)
        if request.get_method() == "GET":
            if sum(item.get_method() == "GET" for item in requests) < 3:
                raise failure
            return _FakeResponse(json.dumps(comments).encode())
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    _, receipt = AGG._post_status_panel_fail_open(
        current=_panel_terminal_row(999, 1, "pass", "a" * 40),
        repository="zlxlabs/gate", repository_id=123, pr_number=42, identity=IDENTITY,
    )
    assert [request.get_method() for request in requests] == ["GET", "GET", "GET", "PATCH"]
    assert sum(request.get_method() == "GET" for request in requests) == 3
    assert receipt["delivery"] == "updated"

def test_comment_lookup_http_4xx_is_not_retried(monkeypatch):
    owner = {"id": 99, "login": "workflow-bot"}
    requests = []
    error = urllib.error.HTTPError("https://api.github.com/x", 404, "missing", hdrs=None, fp=None)
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout=None: (requests.append(request), (_ for _ in ()).throw(error))[1])
    _, receipt = AGG._post_status_panel_fail_open(
        current=_panel_terminal_row(999, 1, "pass", "a" * 40),
        repository="zlxlabs/gate", repository_id=123, pr_number=42, identity=IDENTITY,
    )
    assert len(requests) == 1
    assert requests[0].get_method() == "GET"
    assert receipt["delivery"] == "not_created"
    assert receipt["operation"] == "comment_lookup"
    assert receipt["attempts"] == 1
    assert receipt["reason_code"] == "http_error"

@pytest.mark.parametrize("server_applied,expected_patch_calls", [(True, 1), (False, 2)])
def test_patch_timeout_reads_back_before_retry(monkeypatch, server_applied, expected_patch_calls):
    owner = {"id": 99, "login": "workflow-bot"}
    comments = [{"id": 77, "created_at": "2026-08-16T00:00:00Z", "body": AGG.PANEL_MARKER, "user": owner}]
    requests = []
    patch_calls = 0
    readback_calls = 0
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=[]))
    monkeypatch.setattr(AGG.time, "sleep", lambda seconds: None)

    def fake_urlopen(request, timeout=None):
        nonlocal patch_calls, readback_calls
        requests.append(request)
        if request.get_method() == "GET":
            if request.full_url.endswith("/issues/comments/77"):
                readback_calls += 1
                return _FakeResponse(json.dumps({"body": comments[0]["body"]}).encode())
            return _FakeResponse(json.dumps(comments).encode())
        patch_calls += 1
        next_body = json.loads(request.data.decode())["body"]
        if patch_calls == 1:
            if server_applied:
                comments[0]["body"] = next_body
            raise socket.timeout("response lost")
        comments[0]["body"] = next_body
        return _FakeResponse()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    _, receipt = AGG._post_status_panel_fail_open(
        current=_panel_terminal_row(999, 1, "pass", "a" * 40),
        repository="zlxlabs/gate", repository_id=123, pr_number=42, identity=IDENTITY,
    )
    assert patch_calls == expected_patch_calls
    assert readback_calls == 1
    assert [request.get_method() for request in requests] == (
        ["GET", "PATCH", "GET"] if server_applied else ["GET", "PATCH", "GET", "PATCH"]
    )
    assert receipt["delivery"] == "updated"

@pytest.mark.parametrize("readback_fails", [False, True], ids=["patch-exhausted", "readback-failure-counts"])
def test_patch_retry_exhaustion_is_visible_and_publish_only_stays_green(monkeypatch, capsys, tmp_path, readback_fails):
    audit_dir = tmp_path / "audit"
    audit_dir.mkdir()
    (audit_dir / "primary-review-audit.json").write_text(json.dumps(_valid_primary_record()))
    summary_path = tmp_path / "summary.md"
    terminal_path = tmp_path / "gate-terminal.json"
    delivery_path = tmp_path / "panel-delivery.json"
    assert AGG.main(_cli_args(audit_dir, summary_path, terminal_path=str(terminal_path))) == 0

    owner = {"id": 99, "login": "workflow-bot"}
    comments = [{"id": 77, "created_at": "2026-08-16T00:00:00Z", "body": AGG.PANEL_MARKER, "user": owner}]
    requests = []
    monkeypatch.setenv("GH_TOKEN", "tok")
    monkeypatch.setattr(AGG, "_github_identity", lambda token: owner)
    monkeypatch.setattr(AGG, "_fetch_terminal_history", lambda **kwargs: AGG.HistoryLoad(rows=[]))
    monkeypatch.setattr(AGG.time, "sleep", lambda seconds: None)

    def fake_urlopen(request, timeout=None):
        requests.append(request)
        if request.get_method() == "GET":
            if readback_fails and request.full_url.endswith("/issues/comments/77"):
                raise urllib.error.URLError("readback unavailable")
            if request.full_url.endswith("/issues/comments/77"):
                return _FakeResponse(json.dumps({"body": comments[0]["body"]}).encode())
            return _FakeResponse(json.dumps(comments).encode())
        raise urllib.error.URLError("temporary")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    args = _cli_args(
        audit_dir, summary_path, terminal_path=str(terminal_path), panel_delivery_path=str(delivery_path),
    ) + ["--publish-only"]
    assert AGG.main(args) == 0
    receipt = json.loads(delivery_path.read_bytes())
    assert receipt["delivery"] == "unknown"
    assert receipt["operation"] == "comment_patch"
    assert receipt["attempts"] == 3
    assert receipt["exception_type"] == "URLError"
    assert sum(request.get_method() == "PATCH" for request in requests) == (2 if readback_fails else 3)
    assert sum(request.get_method() == "GET" for request in requests) == (2 if readback_fails else 3)
    output = capsys.readouterr().out
    assert "PANEL_PUBLISH_FAILED operation=comment_patch attempts=3 exc_type=URLError" in output
    assert "面板发布失败，面板可能显示旧状态" in summary_path.read_text(encoding="utf-8")

def test_history_reconstruction_budget_has_one_bounded_configuration_point(monkeypatch):
    assert AGG.DEFAULT_HISTORY_RECONSTRUCTION_BUDGET_SECONDS <= 45
    monkeypatch.delenv(AGG.HISTORY_RECONSTRUCTION_BUDGET_ENV, raising=False)
    assert AGG._PublishBudget.from_environment().history_seconds == 45
    monkeypatch.setenv(AGG.HISTORY_RECONSTRUCTION_BUDGET_ENV, "3.5")
    assert AGG._PublishBudget.from_environment().history_seconds == 3.5
    monkeypatch.setenv(AGG.HISTORY_RECONSTRUCTION_BUDGET_ENV, "45.1")
    with pytest.raises(ValueError, match="between zero and 45"):
        AGG._PublishBudget.from_environment()

def test_warning_output_failure_stays_fail_open(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GH_TOKEN", raising=False)
    def broken_print(*args, **kwargs):
        raise OSError("closed stdout")
    monkeypatch.setattr("builtins.print", broken_print)
    _, receipt = AGG._post_status_panel_fail_open(
        current=_panel_terminal_row(1, 1, "pass", "a" * 40),
        repository="zlxlabs/gate", repository_id=123, pr_number=42, identity=IDENTITY,
    )
    assert receipt["reason_code"] == "missing_token"

def test_panel_delivery_receipt_uses_temp_file_then_atomic_replace(monkeypatch, tmp_path):
    path = tmp_path / "panel-delivery.json"
    replacements = []
    original_replace = Path.replace
    def record_replace(source, target):
        replacements.append((source, target))
        return original_replace(source, target)
    monkeypatch.setattr(Path, "replace", record_replace)
    AGG._persist_panel_delivery(str(path), {"delivery": "created"})
    assert replacements == [(path.with_name(".panel-delivery.json.tmp"), path)]
    assert json.loads(path.read_text(encoding="utf-8"))["delivery"] == "created"
    assert not path.with_name(".panel-delivery.json.tmp").exists()
