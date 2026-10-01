"""URL → testable modules + candidate TCs.

Heuristic analyzer with two entry points:
  - analyze_url(): opens a URL with Playwright, probes the DOM (web)
  - analyze_screen(): dumps current screen via `maestro hierarchy` (mobile)

Both emit the same shape — modules + candidate TCs — so the MCP client
(AI editor) consumes them uniformly to drive `generate_test`. Runner-
agnostic and side-effect-free on the target.
"""
import json as _json
import os
import re
import shutil
import subprocess
from datetime import datetime
from typing import Any
from urllib.parse import urlparse


async def analyze_url(
    url: str,
    timeout_ms: int = 15000,
    auth_cookie: str | None = None,
    auth_storage: dict[str, str] | None = None,
) -> dict[str, Any]:
    resolved_storage: dict[str, str] = {}
    if auth_storage:
        try:
            resolved_storage = _resolve_auth_storage(auth_storage)
        except KeyError as e:
            return {
                "error": f"auth_storage 引用的環境變數不存在：{e.args[0]}",
                "url": url,
            }
        except TypeError as e:
            return {"error": str(e), "url": url}

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "error": "需要 playwright async API：pip install playwright && playwright install chromium",
            "url": url,
        }

    api_calls: list[dict] = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            context = await browser.new_context()
            cookies = _parse_cookie_string(auth_cookie, url) if auth_cookie else []
            if cookies:
                await context.add_cookies(cookies)
            if resolved_storage:
                target_origin = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
                await context.add_init_script(
                    _storage_init_script(resolved_storage, target_origin)
                )
            page = await context.new_page()

            def on_request(req):
                if req.resource_type not in ("fetch", "xhr"):
                    return
                api_calls.append({
                    "method": req.method,
                    "url": req.url,
                    "resource_type": req.resource_type,
                })

            def on_response(resp):
                # Attach status to the last matching call without a status yet.
                for call in api_calls:
                    if call["url"] == resp.url and "status" not in call:
                        call["status"] = resp.status
                        ct = resp.headers.get("content-type", "")
                        call["content_type"] = ct.split(";")[0].strip() if ct else None
                        return

            page.on("request", on_request)
            page.on("response", on_response)

            try:
                await page.goto(url, timeout=timeout_ms, wait_until="domcontentloaded")
            except Exception as e:
                return {"error": f"打開頁面失敗: {type(e).__name__}: {e}", "url": url}

            # Give late XHRs a chance — bounded so the tool stays snappy.
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass

            page_title = await page.title()
            structure = await page.evaluate(_DOM_PROBE_JS)
        finally:
            await browser.close()

    modules = _build_modules(structure or {})
    endpoints = _dedupe_endpoints(api_calls)
    layout_warnings = (structure or {}).get("layout_warnings", []) or []
    return {
        "url": url,
        "page_title": page_title,
        "scanned_at": datetime.now().isoformat(timespec="seconds"),
        "module_count": len(modules),
        "modules": modules,
        "api_endpoint_count": len(endpoints),
        "api_endpoints": endpoints,
        # Visible elements whose content escapes their container at the
        # current viewport — typical "跑版" signal (text overflow, hard-px
        # widths, etc.). Bounded to 20 entries in the probe.
        "layout_warning_count": len(layout_warnings),
        "layout_warnings": layout_warnings,
    }


# A value is treated as an `$ENV_NAME` indirection only when it's the WHOLE
# string and looks like a valid identifier — "$", "$1BADNAME", or a real
# token that happens to start with "$" all fall through as literals instead
# of raising.
_ENV_REF_RE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")


def _resolve_auth_storage(auth_storage: dict[str, str]) -> dict[str, str]:
    """Expand `$ENV_NAME` indirection in auth_storage values.

    A value that fully matches `$ENV_NAME` (see `_ENV_REF_RE`) is looked up
    in os.environ (real token stays out of chat/tool-call logs); anything
    else — including a bare "$" or a malformed reference — passes through
    as a literal. Raises KeyError (with the missing var name) when a valid
    `$ENV_NAME` reference can't be resolved — callers must surface this as
    an explicit error, never silently drop the key. Raises TypeError when
    a value isn't a string at all.
    """
    resolved: dict[str, str] = {}
    for key, value in auth_storage.items():
        if not isinstance(value, str):
            raise TypeError("auth_storage 的值必須是字串")
        m = _ENV_REF_RE.fullmatch(value)
        if m:
            env_name = m.group(1)
            if env_name not in os.environ:
                raise KeyError(env_name)
            resolved[key] = os.environ[env_name]
        else:
            resolved[key] = value
    return resolved


def _storage_init_script(storage: dict[str, str], origin: str) -> str:
    """Build a `context.add_init_script` JS body that seeds localStorage
    before any page script runs — scoped to `origin` only.

    Without an origin guard, the init script would run on EVERY document
    the browsing context creates for this page load, including cross-origin
    iframes (ads / analytics / support-widget embeds) and SSO redirect
    hops — writing the same token into localStorage on domains that have
    no business seeing it. The whole body is also wrapped in try/catch:
    `location.origin` access or localStorage itself can throw
    (SecurityError) in sandboxed / about:blank frames, and that must not
    surface as noisy console errors.

    One `localStorage.setItem(...)` statement per key; keys and values are
    JSON-serialized to keep them safe string literals (no JS-injection via
    quotes/backslashes/newlines in either).
    """
    if not storage:
        return ""
    body = "\n".join(
        f"    localStorage.setItem({_json.dumps(key)}, {_json.dumps(value)});"
        for key, value in storage.items()
    )
    return (
        "try {\n"
        f"  if (location.origin === {_json.dumps(origin)}) {{\n"
        f"{body}\n"
        "  }\n"
        "} catch (e) {}"
    )


def _parse_cookie_string(cookie_str: str, url: str) -> list[dict]:
    host = urlparse(url).hostname or ""
    cookies: list[dict] = []
    for part in cookie_str.split(";"):
        if "=" not in part:
            continue
        name, _, value = part.strip().partition("=")
        if name:
            cookies.append({"name": name, "value": value, "domain": host, "path": "/"})
    return cookies


