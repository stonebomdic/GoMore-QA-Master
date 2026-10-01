"""analyze_url 的「資料表模組偵測」與「implicit form 欄位擷取」。

Spike 已驗證的事實：gwp-admin 類 Tailwind 後台的資料表是原生 `<table>`
（也可能是 ARIA grid 或純 div 重複結構），但既有 module kinds
（form/nav/dialog/section/cta）完全沒有 table 類型；搜尋框等 input 常不在
`<form>` 內，導致 form module `fields=[]`。

這裡直接餵 structure dict 給 `_build_modules()`（跳過真的開瀏覽器 —— JS
probe 的行為由另一支 Playwright 整合測試覆蓋），驗證：
  - 三層 table 偵測（native / aria / repeated）各自產生對應 `kind: "table"`
    module，metadata 帶 headers/column_count/row_count/detection。
  - `standalone_fields` 非空時聚合成一個 `kind: "form"`、
    `metadata.implicit = True` 的 module，與既有原生 form 並存。
  - 空 structure 不產生 table / implicit form。
  - table 的 candidate_tcs 涵蓋五個必含情境，且 headers 存在時會引用
    第一個 header 名稱；`detection == "repeated"` 時語氣轉保守。
"""
from gomore_qa_master.tools.analyzer import _build_modules


def _table_modules(modules):
    return [m for m in modules if m["kind"] == "table"]


def _form_modules(modules):
    return [m for m in modules if m["kind"] == "form"]


# ---------------------------------------------------------------------------
# native <table>
# ---------------------------------------------------------------------------

def test_native_table_produces_table_module():
    structure = {
        "tables": [
            {
                "index": 0,
                "selector": "#users-table",
                "label": "使用者列表",
                "headers": ["姓名", "Email", "狀態"],
                "column_count": 3,
                "row_count": 50,
                "detection": "native",
            }
        ],
    }
    modules = _build_modules(structure)
    tables = _table_modules(modules)
    assert len(tables) == 1
    t = tables[0]
    assert t["name"] == "使用者列表_table_0"
    assert t["selectors"]["container"] == "#users-table"
    assert t["metadata"] == {
        "headers": ["姓名", "Email", "狀態"],
        "column_count": 3,
        "row_count": 50,
        "detection": "native",
        "selector_unique": True,
        "visible": None,
    }


def test_table_selector_unique_defaults_true_when_js_omits_it():
    """Older/未升級的 JS probe 輸出不帶 selector_unique 時，Python 端要給
    一個安全預設值（True），而不是讓 KeyError 炸掉或默默變 None。"""
    structure = {
        "tables": [
            {
                "index": 0, "selector": "#t", "label": "T",
                "headers": [], "column_count": 0, "row_count": 1,
                "detection": "native",
            }
        ],
    }
    modules = _build_modules(structure)
    assert _table_modules(modules)[0]["metadata"]["selector_unique"] is True


def test_table_selector_unique_false_is_propagated():
    structure = {
        "tables": [
            {
                "index": 0, "selector": "div", "label": "T",
                "headers": [], "column_count": 0, "row_count": 1,
                "detection": "native", "selector_unique": False,
            }
        ],
    }
    modules = _build_modules(structure)
    assert _table_modules(modules)[0]["metadata"]["selector_unique"] is False


def test_native_table_name_is_slugged_from_label():
    structure = {
        "tables": [
            {
                "index": 2,
                "selector": "table.data",
                "label": "Order History",
                "headers": [],
                "column_count": 0,
                "row_count": 0,
                "detection": "native",
            }
        ],
    }
    modules = _build_modules(structure)
    t = _table_modules(modules)[0]
    assert t["name"] == "order_history_table_2"


def test_table_with_no_label_falls_back_to_index_name():
    structure = {
        "tables": [
            {
                "index": 1,
                "selector": "table",
                "label": "",
                "headers": [],
                "column_count": 0,
                "row_count": 0,
                "detection": "native",
            }
        ],
    }
    modules = _build_modules(structure)
    t = _table_modules(modules)[0]
    assert t["name"] == "table_table_1"


