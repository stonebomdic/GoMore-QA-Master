# Walkthrough — Native API testing

gomore-qa-master ships two native API runners as of v0.6.1:

- **Track 1 — Schemathesis** (since v0.6.0): point at an OpenAPI 3.x
  schema and get property-based fuzz coverage of every operation.
- **Track 2 — Newman** (since v0.6.1): point at a Postman 2.x collection
  and replay every request with its `pm.test(...)` assertions.

Pick whichever matches how your team already documents the API. The two
tracks share the same MCP tool surface (`run_tests`, `get_failure_details`,
`get_optimization_plan`, etc.) and the same `report.json` / history /
optimizer pipeline — only `QA_RUNNER` and the source-of-truth env var
change between them.

This document covers Track 1 first (Schemathesis), then Track 2 (Newman).

---

## Track 1 — Schemathesis (OpenAPI-driven)

This walkthrough shows the end-to-end loop for testing an OpenAPI-defined
API with gomore-qa-master v0.6.0 using the bundled 3-endpoint sample. By the
end you'll have run property-based fuzz tests, read a failure with the
exact request + response captured, fixed the bug, and seen `run_failed`
return zero failures — all from a single AI client session.

The chain has no new MCP tools — the existing MCP surface drives the
schemathesis runner transparently. The only thing that changes is the
`QA_RUNNER` env var. (v0.7.0 brought the tool count to 18 by adding
the AI Visual Challenge Solver — see
[`docs/walkthrough-visual-challenge.md`](walkthrough-visual-challenge.md).)

---

## Prerequisites

```bash
pip install 'gomore-qa-master[api]'
```

The `[api]` extra pulls in `schemathesis>=3.0,<4`. The base install
stays slim — users who never run API tests don't pay for the dependency.

You don't need Playwright, Maestro, or any other runner installed for
this walkthrough. Schemathesis is a Python package with a CLI; it talks
HTTP, not browsers.

## The sample API

`examples/sample_api_project/openapi.yaml` defines a fictional Library
API with three operations:

| Method + path | Purpose |
|---|---|
| `GET /books` | List books (`limit` query param, 1..100) |
| `POST /books` | Add a book (`{title, author, published_year?}`) |
| `GET /books/{id}` | Fetch one book by id |

Two response schemas (`Book`, `Error`) constrain the shape Schemathesis
will check against. No external `$ref`s, no auth — keeps the demo
hermetic.

## Client config

Drop this into your `claude_desktop_config.json` (or the equivalent for
Cursor / Codex / Gemini CLI):

```jsonc
{
  "mcpServers": {
    "gomore-qa-master": {
      "command": "uvx",
      "args": ["gomore-qa-master"],
      "env": {
        "QA_RUNNER": "schemathesis",
        "QA_OPENAPI_URL": "file:///absolute/path/to/examples/sample_api_project/openapi.yaml",
        "QA_SCHEMATHESIS_DRY_RUN": "1"
      }
    }
  }
}
```

`QA_SCHEMATHESIS_DRY_RUN=1` makes Schemathesis plan operations without
issuing real HTTP — perfect for a dry-walkthrough against a schema-only
artifact. Drop the var (or set it to `0`) and point at a running server
to do a real fuzz pass.

> **Heads up — destructive requests**: by default Schemathesis will
> issue real `POST` / `DELETE` calls against whatever URL is in the
> schema's `servers[0].url`. Either point at a staging URL, use
> `QA_SCHEMATHESIS_DRY_RUN=1`, or stand up a local mock (Prism, mockoon)
> before pointing at a production schema.

## Session transcript

What follows is a transcript of how the AI client orchestrates the
tools. The MCP tool calls are explicit so you can map them onto your own
client; in practice the user just types natural language.

### 1. `get_runner_info` — confirm the runner is wired

> **You**: Which runner is gomore-qa-master using right now?

```json
{
  "current": "schemathesis",
  "available": [
    "pytest", "pytest-playwright", "playwright",
    "jest", "cypress", "go", "go-test",
    "maestro", "mobile",
    "schemathesis", "api"
  ]
}
```

