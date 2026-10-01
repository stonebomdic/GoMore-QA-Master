import asyncio
import json
import re
import time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from urllib.parse import urlparse

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Resource, TextContent, Tool
from pydantic import AnyUrl

from .config import OPTIMIZATION_PATH, PROJECT_ROOT, REPORT_PATH
from .reporters import html as html_reporter
from .runners import REGISTRY as RUNNER_REGISTRY
from .runners import get_runner
from .runners.pytest_playwright import _is_positive_description
from .tools import (
    analyzer,
    generator,
    optimizer,
    qa_context,
    reporter,
    runner,
    telemetry,
)
from .tools.analyzer import _ENV_REF_RE as _AUTH_ENV_REF_RE
from .tools.registry import REGISTRY as TOOL_REGISTRY
from .tools.registry import register

# Single source of truth: report the installed package version so serverInfo
# stays in lockstep with pyproject (which test_skill_distribution ties to the
# plugin manifests). Previously unset → the mcp lib defaulted to its own
# version, so serverInfo drifted from the real project version.
try:
    _SERVER_VERSION = _pkg_version("gomore-qa-master")
except PackageNotFoundError:  # not installed (bare source tree) — dev fallback
    _SERVER_VERSION = "0.0.0+dev"

app = Server("gomore-qa-master", version=_SERVER_VERSION)


@app.list_tools()
async def list_tools() -> list[Tool]:
    # P2: tool surface 由 REGISTRY 單一事實來源生成 —— 新增 tool 只需在
    # tools/schemas.py 補 description/schema 並在下方 register()。
    # 順序 == 註冊順序（tests/fixtures/tool_surface_golden.json 鎖定逐字）。
    return [
        Tool(name=s.name, description=s.description, inputSchema=s.input_schema)
        for s in TOOL_REGISTRY.values()
    ]


@app.list_resources()
async def list_resources() -> list[Resource]:
    """提供測試報告作為 MCP resource，AI 編輯器可即時讀取。"""
    return [
        Resource(
            uri=AnyUrl("report://html"),
            name="Latest Test Report (HTML)",
            description="最近一次測試報告，即時渲染為自包含 HTML",
            mimeType="text/html",
        ),
        Resource(
            uri=AnyUrl("report://json"),
            name="Latest Test Report (JSON)",
            description="原始 report.json（各 runner 的原生格式）",
            mimeType="application/json",
        ),
        Resource(
            uri=AnyUrl("report://optimization"),
            name="Optimization Plan (Markdown)",
            description="自我強化分析：每跑完一次自動產出的下一輪行動清單",
            mimeType="text/markdown",
        ),
    ]


@app.read_resource()
async def read_resource(uri: AnyUrl) -> str:
    uri_str = str(uri)
    if uri_str == "report://html":
        return html_reporter.render_report()
    if uri_str == "report://json":
        if not REPORT_PATH.exists():
            return "{}"
        return REPORT_PATH.read_text(encoding="utf-8")
    if uri_str == "report://optimization":
        if not OPTIMIZATION_PATH.exists():
            optimizer.write_plan()
        if OPTIMIZATION_PATH.exists():
            return OPTIMIZATION_PATH.read_text(encoding="utf-8")
        return "# Optimization Plan\n\n_目前沒有歷史資料可分析。先跑一次 run_tests。_"
    raise ValueError(f"未知的 resource URI: {uri_str}")


@app.call_tool()
async def call_tool(name: str, args: dict) -> list[TextContent]:
    started = time.time()
    err_type: str | None = None
    try:
        return await _dispatch(name, args)
    except Exception as e:
        err_type = type(e).__name__
        return [TextContent(type="text", text=f"執行錯誤: {err_type}: {e}")]
    finally:
        # Telemetry feeds the optimizer's MCP-usability analysis. Best-effort —
        # never break a tool call because logging failed.
        telemetry.log_tool_call(name, args or {}, int((time.time() - started) * 1000), err_type)