# ---------------------------------------------------------------------------
# aria grid
# ---------------------------------------------------------------------------

def test_aria_grid_produces_table_module_with_aria_detection():
    structure = {
        "tables": [
            {
                "index": 0,
                "selector": "[data-testid=\"grid\"]",
                "label": "Products",
                "headers": ["SKU", "Name", "Price"],
                "column_count": 3,
                "row_count": 12,
                "detection": "aria",
            }
        ],
    }
    modules = _build_modules(structure)
    t = _table_modules(modules)[0]
    assert t["metadata"]["detection"] == "aria"
    assert t["metadata"]["headers"] == ["SKU", "Name", "Price"]


# ---------------------------------------------------------------------------
# repeated fallback
# ---------------------------------------------------------------------------

def test_repeated_fallback_produces_table_module_with_conservative_tcs():
    structure = {
        "tables": [
            {
                "index": 0,
                "selector": "#card-list",
                "label": "",
                "headers": [],
                "column_count": 0,
                "row_count": 8,
                "detection": "repeated",
            }
        ],
    }
    modules = _build_modules(structure)
    t = _table_modules(modules)[0]
    assert t["metadata"]["detection"] == "repeated"
    # 保守語氣：candidate_tcs 應以「疑似資料列表」措辭，不應斷言為確定的表格。
    assert any("疑似資料列表" in tc for tc in t["candidate_tcs"])
    assert not any(tc.startswith("表格") for tc in t["candidate_tcs"])


# ---------------------------------------------------------------------------
# 無 thead（headers=[]）
# ---------------------------------------------------------------------------

def test_table_without_thead_has_empty_headers_but_still_builds_module():
    structure = {
        "tables": [
            {
                "index": 0,
                "selector": "table.no-head",
                "label": "Legacy table",
                "headers": [],
                "column_count": 4,
                "row_count": 20,
                "detection": "native",
            }
        ],
    }
    modules = _build_modules(structure)
    t = _table_modules(modules)[0]
    assert t["metadata"]["headers"] == []
    assert t["metadata"]["column_count"] == 4
    # 沒有 header 名稱可引用時，排序 TC 仍要存在，但不應引用空字串欄位名。
    assert any("排序" in tc for tc in t["candidate_tcs"])
    assert not any('「」' in tc for tc in t["candidate_tcs"])


# ---------------------------------------------------------------------------
# table candidate_tcs 內容斷言（五個必含情境 + header 引用）
# ---------------------------------------------------------------------------

def test_table_candidate_tcs_cover_required_scenarios_and_reference_header():
    structure = {
        "tables": [
            {
                "index": 0,
                "selector": "#t",
                "label": "Users",
                "headers": ["Name", "Email"],
                "column_count": 2,
                "row_count": 10,
                "detection": "native",
            }
        ],
    }
    modules = _build_modules(structure)
    tcs = _table_modules(modules)[0]["candidate_tcs"]
    joined = "\n".join(tcs)
    assert "row_count" in joined or "資料列" in joined  # 表格載入應有資料列／空狀態提示
    assert "分頁" in joined
    assert "排序" in joined
    assert "Name" in joined  # 引用第一個 header 名稱增加可操作性
    assert "搜尋" in joined or "過濾" in joined
    assert "詳情" in joined


# ---------------------------------------------------------------------------
# standalone_fields → implicit form
# ---------------------------------------------------------------------------

def test_standalone_fields_aggregate_into_implicit_form_module():
    structure = {
        "standalone_fields": [
            {"label": "關鍵字", "selector": "#q", "type": "text", "required": False},
            {"label": "狀態", "selector": "#status", "type": "select", "required": False},
        ],
    }
    modules = _build_modules(structure)
    forms = _form_modules(modules)
    assert len(forms) == 1
    f = forms[0]
    assert f["name"] == "implicit_form_0"
    assert f["metadata"]["implicit"] is True
    assert f["metadata"]["field_count"] == 2
    assert f["selectors"]["submit"] is None
    assert f["selectors"]["fields"] == structure["standalone_fields"]


