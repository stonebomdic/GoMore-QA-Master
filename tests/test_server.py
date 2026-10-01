"""P3 review round 2 major#4 — `_pick_form_api` / `_auto_generate_tests` 補測。

`_pick_form_api`（server.py）先前完全沒有單元測試就直接餵進
`_render_form_test` 的真斷言渲染路徑；這裡把純函式的五種情境鎖住，
再補一個 orchestration 測試確認 `module["api"]` 真的有被傳下去給
`generator.generate_test`。
"""
from __future__ import annotations

import asyncio

from gomore_qa_master import server

# ---- _pick_form_api — 純函式，五種情境 --------------------------------------


def test_pick_form_api_returns_none_for_non_form_kind():
    module = {"kind": "widget"}
    endpoints = [{"method": "POST", "host": "x.test", "path": "/api/login"}]
    assert server._pick_form_api("https://x.test/login", module, endpoints) is None


def test_pick_form_api_skips_non_post_put_methods():
    module = {"kind": "form"}
    endpoints = [
        {"method": "GET", "host": "x.test", "path": "/api/config"},
        {"method": "DELETE", "host": "x.test", "path": "/api/session"},
    ]
    assert server._pick_form_api("https://x.test/login", module, endpoints) is None


def test_pick_form_api_skips_mismatched_host():
    module = {"kind": "form"}
    endpoints = [{"method": "POST", "host": "other.test", "path": "/api/login"}]
    assert server._pick_form_api("https://x.test/login", module, endpoints) is None


def test_pick_form_api_picks_first_matching_endpoint():
    module = {"kind": "form"}
    endpoints = [
        {"method": "GET", "host": "x.test", "path": "/api/config"},
        {"method": "POST", "host": "x.test", "path": "/api/login"},
        {"method": "PUT", "host": "x.test", "path": "/api/profile"},
    ]
    result = server._pick_form_api("https://x.test/login", module, endpoints)
    assert result == {"method": "POST", "url_substring": "/api/login"}


def test_pick_form_api_missing_path_falls_back_to_url():
    module = {"kind": "form"}
    endpoints = [{"method": "POST", "host": "x.test", "path": "", "url": "https://x.test/api/login"}]
    result = server._pick_form_api("https://x.test/login", module, endpoints)
    assert result == {"method": "POST", "url_substring": "https://x.test/api/login"}


def test_pick_form_api_skips_analytics_paths():
    """review round 2 minor#5：analytics/track 端點同源同 method，但不應
    被選為表單的提交對象。"""
    module = {"kind": "form"}
    endpoints = [
        {"method": "POST", "host": "x.test", "path": "/collect"},
        {"method": "POST", "host": "x.test", "path": "/api/login"},
    ]
    result = server._pick_form_api("https://x.test/login", module, endpoints)
    assert result == {"method": "POST", "url_substring": "/api/login"}


def test_pick_form_api_returns_none_for_implicit_form():
    """analyzer._build_modules 的 implicit_form_0（不在任何 <form> 內的
    欄位聚合）沒有真正的送出按鈕／提交動作可以掛 API 斷言 —— 即使同源同
    method 的 POST 端點存在，也不該被誤配對（否則 codegen 會產生一個斷言
    「點某個不存在的 submit 按鈕後應觸發這支 API」的假陽性測試）。"""
    module = {"kind": "form", "metadata": {"implicit": True, "field_count": 1}}
    endpoints = [{"method": "POST", "host": "x.test", "path": "/api/search"}]
    assert server._pick_form_api("https://x.test/list", module, endpoints) is None


# ---- _select_candidate_tcs — 純函式 -----------------------------------------


def test_select_candidate_tcs_swaps_in_happy_path_when_missing():
    candidates = [
        "所有必填欄位為空時送出，應顯示必填錯誤",
        "只填其他欄位、Password 留空，應顯示該欄位必填錯誤",
        "全部填入合法值後送出，應觸發成功流程",
    ]
    assert server._select_candidate_tcs(candidates, 1) == ["全部填入合法值後送出，應觸發成功流程"]


def test_select_candidate_tcs_noop_when_happy_path_already_selected():
    candidates = ["全部填入合法值後送出，應觸發成功流程", "所有必填欄位為空時送出，應顯示必填錯誤"]
    assert server._select_candidate_tcs(candidates, 2) == candidates


def test_select_candidate_tcs_noop_when_no_happy_path_exists():
    candidates = ["所有必填欄位為空時送出，應顯示必填錯誤"]
    assert server._select_candidate_tcs(candidates, 1) == candidates


# ---- _auto_generate_tests — orchestration ----------------------------------


def test_auto_generate_tests_passes_matched_api_into_module(monkeypatch):
    """review round 2 major#3/#4：預設 tests_per_module=1 時，挑進去的 TC
    要是 happy-path（好讓 module["api"] 真的有機會被用上），而且
    `generator.generate_test` 收到的 module 要帶著 `_pick_form_api` 配對出
    的 api dict。"""
    captured: dict = {}

    async def fake_analyze_url(url, **kwargs):
        return {
            "url": url,
            "page_title": "Login",
            "module_count": 1,
            "api_endpoint_count": 1,
            "modules": [{
                "kind": "form",
                "name": "login_form_0",
                "selectors": {"fields": [], "submit": "#submit"},
                "candidate_tcs": [
                    "所有必填欄位為空時送出，應顯示必填錯誤",
                    "全部填入合法值後送出，應觸發成功流程",
                ],
            }],
            "api_endpoints": [
                {"method": "POST", "host": "x.test", "path": "/api/login"},
            ],
        }

    def fake_generate_test(description, filename, url=None, module=None, business_context=None):
        captured["description"] = description
        captured["module"] = module
        return "generated"

    monkeypatch.setattr(server.analyzer, "analyze_url", fake_analyze_url)
    monkeypatch.setattr(server.generator, "generate_test", fake_generate_test)
    monkeypatch.setattr(server.telemetry, "log_discovered_modules", lambda *a, **k: None)
    monkeypatch.setattr(server.telemetry, "log_generation", lambda *a, **k: None)

    result = asyncio.run(server._auto_generate_tests(
        "https://x.test/login", timeout_ms=15000, auth_cookie=None, tests_per_module=1,
    ))

    assert captured["description"] == "全部填入合法值後送出，應觸發成功流程"
    assert captured["module"]["api"] == {"method": "POST", "url_substring": "/api/login"}
    assert result["tests_generated"] == 1
