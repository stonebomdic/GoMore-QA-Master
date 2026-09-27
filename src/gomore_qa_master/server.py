import asyncio
import json
import time
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from urllib.parse import urlparse

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Resource, TextContent, Tool
from pydantic import AnyUrl

from .config import OPTIMIZATION_PATH, REPORT_PATH
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
    origin = urlparse(url).hostname
    for ep in endpoints:
        if ep.get("method") not in ("POST", "PUT") or ep.get("host") != origin:
            continue
        path = ep.get("path") or ""
        if any(hint in path for hint in _ANALYTICS_PATH_HINTS):
            continue
        return {"method": ep["method"], "url_substring": path or ep.get("url")}
    return None


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
    generated: list[dict] = []
    for module in (analysis.get("modules", []) or []):
        module_api = _pick_form_api(url, module, endpoints)
        module_for_gen = {**module, "api": module_api} if module_api else module
        candidates = module.get("candidate_tcs", []) or []
        module_name = module.get("name", "module")
        for i, tc in enumerate(_select_candidate_tcs(candidates, tests_per_module)):
            slug = f"{module_name}_{i}" if i > 0 else module_name
            try:
                generator.generate_test(
                    description=tc,
                    filename=slug,
                    url=url,
                    module=module_for_gen,
                )
                file_out = f"test_{slug}.py"
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
                    "filename": f"test_{slug}.py",
                    "module_name": module_name,
                    "error": f"{type(e).__name__}: {e}",
                })

    return {
        "url": url,
        "page_title": analysis.get("page_title"),
        "module_count": analysis.get("module_count"),
        "api_endpoint_count": analysis.get("api_endpoint_count"),
        "tests_generated": sum(1 for g in generated if "error" not in g),
        "tests_failed": sum(1 for g in generated if "error" in g),
        "tests": generated,
    }


async def main():
    async with stdio_server() as (read, write):
        await app.run(read, write, app.create_initialization_options())


def run():
    asyncio.run(main())


if __name__ == "__main__":
    run()
