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


BARE_TAG_FIELDS_HTML = """
<html><body>
  <div>
    <input type="checkbox" />
    <input type="checkbox" />
    <input type="text" id="q" />
  </div>
</body></html>
"""


def test_standalone_fields_drop_bare_tag_selector_fields_entirely():
    """Two nameless/unlabelled checkboxes both fall back to the bare
    `"input"` selector — that's unusable as a Playwright locator the
    moment there's more than one on the page, so (review S2) they're
    dropped outright rather than merged into one field."""
    structure = _probe(BARE_TAG_FIELDS_HTML)
    fields = structure["standalone_fields"]
    selectors = {f["selector"] for f in fields}
    assert selectors == {"#q"}


RADIO_GROUP_SAME_NAME_HTML = """
<html><body>
  <input type="radio" name="choice" value="a" />
  <input type="radio" name="choice" value="b" />
  <input type="text" id="q" />
</body></html>
"""


def test_standalone_fields_dedupe_backstop_for_shared_stable_selector():
    """A same-`name` radio group legitimately shares one STABLE selector
    (`input[name="choice"]`, not a bare tag) across multiple real
    elements — the de-dupe pass (kept as a backstop after the bare-tag
    drop) collapses that down to one field entry instead of two
    duplicates."""
    structure = _probe(RADIO_GROUP_SAME_NAME_HTML)
    fields = structure["standalone_fields"]
    assert len(fields) == 2
    selectors = [f["selector"] for f in fields]
    assert len(selectors) == len(set(selectors))
    radio = next(f for f in fields if f["type"] == "radio")
    assert radio["selector"] == 'input[name="choice"]'


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


# ---------------------------------------------------------------------------
# Real-page regression: gwp-admin's search box has ONLY a placeholder — no
# id/data-testid/name/aria-label. The S2 bare-tag drop (previous fix round)
# dropped it entirely, re-breaking the exact field this whole feature was
# built to catch (analyze_url on /users came back `implicit_present: False`
# again). `sel()` + `hasStableSelector()` both needed a placeholder
# fallback, mirrored between the two.
# ---------------------------------------------------------------------------

PLACEHOLDER_ONLY_SEARCH_HTML = """
<html><body>
  <input type="text" placeholder="搜尋使用者" />
</body></html>
"""


def test_standalone_field_with_only_placeholder_is_kept_with_placeholder_selector():
    structure = _probe(PLACEHOLDER_ONLY_SEARCH_HTML)
    fields = structure["standalone_fields"]
    assert len(fields) == 1
    assert fields[0]["selector"] == 'input[placeholder="搜尋使用者"]'


NO_ATTRIBUTES_INPUT_HTML = """
<html><body>
  <input type="text" />
</body></html>
"""


def test_standalone_field_with_no_attributes_at_all_is_still_dropped():
    """An input with no id/data-testid/name/aria-label/placeholder falls
    all the way back to the bare `"input"` tag selector — still unusable,
    so it's still dropped (placeholder is an ADDITIONAL stable-selector
    source, not a blanket exemption from the bare-tag rule)."""
    structure = _probe(NO_ATTRIBUTES_INPUT_HTML)
    assert structure["standalone_fields"] == []


PLACEHOLDER_WITH_QUOTE_HTML = """
<html><body>
  <input type="text" placeholder='Say &quot;hi&quot; here' />
</body></html>
"""


def test_standalone_field_placeholder_with_quote_is_escaped_in_selector():
    """A placeholder containing a literal `"` must not break out of the
    `[placeholder="..."]` selector's string — and the resulting selector
    must actually still resolve back to the one input via Playwright."""
    structure = _probe(PLACEHOLDER_WITH_QUOTE_HTML)
    fields = structure["standalone_fields"]
    assert len(fields) == 1
    selector = fields[0]["selector"]
    assert selector == 'input[placeholder="Say \\"hi\\" here"]'

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(PLACEHOLDER_WITH_QUOTE_HTML)
            assert page.locator(selector).count() == 1
        finally:
            browser.close()


NAME_ONLY_SELECT_TEXTAREA_HTML = """
<html><body>
  <select name="status"><option>all</option><option>active</option></select>
  <textarea name="note"></textarea>
</body></html>
"""


def test_standalone_select_and_textarea_with_only_name_are_kept():
    """A filter-bar `<select name=...>` / `<textarea name=...>` is as common
    as a placeholder-only search box. The `name` fallback in `sel()` and
    `hasStableSelector()` must cover SELECT/TEXTAREA, not just INPUT —
    otherwise the bare-tag drop removes them entirely."""
    structure = _probe(NAME_ONLY_SELECT_TEXTAREA_HTML)
    selectors = {f["selector"] for f in structure["standalone_fields"]}
    assert selectors == {'select[name="status"]', 'textarea[name="note"]'}


TESTID_WITH_QUOTE_HTML = """
<html><body>
  <input type="text" data-testid='x&quot;y' />
</body></html>
"""


