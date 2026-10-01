"""`_DOM_PROBE_JS` 的瀏覽器端行為 — 真的用 Playwright 開一個本地 HTML
fixture（`page.set_content`），執行 `_DOM_PROBE_JS`，驗證三層 table
偵測（native / aria / repeated）與 `standalone_fields` 擷取的實際
DOM 行為（`_build_modules()` 的單元測試只驗證 Python 這一側，餵的是
手寫 structure dict —— 這支測試驗證 JS 真的能從 DOM 產生那樣的
structure）。

`pytest.mark.skipif` 擋掉 playwright 套件不存在 / chromium 執行檔沒裝
的環境（CI 容器可能沒跑 `playwright install chromium`）。

執行時這台機器的 NODE_OPTIONS 若帶 preload 腳本會弄壞 Playwright 的
Node driver（`Connection.init: Connection closed while reading from
the driver`）——本機重現過；在要跑這支測試 / 任何 playwright 指令時
記得 `env -u NODE_OPTIONS`。
"""
import importlib.util

import pytest


def _playwright_chromium_available() -> bool:
    if importlib.util.find_spec("playwright") is None:
        return False
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:
        return False


_SKIP_REASON = "playwright / chromium 不可用（套件未安裝或 `playwright install chromium` 未執行）"
_AVAILABLE = _playwright_chromium_available()

pytestmark = pytest.mark.skipif(not _AVAILABLE, reason=_SKIP_REASON)


NATIVE_TABLE_HTML = """
<html><body>
  <h2>Users</h2>
  <table id="users">
    <caption>User list</caption>
    <thead><tr><th>Name</th><th>Email</th></tr></thead>
    <tbody>
      <tr><td>Alice</td><td>alice@example.com</td></tr>
      <tr><td>Bob</td><td>bob@example.com</td></tr>
    </tbody>
  </table>
</body></html>
"""

ARIA_GRID_HTML = """
<html><body>
  <div role="grid" aria-label="Products">
    <div role="row">
      <span role="columnheader">SKU</span>
      <span role="columnheader">Name</span>
    </div>
    <div role="row"><span role="cell">A1</span><span role="cell">Widget</span></div>
    <div role="row"><span role="cell">A2</span><span role="cell">Gadget</span></div>
  </div>
</body></html>
"""

REPEATED_FALLBACK_HTML = """
<html><body>
  <div id="card-list">
    <div class="card">one</div>
    <div class="card">two</div>
    <div class="card">three</div>
    <div class="card">four</div>
    <div class="card">five</div>
  </div>
</body></html>
"""

NO_THEAD_TABLE_HTML = """
<html><body>
  <table id="legacy">
    <tbody>
      <tr><td>a</td><td>b</td><td>c</td></tr>
      <tr><td>d</td><td>e</td><td>f</td></tr>
    </tbody>
  </table>
</body></html>
"""

STANDALONE_FIELDS_HTML = """
<html><body>
  <input type="text" id="q" placeholder="搜尋" />
  <select id="status"><option>all</option></select>
  <form>
    <input type="text" id="in-form" />
  </form>
</body></html>
"""


def _probe(html: str) -> dict:
    from playwright.sync_api import sync_playwright

    from gomore_qa_master.tools.analyzer import _DOM_PROBE_JS

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(html)
            return page.evaluate(_DOM_PROBE_JS)
        finally:
            browser.close()


def test_native_table_detected_via_real_dom():
    structure = _probe(NATIVE_TABLE_HTML)
    tables = structure["tables"]
    assert len(tables) == 1
    t = tables[0]
    assert t["detection"] == "native"
    assert t["label"] == "User list"  # caption wins
    assert t["headers"] == ["Name", "Email"]
    assert t["column_count"] == 2
    assert t["row_count"] == 2


def test_aria_grid_detected_via_real_dom():
    structure = _probe(ARIA_GRID_HTML)
    tables = structure["tables"]
    assert len(tables) == 1
    t = tables[0]
    assert t["detection"] == "aria"
    assert t["label"] == "Products"
    assert t["headers"] == ["SKU", "Name"]
    assert t["row_count"] == 2  # header row excluded


def test_repeated_fallback_detected_via_real_dom():
    structure = _probe(REPEATED_FALLBACK_HTML)
    tables = structure["tables"]
    assert len(tables) == 1
    t = tables[0]
    assert t["detection"] == "repeated"
    assert t["row_count"] == 5
    assert t["headers"] == []


def test_table_without_thead_has_empty_headers_via_real_dom():
    structure = _probe(NO_THEAD_TABLE_HTML)
    tables = structure["tables"]
    assert len(tables) == 1
    t = tables[0]
    assert t["detection"] == "native"
    assert t["headers"] == []
    assert t["column_count"] == 3  # inferred from first row's cells
    assert t["row_count"] == 2


def test_standalone_fields_exclude_inputs_inside_form():
    structure = _probe(STANDALONE_FIELDS_HTML)
    selectors = {f["selector"] for f in structure["standalone_fields"]}
    assert "#q" in selectors
    assert "#status" in selectors
    assert "#in-form" not in selectors
    assert len(structure["standalone_fields"]) == 2


def test_dom_probe_end_to_end_through_build_modules():
    """Sanity check: real DOM → `_DOM_PROBE_JS` → `_build_modules()`
    produces a `table` module and an `implicit_form_0` module together."""
    from gomore_qa_master.tools.analyzer import _build_modules

    html = NATIVE_TABLE_HTML.replace("</body>", STANDALONE_FIELDS_HTML.split("<body>")[1])
    structure = _probe(html)
    modules = _build_modules(structure)
    kinds = [m["kind"] for m in modules]
    assert "table" in kinds
    assert "form" in kinds
    implicit = next(m for m in modules if m.get("name") == "implicit_form_0")
    assert implicit["metadata"]["implicit"] is True