### 2. `list_tests` — enumerate operations from the schema

> **You**: List the API operations.

```text
GET /books
POST /books
GET /books/{id}
```

The runner shells out to `schemathesis run --dry-run --no-color <url>`,
parses the operation lines, and caps output at 200 lines for large
schemas. Three operations, one per row.

### 3. `run_tests` — fuzz the API

> **You**: Run the tests.

(With `QA_SCHEMATHESIS_DRY_RUN=1` against the bundled sample, the
runner exercises schema parsing and operation enumeration without
issuing real HTTP. Set it to `0` and point at a running server for a
full fuzz pass.)

Expected output against a real backend with one bug:

```json
{
  "exit_code": 1,
  "raw_exit_code": 1,
  "openapi_url": "file:///.../openapi.yaml",
  "stdout_tail": "... POST /books FAILED response_schema_conformance ... "
}
```

`get_test_report` then summarizes:

```json
{
  "total": 9,
  "passed": 7,
  "failed": 2,
  "skipped": 0,
  "flaky_in_run": 0,
  "duration": 14.3
}
```

(9 = three operations × three default checks. Exact count depends on
which checks pass through `QA_SCHEMATHESIS_CHECKS`.)

### 4. `get_optimization_plan` — prioritize the fix

> **You**: What should I fix next?

```markdown
### 1. 🔴 HIGH — broken
- **Target**: POST /books :: response_schema_conformance
- **Evidence**: 3 consecutive runs, identical Schemathesis signature
  ("Response status 500 not in {201, 400}")
- **Suggestion**: response schema doesn't allow 500; either harden the
  validation path or add 500 to the responses block (probably not what
  you want).

### 2. 🟡 MEDIUM — broken
- **Target**: GET /books/{id} :: status_code_conformance
- **Evidence**: returns 204 No Content; schema says 200 or 404
- **Suggestion**: align response code with schema; verify intent with PM
```

### 5. `get_failure_details` — see the actual request + response

> **You**: Show me the POST /books failure.

```json
{
  "nodeid": "POST /books :: response_schema_conformance",
  "message": "Response did not conform to schema: status 500, expected 201|400",
  "duration": 0.18,
  "artifacts": {
    "request_response": {
      "method": "POST",
      "url": "http://localhost:4010/books",
      "request_body": "{\"title\": \"\\u0000\", \"author\": \"x\"}",
      "response_status": 500,
      "response_body": "Internal Server Error",
      "violation": "response_schema_conformance"
    }
  }
}
```

Schemathesis found that a null byte in `title` crashes the validator.
The OpenAPI schema constrains `title` to `minLength: 1, maxLength: 200`
but allows any string — including `"\u0000"`. The bug is upstream of
the schema check.

> **Secret note**: `Authorization`, `password`, `token`, `api_key`,
> `secret`, `access_token`, and `refresh_token` values are redacted to
> `[REDACTED]` in archived reports by default. Set `QA_NO_REDACT=1`
> only for short debug sessions.

### 6. User fixes the bug in their IDE

A normal fix: strip control characters from input or 400 on invalid
unicode. The user makes the change, restarts the API (or relies on a
mock with the fix applied), and re-runs only the previously-failing
operations.

### 7. `run_failed` — verify the fix without re-running passes

> **You**: Re-run just the failures.

```json
{
  "exit_code": 0,
  "raw_exit_code": 0,
  "ops_rerun": 2,
  "stdout_tail": "... 2 passed in 3.1s"
}
```

The runner reads the previous `report.json`, extracts failed
`(method, path)` pairs, and re-invokes Schemathesis with
`--include-method` and `--include-path` filters — scoping the second
run to exactly the operations that needed verification.

`get_test_report` confirms:

```json
{ "total": 2, "passed": 2, "failed": 0, "duration": 3.1 }
```

Zero failures, two operations re-verified.

---