async def _offload(fn, /, *fn_args, **fn_kwargs):
    """Run a blocking handler in a worker thread so the MCP event loop
    stays free to serve concurrent tool calls (P1)."""
    return await asyncio.to_thread(fn, *fn_args, **fn_kwargs)


async def _dispatch(name: str, args: dict) -> list[TextContent]:
    spec = TOOL_REGISTRY.get(name)
    if spec is None:
        return [TextContent(type="text", text=f"未知的 tool: {name}")]
    args = args or {}
    if spec.blocking:
        # subprocess / 網路 / 磁碟 I/O → worker thread，event loop 不被卡（P1）
        return await _offload(spec.handler, args)
    if asyncio.iscoroutinefunction(spec.handler):
        return await spec.handler(args)
    return spec.handler(args)


# ---------------------------------------------------------------------------
# Tool handlers — 一 tool 一薄函式。description/inputSchema 在 tools/schemas.py，
# blocking 與否宣告在檔尾的 register() 區塊（tools/registry.py 的 ToolSpec）。
# ---------------------------------------------------------------------------

def _json_text(payload) -> list[TextContent]:
    return [TextContent(type="text", text=json.dumps(payload, ensure_ascii=False, indent=2))]


def _h_get_runner_info(args: dict) -> list[TextContent]:
    info = {
        "current": get_runner().name,
        "available": sorted({r.name for r in RUNNER_REGISTRY.values()}),
    }
    return _json_text(info)


def _h_list_tests(args: dict) -> list[TextContent]:
    return [TextContent(type="text", text=runner.list_tests())]


def _h_run_tests(args: dict) -> list[TextContent]:
    result = runner.run_tests(
        filter=args.get("filter"),
        headed=args.get("headed", False),
        browser=args.get("browser", "chromium"),
    )
    return _json_text(result)


def _h_run_failed(args: dict) -> list[TextContent]:
    return _json_text(runner.run_failed())


def _h_get_test_report(args: dict) -> list[TextContent]:
    return _json_text(reporter.get_report_summary())


def _h_get_failure_details(args: dict) -> list[TextContent]:
    return _json_text(reporter.get_failure_details(args.get("test_id")))


def _h_generate_test(args: dict) -> list[TextContent]:
    module = args.get("module")
    msg = generator.generate_test(
        args["description"],
        args["filename"],
        url=args.get("url"),
        module=module,
        business_context=args.get("business_context"),
    )
    # Tagging the source lets the optimizer track "URL → AI-generated → adopted"
    # adoption rate per analyze_url module.
    if isinstance(module, dict) and module.get("name"):
        source = f"analyze_url:{module['name']}"
    else:
        source = "manual"
    telemetry.log_generation(args["filename"], args.get("description", ""), source=source)
    return [TextContent(type="text", text=msg)]


def _h_get_qa_context(args: dict) -> list[TextContent]:
    return _json_text(qa_context.load_context(args.get("section")))


def _h_init_qa_knowledge(args: dict) -> list[TextContent]:
    return _json_text(qa_context.init_qa_knowledge(overwrite=args.get("overwrite", False)))


def _h_codegen(args: dict) -> list[TextContent]:
    msg = generator.codegen(args["url"], args.get("output", "recorded_test.py"))
    return [TextContent(type="text", text=msg)]


def _h_generate_html_report(args: dict) -> list[TextContent]:
    target = html_reporter.write_report(args.get("output", "report.html"))
    return [TextContent(type="text", text=f"已產生 HTML 報告：{target}")]


def _h_get_test_history(args: dict) -> list[TextContent]:
    return _json_text(reporter.get_history(args.get("limit", 10)))


def _h_get_optimization_plan(args: dict) -> list[TextContent]:
    plan = optimizer.build_plan(
        history_limit=args.get("history_limit", 10),
        telemetry_limit=args.get("telemetry_limit", 500),
    )
    optimizer.write_plan(plan)
    return _json_text(plan)


