import ast
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import zipfile
from datetime import datetime
from pathlib import Path

from ..config import ARTIFACTS_DIR, HISTORY_DIR, JUNIT_PATH, PROJECT_ROOT, REPORT_PATH
from ..security import safe_run
from .base import TestRunner


def _parse_docstrings(file_path: Path) -> dict[str, str]:
    """Read a test .py file and return {func_name: docstring} for every
    function whose docstring is present (parsed via ast — no import / side
    effects). Returns {} on missing file or syntax error.
    """
    try:
        tree = ast.parse(file_path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return {}
    out: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            doc = ast.get_docstring(node)
            if doc:
                out[node.name] = doc.strip()
    return out


# Trace events whose class.method we want to surface as user-visible "steps".
# Real trace.trace (Playwright 1.59+) uses `class` + `method` fields with
# Capitalized class names — not the older `apiName` string. Internal events
# (tracing.*, etc.) get filtered out by simply not matching this pattern.
_STEP_KEEP_PATTERN = re.compile(r"^(Frame|Page|Locator|ElementHandle|BrowserContext|Keyboard|Mouse)\.")


# Detect once at module load — pytest-rerunfailures lets us auto-retry transient
# failures so the optimizer's flake signal is grounded in repeat-confirmed fails.
_HAS_RERUNFAILURES = importlib.util.find_spec("pytest_rerunfailures") is not None

# MCP server 行程的 PATH 通常不含 venv bin（Phase 5 實測：裸 `pytest` 直接
# FileNotFoundError）。改用當前直譯器 -m pytest，保證跑在同一個環境。
_PYTEST_CMD = [sys.executable, "-m", "pytest"]


# Optional layout-integrity check — appended to generated tests as a
# commented hint. Catches 跑版（text overflow / hard-px width / container
# escape）at the current viewport. Commented because sites with
# intentional horizontal scrollers would always fail it; user opts in.
_OVERFLOW_HINT = (
    "    # Optional layout sanity (uncomment to catch 跑版: text/element 溢出 container):\n"
    "    # overflow = page.evaluate(\"\"\"() => [...document.querySelectorAll('body *')]\n"
    "    #   .filter(e => {\n"
    "    #     const cs = getComputedStyle(e);\n"
    "    #     if (['auto','scroll'].includes(cs.overflowX) || ['auto','scroll'].includes(cs.overflowY)) return false;\n"
    "    #     return e.scrollWidth > e.clientWidth + 2 || e.scrollHeight > e.clientHeight + 2;\n"
    "    #   }).length\"\"\")\n"
    "    # expect(overflow).to_equal(0)\n"
)


TEST_TEMPLATE = '''from playwright.sync_api import Page, expect


def test_{slug}(page: Page):
    """{description}"""
    # TODO: 由 Claude 補完實作
    page.goto("https://example.com")
    expect(page).to_have_title("Example Domain")
'''


# Sample values keyed by input type — used when smart-generating from an
# analyze_url form module so the resulting test is runnable without manual fills.
_SAMPLE_VALUES = {
    "email": "test@example.com",
    "password": "TestPass123!",
    "tel": "0912345678",
    "phone": "0912345678",
    "url": "https://example.com",
    "number": "1",
    "search": "test query",
    "text": "test value",
    "textarea": "Sample input",
    "date": "2026-01-01",
}

# Field `type`s that are never fillable inputs — the analyzer sometimes mis-
# classifies a submit/reset button as a regular field, which used to render a
# `.fill()` call against a button and crash on run. Filtered defensively here
# regardless of analyzer correctness (POC F-1 defect #1).
_NON_FILLABLE_FIELD_TYPES = frozenset({"button", "submit", "reset", "hidden", "image"})

# Field `type`s Playwright's `.fill()` actually accepts (text-like inputs +
# textarea). Deliberately excludes "select"/"checkbox"/"radio" — those throw
# ("Input of type checkbox cannot be filled") and need `select_option()` /
# `check()` instead (`_render_implicit_form_test`, review N3).
_FILLABLE_TEXTLIKE_TYPES = frozenset({
    "text", "search", "email", "number", "tel", "url", "date", "password", "textarea",
})

# Multi-character phrases (bilingual) that mark a description as the
# "submit while all required fields are empty" scenario. Deliberately whole
# phrases, not bare "空"/"blank" — those single tokens false-positive on
# unrelated descriptions like "清空購物車" or "Blank page check" (review
# round 2, minor #7).
_EMPTY_SUBMIT_KEYWORDS = re.compile(
    r"(為空|留空|未填|空白送出|empty submit|without filling|leave blank)",
    re.IGNORECASE,
)

# The analyzer's fixed TC template for "leave exactly one required field
# empty, fill the rest" (tools/analyzer.py:308 — `f"只填其他欄位、{label}
# 留空，應顯示該欄位必填錯誤"`). Parsed here (not in the analyzer) because
# only the runner knows how to turn "which field" into "which fill to skip".
_SINGLE_FIELD_EMPTY_PATTERN = re.compile(r"只填其他欄位、(.+?)\s*留空")

# A selector that's just a bare HTML tag name ("table", "button", "dialog"...)
# with no id/class/attribute/pseudo qualifier. analyze_url's `sel()` helper
# falls back to the tag name alone when it can't find a stable selector
# (no id/data-testid/name/aria-label) — on a real page that tag is almost
# never unique (gwp-admin /users had 2 <table>s and 10+ <button>s), so
# `page.locator(sel)` trips Playwright's strict-mode violation. Anything
# with an id/class/attribute/pseudo-class marker (`#`, `.`, `[`, `:`) is
# assumed qualified enough to not need the `.first` fallback.
_BARE_TAG_SELECTOR_RE = re.compile(r"^[a-z][a-z0-9-]*$")

# Bilingual "this description reads as a happy-path / success scenario"
# phrases. Kept deliberately narrow: an assertion that asserts success only
# gets attached when the description is unambiguously positive — anything
# ambiguous or negative-sounding falls back to the TODO stub instead of
# guessing (review round 2, major #1: "寧缺勿錯").
_POSITIVE_KEYWORDS = re.compile(
    r"(全部填入合法值|全部填寫正確|填寫正確|填入正確|觸發成功|成功流程|成功訊息|"
    r"happy path|happy-path)",
    re.IGNORECASE,
)

# Any of these override a positive-keyword match — a TC can mention "成功"
# while still being a negative case, e.g. "應顯示錯誤" text sitting right
# next to it in the analyzer's own template strings never happens, but user-
# authored descriptions might mix both. Negative wins on conflict.
_NEGATIVE_KEYWORDS = re.compile(
    r"(錯誤|失敗|不合法|不符合|太短|過長|invalid|error|fail(?:ure|ed)?|reject)",
    re.IGNORECASE,
)


def _is_empty_submit_description(description: str | None) -> bool:
    """True when `description` reads as an empty-submit / missing-required-
    field scenario rather than the happy-path fill flow."""
    return bool(_EMPTY_SUBMIT_KEYWORDS.search(description or ""))


def _extract_single_empty_label(description: str | None) -> str | None:
    """Pull `{label}` out of the analyzer's "只填其他欄位、{label} 留空" TC
    template. Returns None when the description doesn't match that exact
    shape (POC review round 2, major #2)."""
    if not description:
        return None
    m = _SINGLE_FIELD_EMPTY_PATTERN.search(description)
    if not m:
        return None
    return m.group(1).strip() or None


def _is_positive_description(description: str | None) -> bool:
    """True only when `description` unambiguously reads as a happy-path /
    success scenario. Negative keywords always win over positive ones, and
    anything that matches neither is treated as *not* positive — a missing
    assertion beats one that asserts the wrong thing (POC review round 2,
    major #1)."""
    text = description or ""
    if _NEGATIVE_KEYWORDS.search(text):
        return False
    return bool(_POSITIVE_KEYWORDS.search(text))


def _is_bare_tag_selector(selector: object) -> bool:
    """True when `selector` is nothing but a bare HTML tag name — no
    `#id`, `.class`, `[attr]` or `:pseudo` qualifier. See
    `_BARE_TAG_SELECTOR_RE` for why that matters."""
    if not isinstance(selector, str) or not selector:
        return False
    return bool(_BARE_TAG_SELECTOR_RE.fullmatch(selector))


def _selector_is_non_unique(selector: str, metadata: dict | None) -> bool:
    """Decide whether `selector` is guaranteed unique on the page.

    Priority order:
      1. `metadata["selector_unique"]` — explicit flag from the analyzer
         (currently only `table` modules set it, from the DOM probe's
         native/aria-level uniqueness check). When present it wins over
         the heuristic below, in both directions.
      2. Bare-tag heuristic (`_is_bare_tag_selector`) — used for every
         other module kind (section/nav/generic/cta/dialog), which have
         no such metadata.
    """
    md = metadata if isinstance(metadata, dict) else {}
    if "selector_unique" in md:
        return not md["selector_unique"]
    return _is_bare_tag_selector(selector)


def _container_locator_expr(selector: str | None, metadata: dict | None) -> tuple[str, str]:
    """Build the `page.locator(...)` expression for a module's container
    selector, degrading to `.first` when `_selector_is_non_unique` says
    it isn't guaranteed unique on the page.

    Returns (locator_expr, comment_line). `comment_line` is `""` when no
    degradation happened; otherwise a single `    # ...\n` line explaining
    the degradation, meant to be emitted just above the `target = ...`
    assignment.
    """
    if not selector:
        return "page.locator('body')", ""
    base = f"page.locator({selector!r})"
    if not _selector_is_non_unique(selector, metadata):
        return base, ""
    comment = (
        f"    # selector {selector!r} 在頁面上可能不唯一（bare tag 或 analyzer "
        "標記 selector_unique=False），改用 .first 並建議補 data-testid 取得穩定唯一選擇器\n"
    )
    return f"{base}.first", comment


def _sanitize_docstring_text(text: str) -> str:
    """Make `text` safe to interpolate into a `\"\"\"...\"\"\"` module
    docstring literal. `text` comes from analyzer/user input, not a trusted
    constant, so a stray `\"\"\"` or backslash could otherwise break out of
    the string literal and corrupt the generated file (review round 2,
    minor #8)."""
    return text.replace("\\", "/").replace('"""', "'''")


class PytestPlaywrightRunner(TestRunner):
    name = "pytest-playwright"
    generation_context_fields = frozenset({"url", "module", "business_context"})

    def list_tests(self) -> str:
        result = safe_run([*_PYTEST_CMD, "--collect-only", "-q"], cwd=PROJECT_ROOT)
        return result.stdout or result.stderr

    def _base_cmd(self, browser: str) -> list[str]:
        cmd = [
            *_PYTEST_CMD,
            f"--browser={browser}",
            # always-on: reporter surfaces pass-state screenshots + step lists,
            # not just failures. Video stays retain-on-failure (heavy + only
            # useful for debugging breaks); tracing has to be on for step
            # extraction even on passes.
            "--screenshot=on",
            "--video=retain-on-failure",
            "--tracing=on",
            f"--output={ARTIFACTS_DIR}",
            "--json-report",
            f"--json-report-file={REPORT_PATH}",
            f"--junitxml={JUNIT_PATH}",
        ]
        if _HAS_RERUNFAILURES:
            cmd += ["--reruns", "1", "--reruns-delay", "0"]
        return cmd

    def run_tests(self, filter=None, **kwargs) -> dict:
        cmd = self._base_cmd(kwargs.get("browser", "chromium"))
        if kwargs.get("headed"):
            cmd.append("--headed")
        if filter:
            cmd.extend(["-k", filter])

        result = safe_run(cmd, cwd=PROJECT_ROOT)
        self._archive_report()
        return {
            "exit_code": result.returncode,
            "stdout_tail": result.stdout[-2000:],
            "stderr_tail": result.stderr[-1000:],
            "retry_enabled": _HAS_RERUNFAILURES,
        }

    def run_failed(self) -> dict:
        cmd = self._base_cmd("chromium") + ["--lf"]
        result = safe_run(cmd, cwd=PROJECT_ROOT)
        self._archive_report()
        return {
            "exit_code": result.returncode,
            "stdout_tail": result.stdout[-2000:],
            "retry_enabled": _HAS_RERUNFAILURES,
        }

    def get_report_summary(self) -> dict:
        if not REPORT_PATH.exists():
            return {"error": "找不到報告，請先執行 run_tests"}
        data = json.loads(REPORT_PATH.read_text())
        summary = data.get("summary", {})
        # Count tests that needed a retry to pass — flake-in-this-run signal.
        # pytest-rerunfailures emits two records for the same nodeid: first a
        # "rerun" outcome, then the final outcome.
        seen_rerun: set[str] = set()
        flaky_in_run = 0
        for t in data.get("tests", []) or []:
            nodeid = t.get("nodeid")
            if t.get("outcome") == "rerun" and nodeid:
                seen_rerun.add(nodeid)
            elif t.get("outcome") == "passed" and nodeid in seen_rerun:
                flaky_in_run += 1
        return {
            "total": summary.get("total", 0),
            "passed": summary.get("passed", 0),
            "failed": summary.get("failed", 0),
            "skipped": summary.get("skipped", 0),
            "flaky_in_run": flaky_in_run,
            "duration": data.get("duration"),
        }

    def get_failure_details(self, test_id=None) -> list[dict]:
        if not REPORT_PATH.exists():
            return [{"error": "找不到報告"}]
        data = json.loads(REPORT_PATH.read_text())
        failures = [t for t in data.get("tests", []) if t.get("outcome") == "failed"]
        if test_id:
            failures = [t for t in failures if test_id in t.get("nodeid", "")]
        return [
            {
                "nodeid": t["nodeid"],
                "message": t.get("call", {}).get("longrepr", ""),
                "duration": t.get("call", {}).get("duration"),
                **self._find_artifacts(t["nodeid"]),
            }
            for t in failures
        ]

    def _find_artifacts(self, nodeid: str) -> dict:
        """Best-effort lookup for failure artifacts (screenshot/trace/video).

        Why: playwright-pytest saves artifacts into a per-test folder whose name
        is a sanitized version of the nodeid + browser. Match by function name
        token after `::` to stay resilient to its internal slug rules.
        """
        out: dict[str, str | None] = {"screenshot": None, "trace": None, "video": None}
        if not ARTIFACTS_DIR.exists():
            return out
        test_func = nodeid.split("::")[-1]
        # \w preserves underscores; playwright-pytest sanitizes those to dashes
        # too. Excluding underscores from the kept set keeps our token aligned
        # with the on-disk folder name (e.g. test_a_b → test-a-b).
        token = re.sub(r"[^a-z0-9]+", "-", test_func.lower()).strip("-")
        if not token:
            return out
        for folder in ARTIFACTS_DIR.iterdir():
            if not folder.is_dir() or folder.name == "history":
                continue
            if token not in folder.name.lower():
                continue
            for png in sorted(folder.glob("*.png")):
                out["screenshot"] = str(png)
                break
            for tz in sorted(folder.glob("trace*.zip")):
                out["trace"] = str(tz)
                break
            for v in sorted(folder.glob("*.webm")):
                out["video"] = str(v)
                break
            break
        return out

    def _archive_report(self) -> None:
        """Snapshot report.json + write a fresh optimization-plan.md.

        Why: the optimizer's whole value is being up-to-date right after the run
        finishes. Hooking here means callers don't have to remember an extra step.
        Lazy import avoids a circular dependency at module-load time.
        """
        if not REPORT_PATH.exists():
            return
        try:
            HISTORY_DIR.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
            shutil.copy2(REPORT_PATH, HISTORY_DIR / f"{stamp}.json")
        except OSError:
            pass
        try:
            from ..tools import optimizer
            optimizer.write_plan()
        except Exception:
            # Optimizer failures should never block test running.
            pass

    def get_all_test_details(self) -> list[dict]:
        """Per-test details (outcome / duration / artifacts / steps / title).

        `title` comes from the test function's docstring — that's the most
        readable "case name" we have. Falls back to None when no docstring is
        present, and the reporter then shows the nodeid alone.

        The reporter uses this to render pass + fail sections in one pass.
        `rerun` records are skipped — pytest-rerunfailures emits them as a
        pre-marker before the final outcome and we don't want them shown as
        their own test.
        """
        if not REPORT_PATH.exists():
            return []
        try:
            data = json.loads(REPORT_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return []
        # Cache docstring extraction per file: typical suite has many tests in
        # the same .py, so re-parsing each time would scale poorly.
        docstring_cache: dict[Path, dict[str, str]] = {}
        results: list[dict] = []
        for t in data.get("tests", []) or []:
            nodeid = t.get("nodeid")
            outcome = t.get("outcome")
            if not nodeid or outcome == "rerun":
                continue
            artifacts = self._find_artifacts(nodeid)
            results.append({
                "nodeid": nodeid,
                "title": self._docstring_for(nodeid, docstring_cache),
                "outcome": outcome,
                "duration": (t.get("call") or {}).get("duration"),
                "message": (t.get("call") or {}).get("longrepr", "") if outcome == "failed" else "",
                "steps": self._extract_steps(artifacts.get("trace")),
                **artifacts,
            })
        return results

    def _docstring_for(self, nodeid: str, cache: dict[Path, dict[str, str]]) -> str | None:
        """Look up the test function's docstring as the human-readable case name.

        nodeid forms handled:
          - tests/x.py::test_y
          - tests/x.py::TestSuite::test_y       (class-based)
          - tests/x.py::test_y[param-id]        (parametrize)
        We walk all FunctionDef/AsyncFunctionDef in the file (cheap, cached)
        and key by bare function name. Parametrize IDs and class scoping
        share the same source function, so collapsing both to the function
        name is the right move.
        """
        file_part, _, suffix = nodeid.partition("::")
        if not file_part or not suffix:
            return None
        func_name = suffix.split("::")[-1].split("[")[0]
        file_path = PROJECT_ROOT / file_part
        if file_path not in cache:
            cache[file_path] = _parse_docstrings(file_path)
        return cache[file_path].get(func_name)

    def _extract_steps(self, trace_zip_path: str | None) -> list[dict]:
        """Parse trace.zip → list of {api, title} user-facing actions.

        Real trace.trace (Playwright 1.59+) emits one `before` event per call
        with `class` + `method` fields (no more `apiName`). We only take
        `before` events — `after` events repeat the callId with timing/error
        data we don't currently surface. callId dedup is no longer strictly
        needed but kept as a safety net for older trace formats.
        """
        if not trace_zip_path:
            return []
        p = Path(trace_zip_path)
        if not p.is_file():
            return []
        seen_ids: set[str] = set()
        steps: list[dict] = []
        try:
            with zipfile.ZipFile(p) as z:
                target = next(
                    (n for n in z.namelist() if n.endswith("trace.trace")),
                    None,
                )
                if not target:
                    return []
                with z.open(target) as f:
                    for raw in f:
                        try:
                            ev = json.loads(raw.decode("utf-8", "replace"))
                        except json.JSONDecodeError:
                            continue
                        if ev.get("type") != "before":
                            continue
                        cls = ev.get("class") or ""
                        method = ev.get("method") or ""
                        if not cls or not method:
                            continue
                        api = f"{cls}.{method}"
                        if not _STEP_KEEP_PATTERN.match(api):
                            continue
                        call_id = ev.get("callId") or ""
                        if call_id and call_id in seen_ids:
                            continue
                        if call_id:
                            seen_ids.add(call_id)
                        steps.append({
                            "api": api,
                            "title": ev.get("title") or api,
                        })
        except (zipfile.BadZipFile, OSError):
            return []
        return steps

    def get_history(self, limit: int = 10) -> list[dict]:
        if not HISTORY_DIR.exists():
            return []
        files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)[:limit]
        history: list[dict] = []
        for f in reversed(files):  # oldest first for natural trend reading
            try:
                data = json.loads(f.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            summary = data.get("summary", {}) or {}
            total = summary.get("total", 0) or 0
            passed = summary.get("passed", 0) or 0
            history.append({
                "file": f.name,
                "timestamp": f.stem,
                "total": total,
                "passed": passed,
                "failed": summary.get("failed", 0) or 0,
                "skipped": summary.get("skipped", 0) or 0,
                "duration": data.get("duration"),
                "pass_rate": (passed / total * 100) if total else 0,
            })
        return history

    def generate_test(
        self,
        description: str,
        filename: str,
        url: str | None = None,
        module: dict | None = None,
        business_context: str | None = None,
    ) -> str:
        if not filename.startswith("test_"):
            filename = f"test_{filename}"
        if not filename.endswith(".py"):
            filename += ".py"
        slug = filename.replace("test_", "").replace(".py", "")

        # If caller hands us a module from analyze_url, render a runnable
        # skeleton with concrete selectors instead of a `# TODO` stub.
        if isinstance(module, dict) and module.get("kind") == "form":
            md = module.get("metadata")
            if isinstance(md, dict) and md.get("implicit"):
                # `analyzer._build_modules`'s implicit_form_0: fields that
                # live outside any <form> (search/filter bars). There's no
                # submit button and no real validation semantics — fill
                # one field, press Enter. Reusing `_render_form_test`
                # would fill every field and look for a submit button
                # that doesn't exist (review: "implicit form 的 render
                # 語意對不上").
                content = self._render_implicit_form_test(description, slug, url, module, business_context)
            else:
                content = self._render_form_test(description, slug, url, module, business_context)
        elif isinstance(module, dict):
            content = self._render_generic_module_test(description, slug, url, module, business_context)
        else:
            content = self._render_basic_test(description, slug, business_context)

        target = PROJECT_ROOT / filename
        target.write_text(content)
        return f"已產生 {target}，內容：\n\n{content}"

    def _business_context_block(self, business_context: str | None) -> str:
        """Indented `# Business context:` comment block for inside a test fn.

        Why a comment vs a docstring: the docstring slot is already the case
        name (single-line summary). Business context is supplementary
        detail — putting it as comments keeps it visible in the source but
        out of the way of automated docstring tooling / report headers.
        """
        if not business_context or not str(business_context).strip():
            return ""
        lines = ["    # Business context:"]
        for raw in str(business_context).strip().splitlines():
            stripped = raw.rstrip()
            lines.append(f"    # {stripped}" if stripped else "    #")
        return "\n".join(lines) + "\n"

    def _render_basic_test(self, description: str, slug: str, business_context: str | None) -> str:
        bc = self._business_context_block(business_context)
        return (
            "from playwright.sync_api import Page, expect\n\n\n"
            f"def test_{slug}(page: Page):\n"
            f"    {description!r}\n"
            f"{bc}"
            "    # TODO: 由 Claude 補完實作\n"
            '    page.goto("https://example.com")\n'
            '    expect(page).to_have_title("Example Domain")\n'
            f"{_OVERFLOW_HINT}"
        )

    def _render_form_test(self, description: str, slug: str, url: str | None, module: dict, business_context: str | None = None) -> str:
        sel = module.get("selectors") or {}
        raw_fields = sel.get("fields") or []
        submit = sel.get("submit")
        # F-1 缺陷 1：analyzer 有時把送出鈕誤放進 fields，導致模板對它呼叫
        # .fill() 直接炸掉。這裡防禦性過濾掉 button 類 type 以及跟 submit
        # 選擇器重複的項目。
        fields = [
            f for f in raw_fields
            if f.get("selector")
            and (f.get("type") or "").lower() not in _NON_FILLABLE_FIELD_TYPES
            and f.get("selector") != submit
        ]

        # --- 判斷這個 description 要渲染哪一種變體 ------------------------
        # F-1 缺陷 2 + review round 2 major #2：analyzer 除了「全部留空」，
        # 還有「只填其他欄位、{label} 留空」這種單一欄位留空的 TC 模板
        # （tools/analyzer.py:308）。後者之前被通用關鍵字（「留空」）誤判成
        # 整份表單留空，這裡先做更精確的比對：解析出 {label}，只跳過對應
        # 的那個欄位；label 對不到任何已知欄位時才 fallback 為全部留空，
        # 並在產出裡註明是 fallback（不是 TC 本意）。
        single_empty_label = _extract_single_empty_label(description)
        skip_selector: str | None = None
        fallback_note: str | None = None
        if single_empty_label is not None:
            target = next(
                (
                    f for f in fields
                    if (f.get("label") or "").strip() == single_empty_label
                    or f.get("selector") == single_empty_label
                ),
                None,
            )
            if target is not None:
                variant = "single-field-empty"
                skip_selector = target["selector"]
            else:
                variant = "empty-submit"
                fallback_note = single_empty_label
        elif _is_empty_submit_description(description):
            variant = "empty-submit"
        else:
            variant = "happy-path"

        # empty-submit / single-field-empty 都是「預期失敗」的負向情境：
        # 不該附上成功斷言，送出後只留錯誤提示的 TODO。
        negative_variant = variant in ("empty-submit", "single-field-empty")

        if variant == "empty-submit":
            fills_body = "    # empty-submit variant：刻意跳過所有 fill，驗證必填提示"
            if fallback_note:
                fills_body += (
                    f"\n    # 注意：TC 提及的欄位「{fallback_note}」未對應到任何已知欄位，"
                    "fallback 為全部留空"
                )
        else:
            fill_lines: list[str] = []
            for f in fields:
                s = f["selector"]
                if variant == "single-field-empty" and s == skip_selector:
                    fill_lines.append(f"    # 依 TC 指示留空：{single_empty_label}")
                    continue
                kind = (f.get("type") or "").lower()
                if kind == "select":
                    fill_lines.append(f"    page.locator({s!r}).select_option(index=1)")
                elif kind in ("checkbox", "radio"):
                    fill_lines.append(f"    page.locator({s!r}).check()")
                else:
                    value = _SAMPLE_VALUES.get(kind, "test value")
                    fill_lines.append(f"    page.locator({s!r}).fill({value!r})")
            fills_body = "\n".join(fill_lines) if fill_lines else "    # No fillable fields detected"

        # --- submit + 斷言 -------------------------------------------------
        # F-1 缺陷 3 + review round 2 major #1 / minor #6：happy-path 且帶
        # module["api"] 時，改產出真實斷言——但只在 (a) 非負向變體、
        # (b) description 明確讀作正向／成功情境、(c) url_substring 夠具體
        # （不是幾乎恆真的 "/"）三者都成立時才附加，避免對「Email 格式錯誤
        # 應顯示錯誤」這類負向 TC 誤掛「status < 400」成功斷言。
        api = module.get("api") if isinstance(module.get("api"), dict) else None
        url_substring = str(api.get("url_substring") or "") if api else ""
        can_assert_api = (
            not negative_variant
            and api is not None
            and url_substring not in ("", "/")
            and _is_positive_description(description)
        )

        if not submit:
            submit_body = "    # No submit button detected"
        elif negative_variant:
            submit_body = f"    page.locator({submit!r}).click()"
        elif can_assert_api:
            method = str(api.get("method") or "POST").upper()
            # json.dumps(..., ensure_ascii=False) gives a double-quoted,
            # escaped literal matching the approved design's example
            # verbatim, without mangling non-ASCII path segments.
            url_substring_literal = json.dumps(url_substring, ensure_ascii=False)
            submit_body = (
                "    with page.expect_response(\n"
                f"        lambda r: r.request.method == {method!r} and {url_substring_literal} in r.url\n"
                "    ) as _resp:\n"
                f"        page.locator({submit!r}).click()\n"
                "    assert _resp.value.status < 400"
            )
        else:
            submit_body = f"    page.locator({submit!r}).click()"

        tcs = module.get("candidate_tcs") or []
        tc_block = "\n".join(f"    # TC: {tc}" for tc in tcs[:3])
        goto_url = url or "https://example.com"
        # description goes on the *function* docstring so the HTML reporter
        # picks it up as the case name. Module docstring keeps just the
        # auto-gen trace for grep-ability, plus the variant this render
        # actually produced — that's what kills the description/body mismatch.
        bc = self._business_context_block(business_context)

        if negative_variant:
            assertion_block = (
                '    # TODO: 斷言錯誤提示，例如 expect(page.get_by_text("必填")).to_be_visible()\n'
            )
        elif can_assert_api:
            # 斷言已經內嵌在 submit_body 的 expect_response 區塊裡。
            assertion_block = ""
        else:
            assertion_block = (
                "    # TODO: 補上實際斷言，例如：\n"
                "    # expect(page).to_have_url(...)\n"
                '    # expect(page.get_by_text("成功")).to_be_visible()\n'
            )

        module_label = _sanitize_docstring_text(str(module.get("name", "(unnamed)")))
        return (
            f'"""Auto-generated from analyze_url module: {module_label} '
            f'(kind=form, variant={variant})"""\n'
            "from playwright.sync_api import Page, expect\n\n\n"
            f"def test_{slug}(page: Page):\n"
            f"    {description!r}\n"
            f"{bc}"
            f"    page.goto({goto_url!r})\n"
            f"{fills_body}\n"
            f"{submit_body}\n"
            + (f"{tc_block}\n" if tc_block else "")
            + assertion_block
            + _OVERFLOW_HINT
        )

    def _render_implicit_form_test(self, description: str, slug: str, url: str | None, module: dict, business_context: str | None = None) -> str:
        """Renders `analyzer._build_modules`'s `implicit_form_0` module —
        fields that live outside any `<form>` (bare search/filter inputs
        next to a data table). There's no submit button and the fields
        were never validated as a single unit, so this intentionally
        does NOT reuse `_render_form_test`'s fill-every-field +
        click-submit flow: it fills one field (preferring a text/search
        type — that's what a filter bar actually is, falling back to any
        other text-like `.fill()`-safe type) and presses Enter, which is
        how an implicit "submission" actually happens here.

        Review-flagged crash (N3): a naive "fall back to fields[0]" would
        hand a checkbox/select field to `.fill()`, which Playwright
        rejects outright ("Input of type checkbox cannot be filled").
        When there's no fillable text-like field at all, we instead
        render the one interaction that *is* valid for that field type
        (`select_option` / `check()`) and skip the Enter press — pressing
        Enter after toggling a checkbox or picking a dropdown option
        isn't "submitting a search", it's a different (and unverified)
        action.
        """
        sel = module.get("selectors") or {}
        raw_fields = sel.get("fields") or []
        fields = [
            f for f in raw_fields
            if f.get("selector") and (f.get("type") or "").lower() not in _NON_FILLABLE_FIELD_TYPES
        ]

        def _kind(f: dict) -> str:
            return (f.get("type") or "").lower()

        target = next((f for f in fields if _kind(f) in ("text", "search")), None)
        if target is None:
            target = next((f for f in fields if _kind(f) in _FILLABLE_TEXTLIKE_TYPES), None)

        is_clear_variant = "清空" in (description or "")
        if target is not None:
            target_selector = target["selector"]
            value = "" if is_clear_variant else _SAMPLE_VALUES.get(_kind(target), "test value")
            action_body = (
                f"    page.locator({target_selector!r}).fill({value!r})\n"
                f"    page.locator({target_selector!r}).press(\"Enter\")"
            )
        else:
            select_field = next((f for f in fields if _kind(f) == "select"), None)
            checkbox_field = next((f for f in fields if _kind(f) in ("checkbox", "radio")), None)
            if select_field is not None:
                action_body = f"    page.locator({select_field['selector']!r}).select_option(index=1)"
            elif checkbox_field is not None:
                action_body = f"    page.locator({checkbox_field['selector']!r}).check()"
            else:
                action_body = "    # No fillable fields detected"

        tcs = module.get("candidate_tcs") or []
        tc_block = "\n".join(f"    # TC: {tc}" for tc in tcs[:3])
        goto_url = url or "https://example.com"
        bc = self._business_context_block(business_context)
        module_label = _sanitize_docstring_text(str(module.get("name", "(unnamed)")))
        return (
            f'"""Auto-generated from analyze_url module: {module_label} '
            '(kind=form, implicit=True)"""\n'
            "from playwright.sync_api import Page, expect\n\n\n"
            f"def test_{slug}(page: Page):\n"
            f"    {description!r}\n"
            f"{bc}"
            f"    page.goto({goto_url!r})\n"
            f"{action_body}\n"
            + (f"{tc_block}\n" if tc_block else "")
            + "    # TODO: 補上實際斷言，例如：\n"
              "    # expect(page.locator(...)).to_have_count(...)\n"
            + _OVERFLOW_HINT
        )

    def _render_generic_module_test(self, description: str, slug: str, url: str | None, module: dict, business_context: str | None = None) -> str:
        kind = module.get("kind", "unknown")
        sel = module.get("selectors") or {}
        metadata = module.get("metadata") if isinstance(module.get("metadata"), dict) else {}

        if kind == "cta":
            body = self._render_cta_body(sel, metadata)
        elif kind == "dialog":
            body = self._render_dialog_body(sel, metadata)
        else:
            # table / section / nav / unrecognized kinds: plain container
            # visibility check, degrading to `.first` when the container
            # selector isn't guaranteed unique (see `_container_locator_expr`).
            target_sel = sel.get("container") or sel.get("trigger") or "body"
            locator_expr, comment = _container_locator_expr(target_sel, metadata)
            body = (
                f"{comment}"
                f"    target = {locator_expr}\n"
                "    expect(target).to_be_visible()\n"
            )

        tcs = module.get("candidate_tcs") or []
        tc_block = "\n".join(f"    # TC: {tc}" for tc in tcs[:3])
        goto_url = url or "https://example.com"
        bc = self._business_context_block(business_context)
        module_label = _sanitize_docstring_text(str(module.get("name", "(unnamed)")))
        return (
            f'"""Auto-generated from analyze_url module: {module_label} (kind={kind})"""\n'
            "from playwright.sync_api import Page, expect\n\n\n"
            f"def test_{slug}(page: Page):\n"
            f"    {description!r}\n"
            f"{bc}"
            f"    page.goto({goto_url!r})\n"
            f"{body}"
            + (f"{tc_block}\n" if tc_block else "")
            + "    # TODO: 補上實際互動與斷言\n"
            + _OVERFLOW_HINT
        )

    def _render_cta_body(self, sel: dict, metadata: dict) -> str:
        """`kind: "cta"` module: prefer a text-based locator over the raw
        selector, but ONLY when the raw selector isn't already guaranteed
        unique (review round 3 #4 — a stable `#id`/`[data-testid]`/
        `aria-label` selector must be kept as-is: its accessible name may
        come from `aria-label`, not innerText, so forcing
        `get_by_role(name=label_text)` there can turn a passing test
        red). analyze_url's cta selector is frequently a bare `button` tag
        (any page with >1 button trips strict-mode on it) — the
        button/link's own accessible name is far more likely to be unique
        in that case.

        - `metadata.label_text` present + selector non-unique:
            - `tag == "a"` → `get_by_role("link", name=...)`.
            - `tag` is any other non-empty string (`"button"`, or a
              `[role="button"]` `<div>`/`<span>`/... — analyze_url's cta
              query only ever matches `button`, `[role="button"]`,
              `a.button` or `a.btn`, so any non-`"a"` tag here really is
              button-ish) → `get_by_role("button", name=...)` (review
              round 3 #3 — matching by role instead of a CSS
              `:has-text()` pseudo-class means a `[role=button]` `<div>`
              can't accidentally match an ancestor the way `div:has-text`
              used to).
            - `tag` missing entirely (e.g. mobile cta modules from
              `analyze_screen`, which never set `tag`) → don't guess a
              role; `page.locator(sel).filter(has_text=label_text)`
              instead. This is a plain Python string argument, not a CSS
              string literal, so there's nothing to escape — a label
              ending in a backslash or containing a newline (review
              round 3 #2's repro) can't produce a broken selector the way
              the old CSS `:has-text("...")` string-building did.
        - No `label_text`, or the selector is already unique → fall back
          to the same bare-tag/.first degradation as every other module
          kind (and the ORIGINAL selector, untouched, when it's unique —
          review round 3 #4: a stable `#id`/`[data-testid]`/`aria-label`
          selector must never be swapped out for a text locator, since
          the accessible name used for matching may come from
          `aria-label` rather than innerText).
        """
        trigger_sel = sel.get("trigger") or sel.get("container") or "body"
        label_text = metadata.get("label_text")
        has_label = isinstance(label_text, str) and bool(label_text)
        if has_label and _selector_is_non_unique(trigger_sel, metadata):
            tag = metadata.get("tag")
            if tag == "a":
                target_expr = f'page.get_by_role("link", name={label_text!r}).first'
            elif isinstance(tag, str) and tag:
                target_expr = f'page.get_by_role("button", name={label_text!r}).first'
            else:
                target_expr = f"page.locator({trigger_sel!r}).filter(has_text={label_text!r}).first"
            return f"    target = {target_expr}\n    expect(target).to_be_visible()\n"

        locator_expr, comment = _container_locator_expr(trigger_sel, metadata)
        return (
            f"{comment}"
            f"    target = {locator_expr}\n"
            "    expect(target).to_be_visible()\n"
        )

    def _render_dialog_body(self, sel: dict, metadata: dict) -> str:
        """`kind: "dialog"` module: `metadata.open_on_load` decides which
        assertion is even honest to make.

        - `open_on_load` True: the dialog is visible as soon as the page
          loads, so the existing `to_be_visible()` assertion still holds —
          selector degradation (bare-tag → `.first`) applies as usual.
        - `open_on_load` False (the common case — a confirm/logout dialog
          that only opens after a trigger click): asserting visible would
          always fail since nothing in this generated test ever opens it.
          Render an existence check first (`to_be_attached()` — a bare
          `to_be_hidden()` alone passes even on ZERO matches, which is an
          empty assertion that proves nothing; review round 3 #5) and
          THEN assert hidden, plus a TODO — the real "does it open
          correctly" coverage needs a trigger step this generator has no
          way to infer. Note for React-portal-style modals: they may not
          be in the DOM at all until opened, but analyzer's DOM probe did
          see this element when it built the module, so `to_be_attached()`
          holds at generation time.
        """
        container_sel = sel.get("container") or "body"
        locator_expr, comment = _container_locator_expr(container_sel, metadata)
        if metadata.get("open_on_load"):
            return (
                f"{comment}"
                f"    target = {locator_expr}\n"
                "    expect(target).to_be_visible()\n"
            )
        # `locator_expr` may already end in `.first` (non-unique selector,
        # see `_container_locator_expr`) — don't chain a redundant second
        # `.first` onto it (review round 3 #5).
        first_ref = "target" if locator_expr.endswith(".first") else "target.first"
        return (
            f"{comment}"
            f"    target = {locator_expr}\n"
            "    # dialog 預設不開啟（open_on_load=False）——先確認元素真的存在於\n"
            "    # DOM（React portal 類 modal 可能完全不在 DOM 裡，但 analyzer 掃描\n"
            "    # 當下有看到這個元素，attached 斷言在產生當下大致成立），再驗證\n"
            "    # 預設是隱藏的；需先觸發開啟才能驗證可見狀態，TODO 補上觸發開啟的步驟\n"
            f"    expect({first_ref}).to_be_attached()\n"
            f"    expect({first_ref}).to_be_hidden()\n"
        )

    def codegen(self, url: str, output: str = "recorded_test.py") -> str:
        target = PROJECT_ROOT / output
        subprocess.run(
            ["playwright", "codegen", "-o", str(target), url],
            cwd=PROJECT_ROOT,
        )
        return f"錄製完成，已存至 {target}"
