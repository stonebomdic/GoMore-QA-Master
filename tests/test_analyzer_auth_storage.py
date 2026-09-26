"""P2 — analyze_url 的 localStorage auth 注入 (auth_storage)。

設計：
- `_resolve_auth_storage(auth_storage)`：純函式，展開 `$ENV_NAME` 間接引用。
  只有整段字串完全符合 `$ENV_NAME`（`_ENV_REF_RE`：英數底線、不可數字開頭）
  格式才視為間接引用並從 os.environ 取值；其餘（含單一 `$`、`$1BADNAME`
  這種不合法變數名、或本來就是明碼的值）一律原樣通過。環境變數不存在時
  raise KeyError(env_name)，讓呼叫端轉成 {"error": ...} 而非靜默略過；
  值不是字串時 raise TypeError。
- `_storage_init_script(storage, origin)`：純函式，把 {key: value} 轉成一段
  以 `if (location.origin === <origin>) { ... }` 包住、且整段包 try/catch
  的 JS —— origin guard 避免 token 被寫進跨網域 iframe / SSO 轉址網域；
  try/catch 避免 sandboxed frame 存取 localStorage 丟 SecurityError 時噴
  console 錯誤。每個 key 各一句 `localStorage.setItem(...)`，key/value 一律
  用 json.dumps 序列化避免 JS 注入。
- `analyze_url(..., auth_storage=...)`：解析失敗（環境變數缺 / 值非字串）
  時整個 tool 回傳 {"error": ...}，不靜默略過；不得在單元測試開真瀏覽器 ——
  這裡只測純函式 + error 早退路徑（在呼叫 playwright 之前就已失敗，或
  playwright import 本身失敗）。
- `server._auto_generate_tests(...)`：內部呼叫 analyze_url 後，若回傳
  帶 error 直接原樣回傳（不會繼續跑 generate_test）——這個轉發 + 早退路徑
  也在這裡測。
"""
import asyncio
import json as _json
import re
import sys

import pytest

from gomore_qa_master.tools.analyzer import (
    _resolve_auth_storage,
    _storage_init_script,
    analyze_url,
)

# ---------------------------------------------------------------------------
# _resolve_auth_storage — 純函式
# ---------------------------------------------------------------------------

def test_resolve_auth_storage_plaintext_passthrough():
    assert _resolve_auth_storage({"token": "abc123"}) == {"token": "abc123"}


def test_resolve_auth_storage_env_indirection(monkeypatch):
    monkeypatch.setenv("QA_WEB_TOKEN", "real-secret-value")
    result = _resolve_auth_storage({"token": "$QA_WEB_TOKEN"})
    assert result == {"token": "real-secret-value"}


def test_resolve_auth_storage_mixed_plaintext_and_env(monkeypatch):
    monkeypatch.setenv("QA_WEB_TOKEN", "real-secret-value")
    result = _resolve_auth_storage({
        "token": "$QA_WEB_TOKEN",
        "tenant": "acme",
    })
    assert result == {"token": "real-secret-value", "tenant": "acme"}


def test_resolve_auth_storage_missing_env_raises(monkeypatch):
    monkeypatch.delenv("QA_MISSING_TOKEN", raising=False)
    with pytest.raises(KeyError):
        _resolve_auth_storage({"token": "$QA_MISSING_TOKEN"})


def test_resolve_auth_storage_empty_dict():
    assert _resolve_auth_storage({}) == {}


def test_resolve_auth_storage_bare_dollar_is_literal():
    """單一 "$" 不符合 `$ENV_NAME` 規則（沒有變數名可取）——當明碼傳遞，
    不應該試圖查環境變數、更不應該 raise。"""
    result = _resolve_auth_storage({"token": "$"})
    assert result == {"token": "$"}


def test_resolve_auth_storage_malformed_env_name_is_literal():
    """`$1BADNAME` 開頭是數字，不是合法識別字——當明碼傳遞。"""
    result = _resolve_auth_storage({"token": "$1BADNAME"})
    assert result == {"token": "$1BADNAME"}


def test_resolve_auth_storage_non_string_value_raises_type_error():
    with pytest.raises(TypeError):
        _resolve_auth_storage({"token": 12345})
    with pytest.raises(TypeError):
        _resolve_auth_storage({"token": None})


# ---------------------------------------------------------------------------
# _storage_init_script — 純函式
# ---------------------------------------------------------------------------

ORIGIN = "https://example.test"


def test_storage_init_script_single_key():
    script = _storage_init_script({"token": "abc123"}, ORIGIN)
    assert 'localStorage.setItem("token", "abc123")' in script


def test_storage_init_script_multiple_keys_one_statement_each():
    script = _storage_init_script({"token": "abc123", "tenant": "acme"}, ORIGIN)
    assert script.count("localStorage.setItem(") == 2
    assert 'localStorage.setItem("token", "abc123")' in script
    assert 'localStorage.setItem("tenant", "acme")' in script