def _dedupe_endpoints(calls: list[dict]) -> list[dict]:
    """Collapse duplicate (method, path) pairs and attach candidate TCs."""
    seen: dict[tuple[str, str], dict] = {}
    for c in calls:
        parsed = urlparse(c["url"])
        key = (c["method"].upper(), parsed.path or "/")
        if key in seen:
            continue
        entry = {
            "method": c["method"].upper(),
            "url": c["url"],
            "path": parsed.path or "/",
            "host": parsed.hostname or "",
            "resource_type": c.get("resource_type"),
            "status": c.get("status"),
            "content_type": c.get("content_type"),
        }
        entry["candidate_tcs"] = _api_candidate_tcs(entry)
        seen[key] = entry
    return list(seen.values())


def _api_candidate_tcs(endpoint: dict) -> list[str]:
    method = endpoint["method"]
    path = endpoint["path"]
    tcs: list[str] = []
    if method == "GET":
        tcs += [
            f"GET {path} 正常請求應回 2xx，response schema 符合契約",
            f"GET {path} 缺少 auth header 應回 401/403",
            f"GET {path} 帶不存在的 ID 應回 404",
            f"GET {path} 帶異常 query 參數應有 graceful 回應（不應 500）",
        ]
    elif method == "POST":
        tcs += [
            f"POST {path} payload 缺必填欄位應回 400 + 欄位錯誤訊息",
            f"POST {path} 合法 payload 應回 2xx 並建立資源",
            f"POST {path} 重複建立應依設計回 409 或 idempotent 2xx",
            f"POST {path} 缺少 auth header 應回 401/403",
            f"POST {path} payload 超過大小限制應回 413 或 400",
        ]
    elif method in ("PUT", "PATCH"):
        tcs += [
            f"{method} {path} 對不存在資源應回 404",
            f"{method} {path} 合法 payload 應更新並回 2xx",
            f"{method} {path} 部分欄位更新應保留未變動欄位（特別針對 PATCH）",
            f"{method} {path} 缺少 auth header 應回 401/403",
        ]
    elif method == "DELETE":
        tcs += [
            f"DELETE {path} 對不存在資源應回 404 或 idempotent 2xx",
            f"DELETE {path} 成功應移除資源，二次呼叫應回 404",
            f"DELETE {path} 缺少 auth header 應回 401/403",
        ]
    status = endpoint.get("status")
    if isinstance(status, int) and 400 <= status < 600:
        tcs.append(f"注意：載入時實際 status={status}，請先確認是否為已知問題或預期狀態")
    return tcs


