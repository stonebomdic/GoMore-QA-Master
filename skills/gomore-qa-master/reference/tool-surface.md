# gomore-qa-master — Tool Surface Cheatsheet

The 19 MCP tools currently exposed by gomore-qa-master, grouped by flow.
One-liner + the input-schema gotchas you actually need to remember.

---

## Plan & Verify (prelude to every other flow)

| Tool | Purpose | Gotchas |
|---|---|---|
| `qa_plan` | Store a critical-points checklist BEFORE acting | `critical_points` accepts list[str] OR list[dict{id?, description, verification_hint?, assert?}]; 30-min TTL; LRU-bounded at 50. **v0.9.3**: also writes to `<QA_PROJECT_ROOT>/test-results/plans/<plan_id>.json` when persistence is on (default ON when QA_PROJECT_ROOT is set; force with `QA_PLAN_PERSIST=true|false`). Response includes `persisted_to` field. **v0.9.6**: a CP `assert` block makes it verified (artifact-backed); top-level `strict` requires all CPs be verified. |
| `verify_plan` | Walk plan CPs against evidence; return per-CP pass/fail + overall status | Attested CPs match by case-insensitive substring on `verification_hint`; status='passed' only when ALL CPs satisfied — partial = 'incomplete', zero = 'failed'. **v0.9.2**: pass `auto_discover: true` to pull evidence from `<QA_PROJECT_ROOT>/report.json` automatically; combine with explicit `evidence` to merge sources. Response includes `evidence_sources` audit trail. **v0.9.3**: transparently loads plans from disk when memory cache misses; response includes `plan_source: "memory" \| "disk"`. **v0.9.6**: CPs with an `assert` block are **verified** — the tool loads the authoritative artifact itself and IGNORES host evidence (can't be faked; a failed test can't satisfy `test_passed`). Missing artifact = fail-closed. `strict` (plan-level or per-call, tighten-only) requires all CPs verified+satisfied for 'passed'. Each entry gets `tier`; response gains `verification{...}`. |

Use them as bookends around Flows 1-5. Skip for one-shot reads.

---

## Core (always available)

| Tool | Purpose | Gotchas |
|---|---|---|
| `get_runner_info` | Which runner is active + all available ones | None |
| `list_tests` | Enumerate tests in the project | Output can be large for >200-test suites; consider filtering |
| `run_tests` | Run tests | `filter` is a keyword; `headed=True` only for debugging; `browser` ∈ pytest-playwright targets |
| `run_failed` | Re-run last failures (pytest `--lf`) | Only meaningful after a prior failing run |
| `get_test_report` | Read latest `report.json` | Path: `test-results/report.json` |
| `get_failure_details` | Per-test failure breakdown | Pass exact `test_name` from `list_tests` output |
| `get_test_history` | Historical test runs from `test-results/history/` | `limit` parameter caps how many runs to look back |

## Generation

