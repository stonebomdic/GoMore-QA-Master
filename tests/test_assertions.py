"""Unit tests for the v0.9.6 typed-assertion checker.

`assertions.evaluate(assertion, artifact)` is a pure function: given a
type-checked assertion dict and an already-loaded authoritative artifact
(pytest-json-report dict, or scan-results dict), it returns
`(satisfied: bool, actual: dict)`. No I/O, no env — all disk access lives
in qa_plan.py, so this layer is 100% locally testable.

Coverage:
  - test_passed / test_outcome against a report's `tests` list
  - test_id match modes (exact / ::suffix default; substring opt-in)
  - the red-team case: a FAILED test must NOT satisfy test_passed
  - finding_present / finding_absent against a scan's `findings` list
  - fail-closed: a missing artifact never satisfies (incl. finding_absent)
  - unknown assert types return a structured error, never raise
"""
from __future__ import annotations

from gomore_qa_master.tools import assertions

# ---- fixtures ----------------------------------------------------------

def _report(*rows):
    return {"tests": list(rows)}


def _row(nodeid, outcome):
    return {"nodeid": nodeid, "outcome": outcome}


def _scan(*findings):
    return {"findings": list(findings)}


# ---- test_passed / test_outcome ---------------------------------------

def test_passed_satisfied_when_outcome_passed():
    report = _report(_row("tests/test_login.py::test_login", "passed"))
    ok, actual = assertions.evaluate(
        {"type": "test_passed", "test_id": "test_login"}, report)
    assert ok is True
    assert actual["outcomes"] == ["passed"]


def test_passed_NOT_satisfied_when_outcome_failed():
    """Red-team: the substring bug let a failed test count as satisfied.
    The typed assertion must return False."""
    report = _report(_row("tests/test_login.py::test_login", "failed"))
    ok, actual = assertions.evaluate(
        {"type": "test_passed", "test_id": "test_login"}, report)
    assert ok is False
    assert actual["outcomes"] == ["failed"]


def test_passed_not_found_is_unsatisfied():
    report = _report(_row("tests/test_other.py::test_other", "passed"))
    ok, actual = assertions.evaluate(
        {"type": "test_passed", "test_id": "test_login"}, report)
    assert ok is False
    assert actual["error"] == "not_found"


def test_passed_missing_test_id_is_bad_assertion():
    ok, actual = assertions.evaluate({"type": "test_passed"}, _report())
    assert ok is False
    assert actual["error"] == "bad_assertion"


def test_outcome_expected_failed():
    report = _report(_row("tests/test_x.py::test_x", "failed"))
    ok, _ = assertions.evaluate(
        {"type": "test_outcome", "test_id": "test_x", "expected": "failed"}, report)
    assert ok is True


def test_outcome_multiple_rows_all_must_match():
    """parametrize: one passed + one failed under the same test_id must NOT
    satisfy test_passed (avoid 'one green branch = pass')."""
    report = _report(
        _row("tests/t.py::test_p[a]", "passed"),
        _row("tests/t.py::test_p[b]", "failed"),
    )
    ok, actual = assertions.evaluate(
        {"type": "test_passed", "test_id": "test_p"}, report)
    assert ok is False
    assert actual["matched"] == 2


# ---- test_id match modes ----------------------------------------------

def test_match_exact_nodeid():
    report = _report(_row("tests/test_login.py::test_login", "passed"))
    ok, _ = assertions.evaluate(
        {"type": "test_passed", "test_id": "tests/test_login.py::test_login"}, report)
    assert ok is True


def test_match_suffix():
    report = _report(_row("tests/test_login.py::test_login", "passed"))
    ok, _ = assertions.evaluate(
        {"type": "test_passed", "test_id": "test_login"}, report)
    assert ok is True


def test_default_match_does_not_substring_collide():
    """Default (exact-or-suffix) must NOT match test_login against
    test_login_redirect — that collision is why bare substring is opt-in."""
    report = _report(_row("tests/test_login.py::test_login_redirect", "passed"))
    ok, actual = assertions.evaluate(
        {"type": "test_passed", "test_id": "test_login"}, report)
    assert ok is False
    assert actual["error"] == "not_found"