_DOM_PROBE_JS = r"""
() => {
  const esc = (s) => (window.CSS && CSS.escape) ? CSS.escape(s) : (s || '').replace(/[^a-zA-Z0-9_-]/g, '_');
  // Escapes a value going INSIDE a `[attr="..."]` selector's double quotes
  // (as opposed to `esc()` above, which escapes an #id/.class identifier).
  // A placeholder/aria-label can legitimately contain a `"` or `\` —
  // without this, that breaks out of the attribute selector's string and
  // produces a selector that either throws or silently matches the wrong
  // thing.
  const escAttr = (s) => (s || '').replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  const sel = (el) => {
    if (!el) return null;
    if (el.id) return '#' + esc(el.id);
    const t = el.getAttribute('data-testid');
    if (t) return `[data-testid="${escAttr(t)}"]`;
    const n = el.getAttribute('name');
    if (n && ['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName)) return `${el.tagName.toLowerCase()}[name="${escAttr(n)}"]`;
    const a = el.getAttribute('aria-label');
    if (a) return `${el.tagName.toLowerCase()}[aria-label="${escAttr(a)}"]`;
    // Last resort before the bare tag name: a gwp-admin-style search box
    // is frequently just `<input placeholder="搜尋...">` with none of the
    // above — without this, every such field fails `hasStableSelector()`
    // and gets dropped from `standalone_fields` entirely (the exact field
    // this whole feature was built to catch).
    if (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') {
      const ph = el.getAttribute('placeholder');
      if (ph) return `${el.tagName.toLowerCase()}[placeholder="${escAttr(ph)}"]`;
    }
    return el.tagName.toLowerCase();
  };
  const txt = (el) => (el && (el.innerText || el.textContent) || '').trim().slice(0, 80);
  const labelFor = (i) => {
    const id = i.getAttribute('id');
    if (id) {
      const l = document.querySelector(`label[for="${esc(id)}"]`);
      if (l) return txt(l);
    }
    const p = i.closest('label');
    if (p) return txt(p);
    return i.getAttribute('aria-label') || i.getAttribute('placeholder') || i.getAttribute('name') || '';
  };

  // Shared actual-visibility check — distinct from a `<dialog>`'s `open`
  // attribute (a DOM/markup state) or any other "is this element the
  // kind of thing we'd normally show" heuristic. A closed `<dialog>`'s
  // children (its cancel/confirm buttons, its own empty `<form>`) and a
  // `display:none` container's cta are exactly the real-world false
  // positives this exists to flag: analyzer still records them as
  // modules (the user needs to know they exist), but `visible: false`
  // tells the renderer not to generate a click/fill/to_be_visible that
  // would be guaranteed-red out of the box. Bounding-rect zero already
  // catches "inside a display:none ancestor" (descendants collapse to
  // 0x0 regardless of their own `display` value), but `visibility` is
  // inherited and does NOT zero out layout, so the explicit computed-
  // style check is still needed for that case.
  const isVisible = (el) => {
    if (!el) return false;
    const r = el.getBoundingClientRect();
    if (r.width <= 0 || r.height <= 0) return false;
    const cs = getComputedStyle(el);
    if (cs.visibility === 'hidden' || cs.display === 'none') return false;
    if (parseFloat(cs.opacity) === 0) return false;
    return true;
  };

  const forms = [...document.querySelectorAll('form')].map((f, i) => {
    const fields = [...f.querySelectorAll('input, textarea, select')]
      .filter(el => el.type !== 'hidden')
      .map(el => ({
        label: labelFor(el),
        selector: sel(el),
        type: el.tagName === 'INPUT' ? (el.type || 'text') : el.tagName.toLowerCase(),
        required: el.required || el.getAttribute('aria-required') === 'true',
      }));
    const sb = f.querySelector('button[type="submit"], input[type="submit"], button:not([type])');
    return {
      index: i, selector: sel(f),
      action: f.getAttribute('action') || null,
      method: (f.getAttribute('method') || 'get').toLowerCase(),
      fields,
      submit: sb ? { selector: sel(sb), text: txt(sb) } : null,
      visible: isVisible(f),
    };
  });

  const navs = [...document.querySelectorAll('nav, [role="navigation"]')].map((n, i) => ({
    index: i, selector: sel(n),
    label: n.getAttribute('aria-label') || '',
    links: [...n.querySelectorAll('a[href]')].map(a => ({
      text: txt(a), href: a.getAttribute('href'),
    })).filter(l => l.text || l.href).slice(0, 30),
  })).filter(n => n.links.length > 0);

  const dialogs = [...document.querySelectorAll('dialog, [role="dialog"], [role="alertdialog"]')].map((d, i) => ({
    index: i, selector: sel(d),
    label: d.getAttribute('aria-label') || txt(d.querySelector('h1,h2,h3,[role="heading"]')) || '',
    // `open` is DOM/markup state (a `<dialog>`'s `open` attribute, or
    // `[role=dialog]`'s `hidden`); `visible` is the actual rendered
    // visibility check — they diverge for e.g. a `[role=dialog]` whose
    // `hidden` attribute is absent but which is `display:none` via CSS.
    open: d.tagName === 'DIALOG' ? d.hasAttribute('open') : !d.hidden,
    visible: isVisible(d),
  }));

  const sections = [...document.querySelectorAll('section[aria-label], section[aria-labelledby], [role="region"][aria-label]')].map((s, i) => {
    const lbId = s.getAttribute('aria-labelledby');
    const labelled = lbId ? document.getElementById(lbId) : null;
    return {
      index: i, selector: sel(s),
      label: s.getAttribute('aria-label') || txt(labelled) || txt(s.querySelector('h1,h2,h3')) || '',
    };
  });

  const ctaPatterns = ['登入','登出','註冊','結帳','送出','提交','下一步','繼續','購買','加入購物車','搜尋','查詢','確認','取消','訂閱','Sign in','Sign up','Login','Logout','Submit','Continue','Next','Checkout','Subscribe','Buy','Add to cart','Search'];
  const ctas = [...document.querySelectorAll('button, [role="button"], a.button, a.btn')]
    .filter(b => !b.closest('form'))
    .map(b => ({ text: txt(b), selector: sel(b), tag: b.tagName.toLowerCase(), visible: isVisible(b) }))
    .filter(b => b.text && ctaPatterns.some(p => b.text.includes(p)))
    .slice(0, 20);

  // Tables: three-tier detection (native <table> > ARIA grid > repeated
  // fallback), de-duped so the same element is never classified twice —
  // native/aria elements are tracked in `seenTableEls` before the next
  // tier runs, so a lower-priority tier simply skips anything already
  // claimed. gwp-admin-style Tailwind back
  // offices render data tables as plain <table> (the common case) but
  // some ship ARIA grids, and some ship neither — just N repeated
  // sibling rows (divs) — hence the fallback tier.
  const seenTableEls = new Set();
  const tables = [];

  // Reviewer-found false positives (real Chromium repro): a `<select>`'s
  // ≥4 `<option>`s, an SVG bar chart's ≥4 `<rect>`s, a ≥4-button toolbar,
  // and an `<article>`'s ≥4 `<p>`s all pattern-match "≥4 same-signature
  // children" without being a data table. `select`/`datalist`/`svg` are
  // excluded as containers outright (closest() below); leaf-ish row tags
  // (plus anything in the SVG namespace) are excluded per-row regardless
  // of container.
  const LEAF_ROW_TAGS = new Set(['OPTION', 'P', 'SPAN', 'BUTTON', 'A', 'BR', 'HR']);
  const SVG_NS = 'http://www.w3.org/2000/svg';
  const isLeafish = (el) => LEAF_ROW_TAGS.has(el.tagName) || el.namespaceURI === SVG_NS;
  // A real data row either has internal structure (≥2 cell-like children)
  // or carries its own text (e.g. a flat `<div class="card">label</div>`
  // row) — a leaf-tagged element never qualifies even if both are true.
  const looksLikeRow = (el) => !isLeafish(el) && (el.children.length >= 2 || txt(el).length > 0);

  // Containers that are themselves a different, already-handled widget
  // type (real table, nav, list, select, svg, or another ARIA
  // table/grid/listbox/menu/tablist/nav) are never candidates for the
  // repeated-fallback tier — this also catches an ARIA grid's internal
  // `rowgroup` wrapper, which otherwise independently pattern-matches
  // "≥4 same-signature [role=row] children" and would double-count the
  // same grid as both "aria" and "repeated".
  const REPEATED_EXCLUDE_SELECTOR =
    'table, nav, ul, ol, select, datalist, svg, ' +
    '[role="table"], [role="grid"], [role="treegrid"], [role="listbox"], ' +
    '[role="menu"], [role="tablist"], [role="navigation"]';

  // Elements with no id / data-testid / name / aria-label / placeholder
  // fall back to a bare tag-name selector (`sel()`'s last resort) — e.g.
  // plain `"div"`. That's useless (and actively dangerous: Playwright's
  // strict-mode locator throws on a non-unique match) as a selector for
  // a *detected* widget, so the repeated-fallback tier refuses to emit
  // one at all rather than hand back a selector nobody can safely click.
  // Must mirror `sel()`'s own fallback chain exactly — this function
  // exists to answer "will `sel()` return something other than a bare
  // tag name for this element?", so a mismatch here silently reintroduces
  // the bare-tag problem it's meant to guard against (real regression:
  // a gwp-admin search `<input placeholder="...">` with no other
  // attributes was being dropped before `sel()` grew the placeholder
  // fallback below was mirrored here too).
  const hasStableSelector = (el) =>
    Boolean(
      el.id ||
      el.getAttribute('data-testid') ||
      (['INPUT', 'SELECT', 'TEXTAREA'].includes(el.tagName) && el.getAttribute('name')) ||
      el.getAttribute('aria-label') ||
      ((el.tagName === 'INPUT' || el.tagName === 'TEXTAREA') && el.getAttribute('placeholder'))
    );

  // Climbs at most 3 ancestor levels looking for a preceding heading —
  // unbounded climbing previously walked all the way to a page-level H1
  // and attributed it to every table on the page.
  const nearbyHeading = (el) => {
    let node = el;
    let depth = 0;
    while (node && depth < 3) {
      let sib = node.previousElementSibling;
      while (sib) {
        if (/^H[1-6]$/.test(sib.tagName) || sib.getAttribute('role') === 'heading') {
          const t = txt(sib);
          if (t) return t;
        }
        sib = sib.previousElementSibling;
      }
      node = node.parentElement;
      depth++;
      if (!node || node === document.body) break;
    }
    return '';
  };

  const tableLabel = (el, captionEl) => {
    if (captionEl) {
      const c = txt(captionEl);
      if (c) return c;
    }
    const aria = el.getAttribute('aria-label');
    if (aria) return aria;
    return nearbyHeading(el);
  };

  // a. native <table>. Uses the table-specific DOM API (`.caption`,
  // `.tHead`, `.tBodies`) instead of descendant selectors like
  // `querySelectorAll('tbody tr')` — those are *this table's own*
  // caption/head/body per spec (never reach into a nested <table> inside
  // a cell), whereas a descendant selector would double-count a nested
  // table's rows/headers as this table's.
  [...document.querySelectorAll('table')].forEach((el) => {
    seenTableEls.add(el);
    const theadEl = el.tHead;
    const headers = theadEl
      ? [...theadEl.querySelectorAll(':scope > tr > th')].map(txt).filter(Boolean).slice(0, 20)
      : [];
    const bodyRows = el.tBodies.length
      ? [...el.tBodies].flatMap(tb => [...tb.rows])
      : [...el.rows].filter(r => !theadEl || !theadEl.contains(r));
    const firstRow = bodyRows[0] || el.rows[0] || null;
    tables.push({
      index: tables.length,
      selector: sel(el),
      label: tableLabel(el, el.caption),
      headers,
      column_count: headers.length || (firstRow ? firstRow.children.length : 0),
      row_count: bodyRows.length,
      detection: 'native',
      selector_unique: hasStableSelector(el),
      visible: isVisible(el),
    });
  });

  // b. ARIA table/grid/treegrid, excluding anything already native.
  [...document.querySelectorAll('[role="table"], [role="grid"], [role="treegrid"]')].forEach((el) => {
    if (seenTableEls.has(el) || el.closest('table')) return;
    seenTableEls.add(el);
    const headers = [...el.querySelectorAll('[role="columnheader"]')].map(txt).filter(Boolean).slice(0, 20);
    const rows = [...el.querySelectorAll('[role="row"]')]
      .filter(r => !r.querySelector(':scope > [role="columnheader"]'));
    const firstDataRow = rows[0] || null;
    const column_count = headers.length || (
      firstDataRow
        ? firstDataRow.querySelectorAll(':scope > [role="cell"], :scope > [role="gridcell"]').length
        : 0
    );
    tables.push({
      index: tables.length,
      selector: sel(el),
      label: tableLabel(el, null),
      headers,
      column_count,
      row_count: rows.length,
      detection: 'aria',
      selector_unique: hasStableSelector(el),
      visible: isVisible(el),
    });
  });

  // c. fallback: ≥4 repeated same-signature (tagName + sorted classList)
  // children in one container, skipping already-claimed widget types
  // (`REPEATED_EXCLUDE_SELECTOR`) and leaf-ish rows (`looksLikeRow`).
  // Signature grouping keeps us from matching an unrelated mix of
  // siblings (e.g. a header + N cards) as "the rows".
  //
  // Known trade-offs (reviewed + accepted, not bugs):
  //   1. Unlike native/aria (always emitted, flagged `selector_unique:
  //      false` when the selector is just a bare tag), this tier refuses
  //      to emit anything without a *stable* container selector at all.
  //      Deliberate asymmetry: native/aria are structurally confident
  //      detections even when the selector happens to be unstable,
  //      whereas "repeated" is already a low-confidence guess — stacking
  //      an unusable selector on top of that guess isn't worth surfacing.
  //   2. `A` is in `LEAF_ROW_TAGS`, so a list where the *entire row* is a
  //      link (`<a class="row">...</a>` repeated N times) is never
  //      detected — it looks leaf-ish like a plain CTA, not a data row.
  //      Known gap, not handled here.
  //   3. The greedy containment-dedup (below) picks by raw row *count*,
  //      not depth. If a container's rows themselves have more same-
  //      signature cells than the container has rows (e.g. 3 rows of 6
  //      cells each), the row-level candidate could rank above the
  //      container-level one and get picked instead. Rare in practice
  //      (real data tables have more rows than columns) — not handled.
  const signature = (el) => el.tagName + '|' + [...el.classList].sort().join('.');
  const repeatedCandidates = [];
  [...document.querySelectorAll('body *')].forEach((container) => {
    if (container.tagName === 'SCRIPT' || container.tagName === 'STYLE') return;
    // No separate "is this nested inside an already-claimed element?"
    // check needed: by this point `seenTableEls` only holds native
    // <table> elements and ARIA table/grid/treegrid elements (tiers a/b
    // above), and `REPEATED_EXCLUDE_SELECTOR` already includes `table`
    // and `[role="table"/"grid"/"treegrid"]` — so `closest()` below
    // catches every such ancestor (at any depth) on its own.
    if (seenTableEls.has(container)) return;
    if (container.closest(REPEATED_EXCLUDE_SELECTOR)) return;
    if (!hasStableSelector(container)) return;
    const children = [...container.children].filter(
      c => c.tagName !== 'SCRIPT' && c.tagName !== 'STYLE'
    );
    if (children.length < 4) return;
    const groups = new Map();
    children.forEach((c) => {
      const s = signature(c);
      if (!groups.has(s)) groups.set(s, []);
      groups.get(s).push(c);
    });
    let best = null;
    groups.forEach((group) => {
      if (group.length >= 4 && group.every(looksLikeRow) && (!best || group.length > best.length)) {
        best = group;
      }
    });
    if (best) repeatedCandidates.push({ container, rows: best });
  });
  // Highest row-count first, then greedily skip any candidate nested
  // inside (or wrapping) one already picked — otherwise a 10-row list
  // whose rows are themselves ≥4-cell containers would surface as both
  // "the list" AND "each row" as separate table modules.
  repeatedCandidates.sort((a, b) => b.rows.length - a.rows.length);
  const pickedRepeated = [];
  for (const rc of repeatedCandidates) {
    if (pickedRepeated.some(p =>
      p.container.contains(rc.container) || rc.container.contains(p.container)
    )) continue;
    pickedRepeated.push(rc);
    if (pickedRepeated.length >= 5) break; // bounded like navs/ctas/layout_warnings below
  }
  pickedRepeated.forEach((rc) => {
    seenTableEls.add(rc.container);
    tables.push({
      index: tables.length,
      selector: sel(rc.container),
      label: tableLabel(rc.container, null),
      headers: [],
      column_count: 0,
      row_count: rc.rows.length,
      detection: 'repeated',
      selector_unique: true, // guaranteed by the hasStableSelector() guard above
      visible: isVisible(rc.container),
    });
  });

  // Fields that live outside any <form> — search boxes / filter bars in
  // gwp-admin-style back offices are frequently bare inputs next to a
  // data table, not wrapped in <form>, so the form-scoped collector above
  // never sees them. Also excludes anything inside an already-detected
  // table/grid (e.g. a per-row selection checkbox) — that's the table's
  // concern, not a standalone filter field. Fields without a stable
  // selector (no id/data-testid/name/aria-label — `hasStableSelector()`,
  // same guard as the repeated-table tier) are dropped outright rather
  // than just de-duped: a Playwright locator built from a bare tag name
  // is unusable the moment there's more than one such field on the page,
  // so there's nothing useful left to de-dupe down to. The de-dupe pass
  // still runs after that as a backstop for the (stable-but-not-unique)
  // case of several elements legitimately sharing one selector, e.g. a
  // same-`name` radio group.
  const seenStandaloneSelectors = new Set();
  const standalone_fields = [...document.querySelectorAll('input:not([type=hidden]), textarea, select')]
    .filter(el => !el.closest('form'))
    .filter(el => !el.closest('table, [role="grid"], [role="table"], [role="treegrid"]'))
    .filter(el => hasStableSelector(el))
    .filter(el => isVisible(el))
    .map(el => ({
      label: labelFor(el),
      selector: sel(el),
      type: el.tagName === 'INPUT' ? (el.type || 'text') : el.tagName.toLowerCase(),
      required: el.required || el.getAttribute('aria-required') === 'true',
    }))
    .filter(f => {
      if (seenStandaloneSelectors.has(f.selector)) return false;
      seenStandaloneSelectors.add(f.selector);
      return true;
    })
    .slice(0, 30);

  // Layout warnings: visible elements whose content overflows their container.
  // Threshold tuning: horizontal >2px is almost always a real break (text
  // 跑版, hard-px width, etc.). Vertical <=10px is usually line-height /
  // emoji baseline noise (emoji glyphs sit a few px above CJK x-height) so
  // we only flag vertical overflow once it exceeds that band. Skips
  // invisible elements + intentional scrollers (overflow: auto/scroll).
  const layout_warnings = [...document.querySelectorAll('body *')]
    .filter(el => {
      if (!isVisible(el)) return false;
      const cs = getComputedStyle(el);
      const dx = el.scrollWidth - el.clientWidth;
      const dy = el.scrollHeight - el.clientHeight;
      if (dx <= 2 && dy <= 10) return false;
      // Intentional scrollers (overflow: auto / scroll) are not bugs.
      if (cs.overflowX === 'auto' || cs.overflowX === 'scroll') return false;
      if (cs.overflowY === 'auto' || cs.overflowY === 'scroll') return false;
      return true;
    })
    .slice(0, 20)
    .map(el => {
      const r = el.getBoundingClientRect();
      return {
        selector: sel(el),
        tag: el.tagName.toLowerCase(),
        text_sample: txt(el).slice(0, 40),
        overflow_x: el.scrollWidth - el.clientWidth,
        overflow_y: el.scrollHeight - el.clientHeight,
        bbox: { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height) },
      };
    });

  return { forms, navs, dialogs, sections, ctas, tables, standalone_fields, layout_warnings };
}
"""