| Tool | Purpose | Gotchas |
|---|---|---|
| `analyze_url` | Discover modules + candidate TCs from a web URL | SPA-heavy sites need `timeout_ms=30000+`; behind-login needs `auth_cookie`, or `auth_storage` for localStorage-token SPAs (`auth_storage` values support `$ENV_NAME` indirection so tokens stay out of logs — **`auth_cookie` does not**: at this stage it's parsed and injected into the browser as a literal string; `$ENV_NAME` indirection for cookies is only expanded one layer up, by `auto_generate_tests`'s conftest scaffold, not here). **v0.9.9**: data-table admin pages (e.g. Tailwind back offices) now surface `kind: "table"` modules via three-tier detection — native `<table>` > ARIA `role=grid/table/treegrid` > a heuristic "≥4 repeated sibling elements" fallback (`metadata.detection`) — plus a `kind: "form"` `implicit_form_0` module aggregating inputs that sit outside any `<form>` (e.g. a bare search box next to a table) |
| `analyze_screen` | Same but for mobile (Maestro hierarchy) | Requires Maestro CLI + a booted device |
| `generate_test` | Generate ONE pytest test from a description + module | `filename` should be a slug, no `.py` |
| `auto_generate_tests` | Chain `analyze_url` → `generate_test` × N | `tests_per_module` defaults to 1 — anything above 3 produces noise. **v0.9.10**: generated selectors now degrade to `.first` (+ a comment) when a container selector isn't guaranteed unique — bare tags like `table`/`button`/`dialog`, or a `table` module whose `metadata.selector_unique` is `False` — closing the Playwright strict-mode-violation failures a real admin-page scan hit (table/button selectors matching 2–10+ elements). `cta` modules prefer a role-based locator over the raw selector, but **only when the selector itself isn't already unique** — a stable `#id`/`[data-testid]`/`aria-label` selector is always kept as-is, since its accessible name may come from `aria-label` rather than innerText and `get_by_role(name=innerText)` would then find nothing. When it does switch: `tag == "a"` → `get_by_role("link", name=...)`; any other known tag (`button`, or a `[role=button]` div/span/...) → `get_by_role("button", name=...)`; tag missing entirely (mobile `cta` modules from `analyze_screen` never set it) → `page.locator(sel).filter(has_text=...)` — a plain Python string argument, never a CSS `:has-text()` string literal, so a label ending in a backslash or containing a newline can't produce a broken selector. `dialog` modules: `open_on_load=False` no longer renders a guaranteed-red `to_be_visible()`; it asserts `to_be_attached()` (so a 0-match selector still fails loudly) then `to_be_hidden()`, with a TODO to add the real trigger step. Filename collisions across modules (e.g. a `dialog` and a `cta` both slugging to the same name) now resolve to `{slug}_{kind}` instead of silently overwriting the earlier file. A `generate_test` call that comes back as an `"error: ..."` string (e.g. a rejected filename) is no longer silently counted as a successful generation — it lands in `tests_failed`, not `tests_generated`. When called with `auth_storage` and at least one test is generated, it also writes `<QA_PROJECT_ROOT>/conftest.py` with a session `browser_context_args` fixture that seeds Playwright's `storage_state` from env vars, read via `os.environ.get(...)` + an explicit `pytest.fail(...)` at fixture time if the var is unset: `$ENV_NAME` values in `auth_storage`/`auth_cookie` read that same env var name; literal values are never written to disk — conftest instead reads a derived, collision-safe `{KEY}_TOKEN` env var and the response's `conftest_warnings` tells you to export it. The file's origin is built from the URL's `scheme://hostname[:port]` only — any `user:pass@` userinfo in the URL is stripped, never written to disk. Calling with only `auth_cookie` (no `auth_storage`) returns `conftest: "skipped (auth_storage required)"` rather than silently doing nothing; a URL with no parseable hostname returns `conftest: "skipped (invalid url: no hostname)"`. A conftest.py we generated ourselves (marked with a leading `# auto-generated by gomore-qa-master auto_generate_tests` comment) is regenerated on a later call if auth values changed; a hand-written one without that marker is never overwritten (`conftest: "skipped (exists)"`). Injected cookies' `domain` is the exact `urlparse(url).hostname` — no port, and (unlike a leading-dot cookie domain) it will not also apply to subdomains, so a token scoped to `app.example.com` won't be sent on requests to `admin.example.com` even though both point at the same conftest. |
| `codegen` | Playwright codegen-style scaffolding | Only meaningful on pytest-playwright |

## Reporting

| Tool | Purpose | Gotchas |
|---|---|---|
| `generate_html_report` | Self-contained dark-mode HTML report | Writes to `test-results/report.html` |
| `get_optimization_plan` | Suite + MCP + AI strategy improvement plan | Reads `test-results/optimization-plan.md`; only meaningful after several runs |

## QA Knowledge Layer

| Tool | Purpose | Gotchas |
|---|---|---|
| `init_qa_knowledge` | Scaffold project's QA knowledge directory | One-shot setup |
| `get_qa_context` | Read methodology + domain knowledge | `section` filter narrows; bilingual (`QA_LANG=en` or `zh-tw`) |

## OWASP API Security Scanner

| Tool | Purpose | Gotchas |
|---|---|---|
| `run_api_security_scan` | Scan an OpenAPI 3.x spec for 5 OWASP API Top 10 issues | Needs `QA_API_SECURITY_CONSENT=true` + `AUTHORIZED_DOMAINS`; `mass_assignment` opt-in; default 4 of 5 categories. **v0.9.4**: pass `plan_id` from `qa_plan` to auto-verify findings against CPs in one shot — response gains `plan_verification` block. Only findings ABOVE `severity_threshold` are seen by verify; lower threshold if a CP targets low-severity findings. **v0.9.6**: writes a redacted `scan-results.json` (path via `GOMORE_QA_SCAN_PATH` → `<QA_PROJECT_ROOT>/scan-results.json`) BEFORE plan verification, so verified `finding_present`/`finding_absent` CPs load ground truth from it — the bookend is now artifact-backed, not host-evidence. |

---

## Environment variables you might surface to the user

| Variable | Required for | Default |
|---|---|---|
| `QA_RUNNER` | Picking a non-pytest backend | `pytest` |
| `QA_PROJECT_ROOT` | Where tests live | CWD |
| `QA_LANG` | QA knowledge bilingual selection | `en` |
| `QA_API_SECURITY_CONSENT` | v0.8 scanner | unset (refuses to run) |
| `QA_API_SECURITY_AUTHORIZED_DOMAINS` | v0.8 external hosts | unset (only localhost allowed) |

---

## Tool naming convention

- The 19 names above are the **canonical** ones the host's MCP client
  sees. Never invent variants.

---

## When the host doesn't surface a tool

If your host's MCP wiring drops a tool (e.g. truncated tool list in
some clients), you can fall back to the CLI:

```bash
# Direct function invocation (the tools package has no CLI entrypoint)
python -c "from gomore_qa_master.tools.runner import list_tests; print(list_tests())"
```

But MCP-first is always preferred when the host supports it — the
schemas + error envelopes are richer.