## What just happened

In a single AI session, the user:

1. Validated the API's behavior against its own OpenAPI contract — 7+
   property-based test cases per operation, autogenerated.
2. Got two real bugs identified with the exact request body that
   triggered each, the actual response, and the schema clause that was
   violated.
3. Got the bugs ranked (broken vs flaky vs warn) so the order of fixes
   is data-driven, not gut-feel.
4. Iterated to green by re-running only the operations that needed it.

No tests were authored by hand. The schema is the source of truth; the
runner fuzzes against it.

## Where this fits in the family pipeline

```
mk-plan-master.generate_spec_draft   → Markdown spec
mk-spec-master.parse_spec            → extracted scenarios + acceptance criteria
[user writes the API + OpenAPI schema in their IDE]
gomore-qa-master (QA_RUNNER=schemathesis) → run_tests → coverage
```

This is the first chain where the family's "code in your IDE" boundary
is on the API side, not the UI side. The OpenAPI schema acts as the
contract between the spec layer and the test layer — no manual test
scaffolding, no boilerplate.

## Knobs worth knowing

| Env | Default | When to change |
|---|---|---|
| `QA_SCHEMATHESIS_MAX_EXAMPLES` | `20` | Bump to `100`+ for nightly / pre-release fuzz; keep low for PR-time. |
| `QA_SCHEMATHESIS_CHECKS` | `all` | Restrict to a subset when isolating a single class of bug (e.g. only `response_schema_conformance` to ignore status-code drift while the API is still settling). |
| `QA_SCHEMATHESIS_AUTH` | — | Set to your bearer token / API key when the API requires auth. Format: `"Bearer xxx"` or whatever the API expects after `Authorization: `. |
| `QA_SCHEMATHESIS_DRY_RUN` | `0` | `1` for plan-without-HTTP; useful when pointing at production for a safety preview. |
| `QA_NO_REDACT` | `0` | `1` only when debugging redaction itself — archived reports may be shared. |
| `QA_TIMEOUT_SECONDS` | `600` | Bump for very large schemas (200+ endpoints × deep fuzz). |

## Where to go from here

- **Try it on your own API**: point `QA_OPENAPI_URL` at an OpenAPI URL
  you already have. Most modern frameworks (FastAPI, NestJS, ASP.NET
  Core, Spring Boot, Go-Swagger) ship one out of the box at
  `/openapi.json` or `/swagger.json`.
- **Pair with a mock**: spin up `npx @stoplight/prism-cli mock openapi.yaml`
  for a self-contained dev loop (see
  `examples/sample_api_project/README.md`).
- **Cross-runner workflows**: API tests live in the same `report.json`
  / history archive as UI / mobile tests. The optimizer ranks them
  side-by-side. A single `get_optimization_plan` call surfaces the
  weakest link across all three layers.

---

## Track 2 — Newman (Postman collections)

If your team's source of truth is a hand-curated Postman collection
rather than an OpenAPI schema, the Newman runner replays the collection
end-to-end and runs every embedded `pm.test(...)` assertion. Same MCP
tool surface, same `report.json` shape — just a different runner key
and a different source artifact.

### Prerequisites

Newman ships via **npm**, not pip:

```bash
npm install -g newman
```

There's no `gomore-qa-master[postman]` extra to install. The runner shells
out to the `newman` binary on PATH; if it's missing, you'll get a clear
`ImportError` pointing at the install line.

### The bundled Postman sample

`examples/sample_api_project/postman-collection.json` defines the same
fictional Library API as the OpenAPI sample, organized into a single
`Books` folder with three requests:

| Method + path | Assertions (via `pm.test(...)`) |
|---|---|
| `GET /books` | 200 status · response is an array |
| `POST /books` | 201 status · response has `id` (cached for next request) |
| `GET /books/{id}` | 200 status · response `id` matches the one captured above |

A `{{baseUrl}}` collection variable (default `http://localhost:4010`)
lets you point at a Prism mock running the bundled OpenAPI schema, or
at any real backend, without editing the file.