def test_data_testid_with_quote_is_escaped_and_resolvable():
    structure = _probe(TESTID_WITH_QUOTE_HTML)
    fields = structure["standalone_fields"]
    assert len(fields) == 1
    selector = fields[0]["selector"]
    assert selector == '[data-testid="x\\"y"]'

    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(TESTID_WITH_QUOTE_HTML)
            assert page.locator(selector).count() == 1
        finally:
            browser.close()


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


# ---------------------------------------------------------------------------
# isVisible() — real Chromium repro of the gwp-admin /users 4 紅 regression:
# a closed logout-confirm `<dialog>`'s cancel/confirm buttons + its own empty
# `<form>`, and a collapsed sidebar's logout cta, were all collected as
# modules (correct — analyzer must still surface them) but rendered with
# click/fill/to_be_visible (wrong — guaranteed red, since nothing in the
# generated test ever opens the dialog / expands the sidebar).
# ---------------------------------------------------------------------------

CLOSED_DIALOG_HTML = """
<html><body>
  <dialog id="confirm-logout">
    <p>Are you sure you want to log out?</p>
    <button type="button">取消</button>
    <button type="button" id="confirm-btn">確認登出</button>
    <form id="logout-form" action="/logout" method="post"></form>
  </dialog>
</body></html>
"""

DISPLAY_NONE_CTA_HTML = """
<html><body>
  <div id="collapsed-sidebar" style="display:none">
    <button type="button">登出</button>
  </div>
</body></html>
"""

NORMAL_VISIBLE_HTML = """
<html><body>
  <button type="button">登入</button>
  <dialog id="welcome" open>
    <p>Welcome</p>
  </dialog>
</body></html>
"""

HIDDEN_TABLE_HTML = """
<html><body>
  <div style="display:none">
    <table id="t">
      <thead><tr><th>A</th></tr></thead>
      <tbody><tr><td>x</td></tr></tbody>
    </table>
  </div>
</body></html>
"""


def test_closed_dialog_button_and_empty_form_are_marked_not_visible():
    structure = _probe(CLOSED_DIALOG_HTML)
    dialogs = structure["dialogs"]
    assert len(dialogs) == 1
    assert dialogs[0]["open"] is False
    assert dialogs[0]["visible"] is False

    ctas = structure["ctas"]
    cta_texts = {c["text"]: c for c in ctas}
    assert "取消" in cta_texts
    assert "確認登出" in cta_texts
    assert cta_texts["取消"]["visible"] is False
    assert cta_texts["確認登出"]["visible"] is False

    forms = structure["forms"]
    assert len(forms) == 1
    assert forms[0]["visible"] is False


def test_display_none_container_cta_is_marked_not_visible():
    structure = _probe(DISPLAY_NONE_CTA_HTML)
    ctas = structure["ctas"]
    assert len(ctas) == 1
    assert ctas[0]["text"] == "登出"
    assert ctas[0]["visible"] is False


def test_normal_visible_elements_are_marked_visible():
    structure = _probe(NORMAL_VISIBLE_HTML)
    ctas = structure["ctas"]
    assert len(ctas) == 1
    assert ctas[0]["visible"] is True

    dialogs = structure["dialogs"]
    assert len(dialogs) == 1
    assert dialogs[0]["open"] is True
    assert dialogs[0]["visible"] is True


def test_hidden_table_variant_is_marked_not_visible():
    structure = _probe(HIDDEN_TABLE_HTML)
    tables = structure["tables"]
    assert len(tables) == 1
    assert tables[0]["detection"] == "native"
    assert tables[0]["visible"] is False


def test_native_table_normally_visible_is_marked_visible():
    structure = _probe(NATIVE_TABLE_HTML)
    tables = structure["tables"]
    assert len(tables) == 1
    assert tables[0]["visible"] is True


# ---------------------------------------------------------------------------
# Opus 覆審 round（real Chromium repro）：
#
# I4 — isVisible 對齊 Playwright 自己的可見性定義（non-empty bounding box +
# visibility != hidden），移除 opacity 檢查。opacity:0 是 MUI/antd 等 UI
# library 常見的「視覺隱藏但仍可互動」自訂 checkbox/radio 手法——舊版
# isVisible 誤判成不可見，會把這類欄位整個排除在 standalone_fields 之外
# （explicit form 的 fields 同理，雖然目前還沒有獨立測試覆蓋那條路徑）。
# layout_warnings 是完全不同的關注點（抓視覺跑版，不是互動性），保留它
# 原本的 opacity 檢查，不跟 isVisible 共用。
#
# I5 — 子元素全部 float / position:absolute / display:contents 的
# `<form>`：表單本身的 bounding rect 可能是 0（display:contents 的元素
# 根本不生成自己的 box；全部子元素 float/absolute 時部分瀏覽器下父層高度
# 會 collapse 成 0），但欄位本身是可見、可互動的——`visible` 不該只看
# form 自己，要 fallback 到任一欄位或 submit 鈕可見。
#
# S1 — 便宜的離屏判斷：bounding rect 完全落在 viewport 原點左／上方（常見
# 的 `position:absolute; left:-9999px` 視覺隱藏手法）視為不可見。
# ---------------------------------------------------------------------------

