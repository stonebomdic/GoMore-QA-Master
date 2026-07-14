"""v0.9.6 — typed-assertion checker (pure functions, no I/O).

`verify_plan` calls in here for any critical point that carries an
`assert` block. Input is an *already-loaded* authoritative artifact dict
(a pytest-json-report, or a scan-results doc); output is
`(satisfied, actual)`. We deliberately touch neither the filesystem nor
env so this layer stays 100% unit-testable — all disk access lives in
qa_plan.py.

The whole point of the verified tier is that the host LLM cannot fake
it: it authors the assertion, but the *evidence* comes from an artifact
the tool loads itself. So the checks here are strict and fail-closed —
a missing artifact never satisfies anything (including `finding_absent`,
where "no evidence" must not be mistaken for "no vulnerability").
"""
from __future__ import annotations

from typing import Any


# ---- report (functional test) family ----------------------------------

def _iter_test_rows(report: dict[str, Any]) -> list[dict]:
    tests = report.get("tests")
    return [t for t in tests if isinstance(t, dict)] if isinstance(tests, list) else []


def _match_test_id(nodeid: str, test_id: str, mode: str) -> bool:
    if mode == "substring":  # opt-in — looser, can collide (test_login vs test_login_redirect)
        return test_id in nodeid
    # default: exact full nodeid, or the trailing function name — including
    # its parametrized variants (test_p matches test_p[a]) but NOT a
    # different function that merely shares a prefix (test_login_redirect).
    if nodeid == test_id:
        return True
    func = nodeid.split("::")[-1]
    return func == test_id or func.startswith(test_id + "[")


def _check_test_outcome(assertion: dict, report: dict, expected: str) -> tuple[bool, dict]:
    test_id = str(assertion.get("test_id") or "").strip()
    if not test_id:
        return False, {"error": "bad_assertion", "hint": "test_id required"}
    mode = str(assertion.get("match") or "exact")
    rows = [r for r in _iter_test_rows(report)
            if _match_test_id(str(r.get("nodeid", "")), test_id, mode)]
    if not rows:
        return False, {"error": "not_found", "test_id": test_id}
    # Multiple matches (e.g. parametrized cases): ALL must meet `expected`,
    # so one green branch can't carry a red sibling.
    outcomes = [str(r.get("outcome")) for r in rows]
    ok = all(o == expected for o in outcomes)
    return ok, {"test_id": test_id, "matched": len(rows),
                "outcomes": outcomes, "expected": expected}


# ---- scan (security) family -------------------------------------------

def _finding_matches(f: dict, rule_id: str, endpoint: str | None) -> bool:
    # Findings carry a specific sub-id (e.g. "OWASP-API1-BOLA-CrossUserData"),
    # but callers naturally assert on the rule-class id ("OWASP-API1-BOLA").
    # Match the class id or any of its sub-ids by prefix; exact still works.
    fid = str(f.get("rule_id", ""))
    if not (fid == rule_id or fid.startswith(rule_id + "-")):
        return False
    if endpoint and str(f.get("endpoint")) != endpoint:
        return False
    return True


def _check_finding(assertion: dict, scan: dict, present: bool) -> tuple[bool, dict]:
    rule_id = str(assertion.get("rule_id") or "").strip()
    if not rule_id:
        return False, {"error": "bad_assertion", "hint": "rule_id required"}
    endpoint = assertion.get("endpoint")
    findings = scan.get("findings")
    if not isinstance(findings, list):
        return False, {"error": "artifact_missing",
                       "hint": "no findings[] in scan artifact"}
    hits = [f for f in findings
            if isinstance(f, dict) and _finding_matches(f, rule_id, endpoint)]
    satisfied = (len(hits) > 0) if present else (len(hits) == 0)
    return satisfied, {"rule_id": rule_id, "endpoint": endpoint, "hits": len(hits)}


# ---- dispatch ----------------------------------------------------------

# Each entry: (artifact_kind, checker). artifact_kind tells verify_plan
# which authoritative artifact to load and hand in as `artifact`.
_DISPATCH = {
    "test_passed": ("report",
                    lambda a, art: _check_test_outcome(a, art, "passed")),
    "test_outcome": ("report",
                     lambda a, art: _check_test_outcome(a, art, str(a.get("expected") or "passed"))),
    "finding_absent": ("scan",
                       lambda a, art: _check_finding(a, art, present=False)),
    "finding_present": ("scan",
                        lambda a, art: _check_finding(a, art, present=True)),
}


def artifact_kind_for(assert_type: str) -> str | None:
    """Return which artifact ("report" | "scan") an assertion type needs,
    or None for an unknown type. Lets verify_plan load only what it needs."""
    entry = _DISPATCH.get(assert_type)
    return entry[0] if entry else None


def evaluate(assertion: dict[str, Any], artifact: dict[str, Any] | None) -> tuple[bool, dict]:
    """Evaluate a typed assertion against a loaded artifact.

    Returns (satisfied, actual). `artifact is None` means the tool failed
    to load the authoritative artifact — fail-closed: never satisfied.
    Unknown assert types return a structured error rather than raising.
    """
    a_type = str(assertion.get("type") or "").strip()
    entry = _DISPATCH.get(a_type)
    if entry is None:
        return False, {"error": "unknown_assert_type", "type": a_type}
    if artifact is None:
        return False, {"error": "artifact_missing", "type": a_type}
    return entry[1](assertion, artifact)