def _slug(text: str, fallback: str) -> str:
    if not text:
        return fallback
    s = re.sub(r"[^\w]+", "_", text.lower()).strip("_")
    return s or fallback


def _table_candidate_tcs(headers: list[str], detection: str) -> list[str]:
    """Candidate TCs for a `kind: "table"` module.

    Always covers five scenarios: row load/empty-state, pagination,
    column sort, search/filter, row click → detail. When `detection`
    is "repeated" (a heuristic guess at div-based row structures, not a
    real `<table>`/ARIA grid), the wording is hedged ("疑似資料列表")
    instead of asserting it's definitely a table/grid. When `headers`
    is non-empty, the sort TC references the first header by name to
    make it directly actionable.
    """
    conservative = detection == "repeated"
    subject = "疑似資料列表" if conservative else "表格"
    tcs = [
        f"{subject}載入後應有資料列（row_count > 0 或顯示空狀態提示）",
        f"{subject}切換分頁後列內容應變更，且列數應符合 page size（若有分頁）",
    ]
    if headers:
        tcs.append(f"點擊「{headers[0]}」欄位標題排序後，資料應依該欄重新排序")
    else:
        tcs.append(f"{subject}欄位排序點擊後應依該欄排序（若支援排序）")
    tcs.append(f"{subject}搜尋／過濾後列數應減少，且內容與條件相符（若支援搜尋／過濾）")
    tcs.append(f"點擊{subject}中任一列（若可點擊）應開啟詳情")
    return tcs