def test_implicit_form_candidate_tcs_exclude_submit_wording():
    structure = {
        "standalone_fields": [
            {"label": "關鍵字", "selector": "#q", "type": "text", "required": True},
        ],
    }
    modules = _build_modules(structure)
    f = _form_modules(modules)[0]
    joined = "\n".join(f["candidate_tcs"])
    assert "送出" not in joined
    assert "提交" not in joined
    assert "Enter" in joined
    assert "清空" in joined


def test_implicit_form_candidate_tcs_exclude_email_format_wording():
    """review N4：`_render_implicit_form_test` 只在「完全沒有 text/search
    欄位」時才會退而求其次碰到 email 欄位——多數情況下 email 欄位根本不
    會被實際渲染進測試，candidate_tcs 不該斷言一個通常不會發生的互動。"""
    structure = {
        "standalone_fields": [
            {"label": "Email", "selector": "#email", "type": "email", "required": True},
        ],
    }
    modules = _build_modules(structure)
    f = _form_modules(modules)[0]
    joined = "\n".join(f["candidate_tcs"])
    assert "Email" not in joined
    assert "格式錯誤" not in joined


def test_implicit_form_candidate_tcs_exclude_single_field_empty_wording():
    """「只填其他欄位、X 留空」這類措辭會被
    `runners/pytest_playwright._extract_single_empty_label` 誤判成要渲染
    fill-everything-but-X 的 TC——implicit form 沒有這種渲染路徑（只填
    第一個欄位後按 Enter），所以 candidate_tcs 不該出現「留空」字樣，
    即使欄位是 required。"""
    structure = {
        "standalone_fields": [
            {"label": "關鍵字", "selector": "#q", "type": "text", "required": True},
            {"label": "狀態", "selector": "#status", "type": "select", "required": True},
        ],
    }
    modules = _build_modules(structure)
    f = _form_modules(modules)[0]
    joined = "\n".join(f["candidate_tcs"])
    assert "留空" not in joined


# ---------------------------------------------------------------------------
# 原生 form 與 implicit form 並存
# ---------------------------------------------------------------------------

def test_native_form_and_implicit_form_coexist():
    structure = {
        "forms": [
            {
                "index": 0,
                "selector": "#login-form",
                "action": "/login",
                "method": "post",
                "fields": [
                    {"label": "Username", "selector": "#u", "type": "text", "required": True},
                ],
                "submit": {"selector": "#login-submit", "text": "登入"},
            }
        ],
        "standalone_fields": [
            {"label": "搜尋", "selector": "#search", "type": "text", "required": False},
        ],
    }
    modules = _build_modules(structure)
    forms = _form_modules(modules)
    assert len(forms) == 2
    names = {f["name"] for f in forms}
    assert "implicit_form_0" in names
    native = next(f for f in forms if f["name"] != "implicit_form_0")
    assert native["metadata"].get("implicit") is not True
    implicit = next(f for f in forms if f["name"] == "implicit_form_0")
    assert implicit["metadata"]["implicit"] is True


# ---------------------------------------------------------------------------
# 空 structure 不產生 table / implicit form modules
# ---------------------------------------------------------------------------

def test_empty_structure_produces_no_table_or_implicit_form_modules():
    modules = _build_modules({})
    assert _table_modules(modules) == []
    assert _form_modules(modules) == []


def test_structure_with_empty_lists_produces_no_table_or_implicit_form_modules():
    modules = _build_modules({"tables": [], "standalone_fields": [], "forms": []})
    assert _table_modules(modules) == []
    assert _form_modules(modules) == []


# ---------------------------------------------------------------------------
# metadata.visible 透傳 + visible=False 的 candidate_tcs 前綴
# （gwp-admin /users 實測：關閉的登出確認 dialog 內按鈕／空 form、收合
# 側欄的登出鈕都是「隱藏元素被收為模組」—— analyzer 仍要記錄它們存在，
# 只是標旗標讓下游 renderer 知道不要產生會保證紅的斷言）。
# ---------------------------------------------------------------------------