async def _h_analyze_url(args: dict) -> list[TextContent]:
    result = await analyzer.analyze_url(
        args["url"],
        timeout_ms=args.get("timeout_ms", 15000),
        auth_cookie=args.get("auth_cookie"),
        auth_storage=args.get("auth_storage"),
    )
    if isinstance(result, dict) and "error" not in result:
        telemetry.log_discovered_modules(args["url"], result.get("modules", []))
    return _json_text(result)


def _h_analyze_screen(args: dict) -> list[TextContent]:
    result = analyzer.analyze_screen(
        args.get("app_id"),
        args.get("launch_app", False),
        args.get("timeout_ms", 30000),
    )
    # Telemetry: log discovered modules with the app_id as the "url"
    # so the optimizer's coverage-gap analysis covers mobile screens too.
    if isinstance(result, dict) and "error" not in result:
        telemetry.log_discovered_modules(
            args.get("app_id") or "screen", result.get("modules", []),
        )
    return _json_text(result)


async def _h_auto_generate_tests(args: dict) -> list[TextContent]:
    result = await _auto_generate_tests(
        url=args["url"],
        timeout_ms=args.get("timeout_ms", 15000),
        auth_cookie=args.get("auth_cookie"),
        auth_storage=args.get("auth_storage"),
        tests_per_module=args.get("tests_per_module", 1),
    )
    return _json_text(result)


def _h_run_api_security_scan(args: dict) -> list[TextContent]:
    from .scanners import SCANNERS
    result = SCANNERS["api_security"].scan(
        args.get("spec_url", ""),
        auth=args.get("auth"),
        categories=args.get("categories"),
        severity_threshold=args.get("severity_threshold", "medium"),
        base_url=args.get("base_url"),
        timeout_s=args.get("timeout_s", 30),
        plan_id=args.get("plan_id"),
    )
    return _json_text(result)


def _h_qa_plan(args: dict) -> list[TextContent]:
    from .tools.qa_plan import qa_plan_tool
    return _json_text(qa_plan_tool(args))


def _h_verify_plan(args: dict) -> list[TextContent]:
    from .tools.qa_plan import verify_plan_tool
    return _json_text(verify_plan_tool(args))


# 註冊順序 == list_tools 順序（golden 鎖定）。blocking=True 者經 _offload。
register("get_runner_info", _h_get_runner_info)
register("list_tests", _h_list_tests, blocking=True)
register("run_tests", _h_run_tests, blocking=True)
register("run_failed", _h_run_failed, blocking=True)
register("get_test_report", _h_get_test_report, blocking=True)
register("get_failure_details", _h_get_failure_details, blocking=True)
register("generate_test", _h_generate_test, blocking=True)
register("codegen", _h_codegen, blocking=True)
register("generate_html_report", _h_generate_html_report, blocking=True)
register("get_test_history", _h_get_test_history, blocking=True)
register("get_optimization_plan", _h_get_optimization_plan, blocking=True)
register("analyze_url", _h_analyze_url)
register("analyze_screen", _h_analyze_screen, blocking=True)
register("init_qa_knowledge", _h_init_qa_knowledge, blocking=True)
register("get_qa_context", _h_get_qa_context, blocking=True)
register("auto_generate_tests", _h_auto_generate_tests)
register("run_api_security_scan", _h_run_api_security_scan, blocking=True)
# v0.9.3 起 qa_plan 有磁碟持久化、v0.9.6 起 verify_plan 讀產物檔 → 皆 blocking。
register("qa_plan", _h_qa_plan, blocking=True)
register("verify_plan", _h_verify_plan, blocking=True)


# Common tracking/analytics endpoints — never the form's own submit target,
# but frequently POST and same-host, so they'd otherwise win the "first
# same-host POST/PUT" heuristic below and produce a nonsense assertion
# (review round 2, minor #5).
_ANALYTICS_PATH_HINTS = ("/collect", "/track", "/beacon", "/analytics")