def _implicit_form_candidate_tcs(fields: list[dict]) -> list[str]:
    """Candidate TCs for the aggregated `standalone_fields` module.

    Deliberately excludes wording that native-form TCs use but doesn't
    match either the field semantics or `_render_implicit_form_test`'s
    actual rendering:
      - "送出/提交"（submit）— these fields have no enclosing <form>/submit
        button; the "submission" is implicit (Enter key, live filter,
        etc.), not a button click.
      - "只填其他欄位、X 留空"（single-field-empty）— reviewer-flagged
        conflict: `runners/pytest_playwright._extract_single_empty_label`
        pattern-matches that exact "留空" phrasing to render a
        fill-everything-but-X test, which contradicts "忽略該條件"
        (implicit filters just ignore an absent condition, they don't
        validate required-ness) and doesn't match how
        `_render_implicit_form_test` actually renders (fill one field,
        press Enter).
      - Email format-validation wording — `_render_implicit_form_test`
        always prefers a text/search field as the one it fills; an email
        field is only ever touched as a last-resort fallback (no text-
        like field at all), so asserting "format error" behavior here
        would describe an interaction the renderer usually never
        performs (review N4).
    """
    tcs: list[str] = ["輸入關鍵字後按 Enter 應觸發查詢／過濾"]
    tcs.append("清空輸入應還原列表")
    return tcs