### Client config

```jsonc
{
  "mcpServers": {
    "gomore-qa-master": {
      "command": "uvx",
      "args": ["gomore-qa-master"],
      "env": {
        "QA_RUNNER": "newman",
        "QA_POSTMAN_COLLECTION": "/absolute/path/to/examples/sample_api_project/postman-collection.json"
      }
    }
  }
}
```

Unlike `QA_OPENAPI_URL`, `QA_POSTMAN_COLLECTION` accepts a **plain
filesystem path** — no `file://` prefix. Postman collections are always
local artifacts, so the scheme-disambiguation argument the OpenAPI case
makes doesn't apply here.

### Session transcript

The MCP tool calls stay the same — only the runner-side semantics change.

**1. `get_runner_info`**

```json
{
  "current": "newman",
  "available": [
    "pytest", "pytest-playwright", "playwright",
    "jest", "cypress", "go", "go-test",
    "maestro", "mobile",
    "schemathesis", "api",
    "newman", "postman"
  ]
}
```

**2. `list_tests`**

The runner parses the collection JSON locally (no subprocess) and emits
one line per request, including the folder breadcrumb:

```text
GET {{baseUrl}}/books?limit=20 :: Books :: List books
POST {{baseUrl}}/books :: Books :: Create book
GET {{baseUrl}}/books/{{bookId}} :: Books :: Get book by id
```

**3. `run_tests`**

Newman replays each request and runs the embedded `pm.test(...)` calls.
The JSON report Newman emits gets translated into gomore-qa-master's
`report.json` shape: **one gomore-qa-master "test" per pm.test assertion**.
Three requests × 2 assertions each = 6 nodeids.

Against a Prism mock that conforms to the schema, all 6 pass. Against a
real backend with a bug, you might see:

```json
{
  "total": 6,
  "passed": 4,
  "failed": 2,
  "skipped": 0,
  "duration": 1.4
}
```

**4. `get_failure_details`**

```json
{
  "nodeid": "POST Create book :: POST /books response has id",
  "message": "expected undefined to have property 'id'",
  "duration": 0.18,
  "artifacts": {
    "request_response": {
      "method": "POST",
      "url": "http://staging.example.com/books",
      "request_body": "{\"title\": \"The Pragmatic Programmer\", \"author\": \"Andrew Hunt\"}",
      "response_status": 201,
      "response_body": "{\"title\": \"The Pragmatic Programmer\"}",
      "violation": "POST /books response has id",
      "parent_folder": "Books"
    }
  }
}
```

The runner captured the exact request body that triggered the failure,
the actual response (status 201 but missing the `id` field the schema
expects), and the assertion message verbatim — same artifact shape as
the Schemathesis runner, so downstream tools (HTML reporter, optimizer)
treat it identically.

**5. `run_failed`**

The runner reads the previous `report.json`, extracts the
`parent_folder` from each failed nodeid's artifacts, and re-runs Newman
scoped to those folders via `--folder` flags. If the collection has no
folder structure (everything at the root), `run_failed` degrades to a
full re-run — which is still cheap on small collections.

### Knobs worth knowing

| Env | Default | When to change |
|---|---|---|
| `QA_POSTMAN_ENVIRONMENT` | — | Point at a Postman environment file with `baseUrl` / credentials. Lets you keep the collection itself environment-agnostic. |
| `QA_POSTMAN_GLOBALS` | — | Same shape as environment, globally scoped. Rarely needed in solo workflows. |
| `QA_POSTMAN_ITERATIONS` | `1` | Soak / flake detection — replay the collection 10× / 100× and see which assertions flap. The optimizer's flake-score logic picks it up automatically. |
| `QA_POSTMAN_FOLDER` | — | CSV of folder names. Useful for "only run the auth flow folder" on a large collection. |
| `QA_POSTMAN_TIMEOUT_REQUEST_MS` | `30000` | Tighten for fast local mocks (250–1000ms catches hung endpoints quickly). |
| `QA_NO_REDACT` | `0` | Same redaction policy as Schemathesis. Default redacts `Authorization`, `password`, `token`, `api_key`, `secret`, `access_token`, `refresh_token`. |

