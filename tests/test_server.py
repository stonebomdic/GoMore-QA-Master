"""P3 review round 2 major#4 — `_pick_form_api` / `_auto_generate_tests` 補測。

`_pick_form_api`（server.py）先前完全沒有單元測試就直接餵進
`_render_form_test` 的真斷言渲染路徑；這裡把純函式的五種情境鎖住，
再補一個 orchestration 測試確認 `module["api"]` 真的有被傳下去給
`generator.generate_test`。
"""
from __future__ import annotations

import ast
import asyncio

import pytest

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


def test_pick_form_api_non_dict_metadata_does_not_crash():
    """Review S1: `(module.get("metadata") or {}).get("implicit")` throws
    AttributeError when `metadata` is a non-dict truthy value (e.g. a
    list) — `or {}` only substitutes on falsy values, so a non-empty list
    sails through to `.get()`, which lists don't have. Must treat it as
    "not implicit" and fall through to the normal matching logic instead
    of crashing."""
    module = {"kind": "form", "metadata": ["not", "a", "dict"]}
    endpoints = [{"method": "POST", "host": "x.test", "path": "/api/login"}]
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


# ---- _resolve_test_filename — 碰撞處理（純函式） -----------------------------
# 真實站回歸（gwp-admin /users）：dialog「確認登出」與 cta「確認登出」兩個
# module 的 slug 相同，碰撞時第二筆無聲覆寫掉第一筆，9 筆產出只落地 8 檔。


def test_resolve_test_filename_no_collision_uses_base_slug():
    used: set[str] = set()
    assert server._resolve_test_filename("foo", "cta", used) == "test_foo.py"
    assert used == {"test_foo.py"}


def test_resolve_test_filename_collision_falls_back_to_kind_suffix():
    used = {"test_foo.py"}
    assert server._resolve_test_filename("foo", "cta", used) == "test_foo_cta.py"


def test_resolve_test_filename_double_collision_appends_numeric_suffix():
    used = {"test_foo.py", "test_foo_cta.py"}
    assert server._resolve_test_filename("foo", "cta", used) == "test_foo_cta_2.py"


def test_resolve_test_filename_triple_collision_increments_further():
    used = {"test_foo.py", "test_foo_cta.py", "test_foo_cta_2.py"}
    assert server._resolve_test_filename("foo", "cta", used) == "test_foo_cta_3.py"


# ---- _auto_generate_tests — 檔名碰撞 end-to-end ------------------------------


def test_auto_generate_tests_dialog_and_cta_same_slug_both_land_on_disk(monkeypatch, tmp_path):
    """dialog「確認登出」與 cta「確認登出」slug 相同 —— 兩筆都要真的落地
    成不同檔案，內容對應各自 kind 的 render 邏輯。"""
    from gomore_qa_master.runners import pytest_playwright as pw
    from gomore_qa_master.tools import generator as gen_mod

    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(gen_mod, "PROJECT_ROOT", tmp_path)

    async def fake_analyze_url(url, **kwargs):
        return {
            "url": url,
            "page_title": "Users",
            "module_count": 2,
            "api_endpoint_count": 0,
            "modules": [
                {
                    "kind": "dialog",
                    "name": "確認登出",
                    "selectors": {"container": "dialog"},
                    "metadata": {"open_on_load": False},
                    "candidate_tcs": ["TC dialog"],
                },
                {
                    "kind": "cta",
                    "name": "確認登出",
                    "selectors": {"trigger": "button"},
                    "metadata": {"label_text": "確認登出", "tag": "button"},
                    "candidate_tcs": ["TC cta"],
                },
            ],
            "api_endpoints": [],
        }

    monkeypatch.setattr(server.analyzer, "analyze_url", fake_analyze_url)
    monkeypatch.setattr(server.telemetry, "log_discovered_modules", lambda *a, **k: None)
    monkeypatch.setattr(server.telemetry, "log_generation", lambda *a, **k: None)

    result = asyncio.run(server._auto_generate_tests(
        "https://x.test/users", timeout_ms=15000, auth_cookie=None, tests_per_module=1,
    ))

    filenames = [g["filename"] for g in result["tests"]]
    assert len(filenames) == 2
    assert len(set(filenames)) == 2, f"檔名碰撞沒被解開：{filenames}"
    for fn in filenames:
        assert (tmp_path / fn).is_file(), f"{fn} 沒有真的落地"

    dialog_entry = next(g for g in result["tests"] if g["module_kind"] == "dialog")
    cta_entry = next(g for g in result["tests"] if g["module_kind"] == "cta")
    assert dialog_entry["filename"] != cta_entry["filename"]

    dialog_content = (tmp_path / dialog_entry["filename"]).read_text()
    cta_content = (tmp_path / cta_entry["filename"]).read_text()
    assert "to_be_hidden()" in dialog_content
    assert "get_by_role" in cta_content