def _pick_form_api(url: str, module: dict, endpoints: list[dict]) -> dict | None:
    """Best-effort match of a form module to the API call it most likely
    submits to: the first same-host (exact hostname match — not full origin;
    doesn't compare scheme/port, so this alone doesn't distinguish http vs
    https or non-default ports on the same host) POST/PUT endpoint seen
    while the page loaded, skipping obvious analytics/tracking calls. Feeds
    `module["api"]` so the runner can render a real response-status
    assertion instead of a TODO stub (POC F-1 defect #3).

    Heuristic, not causal: `endpoints` are captured on page LOAD, not on
    actual form submit, so a match is a hint, and no match just means no
    assertion is added (fallback TODO stays).
    """
    if module.get("kind") != "form":
        return None
    md = module.get("metadata")
    if isinstance(md, dict) and md.get("implicit"):
        # analyzer._build_modules's implicit_form_0 (fields outside any
        # <form>, e.g. a bare search box) has no real submit action to
        # match against — there's no button click to attach a response
        # assertion to, so don't even try the heuristic below.
        return None
    origin = urlparse(url).hostname
    for ep in endpoints:
        if ep.get("method") not in ("POST", "PUT") or ep.get("host") != origin:
            continue
        path = ep.get("path") or ""
        if any(hint in path for hint in _ANALYTICS_PATH_HINTS):
            continue
        return {"method": ep["method"], "url_substring": path or ep.get("url")}
    return None


def _collect_page_tables(modules: list[dict]) -> list[dict]:
    """Visible native `table` modules with analysis-time `row_count > 0`,
    reduced to just the two fields `PytestPlaywrightRunner
    ._render_implicit_form_test`'s commented-out cross-module hint
    needs (`row_count`, `selector`) — kept minimal on purpose so it can't
    be mistaken for a full table-module payload by anything reading
    `module["page_tables"]`.

    Why this exists at all: "搜尋 → 列數變化" is a real, common assertion
    a user would want, but generating it as a LIVE assertion is fail-open
    on false reds — search may need an extra button click to trigger, or
    may render a "no data" row instead of truly 0 rows, either of which
    would redden a freshly generated test with nothing wrong in the app.
    So the renderer only ever emits it pre-commented; this just supplies
    the facts it needs to do that. `selector` (Opus review round, S2) is
    the ACTUAL table's selector observed at analysis time — the hint
    previously hardcoded `'table'`, which silently breaks the moment a
    page has more than one `<table>` or the table isn't selected by the
    bare tag at all (an id/data-testid-qualified selector, `.first` on
    the wrong table, etc.).

    `detection != "native"` (aria/repeated) and `visible is False` tables
    are both excluded — aria/repeated row locators aren't reliable enough
    to assert on cross-module either, and a hidden table's row_count
    isn't a fact about what the user will actually see. `row_count`'s
    `isinstance(..., bool)` exclusion mirrors
    `PytestPlaywrightRunner._render_table_body`'s own `has_rows` guard —
    `bool` is an `int` subclass in Python, so a stray boolean must not
    satisfy "> 0" (Opus review round, S6).
    """
    out: list[dict] = []
    for m in modules:
        if m.get("kind") != "table":
            continue
        md = m.get("metadata")
        if not isinstance(md, dict):
            continue
        if md.get("detection") != "native":
            continue
        if md.get("visible") is False:
            continue
        row_count = md.get("row_count")
        if isinstance(row_count, int) and not isinstance(row_count, bool) and row_count > 0:
            selectors = m.get("selectors")
            selector = selectors.get("container") if isinstance(selectors, dict) else None
            out.append({"row_count": row_count, "selector": selector})
    return out