### When to choose Newman vs Schemathesis

Both runners target the same outcome (verified API behavior), but the
artifact each consumes differs:

| Pick Newman when | Pick Schemathesis when |
|---|---|
| You already maintain a Postman collection | You already maintain an OpenAPI schema |
| You want concrete, hand-authored assertions per request | You want property-based fuzz coverage of every operation |
| Your team flows use `pm.environment.set(...)` chaining between requests | The schema is the source of truth and you trust it |
| You're testing a specific user flow (login → cart → checkout) | You're testing a public REST contract for breakage under fuzz |

Nothing stops you from running both side-by-side — `QA_RUNNER` is just
an env var, and the report archive is shared. A nightly CI run could
fire Schemathesis (broad coverage), and a per-PR run could fire Newman
(targeted flows). Both feed the same optimizer.

---

## Track 3 — Verified security findings (`run_api_security_scan` + `verify_plan`)

The two tracks above test *functional* behavior. The OWASP API scanner
(`run_api_security_scan`, since v0.8) tests *security* behavior — and as
of v0.9.6 its findings can be gated by **verified** `qa_plan` critical
points. This is the pattern to reach for when "this endpoint must not
leak another user's data" is an acceptance criterion, not a hope.

Unlike Tracks 1–2 this is not a `QA_RUNNER` — it's a distinct tool,
gated on `QA_API_SECURITY_CONSENT=true` (and `AUTHORIZED_DOMAINS` for
non-localhost targets). See [`docs/prd-v0.8-api-security.md`](prd-v0.8-api-security.md)
and `examples/sample_vulnerable_api/` for the scanner's own rules and
fixture; this section covers only the `finding_*` bookend.

### Why verified beats "the scan looked clean"

