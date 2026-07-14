"""OWASP API security scanner — SCANNERS registry 的正式入口（P3）。

薄封裝：邏輯（consent 閘門、規則、scan-results.json 落地、plan 驗證）
全部留在 runners/api_security.run_scan —— 產物落地是 scan 操作本身的
契約（verified finding_* 依賴它），不論從哪個入口呼叫都必須成立，
所以不搬上來。
"""
from __future__ import annotations


class ApiSecurityScanner:
    name = "api_security"

    def scan(self, spec_url: str, **kwargs) -> dict:
        # call-time 模組屬性查找 — 測試 monkeypatch runners.api_security.run_scan
        # 時 scanner 路徑同步生效
        from ..runners import api_security
        return api_security.run_scan(spec_url, **kwargs)