OPACITY_ZERO_CHECKBOX_HTML = """
<html><body>
  <input type="checkbox" id="agree" style="opacity:0" />
</body></html>
"""


def test_standalone_field_opacity_zero_is_still_visible_per_playwright_semantics():
    """I4：opacity:0 不等於 Playwright 定義下的「不可見」——沿用舊版
    isVisible（w===0 && h===0，不看 opacity）的語意，opacity:0 的自訂
    checkbox 必須留在 standalone_fields 裡，不能被整個濾掉。"""
    structure = _probe(OPACITY_ZERO_CHECKBOX_HTML)
    selectors = {f["selector"] for f in structure["standalone_fields"]}
    assert "#agree" in selectors


LAYOUT_WARNING_OPACITY_ZERO_HTML = """
<html><body>
  <div id="ghost" style="opacity:0; width:50px; overflow:hidden; white-space:nowrap">
    this text is way too long to fit in fifty pixels wide
  </div>
</body></html>
"""


def test_layout_warnings_still_excludes_opacity_zero_elements():
    """layout_warnings 保留自己原本的 opacity 檢查（不是互動性關注點，不該
    跟 isVisible 共用）——opacity:0 的跑版候選不該被回報成視覺 bug。"""
    structure = _probe(LAYOUT_WARNING_OPACITY_ZERO_HTML)
    selectors = {w["selector"] for w in structure["layout_warnings"]}
    assert "#ghost" not in selectors


ZERO_HEIGHT_FORM_FLOAT_CHILDREN_HTML = """
<html><body>
  <style>
    .float-form { overflow: visible; }
    .float-form input, .float-form button { float: left; }
  </style>
  <form class="float-form" id="float-form">
    <input type="text" id="q" />
    <button type="submit" id="go">Go</button>
  </form>
</body></html>
"""


def test_form_with_all_floated_children_is_still_marked_visible():
    """I5：子元素全部 float 時，部分瀏覽器下沒有 clearfix 的 form 本身高度
    會 collapse 成 0（bounding rect height=0）——但欄位跟送出鈕都還看得到、
    點得到。visible 要 fallback 到欄位／submit 任一可見。"""
    structure = _probe(ZERO_HEIGHT_FORM_FLOAT_CHILDREN_HTML)
    forms = structure["forms"]
    assert len(forms) == 1
    # Sanity check the fixture actually reproduces a zero-size form (if this
    # ever stops collapsing in a future Chromium, the fallback logic is
    # still correct, but this assertion documents the real-browser
    # precondition the fix targets).
    assert forms[0]["visible"] is True


DISPLAY_CONTENTS_FORM_HTML = """
<html><body>
  <form id="contents-form" style="display:contents">
    <input type="text" id="q2" />
    <button type="submit" id="go2">Go</button>
  </form>
</body></html>
"""


def test_form_with_display_contents_is_still_marked_visible():
    """`display:contents` 的元素完全不生成自己的 box——`getBoundingClientRect()`
    必然是全 0——但子元素正常渲染、正常可見。"""
    structure = _probe(DISPLAY_CONTENTS_FORM_HTML)
    forms = structure["forms"]
    assert len(forms) == 1
    assert forms[0]["visible"] is True


ABSOLUTE_CHILDREN_FORM_HTML = """
<html><body>
  <form id="abs-form" style="position:relative; height:0; overflow:visible">
    <input type="text" id="q3" style="position:absolute; top:0; left:0" />
    <button type="submit" id="go3" style="position:absolute; top:30px; left:0">Go</button>
  </form>
</body></html>
"""


def test_form_with_absolute_positioned_children_and_zero_height_is_still_marked_visible():
    """`height:0` 的 form 容器（子元素全 absolute 定位撐不開高度）——欄位
    跟送出鈕都還是可見的，不該因為容器本身 0 高就整組被標成隱藏。"""
    structure = _probe(ABSOLUTE_CHILDREN_FORM_HTML)
    forms = structure["forms"]
    assert len(forms) == 1
    assert forms[0]["visible"] is True


OFFSCREEN_LEFT_FORM_HTML = """
<html><body>
  <form id="offscreen-form" style="position:absolute; left:-9999px; top:0">
    <input type="text" id="q4" />
    <button type="submit" id="go4">Go</button>
  </form>
</body></html>
"""


def test_form_fully_offscreen_to_the_left_is_marked_not_visible():
    """S1：整個 form（含欄位／送出鈕）都用 `left:-9999px` 移到可視區域外
    ——這是常見的「視覺隱藏但仍在 DOM 流程裡」手法，不同於 I5 要救援的
    「容器 0 尺寸但子元素在可視區域內」案例，isVisible 要能分辨兩者。"""
    structure = _probe(OFFSCREEN_LEFT_FORM_HTML)
    forms = structure["forms"]
    assert len(forms) == 1
    assert forms[0]["visible"] is False
