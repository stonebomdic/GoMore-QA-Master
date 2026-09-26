"""v0.9.6 — verified (artifact-backed) critical points in qa_plan/verify_plan.

Two layers coexist:
  - attested  : CP with only `verification_hint` (legacy; substring match)
  - verified  : CP with an `assert` block (typed; tool loads the artifact)

This file covers the qa_plan schema side (Task 2) and the verify_plan
dispatch/tiering/strict side (Task 3). Backward-compat: CPs without
`assert` must behave exactly as before — that is guarded by the existing
test_qa_plan*.py suites; here we only add the new surface.
"""
from __future__ import annotations

import json

import pytest

from gomore_qa_master.tools import qa_plan
from gomore_qa_master.tools.qa_plan import (
    _reset_cache_for_tests,
    qa_plan_tool,
    verify_plan_tool,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    _reset_cache_for_tests()
    yield
    _reset_cache_for_tests()


# ==== Task 2 — qa_plan accepts `assert` + `strict` =====================

def test_cp_retains_assert_block():
    result = qa_plan_tool({
        "task": "login regression",
        "critical_points": [
            {"id": "CP1", "description": "login passes",
             "assert": {"type": "test_passed", "test_id": "test_login"}},
        ],
    })
    assert "error" not in result
    cp = result["critical_points"][0]
    assert cp["assert"] == {"type": "test_passed", "test_id": "test_login"}


def test_cp_without_assert_has_no_assert_key():
    result = qa_plan_tool({
        "task": "t",
        "critical_points": [{"description": "something works"}],
    })
    assert "error" not in result
    assert "assert" not in result["critical_points"][0]


def test_assert_must_be_dict():
    result = qa_plan_tool({
        "task": "t",
        "critical_points": [{"description": "x", "assert": "not-a-dict"}],
    })
    assert result["error"] == "bad_critical_points"


def test_assert_must_have_type():
    result = qa_plan_tool({
        "task": "t",
        "critical_points": [{"description": "x", "assert": {"test_id": "y"}}],
    })
    assert result["error"] == "bad_critical_points"


def test_strict_declared_at_creation_is_stored():
    result = qa_plan_tool({
        "task": "t", "strict": True,
        "critical_points": [
            {"description": "x", "assert": {"type": "test_passed", "test_id": "a"}},
        ],
    })
    assert result["strict"] is True


def test_strict_defaults_false():
    result = qa_plan_tool({"task": "t", "critical_points": ["x"]})
    assert result["strict"] is False


def test_assert_and_strict_survive_json_roundtrip():
    plan = qa_plan._Plan(
        plan_id="abc123abc123", task="t", kind=None,
        critical_points=[qa_plan._CriticalPoint(
            cp_id="CP1", description="x", verification_hint="x",
            assertion={"type": "test_passed", "test_id": "a"})],
        created_at=qa_plan._now(), expires_at=qa_plan._now(),
        strict=True,
    )
    restored = qa_plan._plan_from_json(json.loads(json.dumps(qa_plan._plan_to_json(plan))))
    assert restored is not None
    assert restored.strict is True
    assert restored.critical_points[0].assertion == {"type": "test_passed", "test_id": "a"}


# ==== Task 3 — verify_plan verified/attested + strict + anti-forgery ====

def _make_report(tmp_path, rows):
    p = tmp_path / "report.json"
    p.write_text(json.dumps({"tests": rows}), encoding="utf-8")
    return str(p)


def test_verified_red_team_failed_test_is_unsatisfied(tmp_path):
    """The core fix: a CP asserting test_passed on a FAILED test must be
    unsatisfied — whereas a legacy substring hint would say satisfied."""
    report = _make_report(tmp_path, [
        {"nodeid": "tests/test_login.py::test_login", "outcome": "failed"}])
    plan = qa_plan_tool({
        "task": "t",
        "critical_points": [
            {"id": "CP1", "description": "login passes",
             "assert": {"type": "test_passed", "test_id": "test_login"}},
        ],
    })
    res = verify_plan_tool({"plan_id": plan["plan_id"], "auto_discover": True,
                            "report_path": report})
    cp = res["checklist"][0]
    assert cp["tier"] == "verified"
    assert cp["satisfied"] is False
    assert cp["actual"]["outcomes"] == ["failed"]
    assert res["status"] == "failed"


def test_verified_ignores_forged_host_evidence(tmp_path):
    """Anti-forgery: even if the host passes evidence claiming success, the
    verified CP is judged against the on-disk artifact (failed)."""
    report = _make_report(tmp_path, [
        {"nodeid": "tests/test_login.py::test_login", "outcome": "failed"}])
    plan = qa_plan_tool({
        "task": "t",
        "critical_points": [
            {"id": "CP1", "description": "login passes",
             "assert": {"type": "test_passed", "test_id": "test_login"}},
        ],
    })
    res = verify_plan_tool({
        "plan_id": plan["plan_id"],
        "evidence": [{"nodeid": "tests/test_login.py::test_login",
                      "outcome": "passed"}],  # forged
        "report_path": report, "auto_discover": True,
    })
    assert res["checklist"][0]["satisfied"] is False


def test_attested_and_verified_coexist(tmp_path):
    report = _make_report(tmp_path, [
        {"nodeid": "tests/test_login.py::test_login", "outcome": "passed"}])
    plan = qa_plan_tool({
        "task": "t",
        "critical_points": [
            {"id": "CP1", "description": "verified one",
             "assert": {"type": "test_passed", "test_id": "test_login"}},
            {"id": "CP2", "description": "attested one",
             "verification_hint": "manual sign-off done"},
        ],
    })
    res = verify_plan_tool({
        "plan_id": plan["plan_id"], "auto_discover": True, "report_path": report,
        "evidence": ["manual sign-off done"],
    })
    tiers = {cp["id"]: cp["tier"] for cp in res["checklist"]}
    assert tiers == {"CP1": "verified", "CP2": "attested"}
    assert res["verification"]["verified"] == 1
    assert res["verification"]["attested"] == 1
    assert res["verification"]["verified_satisfied"] == 1
    assert res["status"] == "passed"


def test_verified_missing_artifact_fails_closed(tmp_path):
    plan = qa_plan_tool({
        "task": "t",
        "critical_points": [
            {"id": "CP1", "description": "x",
             "assert": {"type": "test_passed", "test_id": "test_login"}},
        ],
    })
    # auto_discover on, but point at a non-existent report
    res = verify_plan_tool({"plan_id": plan["plan_id"], "auto_discover": True,
                            "report_path": str(tmp_path / "nope.json")})
    cp = res["checklist"][0]
    assert cp["satisfied"] is False
    assert cp["actual"]["error"] == "artifact_missing"


def test_strict_from_plan_cannot_be_loosened(tmp_path):
    """Plan created strict → verify passing strict=False still evaluates strict.
    A mix of verified+attested under strict → incomplete even if all satisfied."""
    report = _make_report(tmp_path, [
        {"nodeid": "tests/test_login.py::test_login", "outcome": "passed"}])
    plan = qa_plan_tool({
        "task": "t", "strict": True,
        "critical_points": [
            {"id": "CP1", "description": "verified",
             "assert": {"type": "test_passed", "test_id": "test_login"}},
            {"id": "CP2", "description": "attested", "verification_hint": "ok"},
        ],
    })
    res = verify_plan_tool({
        "plan_id": plan["plan_id"], "strict": False,  # attempt to loosen
        "auto_discover": True, "report_path": report, "evidence": ["ok"],
    })
    # both satisfied, but CP2 is attested → strict withholds "passed"
    assert res["summary"]["satisfied"] == 2
    assert res["status"] == "incomplete"


def test_verify_time_strict_can_tighten(tmp_path):
    """Plan non-strict, verify strict=True → strict applies."""
    _make_report(tmp_path, [
        {"nodeid": "tests/test_login.py::test_login", "outcome": "passed"}])
    plan = qa_plan_tool({
        "task": "t",
        "critical_points": [
            {"id": "CP1", "description": "attested", "verification_hint": "ok"},
        ],
    })
    res = verify_plan_tool({"plan_id": plan["plan_id"], "strict": True,
                            "evidence": ["ok"]})
    assert res["status"] == "incomplete"  # attested can't pass under strict
