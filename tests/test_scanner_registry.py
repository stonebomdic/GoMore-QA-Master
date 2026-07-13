"""P3 Task 5 — SCANNERS registry + ApiSecurityScanner 薄封裝。

契約：ApiSecurityScanner().scan(...) 輸出與裸 run_scan(...) 逐字相同
（含 consent/authorized-domains 閘門行為）；server 的 run_api_security_scan
handler 改經 SCANNERS 取得 scanner。
"""
from __future__ import annotations

import asyncio
import json

import pytest


def test_scanners_registry_exposes_api_security():
    from gomore_qa_master.scanners import SCANNERS
    scanner = SCANNERS["api_security"]
    assert scanner.name == "api_security"
    assert callable(scanner.scan)


def test_scan_output_identical_to_run_scan_consent_gate(monkeypatch):
    """無 consent 時兩邊回一模一樣的拒絕信封（閘門行為不變）。"""
    monkeypatch.delenv("QA_API_SECURITY_CONSENT", raising=False)
    from gomore_qa_master.runners.api_security import run_scan
    from gomore_qa_master.scanners import SCANNERS

    direct = run_scan("http://localhost/openapi.json")
    via_scanner = SCANNERS["api_security"].scan("http://localhost/openapi.json")
    assert via_scanner == direct
    assert "error" in direct  # 確認真的踩到 consent 閘門


def test_scan_output_identical_to_run_scan_stubbed(monkeypatch):
    """stub 掉底層 run_scan，scanner 轉發全部 kwargs 且回傳逐字相同。"""
    seen = {}

    def fake_run_scan(spec_url, **kwargs):
        seen["spec_url"] = spec_url
        seen.update(kwargs)
        return {"findings": [{"rule_id": "X"}], "scan_results_path": "/tmp/x.json"}

    monkeypatch.setattr(
        "gomore_qa_master.runners.api_security.run_scan", fake_run_scan)
    from gomore_qa_master.scanners import SCANNERS

    result = SCANNERS["api_security"].scan(
        "spec.yaml", auth={"token": "t"}, categories=["bola"],
        severity_threshold="low", base_url="http://x", timeout_s=5,
        plan_id="p1")
    assert result == {"findings": [{"rule_id": "X"}],
                      "scan_results_path": "/tmp/x.json"}
    assert seen == {"spec_url": "spec.yaml", "auth": {"token": "t"},
                    "categories": ["bola"], "severity_threshold": "low",
                    "base_url": "http://x", "timeout_s": 5, "plan_id": "p1"}


def test_server_handler_routes_through_scanners(monkeypatch):
    """server dispatch 的 run_api_security_scan 走 SCANNERS registry。"""
    from gomore_qa_master import server
    from gomore_qa_master.scanners import SCANNERS

    called = {}

    class FakeScanner:
        name = "api_security"

        def scan(self, spec_url, **kwargs):
            called["spec_url"] = spec_url
            return {"findings": []}

    monkeypatch.setitem(SCANNERS, "api_security", FakeScanner())
    result = asyncio.run(server._dispatch(
        "run_api_security_scan", {"spec_url": "spec.yaml"}))
    assert called == {"spec_url": "spec.yaml"}
    assert json.loads(result[0].text) == {"findings": []}