def test_table_metadata_visible_true_is_propagated():
    structure = {
        "tables": [
            {
                "index": 0, "selector": "#t", "label": "T",
                "headers": [], "column_count": 0, "row_count": 1,
                "detection": "native", "visible": True,
            }
        ],
    }
    modules = _build_modules(structure)
    assert _table_modules(modules)[0]["metadata"]["visible"] is True


def test_table_metadata_visible_false_is_propagated():
    structure = {
        "tables": [
            {
                "index": 0, "selector": "#t", "label": "T",
                "headers": [], "column_count": 0, "row_count": 1,
                "detection": "native", "visible": False,
            }
        ],
    }
    modules = _build_modules(structure)
    assert _table_modules(modules)[0]["metadata"]["visible"] is False


def test_table_metadata_visible_defaults_none_when_js_omits_it():
    structure = {
        "tables": [
            {
                "index": 0, "selector": "#t", "label": "T",
                "headers": [], "column_count": 0, "row_count": 1,
                "detection": "native",
            }
        ],
    }
    modules = _build_modules(structure)
    assert _table_modules(modules)[0]["metadata"]["visible"] is None


def test_form_module_visible_false_prefixes_first_candidate_tc():
    structure = {
        "forms": [
            {
                "index": 0, "selector": "#logout-form", "action": "/logout",
                "method": "post", "fields": [], "submit": None, "visible": False,
            }
        ],
    }
    modules = _build_modules(structure)
    f = _form_modules(modules)[0]
    assert f["metadata"]["visible"] is False
    assert f["candidate_tcs"][0].startswith("（需先觸發顯示）")


def test_form_module_visible_true_does_not_prefix_candidate_tc():
    structure = {
        "forms": [
            {
                "index": 0, "selector": "#f", "action": "/x",
                "method": "post", "fields": [], "submit": None, "visible": True,
            }
        ],
    }
    modules = _build_modules(structure)
    f = _form_modules(modules)[0]
    assert not f["candidate_tcs"][0].startswith("（需先觸發顯示）")


def test_cta_module_visible_false_prefixes_first_candidate_tc_and_propagates_metadata():
    structure = {
        "ctas": [
            {"text": "確認登出", "selector": "button", "tag": "button", "visible": False},
        ],
    }
    modules = _build_modules(structure)
    cta = next(m for m in modules if m["kind"] == "cta")
    assert cta["metadata"]["visible"] is False
    assert cta["candidate_tcs"][0].startswith("（需先觸發顯示）")


def test_cta_module_visible_true_does_not_prefix_candidate_tc():
    structure = {
        "ctas": [
            {"text": "登入", "selector": "button", "tag": "button", "visible": True},
        ],
    }
    modules = _build_modules(structure)
    cta = next(m for m in modules if m["kind"] == "cta")
    assert not cta["candidate_tcs"][0].startswith("（需先觸發顯示）")


def test_dialog_module_visible_is_propagated_distinct_from_open():
    """`open` is dialog-attribute state; `visible` is actual rendered
    visibility — they can diverge (e.g. CSS display:none overriding a
    present `[role=dialog]` without a `hidden` attribute)."""
    structure = {
        "dialogs": [
            {"index": 0, "selector": "#d", "label": "確認登出", "open": False, "visible": False},
        ],
    }
    modules = _build_modules(structure)
    d = next(m for m in modules if m["kind"] == "dialog")
    assert d["metadata"]["open_on_load"] is False
    assert d["metadata"]["visible"] is False


def test_multiple_tables_each_produce_own_module():
    structure = {
        "tables": [
            {
                "index": 0, "selector": "#a", "label": "A",
                "headers": ["x"], "column_count": 1, "row_count": 1,
                "detection": "native",
            },
            {
                "index": 1, "selector": "#b", "label": "B",
                "headers": [], "column_count": 0, "row_count": 3,
                "detection": "aria",
            },
        ],
    }
    modules = _build_modules(structure)
    tables = _table_modules(modules)
    assert len(tables) == 2
    assert {t["name"] for t in tables} == {"a_table_0", "b_table_1"}
