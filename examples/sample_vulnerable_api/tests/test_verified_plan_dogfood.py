"""v0.9.6 Phase 2 dogfood — verified finding_* CPs against a real scan.

Boots the deliberately-vulnerable Flask fixture, runs a real
`run_api_security_scan` (which persists scan-results.json), then verifies
a plan whose CPs are artifact-backed `finding_present` / `finding_absent`
assertions. This proves the verified tier works end-to-end against ground
truth — vuln endpoints ARE flagged, safe endpoints are NOT — with the
tool loading the scan artifact itself.
"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

from gomore_qa_master.runners.api_security import run_scan
from gomore_qa_master.tools.qa_plan import (
    qa_plan_tool, verify_plan_tool, _reset_cache_for_tests,
)

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"
SPEC_PATH = str(Path(__file__).resolve().parents[1] / "openapi.yaml")
PORT = 5099
BASE_URL = f"http://127.0.0.1:{PORT}"


def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.2)
        return s.connect_ex(("127.0.0.1", port)) == 0


@pytest.fixture(scope="module")
def vuln_app():
    if _port_open(PORT):
        pytest.skip(f"Port {PORT} already in use")
    proc = subprocess.Popen(
        [sys.executable, str(APP_PATH)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                if requests.get(f"{BASE_URL}/health", timeout=0.5).status_code == 200:
                    break
            except requests.RequestException:
                pass
            time.sleep(0.1)
        else:
            _, stderr = proc.communicate(timeout=1)
            raise RuntimeError(f"Flask app didn't boot: {stderr.decode()!r}")
        yield BASE_URL
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()


def _login(u: str, p: str) -> str:
    r = requests.post(f"{BASE_URL}/login", json={"username": u, "password": p}, timeout=2)
    r.raise_for_status()
    return r.json()["token"]


@pytest.fixture
def scanned(vuln_app, tmp_path, monkeypatch):
    """Run a real scan that persists scan-results.json to a tmp path."""
    _reset_cache_for_tests()
    monkeypatch.setenv("QA_API_SECURITY_CONSENT", "true")
    monkeypatch.setenv("GOMORE_QA_SCAN_PATH", str(tmp_path / "scan-results.json"))
    auth = {
        "token": _login("alice", "alice123"),
        "alt_user_token": _login("bob", "bob123"),
        "bola_test_ids": {"user_a": [1, 3], "user_b": [2]},
    }
    result = run_scan(SPEC_PATH, auth=auth, base_url=vuln_app,
                      severity_threshold="low", timeout_s=10)
    assert "error" not in result, f"scan failed: {result}"
    assert (tmp_path / "scan-results.json").is_file()
    yield result
    _reset_cache_for_tests()


def test_finding_present_on_vuln_endpoint(scanned):
    """The BOLA finding on /vuln/orders/{order_id} must satisfy a verified
    finding_present CP — loaded from the scan artifact, not host evidence."""
    plan = qa_plan_tool({"task": "confirm BOLA present", "critical_points": [
        {"id": "CP1", "description": "BOLA on vuln orders",
         "assert": {"type": "finding_present", "rule_id": "OWASP-API1-BOLA",
                    "endpoint": "GET /vuln/orders/{order_id}"}},
    ]})
    res = verify_plan_tool({"plan_id": plan["plan_id"]})  # no evidence — artifact-backed
    cp = res["checklist"][0]
    assert cp["tier"] == "verified"
    assert cp["satisfied"] is True
    assert cp["actual"]["hits"] >= 1


def test_finding_absent_on_safe_endpoint(scanned):
    """The safe orders endpoint must have NO BOLA finding → finding_absent
    is satisfied."""
    plan = qa_plan_tool({"task": "confirm safe endpoint clean", "critical_points": [
        {"id": "CP1", "description": "no BOLA on safe orders",
         "assert": {"type": "finding_absent", "rule_id": "OWASP-API1-BOLA",
                    "endpoint": "GET /safe/me/orders"}},
    ]})
    res = verify_plan_tool({"plan_id": plan["plan_id"]})
    assert res["checklist"][0]["satisfied"] is True


def test_finding_absent_fails_on_vuln_endpoint(scanned):
    """finding_absent must NOT be satisfied where the vuln actually exists."""
    plan = qa_plan_tool({"task": "wrongly expect clean", "critical_points": [
        {"id": "CP1", "description": "expect no BOLA (but there is one)",
         "assert": {"type": "finding_absent", "rule_id": "OWASP-API1-BOLA",
                    "endpoint": "GET /vuln/orders/{order_id}"}},
    ]})
    res = verify_plan_tool({"plan_id": plan["plan_id"]})
    assert res["checklist"][0]["satisfied"] is False


def test_scan_plan_id_bookend_with_verified_finding(vuln_app, tmp_path, monkeypatch):
    """v0.9.4 bookend + v0.9.6 verified: pass plan_id to run_scan; because the
    artifact is written BEFORE plan verification, a verified finding_present CP
    is satisfied in the returned plan_verification block."""
    _reset_cache_for_tests()
    monkeypatch.setenv("QA_API_SECURITY_CONSENT", "true")
    monkeypatch.setenv("GOMORE_QA_SCAN_PATH", str(tmp_path / "scan-results.json"))
    auth = {
        "token": _login("alice", "alice123"),
        "alt_user_token": _login("bob", "bob123"),
        "bola_test_ids": {"user_a": [1, 3], "user_b": [2]},
    }
    plan = qa_plan_tool({"task": "bookend", "critical_points": [
        {"id": "CP1", "description": "BOLA present",
         "assert": {"type": "finding_present", "rule_id": "OWASP-API1-BOLA"}},
    ]})
    result = run_scan(SPEC_PATH, auth=auth, base_url=vuln_app,
                      severity_threshold="low", timeout_s=10, plan_id=plan["plan_id"])
    _reset_cache_for_tests()
    assert "error" not in result
    pv = result["plan_verification"]
    assert pv["checklist"][0]["tier"] == "verified"
    assert pv["checklist"][0]["satisfied"] is True