def _select_candidate_tcs(candidates: list[str], limit: int) -> list[str]:
    """First `limit` TCs, but guarantee a happy-path TC is among them when
    one exists in `candidates`.

    Why: the analyzer's form TCs always start with the "所有必填欄位為空"
    negative case (tools/analyzer.py:305), so with the default
    tests_per_module=1 the happy-path TC — the only one that can trigger
    `_render_form_test`'s real API-status assertion — was never selected
    (review round 2, major #3). Swaps the last selected slot for the first
    positive TC found; a no-op when one is already selected or none exists.
    """
    if limit <= 0 or not candidates:
        return list(candidates[:limit])
    selected = list(candidates[:limit])
    if any(_is_positive_description(tc) for tc in selected):
        return selected
    happy = next((tc for tc in candidates if _is_positive_description(tc)), None)
    if happy is not None:
        selected[-1] = happy
    return selected


def _resolve_test_filename(slug: str, kind: str, used: set[str]) -> str:
    """Pick a non-colliding `test_*.py` filename for one generated test,
    tracking what this `_auto_generate_tests` run has already used in
    `used` (mutated in place).

    Why: `analyzer._build_modules` can hand back two *different* modules
    (e.g. a `dialog` and a `cta`) that both slug to the same name — a real
    gwp-admin /users scan had a dialog「確認登出」and a cta「確認登出」,
    both slugging to `test_確認登出.py`. Writing the second straight to
    that path silently overwrote the first (9 generated TCs, only 8 files
    on disk). Collision resolution order: bare slug → `{slug}_{kind}` →
    `{slug}_{kind}_2`, `_3`, ... until a free name is found.
    """
    fname = f"test_{slug}.py"
    if fname not in used:
        used.add(fname)
        return fname
    fname = f"test_{slug}_{kind}.py"
    if fname not in used:
        used.add(fname)
        return fname
    n = 2
    while True:
        fname = f"test_{slug}_{kind}_{n}.py"
        if fname not in used:
            used.add(fname)
            return fname
        n += 1


# Marker written as the first line of every conftest.py we generate.
# `_write_auth_conftest` uses its presence to tell "a conftest we wrote
# before, safe to regenerate" apart from "a hand-written conftest, never
# touch it" — review round 3 #10: without this, a conftest we wrote
# ourselves on a previous auto_generate_tests run would permanently
# `skipped (exists)` on every later run, even as auth_storage changes.
_AUTH_CONFTEST_MARKER = "# auto-generated by gomore-qa-master auto_generate_tests"

_AUTH_ENV_NAME_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_]")


def _derive_literal_env_name(key: str, used: set[str]) -> str:
    """Derive a safe, collision-free env var name for a literal (non-
    `$ENV_NAME`) auth_storage/cookie value, from its original `key`.
    `used` is mutated in place so repeated calls across one conftest
    build never hand back the same name twice.

    Review round 3 #6 — concrete failure shapes this guards against:
      - Non-identifier chars (`-`, space, punctuation, non-ASCII) →
        replaced with `_` so the result is always a valid Python
        identifier / env var name.
      - Sanitizing strips *everything* meaningful (e.g. a fully
        non-ASCII key, or an empty key) → the naive `{SANITIZED}_TOKEN`
        would collapse to a useless `___TOKEN` (or just `_TOKEN`) that
        gives the user no hint which value it is — fall back to a
        numbered `AUTH_TOKEN_{n}` instead.
      - Sanitized name starts with a digit (`1abc` → `1ABC`) — not a
        legal-looking identifier as a leading segment — prefixed with
        `AUTH_`.
      - Two different keys sanitize to the same name (`access-token` and
        `access_token` both → `ACCESS_TOKEN`) — numbered `_2`, `_3`...
        suffix so neither silently shadows the other.
    """
    sanitized = _AUTH_ENV_NAME_SANITIZE_RE.sub("_", str(key)).upper()
    if not sanitized or not sanitized.strip("_"):
        base = None  # nothing usable survived sanitizing
    elif sanitized[0].isdigit():
        base = f"AUTH_{sanitized}_TOKEN"
    else:
        base = f"{sanitized}_TOKEN"

    if base is None:
        n = 1
        while True:
            candidate = f"AUTH_TOKEN_{n}"
            if candidate not in used:
                used.add(candidate)
                return candidate
            n += 1

    if base not in used:
        used.add(base)
        return base
    n = 2
    while f"{base}_{n}" in used:
        n += 1
    name = f"{base}_{n}"
    used.add(name)
    return name