def _build_modules(structure: dict) -> list[dict]:
    modules: list[dict] = []

    for form in structure.get("forms") or []:
        fields = form.get("fields") or []
        basis = form.get("action") or " ".join(f.get("label", "") for f in fields if f.get("label")) or "form"
        name = f"{_slug(basis, 'form')}_form_{form['index']}"
        required = [f for f in fields if f.get("required")]
        has_password = any((f.get("type") or "").lower() == "password" for f in fields)
        has_email = any((f.get("type") or "").lower() == "email" for f in fields)

        tcs: list[str] = []
        if fields:
            tcs.append("所有必填欄位為空時送出，應顯示必填錯誤")
            for f in required[:3]:
                label = f.get("label") or f.get("selector") or "field"
                tcs.append(f"只填其他欄位、{label} 留空，應顯示該欄位必填錯誤")
            if has_email:
                tcs.append("Email 欄位填入格式錯誤的字串（無 @），應顯示格式錯誤")
            if has_password:
                tcs.append("Password 欄位輸入後應預設遮蔽（type=password）")
                tcs.append("Password 太短或不符合複雜度規則時應顯示錯誤")
            tcs.append("全部填入合法值後送出，應觸發成功流程（導頁或顯示成功訊息）")
        else:
            tcs.append("直接點擊送出，應有適當回應或無作用")

        visible = form.get("visible")
        if visible is False and tcs:
            tcs[0] = f"（需先觸發顯示）{tcs[0]}"

        modules.append({
            "kind": "form",
            "name": name,
            "selectors": {
                "container": form.get("selector"),
                "fields": fields,
                "submit": (form.get("submit") or {}).get("selector"),
            },
            "metadata": {
                "method": form.get("method"),
                "action": form.get("action"),
                "field_count": len(fields),
                "visible": visible,
            },
            "candidate_tcs": tcs,
        })

    for nav in structure.get("navs") or []:
        label = nav.get("label") or f"nav_{nav['index']}"
        link_count = len(nav.get("links") or [])
        modules.append({
            "kind": "nav",
            "name": _slug(label, f"nav_{nav['index']}"),
            "selectors": {"container": nav.get("selector")},
            "links": nav.get("links"),
            "candidate_tcs": [
                f"nav 內每個連結（共 {link_count} 個）點擊後應導向對應 href",
                "在小螢幕寬度下 nav 應可摺疊／展開（若為 responsive）",
                "鍵盤 Tab 鍵應能依序聚焦每個 nav 連結",
            ],
        })

    for d in structure.get("dialogs") or []:
        label = d.get("label") or f"dialog_{d['index']}"
        modules.append({
            "kind": "dialog",
            "name": _slug(label, f"dialog_{d['index']}"),
            "selectors": {"container": d.get("selector")},
            "metadata": {"open_on_load": d.get("open"), "visible": d.get("visible")},
            "candidate_tcs": [
                "觸發 dialog 開啟後焦點應落入 dialog 內",
                "按 ESC 或點擊遮罩應關閉 dialog（如設計允許）",
                "dialog 開啟時背景滾動應被鎖定",
                "關閉後焦點應回到觸發按鈕",
            ],
        })

    for s in structure.get("sections") or []:
        label = s.get("label") or f"section_{s['index']}"
        modules.append({
            "kind": "section",
            "name": _slug(label, f"section_{s['index']}"),
            "selectors": {"container": s.get("selector")},
            "candidate_tcs": [f"{label} 區塊應正確渲染（非空、無 console error）"],
        })

    for cta in structure.get("ctas") or []:
        text = cta.get("text") or ""
        visible = cta.get("visible")
        cta_tcs = [
            f"點擊「{text}」應觸發對應動作（導頁／開 dialog／送 API）",
            f"「{text}」在 loading 狀態下應禁用以避免重複觸發",
        ]
        if visible is False:
            cta_tcs[0] = f"（需先觸發顯示）{cta_tcs[0]}"
        modules.append({
            "kind": "cta",
            "name": _slug(text, "cta"),
            "selectors": {"trigger": cta.get("selector")},
            "metadata": {"label_text": text, "tag": cta.get("tag"), "visible": visible},
            "candidate_tcs": cta_tcs,
        })

    for t in structure.get("tables") or []:
        headers = t.get("headers") or []
        detection = t.get("detection") or "native"
        label = t.get("label") or ""
        name = f"{_slug(label, 'table')}_table_{t['index']}"
        modules.append({
            "kind": "table",
            "name": name,
            "selectors": {"container": t.get("selector")},
            "metadata": {
                "headers": headers,
                "column_count": t.get("column_count"),
                "row_count": t.get("row_count"),
                "detection": detection,
                "selector_unique": t.get("selector_unique", True),
                "visible": t.get("visible"),
            },
            "candidate_tcs": _table_candidate_tcs(headers, detection),
        })

    standalone_fields = structure.get("standalone_fields") or []
    if standalone_fields:
        modules.append({
            "kind": "form",
            "name": "implicit_form_0",
            "selectors": {
                "container": None,
                "fields": standalone_fields,
                "submit": None,
            },
            "metadata": {
                "implicit": True,
                "field_count": len(standalone_fields),
            },
            "candidate_tcs": _implicit_form_candidate_tcs(standalone_fields),
        })

    return modules


# ---- analyze_screen (mobile) ----------------------------------------------

