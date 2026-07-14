"""v0.9.6 Phase 2 — run_scan persists a scan-results.json artifact.

verify_plan's verified-tier finding_* assertions load this artifact
themselves (never host-supplied evidence), so the scanner must write an
authoritative, secret-redacted copy of its findings to a resolvable path.
"""
from __future__ import annotations

import json

import pytest

from gomore_qa_master import config
from gomore_qa_master.runners import api_security
from gomore_qa_master.runners.api_security import run_scan


MINIMAL_SPEC = {
    "openapi": "3.0.0",
    "info": {"title": "Test API", "version": "1.0"},
    "servers": [{"url": "http://localhost:9999"}],
    "paths": {
        "/foo": {"get": {"operationId": "getFoo",
                         "responses": {"200": {"description": "OK"}}}},
    },
}


@pytest.fixture
def spec_file(tmp_path):
    p = tmp_path / "spec.yaml"
    p.write_text(json.dumps(MINIMAL_SPEC))
    return str(p)


@pytest.fixture
def scan_out(tmp_path, monkeypatch):
    out = tmp_path / "scan-results.json"
    monkeypatch.setenv("GOMORE_QA_SCAN_PATH", str(out))
    monkeypatch.setenv("QA_API_SECURITY_CONSENT", "true")
    return out


# ---- path resolution ---------------------------------------------------

def test_default_scan_path_honors_env(monkeypatch, tmp_path):
    monkeypatch.setenv("GOMORE_QA_SCAN_PATH", str(tmp_path / "x.json"))
    assert config.default_scan_path() == (tmp_path / "x.json").resolve()


def test_default_scan_path_falls_back_to_project_root(monkeypatch, tmp_path):
    monkeypatch.delenv("GOMORE_QA_SCAN_PATH", raising=False)
    monkeypatch.setenv("QA_PROJECT_ROOT", str(tmp_path))
    assert config.default_scan_path() == (tmp_path / "scan-results.json").resolve()


# ---- run_scan writes the artifact --------------------------------------

def test_run_scan_writes_scan_results_file(spec_file, scan_out):
    result = run_scan(spec_file, severity_threshold="info")
    assert "error" not in result
    assert scan_out.is_file()
    written = json.loads(scan_out.read_text(encoding="utf-8"))
    assert "findings" in written
    assert written["findings"] == result["findings"]
    assert written["scan_id"] == result["scan_id"]


def test_run_scan_write_is_best_effort(spec_file, monkeypatch, tmp_path):
    """A non-writable scan path must NOT break the scan — the result still
    returns, the write is silently skipped."""
    monkeypatch.setenv("QA_API_SECURITY_CONSENT", "true")
    # Point at a path whose parent is a file (mkdir will fail)
    blocker = tmp_path / "blocker"
    blocker.write_text("x")
    monkeypatch.setenv("GOMORE_QA_SCAN_PATH", str(blocker / "nested" / "scan.json"))
    result = run_scan(spec_file, severity_threshold="info")
    assert "error" not in result  # scan itself still succeeded


# ---- redaction ---------------------------------------------------------

def test_redact_scrubs_bearer_and_secrets():
    raw = json.dumps({
        "headers": "Authorization: Bearer eyJhbGSECRETsig",
        "password": "hunter2",
        "access_token": "tok_abc123",
    })
    scrubbed = api_security._redact_scan_text(raw)
    assert "eyJhbGSECRETsig" not in scrubbed
    assert "hunter2" not in scrubbed
    assert "tok_abc123" not in scrubbed
    assert "[REDACTED]" in scrubbed
