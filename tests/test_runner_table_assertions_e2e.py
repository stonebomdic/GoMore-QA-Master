"""真實 Chromium E2E：驗證 renderer 產出的 table 斷言程式碼本身能在真的
瀏覽器裡跑過——而不是只驗證產出的字串長得對。

I1/I2（Opus 覆審 round）的假紅都是「字串看起來對，實際執行卻會失敗」的
典型案例：
  - analyzer probe 的 `headers` 已經 `.map(txt).filter(Boolean)`——
    `headers[0]` 是第一個「非空」表頭文字，但 DOM 裡真正的第一個 `<th>`
    完全可能是空的（例如勾選欄的表頭）。斷言若寫死「第一個 th」，對不到
    就會 FAIL。
  - probe 用 `innerText`（會反映 CSS `text-transform`、把 `<br>` 轉成換
    行等渲染結果），但 Playwright `to_contain_text` 預設比對的是
    `textContent`——兩者在這類樣式下會不一致，必須加 `use_inner_text=True`
    才會跟 probe 看到的文字一致。

這支測試用 `page.set_content` 開一個同時具備「首欄空白表頭」與「表頭套
`text-transform:uppercase`」的 `<table>`，透過 analyzer 的真實 DOM probe
取得 headers/row_count/detection，餵給 `_build_modules` 建出 table
module，再請 renderer（`_render_table_body`）產生斷言程式碼，最後把那段
程式碼原樣 `exec()` 在同一個真實頁面上——通過才代表 I1/I2 真的修好，不是
只是字串符合預期。

另外一支測試驗證 S3（DOM API 建表沒有自動 `<tbody>`）：用
`document.createElement`/`appendChild` 建表而非 parse HTML 字串，確認
renderer 產出的 row locator（`:scope > tbody > tr, :scope > tr`）兩種
真實 DOM 形狀都涵蓋。

`pytest.mark.skipif` 擋掉 playwright 套件不存在 / chromium 執行檔沒裝的
環境。執行時這台機器的 NODE_OPTIONS 若帶 preload 腳本會弄壞 Playwright
的 Node driver——記得 `env -u NODE_OPTIONS`。
"""
import importlib.util
import textwrap

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
pytestmark = pytest.mark.skipif(not _playwright_chromium_available(), reason=_SKIP_REASON)


BLANK_FIRST_HEADER_UPPERCASE_HTML = """
<html><body>
  <table id="users">
    <thead>
      <tr><th></th><th style="text-transform:uppercase">name</th></tr>
    </thead>
    <tbody>
      <tr><td><input type="checkbox"></td><td>Alice</td></tr>
      <tr><td><input type="checkbox"></td><td>Bob</td></tr>
    </tbody>
  </table>
</body></html>
"""


def test_rendered_native_table_header_assertion_passes_against_real_dom_with_blank_th_and_uppercase_css():
    from playwright.sync_api import expect, sync_playwright

    from gomore_qa_master.runners.pytest_playwright import PytestPlaywrightRunner
    from gomore_qa_master.tools.analyzer import _DOM_PROBE_JS, _build_modules

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(BLANK_FIRST_HEADER_UPPERCASE_HTML)
            structure = page.evaluate(_DOM_PROBE_JS)
            modules = _build_modules(structure)
            table_module = next(m for m in modules if m["kind"] == "table")

            # Real-DOM sanity check: the probe skipped the blank first
            # <th> (filter(Boolean)), and the surviving header text
            # reflects the uppercase CSS — this is innerText, not
            # textContent (which would still be "name", lowercase).
            assert table_module["metadata"]["headers"] == ["NAME"]

            runner = PytestPlaywrightRunner()
            body = runner._render_table_body(
                table_module["selectors"], table_module["metadata"],
            )
            # I1: must never pin the assertion to "the first <th>" —
            # the blank header column means that's the wrong element.
            assert 'table.locator("thead th").first' not in body
            assert 'table.locator("thead")' in body
            # I2: must compare via innerText, matching how the probe
            # itself read the header text.
            assert "use_inner_text=True" in body

            # 真的把這段產出程式碼在同一頁面上執行 —— 通過即代表 I1/I2 修好了。
            namespace = {"page": page, "expect": expect}
            exec(textwrap.dedent(body), namespace)  # noqa: S102 — 刻意執行 renderer 的真實產出，而非只檢查字串
        finally:
            browser.close()


def test_rendered_native_table_row_assertion_covers_dom_built_table_with_no_auto_tbody():
    """S3：透過 DOM API（`createElement`/`appendChild`）建出的 `<table>`
    不會有瀏覽器自動補的 `<tbody>`——`table.locator("tbody tr")` 會 0
    匹配，即使 `el.tBodies`/`el.rows`（analyzer probe 實際讀取的 API）
    看得到那些列。渲染出的 row locator 必須同時涵蓋「有 tbody」與
    「沒有 tbody」兩種真實 DOM 形狀才能通過。"""
    from playwright.sync_api import expect, sync_playwright

    from gomore_qa_master.runners.pytest_playwright import PytestPlaywrightRunner

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content("<html><body><table id='t'></table></body></html>")
            page.evaluate(
                """() => {
                    const t = document.getElementById('t');
                    for (let i = 0; i < 3; i++) {
                        const tr = document.createElement('tr');
                        const td = document.createElement('td');
                        td.textContent = 'row' + i;
                        tr.appendChild(td);
                        t.appendChild(tr);
                    }
                }"""
            )
            # Sanity check the fixture actually reproduces the no-tbody shape.
            assert page.eval_on_selector("#t", "el => el.tBodies.length") == 0
            assert page.eval_on_selector("#t", "el => el.rows.length") == 3

            runner = PytestPlaywrightRunner()
            metadata = {"headers": [], "column_count": 1, "row_count": 3, "detection": "native"}
            body = runner._render_table_body({"container": "#t"}, metadata)
            assert ':scope > tbody > tr, :scope > tr' in body

            namespace = {"page": page, "expect": expect}
            exec(textwrap.dedent(body), namespace)  # noqa: S102
        finally:
            browser.close()


def test_rendered_native_table_row_assertion_also_covers_parsed_html_table_with_real_tbody():
    """反向情境：HTML 字串 parse 出來的表格「有」自動補的 `<tbody>`——
    `:scope` 版本的 locator 不能因為涵蓋了無 tbody 情境就讓有 tbody 的
    常見情境變假紅。"""
    from playwright.sync_api import expect, sync_playwright

    from gomore_qa_master.runners.pytest_playwright import PytestPlaywrightRunner
    from gomore_qa_master.tools.analyzer import _DOM_PROBE_JS, _build_modules

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(
                "<html><body><table id='t'>"
                "<tbody><tr><td>a</td></tr><tr><td>b</td></tr></tbody>"
                "</table></body></html>"
            )
            assert page.eval_on_selector("#t", "el => el.tBodies.length") == 1

            structure = page.evaluate(_DOM_PROBE_JS)
            modules = _build_modules(structure)
            table_module = next(m for m in modules if m["kind"] == "table")

            runner = PytestPlaywrightRunner()
            body = runner._render_table_body(
                table_module["selectors"], table_module["metadata"],
            )
            namespace = {"page": page, "expect": expect}
            exec(textwrap.dedent(body), namespace)  # noqa: S102
        finally:
            browser.close()