def analyze_screen(
    app_id: str | None = None,
    launch_app: bool = False,
    timeout_ms: int = 30000,
) -> dict[str, Any]:
    """Mobile equivalent of analyze_url. Captures current screen via
    `maestro hierarchy` and surfaces interactive elements as modules.

    Requires:
      - Maestro CLI installed (https://maestro.mobile.dev)
      - A simulator / emulator / device booted with the target app foregrounded

    Args:
      app_id: Optional. When `launch_app=True`, launches this bundle id first.
      launch_app: When True + app_id given, runs `launchApp` before hierarchy.
      timeout_ms: Subprocess timeout for the hierarchy dump.

    Returns same shape as analyze_url (`modules` + `candidate_tcs` per module),
    plus a `screen_summary` describing what was found.
    """
    if not shutil.which("maestro"):
        return {
            "error": "maestro CLI 找不到。安裝：curl -fsSL https://get.maestro.mobile.dev | bash",
        }

    # Remote-ADB endpoint (BlueStacks / Genymotion / cloud farm). Best-effort:
    # surface failure as a hint but still let Maestro try — a local emulator
    # may also be booted alongside the configured host.
    from ..config import ANDROID_HOST, connect_android_host
    android_host_ok, android_host_msg = connect_android_host()
    # BlueStacks `hierarchy` over TCP-ADB is typically 2–3× slower than a
    # local emulator. Quietly raise the floor when a remote host is in play
    # so the default 30s ceiling doesn't false-positive a timeout.
    if ANDROID_HOST and timeout_ms < 60000:
        timeout_ms = 60000

    # Optional: launch the app first so hierarchy reflects its starting screen.
    # We write the launch flow to a temp file because `maestro test -` (stdin)
    # behaved inconsistently across versions; temp-file is the well-trodden
    # path.
    if app_id and launch_app:
        import os as _os
        import tempfile
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".yaml", delete=False, encoding="utf-8",
        )
        try:
            tmp.write(
                f"appId: {app_id}\n"
                "---\n"
                "- launchApp:\n"
                "    clearState: false\n"
                "- waitForAnimationToEnd:\n"
                "    timeout: 5000\n"
            )
            tmp.close()
            subprocess.run(
                ["maestro", "test", tmp.name],
                capture_output=True,
                text=True,
                timeout=timeout_ms / 1000 + 10,
            )
        except subprocess.TimeoutExpired:
            return {"error": "launch app 逾時"}
        except OSError as e:
            return {"error": f"無法啟動 app：{type(e).__name__}: {e}"}
        finally:
            try:
                _os.unlink(tmp.name)
            except OSError:
                pass

    # Pull current screen hierarchy.
    try:
        result = subprocess.run(
            ["maestro", "hierarchy"],
            capture_output=True,
            text=True,
            timeout=timeout_ms / 1000,
        )
    except subprocess.TimeoutExpired:
        hint = ""
        if ANDROID_HOST:
            hint = (
                f" QA_ANDROID_HOST={ANDROID_HOST}（BlueStacks / 遠端 ADB）— "
                f"adb 連線狀態：{'OK' if android_host_ok else android_host_msg or 'failed'}。"
                "建議：1) 確認 BlueStacks 已開機且 Android Debug Bridge 已啟用 "
                "(Settings → Advanced → Android Debug Bridge: ON)；"
                "2) `adb devices` 應列出該 host；3) 重啟 BlueStacks 後重跑。"
            )
        return {"error": f"maestro hierarchy 逾時 — simulator 沒回應或無 booted device。{hint}"}
    except OSError as e:
        return {"error": f"執行 maestro 失敗：{type(e).__name__}: {e}"}

    if result.returncode != 0:
        return {
            "error": "maestro hierarchy 失敗",
            "stderr_tail": (result.stderr or "")[-500:],
        }

    # Strip preamble lines ("None:" / device label) — JSON starts at the first `{`.
    raw = result.stdout
    brace = raw.find("{")
    if brace < 0:
        return {"error": "hierarchy 輸出無 JSON 主體", "stdout_tail": raw[-500:]}
    try:
        tree = _json.loads(raw[brace:])
    except _json.JSONDecodeError as e:
        return {"error": f"JSON 解析失敗：{e}", "stdout_tail": raw[brace:brace + 500]}

    nodes = []
    _walk_screen(tree, nodes, depth=0)
    modules, summary = _build_screen_modules(nodes)

    out: dict[str, Any] = {
        "app_id": app_id,
        "scanned_at": datetime.now().isoformat(timespec="seconds"),
        "module_count": len(modules),
        "modules": modules,
        "screen_summary": summary,
    }
    if ANDROID_HOST:
        out["android_host"] = ANDROID_HOST
        out["android_host_connected"] = android_host_ok
        if android_host_msg:
            out["android_host_message"] = android_host_msg
    return out


# Heuristics for filtering noise from analyze_screen output. Real-world
# iOS/Android hierarchies surface asset names (bg_*, ic_*, *_filled) and
# placeholder text (--, single ASCII chars) as accessibility labels. These
# rarely correspond to user-intended interactions and just dilute the
# candidate list. Patterns below were tuned against a real mobile home
# screen where the raw output mixed real buttons with asset identifiers.
_NOISE_PREFIX_RE = re.compile(r"^(bg_|ic_|icon_|img_|image_)")
_NOISE_SUFFIX_RE = re.compile(r"(_filled|_outline|_image|_logo|_brand_logo|_active|_inactive)$")
_NOISE_PUNCT_RE = re.compile(r"^[-_.,\s　]+$")
_NOISE_NUM_ONLY_RE = re.compile(r"^[\d.,\-\+%元$]+$")


def _is_noise_text(text: str) -> bool:
    """Return True for labels that look like asset names / placeholders
    rather than user-facing CTA copy."""
    t = (text or "").strip()
    if not t:
        return True
    # Single ASCII character (e.g. "x", "+") is almost never a real button
    # in a CJK app; single Chinese characters can be (e.g. 「我」) so we
    # only filter single-char when ASCII.
    if len(t) == 1 and t.isascii():
        return True
    if _NOISE_PUNCT_RE.match(t):
        return True
    if _NOISE_NUM_ONLY_RE.match(t):
        return True
    if _NOISE_PREFIX_RE.search(t):
        return True
    return bool(_NOISE_SUFFIX_RE.search(t))


def _walk_screen(node: dict, out: list, depth: int) -> None:
    """Flatten the Maestro hierarchy tree into a list of attribute dicts.

    Maestro nests view containers heavily — we keep every node with any
    interactive signal (text / accessibilityText / hintText / resource-id)
    plus its bounds for downstream classification.
    """
    if not isinstance(node, dict) or depth > 60:
        return
    attrs = node.get("attributes") or {}
    if isinstance(attrs, dict):
        flat = {
            "text": (attrs.get("text") or "").strip(),
            "accessibility_text": (attrs.get("accessibilityText") or "").strip(),
            "hint_text": (attrs.get("hintText") or "").strip(),
            "title": (attrs.get("title") or "").strip(),
            "value": (attrs.get("value") or "").strip(),
            "resource_id": (attrs.get("resource-id") or "").strip(),
            "bounds": attrs.get("bounds") or "",
            "enabled": (attrs.get("enabled") or "false").lower() == "true",
            "focused": (attrs.get("focused") or "false").lower() == "true",
            "selected": (attrs.get("selected") or "false").lower() == "true",
            "checked": (attrs.get("checked") or "false").lower() == "true",
            "depth": depth,
        }
        # Keep nodes with at least one identifying signal, plus an enabled flag.
        if any([flat["text"], flat["accessibility_text"], flat["hint_text"],
                flat["title"], flat["resource_id"]]):
            out.append(flat)
    for child in node.get("children") or []:
        _walk_screen(child, out, depth + 1)