def test_storage_init_script_scoped_to_origin():
    """跨網域 iframe / SSO 轉址網域的 document 也會跑到這段 init script ——
    必須用 location.origin guard 包住，只在目標 origin 才真的 setItem。"""
    script = _storage_init_script({"token": "abc123"}, ORIGIN)
    assert f"location.origin === {_json.dumps(ORIGIN)}" in script
    assert "if (" in script


def test_storage_init_script_wrapped_in_try_catch():
    """sandboxed frame / about:blank 存取 localStorage 或 location.origin
    可能丟 SecurityError —— 整段必須包 try/catch，不能讓例外冒出去噴 console。"""
    script = _storage_init_script({"token": "abc123"}, ORIGIN)
    assert script.strip().startswith("try {")
    assert "catch" in script


def test_storage_init_script_escapes_js_injection_payload_in_value():
    # 值裡帶雙引號 / 反斜線 / 換行 — json.dumps 必須把它序列化成安全字面值，
    # 不能讓值本身逃出字串邊界去注入額外 JS。
    payload = '");alert(1);//'
    script = _storage_init_script({"token": payload}, ORIGIN)
    # 未跳脫的原始注入字串（雙引號沒有被跳脫）不應原樣出現在 JS 裡。
    assert '"token", "");alert(1);//"' not in script
    # json.dumps 應把雙引號跳脫成 \" —— 逃逸字元確實出現在輸出中。
    assert '\\"' in script
    # 反解序列化後應該精確等於原始 payload（值本身沒有被破壞或截斷）。
    m = re.search(r'localStorage\.setItem\("token", (".*")\);', script)
    assert m
    assert _json.loads(m.group(1)) == payload


def test_storage_init_script_escapes_js_injection_payload_in_key():
    # key 本身也要能安全序列化——惡意 key 不能逃出字串邊界。
    malicious_key = '"];alert(1);//'
    script = _storage_init_script({malicious_key: "v"}, ORIGIN)
    m = re.search(r'localStorage\.setItem\((".*?"), "v"\);', script)
    assert m
    assert _json.loads(m.group(1)) == malicious_key


def test_storage_init_script_empty_dict_returns_empty_or_noop():
    script = _storage_init_script({}, ORIGIN)
    assert "localStorage.setItem" not in script


# ---------------------------------------------------------------------------
# analyze_url — error 路徑（環境變數缺失 / 非字串值時整個 tool 回傳
# error，不開瀏覽器）
# ---------------------------------------------------------------------------

def test_analyze_url_auth_storage_missing_env_returns_error(monkeypatch):
    monkeypatch.delenv("QA_MISSING_TOKEN_XYZ", raising=False)
    result = asyncio.run(analyze_url(
        "http://localhost/app",
        auth_storage={"token": "$QA_MISSING_TOKEN_XYZ"},
    ))
    assert "error" in result
    assert "QA_MISSING_TOKEN_XYZ" in result["error"]
    # e.args[0] 不應帶 repr 的引號（KeyError 的 str() 才會有引號）。
    assert "'QA_MISSING_TOKEN_XYZ'" not in result["error"]


def test_analyze_url_auth_storage_non_string_value_returns_error():
    result = asyncio.run(analyze_url(
        "http://localhost/app",
        auth_storage={"token": 12345},
    ))
    assert result == {"error": "auth_storage 的值必須是字串", "url": "http://localhost/app"}


def test_analyze_url_no_playwright_returns_import_error_message(monkeypatch):
    """auth_storage=None（預設）不應影響既有行為 —— 走到既有的 ImportError
    分支時訊息不變。用 sys.modules 注入強制觸發 ImportError，避免真的呼叫
    playwright 開瀏覽器（原本的斷言幾乎恆真，且會真的嘗試連線）。"""
    monkeypatch.setitem(sys.modules, "playwright.async_api", None)
    result = asyncio.run(analyze_url("http://localhost/app", auth_storage=None))
    assert "error" in result
    assert "playwright" in result["error"].lower()
    assert result["url"] == "http://localhost/app"


# ---------------------------------------------------------------------------
# server._auto_generate_tests — analyze_url 的 error 經轉發函式原樣早退
# ---------------------------------------------------------------------------

def test_auto_generate_tests_propagates_auth_storage_error(monkeypatch):
    monkeypatch.delenv("QA_MISSING_TOKEN_XYZ", raising=False)
    from gomore_qa_master import server

    result = asyncio.run(server._auto_generate_tests(
        url="http://localhost/app",
        timeout_ms=15000,
        auth_cookie=None,
        tests_per_module=1,
        auth_storage={"token": "$QA_MISSING_TOKEN_XYZ"},
    ))
    assert "error" in result
    assert "QA_MISSING_TOKEN_XYZ" in result["error"]
    # 早退：不應該有任何 generated 欄位（代表沒有繼續跑 generate_test）。
    assert "generated" not in result