def _build_auth_conftest(
    url: str, auth_storage: dict[str, str] | None, auth_cookie: str | None,
) -> tuple[str | None, list[str]]:
    """Render a conftest.py body that seeds Playwright's `storage_state`
    (localStorage + cookies) via a session-scoped `browser_context_args`
    fixture, so every generated test in the project runs already logged
    in — closes the real-world gap where a behind-login site's generated
    suite came back all-red because nothing ever authenticated.

    Returns (content, warnings). `content` is `None` when `url` has no
    parseable hostname (review round 3 #1 — nothing sane to scope the
    injected localStorage/cookies to), in which case `warnings` explains
    why and the caller must not write anything.

    Every value — `$ENV_NAME` indirection or literal — ends up read via
    `os.environ.get(...)` + an explicit `pytest.fail(...)` at fixture
    time (review round 3 #10), never embedded in the file itself. Only
    literal values get a `conftest_warnings` entry (see
    `_derive_literal_env_name`) telling the caller which env var to
    `export` before running tests.
    """
    parsed = urlparse(url)
    # `.hostname` (unlike `.netloc`) already excludes `user:pass@` userinfo
    # and the port, and is lowercased — review round 3 #1: using `.netloc`
    # here both wrote credentials straight into conftest.py and produced
    # an origin that doesn't match what the browser actually sees.
    hostname = parsed.hostname
    if hostname is None:
        return None, [f"無法從 URL {url!r} 解析出 hostname，不產出 auth conftest.py"]
    # 預設 port（https:443 / http:80）要省略，否則與瀏覽器正規化後的
    # origin 字串不符，storage_state 的 localStorage 可能對不上 origin。
    default_port = {"https": 443, "http": 80}.get(parsed.scheme)
    port_suffix = f":{parsed.port}" if parsed.port and parsed.port != default_port else ""
    origin = f"{parsed.scheme}://{hostname}{port_suffix}"

    warnings: list[str] = []
    used_literal_env_names: set[str] = set()
    env_var_names: dict[str, str] = {}  # env_name -> local python variable name
    check_lines: list[str] = []

    def _value_var(key: str, value: str, purpose: str) -> str:
        match = _AUTH_ENV_REF_RE.fullmatch(value) if isinstance(value, str) else None
        if match:
            env_name = match.group(1)
        else:
            env_name = _derive_literal_env_name(key, used_literal_env_names)
            warnings.append(
                f'{purpose}的 "{key}" 是明碼字面值，不會寫入 conftest.py —— '
                f"執行測試前請自行 `export {env_name}=<實際的 token 值>`"
            )
        if env_name not in env_var_names:
            # 流水號而非 env_name.lower()：$FOO 與 $foo 是兩個不同的環境
            # 變數，lower() 會讓它們共用同一個 Python 區域變數，後者的
            # 賦值與 pytest.fail 檢查會把前者整個蓋掉。
            var_name = f"_auth_env_{len(env_var_names)}"
            env_var_names[env_name] = var_name
            check_lines.append(f'    {var_name} = os.environ.get("{env_name}")')
            check_lines.append(f"    if {var_name} is None:")
            check_lines.append(
                "        pytest.fail("
                f'"auto_generate_tests 產出的 auth 鷹架需要環境變數 {env_name}，'
                f'請先 `export {env_name}=<實際的值>` 再跑測試")'
            )
        return env_var_names[env_name]

    storage_lines: list[str] = []
    for key, value in (auth_storage or {}).items():
        if not isinstance(value, str):
            continue
        var_name = _value_var(key, value, "auth_storage")
        storage_lines.append(f'                    {{"name": {key!r}, "value": {var_name}}},')

    cookie_lines: list[str] = []
    for part in (auth_cookie or "").split(";"):
        if "=" not in part:
            continue
        name, _, value = part.strip().partition("=")
        if not name:
            continue
        var_name = _value_var(name, value, "auth_cookie")
        cookie_lines.append(
            f'            {{"name": {name!r}, "value": {var_name}, '
            f'"domain": {hostname!r}, "path": "/"}},'
        )

    lines = [
        _AUTH_CONFTEST_MARKER + "（手改此檔請刪除本行，否則下次產出會整份覆寫）",
        '"""Auto-generated by auto_generate_tests —— session auth 鷹架。',
        "",
        "組成 Playwright storage_state（localStorage + cookies）並透過",
        "browser_context_args session fixture 注入，讓這個專案底下所有測試",
        "一開始就是已登入的 context（不然 behind-login 站產出的測試不登入",
        "全部會紅）。",
        "",
        "Token 絕不落檔：以下只引用環境變數名稱，真正的值在 fixture 執行時",
        "才從環境變數讀出；缺少對應環境變數時會直接 pytest.fail 並提示要",
        "export 哪一個（哪些變數是明碼衍生、要不要自己設，見",
        "auto_generate_tests 回傳的 conftest_warnings）。",
        '"""',
        "import os",
        "",
        "import pytest",
        "",
        "",
        '@pytest.fixture(scope="session")',
        "def browser_context_args(browser_context_args):",
        *check_lines,
        f"    origin = {origin!r}",
        "    storage_state = {",
        '        "origins": [',
        "            {",
        '                "origin": origin,',
        '                "localStorage": [',
        *(storage_lines or ["                    # (無 auth_storage)"]),
        "                ],",
        "            },",
        "        ],",
        '        "cookies": [',
        *cookie_lines,
        "        ],",
        "    }",
        '    return {**browser_context_args, "storage_state": storage_state}',
        "",
    ]
    return "\n".join(lines), warnings


