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


# ---------------------------------------------------------------------------
# Reviewer-found false positives / dedup bugs (real Chromium repro) — all
# fixed in `_DOM_PROBE_JS`'s repeated-fallback tier + table scoping.
# ---------------------------------------------------------------------------

NESTED_DIV_TABLE_HTML = """
<html><body>
  <div id="list">
    <div class="row"><div class="cell">a0</div><div class="cell">b0</div><div class="cell">c0</div><div class="cell">d0</div><div class="cell">e0</div></div>
    <div class="row"><div class="cell">a1</div><div class="cell">b1</div><div class="cell">c1</div><div class="cell">d1</div><div class="cell">e1</div></div>
    <div class="row"><div class="cell">a2</div><div class="cell">b2</div><div class="cell">c2</div><div class="cell">d2</div><div class="cell">e2</div></div>
    <div class="row"><div class="cell">a3</div><div class="cell">b3</div><div class="cell">c3</div><div class="cell">d3</div><div class="cell">e3</div></div>
    <div class="row"><div class="cell">a4</div><div class="cell">b4</div><div class="cell">c4</div><div class="cell">d4</div><div class="cell">e4</div></div>
    <div class="row"><div class="cell">a5</div><div class="cell">b5</div><div class="cell">c5</div><div class="cell">d5</div><div class="cell">e5</div></div>
    <div class="row"><div class="cell">a6</div><div class="cell">b6</div><div class="cell">c6</div><div class="cell">d6</div><div class="cell">e6</div></div>
    <div class="row"><div class="cell">a7</div><div class="cell">b7</div><div class="cell">c7</div><div class="cell">d7</div><div class="cell">e7</div></div>
    <div class="row"><div class="cell">a8</div><div class="cell">b8</div><div class="cell">c8</div><div class="cell">d8</div><div class="cell">e8</div></div>
    <div class="row"><div class="cell">a9</div><div class="cell">b9</div><div class="cell">c9</div><div class="cell">d9</div><div class="cell">e9</div></div>
  </div>
</body></html>
"""


def test_nested_repeated_rows_count_as_one_table_not_container_plus_rows():
    """container (10 rows) + each row (5 same-signature cells) all
    independently pattern-match "≥4 same-signature children" — without
    containment dedup this produced 1 + N separate `table` entries for
    the same visual widget."""
    structure = _probe(NESTED_DIV_TABLE_HTML)
    tables = structure["tables"]
    assert len(tables) == 1
    assert tables[0]["detection"] == "repeated"
    assert tables[0]["row_count"] == 10
    assert tables[0]["selector"] == "#list"


ARIA_GRID_WITH_ROWGROUP_HTML = """
<html><body>
  <div role="grid" aria-label="Products">
    <div role="rowgroup">
      <div role="row"><span role="columnheader">SKU</span><span role="columnheader">Name</span></div>
    </div>
    <div role="rowgroup">
      <div role="row"><span role="cell">A1</span><span role="cell">W1</span></div>
      <div role="row"><span role="cell">A2</span><span role="cell">W2</span></div>
      <div role="row"><span role="cell">A3</span><span role="cell">W3</span></div>
      <div role="row"><span role="cell">A4</span><span role="cell">W4</span></div>
      <div role="row"><span role="cell">A5</span><span role="cell">W5</span></div>
      <div role="row"><span role="cell">A6</span><span role="cell">W6</span></div>
    </div>
  </div>
</body></html>
"""


def test_aria_grid_rowgroup_not_double_counted_as_repeated():
    """The `rowgroup` wrapping 6 same-signature `[role="row"]` children
    independently pattern-matches the repeated-fallback tier — it must be
    excluded because it's already inside the ARIA grid counted above."""
    structure = _probe(ARIA_GRID_WITH_ROWGROUP_HTML)
    tables = structure["tables"]
    assert len(tables) == 1
    assert tables[0]["detection"] == "aria"
    assert tables[0]["row_count"] == 6


SELECT_FALSE_POSITIVE_HTML = """
<html><body>
  <select id="s">
    <option>a</option><option>b</option><option>c</option><option>d</option>
  </select>
</body></html>
"""

SVG_FALSE_POSITIVE_HTML = """
<html><body>
  <svg id="chart" width="100" height="100">
    <rect class="bar" x="0" y="0" width="5" height="10"></rect>
    <rect class="bar" x="10" y="0" width="5" height="20"></rect>
    <rect class="bar" x="20" y="0" width="5" height="30"></rect>
    <rect class="bar" x="30" y="0" width="5" height="40"></rect>
    <rect class="bar" x="40" y="0" width="5" height="50"></rect>
    <rect class="bar" x="50" y="0" width="5" height="60"></rect>
    <rect class="bar" x="60" y="0" width="5" height="70"></rect>
    <rect class="bar" x="70" y="0" width="5" height="80"></rect>
  </svg>
</body></html>
"""

BUTTON_GROUP_HTML = """
<html><body>
  <div id="toolbar">
    <button>A</button><button>B</button><button>C</button><button>D</button><button>E</button>
  </div>
</body></html>
"""

ARTICLE_PARAGRAPHS_HTML = """
<html><body>
  <article id="art">
    <p>one</p><p>two</p><p>three</p><p>four</p><p>five</p><p>six</p>
  </article>
</body></html>
"""