A `verify_plan` critical point with a `verification_hint` is **attested**:
it matches on a case-insensitive substring of whatever evidence you hand
it — which means you (or the AI) can satisfy it by *saying* the right
words. A CP with an `assert` block is **verified**: `verify_plan` loads
`scan-results.json` itself and judges the typed assertion against ground
truth, **ignoring any evidence passed for that CP**. A vulnerability that
actually exists cannot be waved away, and — critically — a *missing*
scan artifact fails the CP **closed** ("no evidence" is never "no
vulnerability", so `finding_absent` is not satisfied either).

### The two assertion types for scans

| `assert.type` | Satisfied when | Use for |
|---|---|---|
| `finding_present` | scan has ≥1 finding matching `rule_id` (+ optional `endpoint`) | proving a known-vulnerable fixture *is* flagged (regression on the scanner, or a red-team demo) |
| `finding_absent` | scan has **zero** matching findings **and** the artifact exists | the real gate: "this endpoint has no BOLA / broken-auth / etc." |

Matching rules (identical to the `test_*` types' philosophy — see
`SKILL.md`):

- `rule_id` matches the **rule-class id by prefix**. You write the class
  (`OWASP-API1-BOLA`); it covers every sub-finding the scanner emits
  under it (`OWASP-API1-BOLA-CrossUserDataExposure`,
  `…-CrossUserDataExposure`, …). You don't have to know the sub-ids.
- `endpoint` (optional) is the finding's exact `"METHOD /path"` string,
  e.g. `GET /vuln/orders/{order_id}` — note the space and the
  path-template braces. Omit it to match the rule *anywhere* in the scan.

### Session transcript

Assume the bundled `examples/sample_vulnerable_api` is running on
`http://127.0.0.1:5099` (a deliberately-broken Flask app — `/vuln/*`
endpoints leak data, `/safe/*` endpoints don't).

**1. `qa_plan` — declare the security contract before scanning**

> **You**: Before I scan, lock in that the safe orders endpoint must be
> clean and that we still catch the known BOLA on the vulnerable one.

```jsonc
qa_plan(task="orders BOLA gate", strict=true, critical_points=[
  {"id":"CP1","description":"safe orders endpoint has no BOLA",
   "assert":{"type":"finding_absent","rule_id":"OWASP-API1-BOLA",
             "endpoint":"GET /safe/me/orders"}},
  {"id":"CP2","description":"scanner still flags the known-vulnerable endpoint",
   "assert":{"type":"finding_present","rule_id":"OWASP-API1-BOLA",
             "endpoint":"GET /vuln/orders/{order_id}"}},
])
// → { "plan_id": "…", "strict": true, "persisted_to": "…/plans/….json" }
```

`strict:true` means `passed` requires **every** CP be verified-tier AND
satisfied — an attested CP can never carry a strict plan. Declare it at
`qa_plan` time; it can be tightened at verify time but never loosened.

**2. `run_api_security_scan` — pass `plan_id` for a one-shot bookend**

> **You**: Scan it, and verify against the plan in the same call.

```jsonc
run_api_security_scan(
  spec_url="file:///…/examples/sample_vulnerable_api/openapi.yaml",
  base_url="http://127.0.0.1:5099",
  severity_threshold="low",          // lower it if a CP targets low-sev findings
  plan_id="…"                        // ← makes the scan verify in-line
)
```

The scanner writes a redacted `scan-results.json` **before** it runs the
plan verification — that ordering is what lets the verified `finding_*`
CPs load ground truth from the artifact the scan just produced. The
response carries a `plan_verification` block:

```json
{
  "scan_id": "412ed09fb91d",
  "findings": [ /* … */ ],
  "scan_results_path": "/…/scan-results.json",
  "plan_verification": {
    "plan_id": "…",
    "status": "passed",
    "checklist": [
      {"id": "CP1", "tier": "verified", "satisfied": true,
       "actual": {"rule_id": "OWASP-API1-BOLA",
                  "endpoint": "GET /safe/me/orders", "hits": 0}},
      {"id": "CP2", "tier": "verified", "satisfied": true,
       "actual": {"rule_id": "OWASP-API1-BOLA",
                  "endpoint": "GET /vuln/orders/{order_id}", "hits": 2}}
    ],
    "verification": {"verified": 2, "verified_satisfied": 2,
                     "attested": 0, "attested_satisfied": 0}
  }
}
```

`CP2.actual.hits == 2` because the fixture leaks in both directions
(user_a reads user_b's order and vice versa) — both roll up under the
`OWASP-API1-BOLA` prefix. `CP1.hits == 0` proves the safe endpoint is
clean *and* that a scan artifact existed to prove it.

**3. Standalone `verify_plan` — same result, artifact-backed**

If you scanned earlier (or in a separate step), verify without re-running
the scan. No `evidence` needed — verified CPs self-load the artifact:

```jsonc
verify_plan(plan_id="…")
// identical checklist; reads the scan-results.json on disk
```

Pass `evidence` and it's *ignored* for the verified CPs — that's the
anti-forgery guarantee, not a bug. Only attested CPs (hint-only) consume
evidence.

### What breaks it — and why that's the point

| Situation | CP outcome | Rationale |
|---|---|---|
| Scan never ran / `scan-results.json` missing | `finding_present` **and** `finding_absent` both fail | fail-closed: absence of evidence ≠ evidence of absence |
| Real BOLA exists on the endpoint | `finding_absent` **not** satisfied | you cannot green a gate over a live vuln |
| You pass `evidence:["all clear"]` for a verified CP | ignored; verdict from artifact only | attestation can't override ground truth |
| `severity_threshold` above the finding's severity | finding filtered out before verify sees it | lower the threshold if a CP targets low-sev findings |

The last row is the one that bites in practice: a `finding_present` CP
silently "passes as absent" if the finding's severity is below the scan's
threshold. When a CP targets a specific finding, scan at
`severity_threshold="low"` so nothing is filtered out from under it.

---

## Track 3 — OWASP scan with verified `finding_*` bookends (v0.9.6+)

The two tracks above verify *functional* behavior. The third API angle
is the OWASP API Top 10 scanner (`run_api_security_scan`, since v0.8) —
and since v0.9.6 you can wrap it in **verified** plan bookends: declare
which findings must (or must not) exist *before* scanning, and let
`verify_plan` judge the outcome from the scan artifact itself. The tool
loads `scan-results.json` on its own and **ignores any evidence the
host passes** — a security gate the AI can't talk its way past.

The walkthrough below uses the deliberately-vulnerable Flask fixture at
`examples/sample_vulnerable_api/` (a BOLA hole on
`GET /vuln/orders/{order_id}`, a safe twin at `GET /safe/me/orders`).

### 1. `qa_plan` — declare the security contract up front

> **You**: Before scanning, pin down what "secure enough" means.

```
qa_plan(task="orders endpoint security gate", strict=true, critical_points=[
  {"id":"CP1","description":"the known BOLA on the legacy route is detected",
   "assert":{"type":"finding_present","rule_id":"OWASP-API1-BOLA",
             "endpoint":"GET /vuln/orders/{order_id}"}},
  {"id":"CP2","description":"the rewritten route is clean",
   "assert":{"type":"finding_absent","rule_id":"OWASP-API1-BOLA",
             "endpoint":"GET /safe/me/orders"}},
])
```

Both CPs carry an `assert` block → both are **verified** tier. With
`strict:true`, `verify_plan` will only ever say `passed` when every CP
is verified AND satisfied — an attested "trust me" can never green-light
this plan. `strict` can be tightened at verify time but never loosened.

Two matching rules worth knowing (they bit us during dogfooding):

- **`rule_id` is a prefix match on the rule class.** Real findings
  carry sub-ids like `OWASP-API1-BOLA-CrossUserDataExposure`; you write
  the class id `OWASP-API1-BOLA` and it covers all sub-ids.
- **`endpoint` is the finding's exact `"METHOD /path"` string** — e.g.
  `GET /vuln/orders/{order_id}`, template braces included. Omit it to
  match the rule anywhere in the scan.

### 2. `run_api_security_scan` — scan with `plan_id` for a one-shot bookend

```
run_api_security_scan(
  spec_url="examples/sample_vulnerable_api/openapi.yaml",
  base_url="http://127.0.0.1:5099",
  auth={"token": "<user-a>", "alt_user_token": "<user-b>",
        "bola_test_ids": {"user_a": [1, 3], "user_b": [2]}},
  severity_threshold="low",
  plan_id="<from step 1>",
)
```

The response ends with:

```json
{
  "findings": [
    {
      "rule_id": "OWASP-API1-BOLA-CrossUserDataExposure",
      "endpoint": "GET /vuln/orders/{order_id}",
      "severity": "high"
    }
  ],
  "scan_results_path": "/path/to/project/scan-results.json",
  "plan_verification": {
    "status": "passed",
    "checklist": [
      {"id": "CP1", "tier": "verified", "satisfied": true,
       "actual": {"hits": 1}},
      {"id": "CP2", "tier": "verified", "satisfied": true,
       "actual": {"hits": 0}}
    ]
  }
}
```

Ordering matters and is guaranteed: the scanner writes (redacted)
`scan-results.json` **before** running plan verification, so the
verified `finding_*` CPs in the same call already read ground truth.
The artifact path resolves as `GOMORE_QA_SCAN_PATH` →
`<QA_PROJECT_ROOT>/scan-results.json`.

### 3. `verify_plan` — or verify later, standalone

Skip `plan_id` at scan time and the bookend still works afterwards:

```
verify_plan(plan_id)        # no evidence argument — none is needed
```

Verified CPs self-load `scan-results.json`. Three outcomes to expect:

| Situation | Result |
|---|---|
| BOLA found on the vuln route | CP1 `satisfied: true` (`actual.hits >= 