def _write_auth_conftest(
    url: str, auth_storage: dict[str, str] | None, auth_cookie: str | None,
) -> dict:
    """Write `<QA_PROJECT_ROOT>/conftest.py` with the auth scaffold from
    `_build_auth_conftest`.

    Never clobbers a hand-written conftest — but a conftest *we* wrote on
    an earlier `auto_generate_tests` run (identified by
    `_AUTH_CONFTEST_MARKER` as its first line) is fair game to regenerate
    (review round 3 #10), since auth_storage/auth_cookie may have changed
    between runs. Returns the `conftest`/`conftest_warnings` keys to merge
    into `_auto_generate_tests`'s result dict.
    """
    conftest_path = PROJECT_ROOT / "conftest.py"
    if conftest_path.exists():
        try:
            existing_first_line = conftest_path.read_text(encoding="utf-8").splitlines()[:1]
        except (OSError, UnicodeDecodeError):
            existing_first_line = []
        if not existing_first_line or not existing_first_line[0].startswith(_AUTH_CONFTEST_MARKER):
            return {"conftest": "skipped (exists)"}
    content, warnings = _build_auth_conftest(url, auth_storage, auth_cookie)
    if content is None:
        result: dict = {"conftest": "skipped (invalid url: no hostname)"}
        if warnings:
            result["conftest_warnings"] = warnings
        return result
    conftest_path.parent.mkdir(parents=True, exist_ok=True)
    conftest_path.write_text(content, encoding="utf-8")
    result = {"conftest": "written"}
    if warnings:
        result["conftest_warnings"] = warnings
    return result