def test_select_options_are_not_detected_as_repeated_table():
    structure = _probe(SELECT_FALSE_POSITIVE_HTML)
    assert structure["tables"] == []


def test_svg_bar_chart_is_not_detected_as_repeated_table():
    structure = _probe(SVG_FALSE_POSITIVE_HTML)
    assert structure["tables"] == []


def test_button_group_is_not_detected_as_repeated_table():
    structure = _probe(BUTTON_GROUP_HTML)
    assert structure["tables"] == []


def test_article_paragraphs_are_not_detected_as_repeated_table():
    structure = _probe(ARTICLE_PARAGRAPHS_HTML)
    assert structure["tables"] == []


BARE_TAG_REPEATED_HTML = """
<html><body>
  <div>
    <div class="card">one</div>
    <div class="card">two</div>
    <div class="card">three</div>
    <div class="card">four</div>
  </div>
</body></html>
"""


def test_repeated_fallback_skipped_when_container_has_no_stable_selector():
    """A bare `sel()` fallback like "div" would make Playwright's strict
    locator throw on a non-unique match — the repeated tier must refuse
    to emit a table at all rather than hand back an unusable selector."""
    structure = _probe(BARE_TAG_REPEATED_HTML)
    assert structure["tables"] == []


BARE_TAG_NATIVE_TABLE_HTML = """
<html><body>
  <table>
    <thead><tr><th>A</th></tr></thead>
    <tbody><tr><td>x</td></tr></tbody>
  </table>
</body></html>
"""


def test_native_table_without_stable_selector_flags_selector_unique_false():
    """Unlike the repeated tier, native/ARIA tables are always emitted
    (they're a confident detection) — but metadata must flag when the
    selector is just a bare tag name, so downstream codegen knows not to
    trust it as a unique Playwright locator."""
    structure = _probe(BARE_TAG_NATIVE_TABLE_HTML)
    tables = structure["tables"]
    assert len(tables) == 1
    assert tables[0]["selector"] == "table"
    assert tables[0]["selector_unique"] is False


NESTED_NATIVE_TABLE_HTML = """
<html><body>
  <table id="outer">
    <thead><tr><th>Name</th><th>Detail</th></tr></thead>
    <tbody>
      <tr><td>Alice</td><td>
        <table id="inner">
          <thead><tr><th>Sub</th></tr></thead>
          <tbody><tr><td>x</td></tr><tr><td>y</td></tr></tbody>
        </table>
      </td></tr>
      <tr><td>Bob</td><td>-</td></tr>
    </tbody>
  </table>
</body></html>
"""


def test_nested_native_table_row_counts_scoped_to_own_table():
    """A table nested inside a cell must not have its rows/headers
    counted as the outer table's — `.tHead`/`.tBodies` are scoped to the
    element they're read from, unlike a descendant `querySelectorAll`."""
    structure = _probe(NESTED_NATIVE_TABLE_HTML)
    tables = {t["selector"]: t for t in structure["tables"]}
    assert len(tables) == 2
    outer = tables["#outer"]
    inner = tables["#inner"]
    assert outer["row_count"] == 2
    assert outer["headers"] == ["Name", "Detail"]
    assert inner["row_count"] == 2
    assert inner["headers"] == ["Sub"]


# ---------------------------------------------------------------------------
# standalone_fields exclusions / dedup
# ---------------------------------------------------------------------------

TABLE_ROW_CHECKBOX_HTML = """
<html><body>
  <table>
    <thead><tr><th><input type="checkbox" id="select-all" /></th><th>Name</th></tr></thead>
    <tbody>
      <tr><td><input type="checkbox" class="row-cb" /></td><td>Alice</td></tr>
      <tr><td><input type="checkbox" class="row-cb" /></td><td>Bob</td></tr>
    </tbody>
  </table>
  <input type="text" id="q" />
</body></html>
"""


def test_standalone_fields_exclude_checkboxes_inside_table():
    structure = _probe(TABLE_ROW_CHECKBOX_HTML)
    selectors = {f["selector"] for f in structure["standalone_fields"]}
    assert selectors == {"#q"}


DUPLICATE_SELECTOR_FIELDS_HTML = """
<html><body>
  <div>
    <input type="checkbox" />
    <input type="checkbox" />
    <input type="text" id="q" />
  </div>
</body></html>
"""


def test_standalone_fields_dedupe_by_selector():
    """Two nameless/unlabelled checkboxes both fall back to the bare
    `"input"` selector — without de-duping, both would appear as
    separate fields pointing at the exact same (non-unique) locator."""
    structure = _probe(DUPLICATE_SELECTOR_FIELDS_HTML)
    fields = structure["standalone_fields"]
    assert len(fields) == 2
    selectors = [f["selector"] for f in fields]
    assert len(selectors) == len(set(selectors))


DISPLAY_NONE_FIELD_HTML = """
<html><body>
  <input type="text" id="hidden-one" style="display:none" />
  <input type="text" id="visible-one" />
</body></html>
"""


def test_standalone_fields_exclude_display_none():
    structure = _probe(DISPLAY_NONE_FIELD_HTML)
    selectors = {f["selector"] for f in structure["standalone_fields"]}
    assert selectors == {"#visible-one"}


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