def _parse_bounds(b: str) -> tuple[int, int, int, int] | None:
    """`[x1,y1][x2,y2]` → (x, y, w, h) in screen pixels. None if unparseable."""
    m = re.match(r"\[(-?\d+),(-?\d+)\]\[(-?\d+),(-?\d+)\]", b or "")
    if not m:
        return None
    x1, y1, x2, y2 = map(int, m.groups())
    return x1, y1, max(0, x2 - x1), max(0, y2 - y1)


def _node_label(n: dict) -> str:
    """Human-readable label — first non-empty among text / a11yText / title / hint."""
    for k in ("text", "accessibility_text", "title", "hint_text"):
        if n.get(k):
            return n[k]
    return ""


def _build_screen_modules(nodes: list[dict]) -> tuple[list[dict], dict]:
    """Classify flattened nodes into modules + a screen-level summary.

    iOS Maestro hierarchy doesn't expose XCUIElement classes; we lean on
    field semantics: hintText → input, text+enabled → CTA candidate,
    selected toggling at low y → likely tab/segmented control. Bounded
    output (top N) to keep payload tractable.
    """
    inputs: list[dict] = []
    ctas: list[dict] = []
    selected_nodes: list[dict] = []
    label_only: list[dict] = []

    for n in nodes:
        bounds = _parse_bounds(n["bounds"])
        # Skip invisible / zero-area + iOS status bar (y < 50 covers signal /
        # battery / time / wifi indicators that aren't part of the app's UI).
        if bounds and (bounds[2] == 0 or bounds[3] == 0):
            continue
        if bounds and bounds[1] < 50:
            continue

        # Inputs: hint text reliably means a TextField/EditText.
        if n["hint_text"] or (n["focused"] and not n["text"]):
            inputs.append({**n, "_bounds": bounds})
            continue

        # CTAs: text + enabled is the obvious case (UIControl-style).
        # ALSO promote leaf-ish nodes with meaningful text + reasonable bounds
        # to CTA candidates — SwiftUI / RN buttons often appear with enabled=false
        # at the leaf level even though they're tappable. Threshold of 24x24 px
        # filters decorative micro-labels but keeps real buttons.
        # Noise filter drops asset-name labels (bg_*, *_filled) and placeholder
        # text ("--", single ASCII chars, pure digits/currency).
        label = n["text"] or n["accessibility_text"]
        if label and not _is_noise_text(label):
            if n["enabled"]:
                ctas.append({**n, "_bounds": bounds})
                continue
            if bounds and bounds[2] >= 24 and bounds[3] >= 24:
                ctas.append({**n, "_bounds": bounds, "_inferred": True})
                continue

        if n["selected"] and label:
            selected_nodes.append({**n, "_bounds": bounds})
        elif label:
            label_only.append({**n, "_bounds": bounds})

    # Dedup CTAs by label (keep first; iOS often nests duplicates per layer).
    seen = set()
    unique_ctas = []
    for c in ctas:
        key = _node_label(c)
        if key in seen:
            continue
        seen.add(key)
        unique_ctas.append(c)

    modules: list[dict] = []

    if inputs:
        fields = [
            {
                "label": _node_label(f) or f.get("hint_text") or "(unnamed input)",
                "hint": f.get("hint_text"),
                "resource_id": f.get("resource_id") or None,
            }
            for f in inputs[:10]
        ]
        modules.append({
            "kind": "form",
            "name": "screen_inputs",
            "selectors": {"fields": fields},
            "candidate_tcs": [
                "所有必填欄位為空時送出，應顯示必填錯誤",
                "輸入超長字串應安全處理（截斷或拒絕）",
                "Email / 數字等格式欄位輸入錯誤格式應提示",
                "鍵盤遮蔽輸入框時應 scroll 至可見",
            ],
        })

    for cta in unique_ctas[:15]:
        label = _node_label(cta)
        modules.append({
            "kind": "cta",
            "name": _slug(label, "cta"),
            "selectors": {
                "text": label,
                "resource_id": cta.get("resource_id") or None,
            },
            "metadata": {
                "label_text": label,
                "enabled": cta.get("enabled"),
                "bounds": cta.get("_bounds"),
            },
            "candidate_tcs": [
                f"點擊「{label}」應觸發對應動作（導頁／open modal／API call）",
                f"「{label}」在 loading 狀態下應禁用以避免重複觸發",
            ],
        })

    # Tab bar / segmented control inference: ≥ 2 selected-capable items at
    # similar y position near top or bottom of screen.
    if len(selected_nodes) >= 2:
        ys = sorted({n["_bounds"][1] for n in selected_nodes if n["_bounds"]})
        if ys:
            # cluster: nodes within 30px of each other → same row
            groups: list[list[dict]] = []
            for n in selected_nodes:
                placed = False
                if not n["_bounds"]:
                    continue
                for g in groups:
                    if any(abs(n["_bounds"][1] - m["_bounds"][1]) <= 30 for m in g):
                        g.append(n)
                        placed = True
                        break
                if not placed:
                    groups.append([n])
            for g in groups:
                if len(g) >= 2:
                    labels = [_node_label(m) for m in g if _node_label(m)]
                    if not labels:
                        continue
                    modules.append({
                        "kind": "tab_bar",
                        "name": "tab_bar",
                        "tabs": [{"label": l} for l in labels],
                        "candidate_tcs": [
                            f"切換每個 tab（共 {len(labels)} 個）應顯示對應內容",
                            "tab 選中狀態視覺應正確（高亮 / icon 變色）",
                            "切換 tab 後再切回原 tab 狀態應保留（如 scroll 位置）",
                        ],
                    })

    summary = {
        "input_count": len(inputs),
        "interactive_count": len(unique_ctas),
        "selected_count": len(selected_nodes),
        "label_only_count": len(label_only),
        "total_meaningful_nodes": len(inputs) + len(unique_ctas) + len(selected_nodes) + len(label_only),
    }
    return modules, summary