async def _auto_generate_tests(
    url: str,
    timeout_ms: int,
    auth_cookie: str | None,
    tests_per_module: int,
    auth_storage: dict[str, str] | None = None,
) -> dict:
    """analyze_url → per module → generate_test × N. All-in-one orchestration.

    Why inline in server.py: the chain is short and stays close to where the
    individual tools are already wired. Telemetry hooks mirror the manual path
    so the optimizer still sees the same discovery + generation signals.
    """
    analysis = await analyzer.analyze_url(
        url, timeout_ms=timeout_ms, auth_cookie=auth_cookie, auth_storage=auth_storage,
    )
    if isinstance(analysis, dict) and "error" in analysis:
        return analysis
    if isinstance(analysis, dict):
        telemetry.log_discovered_modules(url, analysis.get("modules", []) or [])

    endpoints = (analysis.get("api_endpoints") or []) if isinstance(analysis, dict) else []
    modules_list = analysis.get("modules", []) or []
    page_tables = _collect_page_tables(modules_list)
    generated: list[dict] = []
    used_filenames: set[str] = set()
    for module in modules_list:
        module_api = _pick_form_api(url, module, endpoints)
        # Copy rather than mutate `module` in place — `page_tables` is an
        # auto_generate_tests-only enrichment (the implicit-form renderer's
        # commented-out cross-module hint), not part of analyze_url's own
        # module shape.
        module_for_gen = dict(module)
        if module_api:
            module_for_gen["api"] = module_api
        if page_tables:
            module_for_gen["page_tables"] = page_tables
        candidates = module.get("candidate_tcs", []) or []
        module_name = module.get("name", "module")
        module_kind = module.get("kind") or "module"
        for i, tc in enumerate(_select_candidate_tcs(candidates, tests_per_module)):
            slug = f"{module_name}_{i}" if i > 0 else module_name
            # 碰撞處理：不同 module（例如 dialog 跟 cta）可能 slug 成同一個
            # 名字（真實站回歸：兩個都叫「確認登出」）。用本輪已用檔名 set
            # 擋掉無聲覆寫 —— 見 _resolve_test_filename 的 docstring。
            file_out = _resolve_test_filename(slug, module_kind, used_filenames)
            try:
                msg = generator.generate_test(
                    description=tc,
                    filename=file_out,
                    url=url,
                    module=module_for_gen,
                )
                # `generator.generate_test` doesn't always raise on failure —
                # an invalid filename (security.validate_filename rejecting
                # it) comes back as a plain `"error: ..."` string instead
                # (review round 3 #11). Without this check that string was
                # silently counted as a successful generation.
                if isinstance(msg, str) and msg.startswith("error:"):
                    generated.append({
                        "filename": file_out,
                        "module_name": module_name,
                        "error": msg,
                    })
                    continue
                generated.append({
                    "filename": file_out,
                    "description": tc,
                    "module_kind": module.get("kind"),
                    "module_name": module_name,
                })
                telemetry.log_generation(
                    file_out, tc, source=f"auto_generate_tests:{module_name}",
                )
            except Exception as e:
                generated.append({
                    "filename": file_out,
                    "module_name": module_name,
                    "error": f"{type(e).__name__}: {e}",
                })

    tests_generated = sum(1 for g in generated if "error" not in g)
    result = {
        "url": url,
        "page_title": analysis.get("page_title"),
        "module_count": analysis.get("module_count"),
        "api_endpoint_count": analysis.get("api_endpoint_count"),
        "tests_generated": tests_generated,
        "tests_failed": sum(1 for g in generated if "error" in g),
        "tests": generated,
    }
    # auth 鷹架只在真的有東西可以用它的時候才產出 —— 沒有 auth_storage，或
    # 一筆測試都沒成功產出時都跳過（避免留一個指向空測試集的空殼 conftest）。
    if tests_generated >= 1:
        if auth_storage:
            result.update(_write_auth_conftest(url, auth_storage, auth_cookie))
        elif auth_cookie:
            # localStorage 是 conftest 鷹架唯一能可靠注入的東西（cookies 只
            # 是附帶項目）——只帶 auth_cookie、沒有 auth_storage 時，明確
            # 告知使用者鷹架沒有產出，而不是默默什麼都不做（review round 3
            # #7）。
            result["conftest"] = "skipped (auth_storage required)"
    return result


async def main():
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()