def test_substring_mode_is_opt_in():
    report = _report(_row("tests/test_login.py::test_login_redirect", "passed"))
    ok, _ = assertions.evaluate(
        {"type": "test_passed", "test_id": "test_login", "match": "substring"}, report)
    assert ok is True


# ---- finding_present / finding_absent ---------------------------------

def _finding(rule_id, endpoint):
    return {"rule_id": rule_id, "endpoint": endpoint, "severity": "high"}


def test_finding_present_satisfied():
    scan = _scan(_finding("OWASP-API1-BOLA", "/orders/{id}"))
    ok, actual = assertions.evaluate(
        {"type": "finding_present", "rule_id": "OWASP-API1-BOLA"}, scan)
    assert ok is True
    assert actual["hits"] == 1


def test_finding_present_matches_rule_class_id_by_prefix():
    """Callers assert on the rule-class id; findings carry a specific sub-id.
    The class id must match its sub-ids by prefix."""
    scan = _scan(_finding("OWASP-API1-BOLA-CrossUserDataExposure", "/orders/{id}"))
    ok, actual = assertions.evaluate(
        {"type": "finding_present", "rule_id": "OWASP-API1-BOLA"}, scan)
    assert ok is True
    assert actual["hits"] == 1


def test_finding_absent_prefix_aware():
    """A BOLA sub-id present means finding_absent(OWASP-API1-BOLA) is NOT satisfied."""
    scan = _scan(_finding("OWASP-API1-BOLA-CrossUserDataExposure", "/orders/{id}"))
    ok, _ = assertions.evaluate(
        {"type": "finding_absent", "rule_id": "OWASP-API1-BOLA"}, scan)
    assert ok is False


def test_finding_present_endpoint_scoped():
    scan = _scan(_finding("OWASP-API1-BOLA", "/orders/{id}"))
    ok, _ = assertions.evaluate(
        {"type": "finding_present", "rule_id": "OWASP-API1-BOLA",
         "endpoint": "/users/{id}"}, scan)
    assert ok is False


def test_finding_absent_satisfied_when_clean():
    scan = _scan(_finding("OWASP-API8-HEADERS", "/data"))
    ok, _ = assertions.evaluate(
        {"type": "finding_absent", "rule_id": "OWASP-API1-BOLA",
         "endpoint": "/safe/me/orders"}, scan)
    assert ok is True


def test_finding_absent_not_satisfied_when_present():
    scan = _scan(_finding("OWASP-API1-BOLA", "/orders/{id}"))
    ok, _ = assertions.evaluate(
        {"type": "finding_absent", "rule_id": "OWASP-API1-BOLA"}, scan)
    assert ok is False


# ---- fail-closed on missing artifact ----------------------------------

def test_missing_artifact_never_satisfies_test_passed():
    ok, actual = assertions.evaluate(
        {"type": "test_passed", "test_id": "test_login"}, None)
    assert ok is False
    assert actual["error"] == "artifact_missing"


def test_missing_artifact_never_satisfies_finding_absent():
    """The dangerous case: 'no finding = pass' must NOT be satisfied when the
    scan artifact is missing (absence of evidence != evidence of absence)."""
    ok, actual = assertions.evaluate(
        {"type": "finding_absent", "rule_id": "OWASP-API1-BOLA"}, None)
    assert ok is False
    assert actual["error"] == "artifact_missing"


# ---- unknown type ------------------------------------------------------

def test_unknown_assert_type_returns_error_not_raise():
    ok, actual = assertions.evaluate({"type": "teleport"}, _report())
    assert ok is False
    assert actual["error"] == "unknown_assert_type"


# ---- artifact_kind_for -------------------------------------------------

def test_artifact_kind_for():
    assert assertions.artifact_kind_for("test_passed") == "report"
    assert assertions.artifact_kind_for("finding_absent") == "scan"
    assert assertions.artifact_kind_for("nope") is None