# ---- _auto_generate_tests — auth conftest 鷹架 ------------------------------
# 真實站實測：behind-login 站的產出不帶 auth 鷹架，不登入全紅。帶
# auth_storage 時額外在 PROJECT_ROOT 產出 conftest.py，用
# browser_context_args session fixture 注入 storage_state；token 絕不落檔。


async def _fake_analyze_url_one_section_module(url, **kwargs):
    return {
        "url": url,
        "page_title": "Dashboard",
        "module_count": 1,
        "api_endpoint_count": 0,
        "modules": [{
            "kind": "section",
            "name": "dashboard_section_0",
            "selectors": {"container": "#dashboard"},
            "candidate_tcs": ["TC"],
        }],
        "api_endpoints": [],
    }


def _patch_project_roots(monkeypatch, tmp_path):
    from gomore_qa_master.runners import pytest_playwright as pw
    from gomore_qa_master.tools import generator as gen_mod

    monkeypatch.setattr(server, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(pw, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(gen_mod, "PROJECT_ROOT", tmp_path)


def _patch_common_auto_generate_mocks(monkeypatch, analyze_fn=_fake_analyze_url_one_section_module):
    monkeypatch.setattr(server.analyzer, "analyze_url", analyze_fn)
    monkeypatch.setattr(server.telemetry, "log_discovered_modules", lambda *a, **k: None)
    monkeypatch.setattr(server.telemetry, "log_generation", lambda *a, **k: None)


def test_auto_generate_tests_env_indirection_writes_conftest_without_literal_token(monkeypatch, tmp_path):
    _patch_project_roots(monkeypatch, tmp_path)
    _patch_common_auto_generate_mocks(monkeypatch)
    monkeypatch.setenv("QA_WEB_TOKEN", "super-secret-fake-token-xyz")

    result = asyncio.run(server._auto_generate_tests(
        "https://x.test/dashboard", timeout_ms=15000, auth_cookie=None,
        tests_per_module=1, auth_storage={"token": "$QA_WEB_TOKEN"},
    ))

    assert result["conftest"] == "written"
    assert "conftest_warnings" not in result
    content = (tmp_path / "conftest.py").read_text()
    assert ast.parse(content)
    assert content.startswith(server._AUTH_CONFTEST_MARKER)
    assert 'os.environ.get("QA_WEB_TOKEN")' in content
    assert "super-secret-fake-token-xyz" not in content
    assert "browser_context_args" in content
    assert "storage_state" in content
    assert "pytest.fail(" in content  # review round 3 #10


def test_auto_generate_tests_literal_auth_storage_value_not_written_with_warning(monkeypatch, tmp_path):
    _patch_project_roots(monkeypatch, tmp_path)
    _patch_common_auto_generate_mocks(monkeypatch)

    result = asyncio.run(server._auto_generate_tests(
        "https://x.test/dashboard", timeout_ms=15000, auth_cookie=None,
        tests_per_module=1, auth_storage={"token": "literal-fake-token-value-abc"},
    ))

    assert result["conftest"] == "written"
    content = (tmp_path / "conftest.py").read_text()
    assert ast.parse(content)
    assert "literal-fake-token-value-abc" not in content
    assert 'os.environ.get("TOKEN_TOKEN")' in content
    assert result.get("conftest_warnings")
    assert any("TOKEN_TOKEN" in w for w in result["conftest_warnings"])


def test_auto_generate_tests_existing_conftest_is_not_overwritten(monkeypatch, tmp_path):
    _patch_project_roots(monkeypatch, tmp_path)
    _patch_common_auto_generate_mocks(monkeypatch)
    (tmp_path / "conftest.py").write_text("# existing conftest, hand-written\n")

    result = asyncio.run(server._auto_generate_tests(
        "https://x.test/dashboard", timeout_ms=15000, auth_cookie=None,
        tests_per_module=1, auth_storage={"token": "$QA_WEB_TOKEN"},
    ))

    assert result["conftest"] == "skipped (exists)"
    assert (tmp_path / "conftest.py").read_text() == "# existing conftest, hand-written\n"


def test_auto_generate_tests_without_auth_storage_does_not_write_conftest(monkeypatch, tmp_path):
    _patch_project_roots(monkeypatch, tmp_path)
    _patch_common_auto_generate_mocks(monkeypatch)

    result = asyncio.run(server._auto_generate_tests(
        "https://x.test/dashboard", timeout_ms=15000, auth_cookie=None, tests_per_module=1,
    ))

    assert "conftest" not in result
    assert not (tmp_path / "conftest.py").exists()


def test_auto_generate_tests_auth_cookie_injected_alongside_storage_with_env_indirection(monkeypatch, tmp_path):
    """auth_cookie 比照 auth_storage 產出 cookies 注入，值一樣用
    $ENV_NAME 間接引用規則（不落字面值）。"""
    _patch_project_roots(monkeypatch, tmp_path)
    _patch_common_auto_generate_mocks(monkeypatch)

    result = asyncio.run(server._auto_generate_tests(
        "https://x.test/dashboard", timeout_ms=15000,
        auth_cookie="session=$QA_SESSION_TOKEN", tests_per_module=1,
        auth_storage={"token": "$QA_WEB_TOKEN"},
    ))

    assert result["conftest"] == "written"
    content = (tmp_path / "conftest.py").read_text()
    assert ast.parse(content)
    assert 'os.environ.get("QA_SESSION_TOKEN")' in content
    assert "session" in content
    assert "x.test" in content  # domain


def test_auto_generate_tests_no_tests_generated_skips_conftest(monkeypatch, tmp_path):
    """規格：帶 auth_storage 但一筆測試都沒成功產出時，不該額外產出
    conftest.py（避免空殼鷹架誤導）。"""
    _patch_project_roots(monkeypatch, tmp_path)

    async def fake_analyze_url_no_modules(url, **kwargs):
        return {
            "url": url, "page_title": "Empty", "module_count": 0,
            "api_endpoint_count": 0, "modules": [], "api_endpoints": [],
        }

    _patch_common_auto_generate_mocks(monkeypatch, analyze_fn=fake_analyze_url_no_modules)

    result = asyncio.run(server._auto_generate_tests(
        "https://x.test/empty", timeout_ms=15000, auth_cookie=None,
        tests_per_module=1, auth_storage={"token": "$QA_WEB_TOKEN"},
    ))

    assert "conftest" not in result
    assert not (tmp_path / "conftest.py").exists()


# ---- review round 3 — userinfo / hostname-less URL (#1) ---------------------
# origin 之前用 parsed.netloc 組出來，會連 "user:pass@" 一起帶進 conftest
# （secret 落檔）且跟瀏覽器實際的 origin 對不起來。改用 scheme + hostname
# （小寫，不含 userinfo/port）+ port（有才加）。


def test_build_auth_conftest_userinfo_url_does_not_leak_credentials_into_origin():
    content, warnings = server._build_auth_conftest(
        "https://admin:s3cr3t-pw@Evil.Example.com:9443/users",
        {"token": "$QA_WEB_TOKEN"},
        None,
    )
    assert content is not None
    ast.parse(content)
    assert "admin" not in content
    assert "s3cr3t-pw" not in content
    assert "origin = 'https://evil.example.com:9443'" in content
    assert not warnings


def test_build_auth_conftest_default_port_omitted_from_origin():
    """URL 明寫預設 port（https:443 / http:80）時 origin 要省略它——
    瀏覽器正規化後的 origin 字串不含預設 port，寫進去 localStorage
    會對不上 origin。非預設 port 照舊保留。"""
    content, _ = server._build_auth_conftest(
        "https://x.example.com:443/users", {"token": "$QA_WEB_TOKEN"}, None,
    )
    assert "origin = 'https://x.example.com'" in content

    content, _ = server._build_auth_conftest(
        "http://x.example.com:80/users", {"token": "$QA_WEB_TOKEN"}, None,
    )
    assert "origin = 'http://x.example.com'" in content

    content, _ = server._build_auth_conftest(
        "https://x.example.com:9443/users", {"token": "$QA_WEB_TOKEN"}, None,
    )
    assert "origin = 'https://x.example.com:9443'" in content


def test_build_auth_conftest_case_sensitive_env_refs_get_distinct_variables():
    """$FOO 與 $foo 是兩個不同的環境變數：各自要有獨立的 Python 區域變數
    與缺值檢查，不能因 lower() 共用變數名而互相覆蓋。"""
    content, _ = server._build_auth_conftest(
        "https://x.example.com/users",
        {"k1": "$FOO", "k2": "$foo"},
        None,
    )
    ast.parse(content)
    assert 'os.environ.get("FOO")' in content
    assert 'os.environ.get("foo")' in content
    import re as _re
    var_names = set(_re.findall(r"(_auth_env_\d+) = os\.environ\.get", content))
    assert len(var_names) == 2


def test_build_auth_conftest_hostname_none_returns_no_content_with_warning():
    content, warnings = server._build_auth_conftest(
        "not-a-real-url-with-no-host", {"token": "$QA_WEB_TOKEN"}, None,
    )
    assert content is None
    assert warnings
    assert any("hostname" in w for w in warnings)


def test_auto_generate_tests_hostname_none_url_skips_conftest_without_crashing(monkeypatch, tmp_path):
    _patch_project_roots(monkeypatch, tmp_path)
    _patch_common_auto_generate_mocks(monkeypatch)

    result = asyncio.run(server._auto_generate_tests(
        "not-a-real-url-with-no-host", timeout_ms=15000, auth_cookie=None,
        tests_per_module=1, auth_storage={"token": "$QA_WEB_TOKEN"},
    ))

    assert result["conftest"] == "skipped (invalid url: no hostname)"
    assert not (tmp_path / "conftest.py").exists()


# ---- review round 3 — env 名推導 edge cases (#6) -----------------------------


@pytest.mark.parametrize("key", [
    "token",
    "access-token",
    "1abc",
    "",
    "中文",
    'token\', \'value\': \'x\'}]}]  # ',
    "line1\nline2",
    'q"\'`',
])
def test_derive_literal_env_name_always_valid_identifier_like(key):
    used: set[str] = set()
    name = server._derive_literal_env_name(key, used)
    assert name.isidentifier()
    assert name == name.upper()
    assert name in used


def test_derive_literal_env_name_collision_gets_numbered_suffix():
    used: set[str] = set()
    first = server._derive_literal_env_name("access-token", used)
    second = server._derive_literal_env_name("access_token", used)
    assert first != second
    assert second.endswith("_2")


def test_derive_literal_env_name_leading_digit_gets_auth_prefix():
    used: set[str] = set()
    name = server._derive_literal_env_name("1abc", used)
    assert name.startswith("AUTH_")


def test_derive_literal_env_name_fully_non_ascii_key_gets_numbered_fallback():
    used: set[str] = set()
    name = server._derive_literal_env_name("中文", used)
    assert name.startswith("AUTH_TOKEN_")


def test_derive_literal_env_name_empty_key_gets_numbered_fallback():
    used: set[str] = set()
    name = server._derive_literal_env_name("", used)
    assert name.startswith("AUTH_TOKEN_")


@pytest.mark.parametrize("malicious_key", [
    'token\', \'value\': \'x\'}]}]  # ',
    "line1\nline2",
    'q"\'`',
    "中文",
    "access-token",
    "1abc",
    "",
])
def test_auto_generate_tests_malicious_auth_storage_keys_never_leak_literal_value(
    monkeypatch, tmp_path, malicious_key,
):
    """惡意／邊角 key（reviewer 提供的集合）都要：產出的 conftest.py 能被
    ast.parse，且字面值永遠不落檔。"""
    _patch_project_roots(monkeypatch, tmp_path)
    _patch_common_auto_generate_mocks(monkeypatch)
    literal_value = "super-secret-literal-fake-value-zzz"

    result = asyncio.run(server._auto_generate_tests(
        "https://x.test/dashboard", timeout_ms=15000, auth_cookie=None,
        tests_per_module=1, auth_storage={malicious_key: literal_value},
    ))

    assert result["conftest"] == "written"
    content = (tmp_path / "conftest.py").read_text()
    ast.parse(content)
    assert literal_value not in content
    assert result.get("conftest_warnings")


# ---- review round 3 — auth_cookie 單獨帶時的回報 (#7) ------------------------


def test_auto_generate_tests_auth_cookie_only_reports_auth_storage_required(monkeypatch, tmp_path):
    _patch_project_roots(monkeypatch, tmp_path)
    _patch_common_auto_generate_mocks(monkeypatch)

    result = asyncio.run(server._auto_generate_tests(
        "https://x.test/dashboard", timeout_ms=15000,
        auth_cookie="session=$QA_SESSION_TOKEN", tests_per_module=1,
    ))

    assert result["conftest"] == "skipped (auth_storage required)"
    assert not (tmp_path / "conftest.py").exists()


# ---- review round 3 — conftest marker 允許覆寫自己產出的檔案 (#10) ----------


def test_auto_generate_tests_regenerates_conftest_with_marker(monkeypatch, tmp_path):
    """已存在的 conftest.py 若帶有我們自己的 marker（代表是上一輪
    auto_generate_tests 產出的），允許覆寫——不然 auth_storage 換了，
    conftest 永遠卡在舊內容。"""
    _patch_project_roots(monkeypatch, tmp_path)
    _patch_common_auto_generate_mocks(monkeypatch)
    old_content = f"{server._AUTH_CONFTEST_MARKER}\n# 上一輪產出的舊內容\n"
    (tmp_path / "conftest.py").write_text(old_content)

    result = asyncio.run(server._auto_generate_tests(
        "https://x.test/dashboard", timeout_ms=15000, auth_cookie=None,
        tests_per_module=1, auth_storage={"token": "$QA_NEW_TOKEN"},
    ))

    assert result["conftest"] == "written"
    new_content = (tmp_path / "conftest.py").read_text()
    assert new_content != old_content
    assert "QA_NEW_TOKEN" in new_content


def test_auto_generate_tests_hand_written_conftest_without_marker_is_never_overwritten(
    monkeypatch, tmp_path,
):
    _patch_project_roots(monkeypatch, tmp_path)
    _patch_common_auto_generate_mocks(monkeypatch)
    hand_written = "# hand-written conftest, no marker here\n"
    (tmp_path / "conftest.py").write_text(hand_written)

    result = asyncio.run(server._auto_generate_tests(
        "https://x.test/dashboard", timeout_ms=15000, auth_cookie=None,
        tests_per_module=1, auth_storage={"token": "$QA_WEB_TOKEN"},
    ))

    assert result["conftest"] == "skipped (exists)"
    assert (tmp_path / "conftest.py").read_text() == hand_written


# ---- review round 3 — generate_test 回傳 "error:" 不應算成功 (#11) ----------


def test_auto_generate_tests_generator_error_string_counts_as_failure_not_success(
    monkeypatch, tmp_path,
):
    """generator.generate_test 在 filename 驗證失敗時不會 raise，而是回傳
    一個 "error: ..." 字串——這種情況之前被無聲當成功數進 tests_generated，
    也可能因此誤觸發 conftest 產出門檻。"""
    _patch_project_roots(monkeypatch, tmp_path)

    async def fake_analyze_url(url, **kwargs):
        return {
            "url": url, "page_title": "X", "module_count": 1,
            "api_endpoint_count": 0,
            "modules": [{
                "kind": "section", "name": "sec_0",
                "selectors": {"container": "#x"}, "candidate_tcs": ["TC"],
            }],
            "api_endpoints": [],
        }

    monkeypatch.setattr(server.analyzer, "analyze_url", fake_analyze_url)
    monkeypatch.setattr(server.telemetry, "log_discovered_modules", lambda *a, **k: None)
    monkeypatch.setattr(server.telemetry, "log_generation", lambda *a, **k: None)
    monkeypatch.setattr(
        server.generator, "generate_test",
        lambda **kwargs: "error: 檔名驗證失敗",
    )

    result = asyncio.run(server._auto_generate_tests(
        "https://x.test/dashboard", timeout_ms=15000, auth_cookie=None,
        tests_per_module=1, auth_storage={"token": "$QA_WEB_TOKEN"},
    ))

    assert result["tests_generated"] == 0
    assert result["tests_failed"] == 1
    assert result["tests"][0]["error"] == "error: 檔名驗證失敗"
    assert "description" not in result["tests"][0]
    # 一筆都沒真的成功 → 不該觸發 conftest 門檻。
    assert "conftest" not in result
    assert not (tmp_path / "conftest.py").exists()
