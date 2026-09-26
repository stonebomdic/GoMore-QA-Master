"""Unit tests for `security_rules.bola` — both BOLA (API1) and FLA (API5)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests

from gomore_qa_master.security_rules import (
    APIClient,
    AuthPair,
    OperationContext,
    Severity,
    bola_rule,
    function_authz_rule,
)
from gomore_qa_master.security_rules.bola import (
    _count_path_params,
    _fingerprint,
    _matches_admin_pattern,
    _substitute_first_path_param,
)


def _fake_response(*, status: int = 200, text: str = ""):
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    return resp


def _op(path: str, *, method: str = "GET", requires_auth: bool = True) -> OperationContext:
    return OperationContext(
        method=method, path=path, operation_id=None,
        requires_auth=requires_auth, spec={},
    )


def _client(*, auth_pair: AuthPair | None = None, response=None,
            responses: list | None = None) -> APIClient:
    c = APIClient(base_url="http://test", auth_pair=auth_pair)
    if responses:
        c.request = MagicMock(side_effect=responses)
    else:
        c.request = MagicMock(return_value=response or _fake_response(status=200))
    return c


def _pair(**overrides) -> AuthPair:
    defaults = {
        "user_a_token": "alice-token",
        "user_b_token": "bob-token",
        "bola_test_ids": {"user_a": [1, 3], "user_b": [2]},
    }
    defaults.update(overrides)
    return AuthPair(**defaults)


# ---- helpers --------------------------------------------------------------

@pytest.mark.parametrize("path,expected", [
    ("/orders/{id}", 1),
    ("/users/{user_id}/orders/{order_id}", 2),
    ("/health", 0),
    ("/a/{b}/c/{d}/e/{f}", 3),
])
def test_count_path_params(path, expected):
    assert _count_path_params(path) == expected


def test_substitute_first_path_param():
    assert _substitute_first_path_param("/orders/{id}", 5) == "/orders/5"
    assert _substitute_first_path_param("/no/params", 5) == "/no/params"
    # Multi-param: only the first substituted
    assert _substitute_first_path_param("/users/{u}/orders/{o}", 9) == "/users/9/orders/{o}"


def test_matches_admin_pattern():
    assert _matches_admin_pattern("/admin/users", ["/admin/"]) is True
    assert _matches_admin_pattern("/safe/admin/users", ["/admin/"]) is True
    assert _matches_admin_pattern("/users", ["/admin/"]) is False
    assert _matches_admin_pattern("/internal/reports", ["/admin/", "/internal/"]) is True


# ---- BOLA: applies_to ----------------------------------------------------

def test_bola_applies_to_single_path_param_get():
    assert bola_rule.applies_to(_op("/orders/{id}")) is True


def test_bola_skips_no_auth_op():
    assert bola_rule.applies_to(_op("/orders/{id}", requires_auth=False)) is False


def test_bola_skips_post():
    assert bola_rule.applies_to(_op("/orders/{id}", method="POST")) is False


def test_bola_skips_no_path_param():
    """No path param → nothing to substitute → can't BOLA-test."""
    assert bola_rule.applies_to(_op("/orders")) is False


def test_bola_skips_multi_path_param_for_now():
    """Filed for v0.8.1. Multi-param substitution needs richer
    strategy than 'replace the only one with target id'."""
    assert bola_rule.applies_to(_op("/users/{u}/orders/{o}")) is False


# ---- BOLA: skip-with-info conditions -------------------------------------

def test_bola_skips_with_info_when_no_auth_pair():
    c = _client(auth_pair=None)
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    assert len(findings) == 1
    assert findings[0].rule_id.endswith("-Skipped")
    assert findings[0].severity == Severity.INFO
    assert findings[0].evidence["reason"] == "no_auth_pair_provided"
    c.request.assert_not_called()


def test_bola_skips_with_info_when_no_bola_ids():
    c = _client(auth_pair=AuthPair("a", "b", bola_test_ids=None))
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    assert findings[0].evidence["reason"] == "no_bola_test_ids"


def test_bola_skips_with_info_when_one_side_empty():
    c = _client(auth_pair=AuthPair("a", "b",
                                   bola_test_ids={"user_a": [1], "user_b": []}))
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    assert findings[0].evidence["reason"] == "incomplete_bola_test_ids"


# ---- BOLA: core behavior ---------------------------------------------------
#
# Every direction now runs (up to) 4 probes, always in this order:
#   1. owner baseline  — target owner's own token, same path (done FIRST)
#   2. actor probe     — actor's own token, same path (non-2xx => no
#                        finding, same as always)
#   3. unauth probe    — token=None, same path
#   4. actor control   — actor's own token, actor's OWN first object id
#
# To keep two-direction tests simple, the "other" direction is usually
# made quiet by having its actor probe return a non-2xx status (403),
# which stops it after exactly 2 calls (owner baseline + actor probe).

def test_bola_two_directions_real_leak_yields_two_critical_findings():
    """Both directions: actor's illicit read matches the owner's own
    legitimate read, and an anonymous caller is blocked — the textbook
    cross-user-leak signature — so both stay CRITICAL."""
    c = _client(
        auth_pair=_pair(),
        responses=[
            # -- direction 1: alice (actor) -> bob's id=2 (owner) --
            _fake_response(status=200, text='{"id":2,"item":"bob pizza"}'),   # 1 owner baseline
            _fake_response(status=200, text='{"id":2,"item":"bob pizza"}'),   # 2 actor probe (== owner)
            _fake_response(status=403),                                        # 3 unauth (blocked)
            _fake_response(status=200, text='{"id":1,"item":"alice coffee"}'),  # 4 actor control
            # -- direction 2: bob (actor) -> alice's id=1 (owner) --
            _fake_response(status=200, text='{"id":1,"item":"alice coffee"}'),  # 1 owner baseline
            _fake_response(status=200, text='{"id":1,"item":"alice coffee"}'),  # 2 actor probe (== owner)
            _fake_response(status=403),                                        # 3 unauth (blocked)
            _fake_response(status=200, text='{"id":2,"item":"bob pizza"}'),    # 4 actor control
        ],
    )
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    crit = [f for f in findings if f.severity == Severity.CRITICAL]
    assert len(crit) == 2
    assert all(f.rule_id.endswith("-CrossUserDataExposure") for f in crit)
    assert {f.evidence["actor"] for f in crit} == {"user_a", "user_b"}
    assert {f.evidence["target_owner"] for f in crit} == {"user_a", "user_b"}
    for f in crit:
        assert f.evidence["actor_fingerprint"] == f.evidence["owner_fingerprint"]
        assert f.evidence["unauth_blocked"] is True


def test_bola_safe_endpoint_403_yields_no_findings():
    c = _client(
        auth_pair=_pair(),
        responses=[
            _fake_response(status=200),   # dir1 owner baseline (moot — actor never hits)
            _fake_response(status=403),   # dir1 actor probe
            _fake_response(status=200),   # dir2 owner baseline (moot)
            _fake_response(status=403),   # dir2 actor probe
        ],
    )
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    assert findings == []


def test_bola_404_treated_as_safe():
    """404 = id doesn't exist for this user → no leak."""
    c = _client(
        auth_pair=_pair(),
        responses=[
            _fake_response(status=200),
            _fake_response(status=404),
            _fake_response(status=200),
            _fake_response(status=404),
        ],
    )
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    assert findings == []


def test_bola_one_direction_leaks_other_quiet():
    """Asymmetric vuln: alice can read bob's order but not vice versa."""
    c = _client(
        auth_pair=_pair(),
        responses=[
            # direction 1: real leak
            _fake_response(status=200, text='{"item":"bob"}'),   # owner baseline
            _fake_response(status=200, text='{"item":"bob"}'),   # actor probe (== owner)
            _fake_response(status=403),                            # unauth blocked
            _fake_response(status=200, text='{"item":"alice-own"}'),  # actor control
            # direction 2: quiet
            _fake_response(status=200),   # owner baseline (moot)
            _fake_response(status=403),   # actor probe: blocked
        ],
    )
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    crit = [f for f in findings if f.severity == Severity.CRITICAL]
    assert len(crit) == 1
    assert crit[0].evidence["actor"] == "user_a"
    assert crit[0].evidence["target_owner"] == "user_b"


def test_bola_probe_sequence_uses_get_and_correct_tokens_in_order():
    """The 4-probe diff always issues, in this exact order: owner
    baseline, actor probe, unauth probe (token=None), actor control —
    all as a literal "GET", regardless of `op.method` (which is "GET"
    here anyway, since applies_to() enforces that)."""
    c = _client(
        auth_pair=_pair(),
        responses=[
            _fake_response(status=200, text='{"id":2,"item":"bob pizza"}'),   # owner baseline
            _fake_response(status=200, text='{"id":2,"item":"bob pizza"}'),   # actor probe
            _fake_response(status=403),                                        # unauth probe
            _fake_response(status=200, text='{"id":1,"item":"alice coffee"}'),  # actor control
            _fake_response(status=200),   # dir2 owner baseline (moot)
            _fake_response(status=403),   # dir2 actor probe: blocked, keeps dir2 quiet
        ],
    )
    bola_rule.execute(c, _op("/orders/{id}"))
    calls = [(call.args[0], call.args[1], call.kwargs.get("token", "<unset>"))
             for call in c.request.call_args_list]
    assert calls[0] == ("GET", "/orders/2", "bob-token")     # 1 owner baseline
    assert calls[1] == ("GET", "/orders/2", "alice-token")   # 2 actor probe
    assert calls[2] == ("GET", "/orders/2", None)            # 3 unauth probe
    assert calls[3] == ("GET", "/orders/1", "alice-token")   # 4 actor control (alice's OWN id=1)


def test_bola_finding_carries_remediation_hint():
    c = _client(
        auth_pair=_pair(),
        responses=[
            _fake_response(status=200, text='{"item":"x"}'),   # owner baseline
            _fake_response(status=200, text='{"item":"x"}'),   # actor probe (== owner)
            _fake_response(status=403),                          # unauth blocked
            _fake_response(status=200, text='{"item":"y"}'),   # actor control
            _fake_response(status=200),
            _fake_response(status=403),
        ],
    )
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    crit = next(f for f in findings if f.severity == Severity.CRITICAL)
    assert "object-level authorization" in crit.title
    assert "owner" in crit.remediation_hint
    assert crit.evidence["status_code"] == 200


# ---- BOLA: fingerprint-driven verdicts (A′ design) -------------------------
#
# Once the actor's probe hits 2xx, `_probe_direction` gathers owner /
# unauth / actor-control fingerprints (`_fingerprint`: JSON-normalized
# + volatile-field-stripped sha256, falling back to whitespace-
# stripped text) before drawing ANY conclusion. See the module
# docstring for the full decision table.

def test_bola_public_content_yields_high_not_info():
    """An unauthenticated request gets the SAME content as the actor's
    — the spec declares auth required but it isn't enforced at all,
    for ANYONE, not just the actor. This is NOT a dismissal: silently
    downgrading it to INFO would be a false-negative regression versus
    the pre-diff design (bare 2xx was at least CRITICAL). It's HIGH —
    a `broken_auth`-shaped defect, layered under a BOLA probe. Only an
    explicit `bola_shared_endpoints` declaration gets to INFO."""
    body = '{"id":2,"name":"public catalog entry"}'
    c = _client(
        auth_pair=_pair(),
        responses=[
            _fake_response(status=200, text='{"id":2,"name":"owner-only view"}'),  # owner baseline
            _fake_response(status=200, text=body),   # actor probe
            _fake_response(status=200, text=body),   # unauth probe: matches actor => public
            _fake_response(status=200),   # dir2 owner baseline (moot)
            _fake_response(status=403),   # dir2 actor probe: blocked, quiet
        ],
    )
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    assert not any(f.severity == Severity.CRITICAL for f in findings)
    public = [f for f in findings if f.rule_id.endswith("-PublicContent")]
    assert len(public) == 1
    assert public[0].severity == Severity.HIGH
    assert public[0].evidence["actor_fingerprint"] == public[0].evidence["unauth_fingerprint"]
    assert public[0].evidence["unauth_status_code"] == 200
    assert "authentication" in public[0].title
    assert "bola_shared_endpoints" in public[0].remediation_hint


def test_bola_public_content_declared_shared_still_downgrades_to_info():
    """The ONLY way a genuinely-public object gets to INFO is an
    explicit `bola_shared_endpoints` declaration — not the content
    diff alone. Declaring it wins even over the PublicContent verdict."""
    body = '{"id":2,"name":"public catalog entry"}'
    pair = _pair(bola_shared_endpoints=["/orders/*"])
    c = _client(
        auth_pair=pair,
        responses=[
            _fake_response(status=200, text='{"id":2,"name":"owner-only view"}'),  # owner baseline
            _fake_response(status=200, text=body),   # actor probe
            _fake_response(status=200, text=body),   # declared-shared branch's own unauth probe
            _fake_response(status=200),   # dir2 owner baseline (moot)
            _fake_response(status=403),   # dir2 actor probe: blocked, quiet
        ],
    )
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    assert not any(f.severity in (Severity.CRITICAL, Severity.HIGH) for f in findings)
    declared = [f for f in findings if f.rule_id.endswith("-DeclaredShared")]
    assert len(declared) == 1
    assert declared[0].severity == Severity.INFO


def test_bola_scoped_view_when_actor_gets_own_data_back():
    """The endpoint ignores the path id entirely and just returns the
    caller's own data (e.g. from a JWT `sub` claim) — positive proof
    there's no cross-user id-selection happening, so this is INFO, not
    a leak."""
    actor_and_own_body = '{"id":1,"item":"alice coffee"}'
    owner_body = '{"id":2,"item":"bob pizza"}'
    c = _client(
        auth_pair=_pair(),
        responses=[
            _fake_response(status=200, text=owner_body),          # owner baseline
            _fake_response(status=200, text=actor_and_own_body),  # actor probe: got alice's OWN data
            _fake_response(status=403),                             # unauth blocked
            _fake_response(status=200, text=actor_and_own_body),  # actor control: matches actor probe
            _fake_response(status=200),
            _fake_response(status=403),
        ],
    )
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    assert not any(f.severity == Severity.CRITICAL for f in findings)
    scoped = [f for f in findings if f.rule_id.endswith("-ScopedView")]
    assert len(scoped) == 1
    assert scoped[0].severity == Severity.INFO
    assert scoped[0].evidence["actor_fingerprint"] == scoped[0].evidence["actor_own_fingerprint"]
    assert scoped[0].evidence["actor_fingerprint"] != scoped[0].evidence["owner_fingerprint"]


def test_bola_ambiguous_partial_diff_stays_critical_with_diff_class():
    """actor's content matches neither the owner's baseline nor the
    actor's own control response — ambiguous (could be a partial leak
    or an un-stripped volatile field). Ship it CRITICAL for a human to
    review rather than silently dropping it."""
    c = _client(
        auth_pair=_pair(),
        responses=[
            _fake_response(status=200, text='{"id":2,"item":"bob pizza"}'),     # owner baseline
            _fake_response(status=200, text='{"id":2,"item":"partial view"}'),  # actor probe
            _fake_response(status=403),                                          # unauth blocked
            _fake_response(status=200, text='{"id":1,"item":"alice coffee"}'),  # actor control
            _fake_response(status=200),
            _fake_response(status=403),
        ],
    )
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    crit = [f for f in findings if f.severity == Severity.CRITICAL]
    assert len(crit) == 1
    assert crit[0].rule_id.endswith("-CrossUserDataExposure")
    assert crit[0].evidence["diff_class"] == "partial_or_volatile"


def test_bola_volatile_field_alone_does_not_defeat_leak_detection():
    """Only a volatile field (timestamp) differs between the owner's
    and actor's responses — `_fingerprint` normalizes it away, so this
    is still recognized as the SAME content and flagged as a real
    leak, not miscategorized as an ambiguous partial diff."""
    owner_text = '{"id":2,"item":"bob pizza","timestamp":"2026-01-01T00:00:00Z"}'
    actor_text = '{"id":2,"item":"bob pizza","timestamp":"2026-09-26T08:00:00Z"}'
    assert _fingerprint(_fake_response(text=owner_text)) == _fingerprint(_fake_response(text=actor_text))
    c = _client(
        auth_pair=_pair(),
        responses=[
            _fake_response(status=200, text=owner_text),
            _fake_response(status=200, text=actor_text),
            _fake_response(status=403),
            _fake_response(status=200, text='{"id":1,"item":"alice coffee"}'),
            _fake_response(status=200),
            _fake_response(status=403),
        ],
    )
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    crit = [f for f in findings if f.severity == Severity.CRITICAL]
    assert len(crit) == 1
    assert crit[0].rule_id.endswith("-CrossUserDataExposure")
    assert "diff_class" not in crit[0].evidence


def test_bola_more_volatile_fields_also_stripped():
    """trace_id / correlation_id / expires_at / nonce are also
    stripped — not just timestamp/request_id/server_time/generated_at."""
    owner_text = ('{"id":2,"item":"bob pizza","trace_id":"aaa","correlation_id":"bbb",'
                  '"expires_at":"2026-01-01T00:00:00Z","nonce":"111"}')
    actor_text = ('{"id":2,"item":"bob pizza","trace_id":"zzz","correlation_id":"yyy",'
                  '"expires_at":"2027-01-01T00:00:00Z","nonce":"999"}')
    assert _fingerprint(_fake_response(text=owner_text)) == _fingerprint(_fake_response(text=actor_text))


def test_bola_unauth_2xx_different_content_treated_as_blocked_real_leak():
    """unauth gets a 2xx but DIFFERENT content (e.g. a login-page HTML
    redirect for an unauthenticated browser session) — that still
    counts as "blocked" for the purposes of this diff: the anonymous
    caller did NOT get the actor's content. Combined with actor's
    fingerprint matching the owner's, this is still the real leak
    signature, not `-PublicContent`."""
    login_page = "<html><body>Please log in</body></html>"
    shared_body = '{"id":2,"item":"bob pizza"}'
    c = _client(
        auth_pair=_pair(),
        responses=[
            _fake_response(status=200, text=shared_body),           # owner baseline
            _fake_response(status=200, text=shared_body),           # actor probe (== owner)
            _fake_response(status=200, text=login_page),            # unauth: 2xx but DIFFERENT
            _fake_response(status=200, text='{"id":1,"item":"a"}'),  # actor control
            _fake_response(status=200),
            _fake_response(status=403),
        ],
    )
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    crit = [f for f in findings if f.severity == Severity.CRITICAL]
    assert len(crit) == 1
    assert crit[0].rule_id.endswith("-CrossUserDataExposure")
    assert crit[0].evidence["unauth_blocked"] is True
    assert crit[0].evidence["unauth_status_code"] == 200
    assert not any(f.rule_id.endswith("-PublicContent") for f in findings)


def test_bola_partial_leak_subset_of_owner_fields_flagged_ambiguous():
    """actor's content is a strict SUBSET of the owner's fields (e.g.
    the endpoint redacts some fields for non-owners but still leaks
    the rest) — neither byte-identical to the owner's full record nor
    to the actor's own control, so it lands in the ambiguous bucket
    for human review rather than being silently dropped or
    miscategorized as a clean leak/no-leak."""
    owner_body = ('{"id":2,"item":"bob pizza","email":"bob@example.com",'
                  '"phone":"555-1234"}')
    actor_body = '{"id":2,"item":"bob pizza"}'  # strict subset of owner's fields
    c = _client(
        auth_pair=_pair(),
        responses=[
            _fake_response(status=200, text=owner_body),
            _fake_response(status=200, text=actor_body),
            _fake_response(status=403),
            _fake_response(status=200, text='{"id":1,"item":"alice coffee"}'),
            _fake_response(status=200),
            _fake_response(status=403),
        ],
    )
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    crit = [f for f in findings if f.severity == Severity.CRITICAL]
    assert len(crit) == 1
    assert crit[0].rule_id.endswith("-CrossUserDataExposure")
    assert crit[0].evidence["diff_class"] == "partial_or_volatile"


def test_bola_declared_shared_endpoint_downgrades_to_info():
    """`auth.bola_shared_endpoints` is an explicit escape hatch: it
    overrides whatever the content diff would otherwise conclude.
    Matching is glob (`fnmatch`) against the WHOLE path, not a
    substring — "/orders/*" matches "/orders/2", "/orders/" alone
    would not."""
    pair = _pair(bola_shared_endpoints=["/orders/*"])
    c = _client(
        auth_pair=pair,
        responses=[
            _fake_response(status=200, text='{"id":2,"item":"bob pizza"}'),
            _fake_response(status=200, text='{"id":2,"item":"a shared view"}'),  # would be ambiguous
            _fake_response(status=403),
            _fake_response(status=200),
            _fake_response(status=403),
        ],
    )
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    assert not any(f.severity == Severity.CRITICAL for f in findings)
    declared = [f for f in findings if f.rule_id.endswith("-DeclaredShared")]
    assert len(declared) == 1
    assert declared[0].severity == Severity.INFO
    assert declared[0].evidence["declared_shared"] is True
    assert declared[0].evidence["matched_pattern"] == "/orders/*"
    assert "actor_fingerprint" in declared[0].evidence
    assert "owner_fingerprint" in declared[0].evidence
    assert "unauth_fingerprint" in declared[0].evidence


def test_bola_shared_endpoints_matching_is_glob_not_substring():
    """A bare substring pattern with no wildcard does NOT match under
    `fnmatch` — `bola_shared_endpoints` is glob, matched against the
    WHOLE path, not "does this substring appear anywhere". This is
    deliberate: a careless bare-prefix pattern must not silently
    swallow every path under it."""
    pair = _pair(bola_shared_endpoints=["/orders/"])  # no wildcard — won't match "/orders/2"
    c = _client(
        auth_pair=pair,
        responses=[
            _fake_response(status=200, text='{"id":2,"item":"bob pizza"}'),
            _fake_response(status=200, text='{"id":2,"item":"bob pizza"}'),  # == owner => real leak
            _fake_response(status=403),
            _fake_response(status=200, text='{"id":1,"item":"alice coffee"}'),
            _fake_response(status=200),
            _fake_response(status=403),
        ],
    )
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    assert not any(f.rule_id.endswith("-DeclaredShared") for f in findings)
    crit = [f for f in findings if f.severity == Severity.CRITICAL]
    assert len(crit) == 1
    assert crit[0].rule_id.endswith("-CrossUserDataExposure")


def test_bola_declared_shared_wins_over_owner_baseline_failure():
    """L1: the declared-shared check happens BEFORE the owner-baseline
    fail-closed check. A declared-shared endpoint must stay INFO even
    when the owner baseline probe errors out — it must never fall
    through to the fail-closed CRITICAL path."""
    pair = _pair(bola_shared_endpoints=["/orders/*"])
    c = _client(auth_pair=pair)
    c.request = MagicMock(side_effect=[
        requests.ConnectionError("boom"),            # dir1 owner baseline blows up
        _fake_response(status=200, text='{"item":"x"}'),  # dir1 actor probe: 2xx
        _fake_response(status=403),                    # dir1 declared-shared's own unauth probe
        _fake_response(status=200),                    # dir2 owner baseline
        _fake_response(status=403),                    # dir2 actor probe: blocked, quiet
    ])
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    assert not any(f.severity == Severity.CRITICAL for f in findings)
    declared = [f for f in findings if f.rule_id.endswith("-DeclaredShared")]
    assert len(declared) == 1
    assert declared[0].evidence["owner_fingerprint"] == "unavailable"


def test_bola_declared_shared_records_unavailable_when_unauth_probe_fails():
    """L1: the declared-shared branch's OWN unauth probe erroring out
    still yields DeclaredShared — with `"unavailable"` recorded rather
    than propagating the exception or falling back to CRITICAL."""
    pair = _pair(bola_shared_endpoints=["/orders/*"])
    c = _client(auth_pair=pair)
    c.request = MagicMock(side_effect=[
        _fake_response(status=200, text='{"item":"owner"}'),  # owner baseline
        _fake_response(status=200, text='{"item":"actor"}'),  # actor probe
        requests.ConnectionError("boom"),                       # declared-shared's unauth probe blows up
        _fake_response(status=200),                             # dir2 owner baseline
        _fake_response(status=403),                             # dir2 actor probe: blocked, quiet
    ])
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    assert not any(f.severity == Severity.CRITICAL for f in findings)
    declared = [f for f in findings if f.rule_id.endswith("-DeclaredShared")]
    assert len(declared) == 1
    assert declared[0].evidence["unauth_fingerprint"] == "unavailable"


# ---- BOLA: fail-closed on probe failures -----------------------------------
#
# Any exception during probes 1/3/4, or a non-2xx owner baseline (1),
# stays CRITICAL rather than let an investigative probe's own failure
# make a real finding disappear. The actor's OWN probe (2) keeps its
# pre-existing contract: a request error there means we never even
# confirmed a hit, so it's an INFO `-ProbeFailed`, unchanged.

def test_bola_actor_probe_exception_yields_info_probe_failed():
    """Unchanged from before this redesign: the actor's own probe
    erroring out means no hit was ever confirmed — INFO, not CRITICAL."""
    c = _client(auth_pair=_pair())
    c.request = MagicMock(side_effect=[
        _fake_response(status=200),          # dir1 owner baseline
        requests.ConnectionError("boom"),     # dir1 actor probe blows up
        _fake_response(status=200),          # dir2 owner baseline
        _fake_response(status=403),          # dir2 actor probe: blocked, quiet
    ])
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    assert not any(f.severity == Severity.CRITICAL for f in findings)
    failed = [f for f in findings if f.rule_id.endswith("-ProbeFailed")]
    assert len(failed) == 1
    assert failed[0].severity == Severity.INFO


def test_bola_owner_baseline_exception_stays_critical_conservatively():
    c = _client(auth_pair=_pair())
    c.request = MagicMock(side_effect=[
        requests.ConnectionError("boom"),           # dir1 owner baseline blows up
        _fake_response(status=200, text='{"item":"bob"}'),  # dir1 actor probe: 2xx
        _fake_response(status=200),
        _fake_response(status=403),
    ])
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    crit = [f for f in findings if f.severity == Severity.CRITICAL]
    assert len(crit) == 1
    assert crit[0].rule_id.endswith("-CrossUserDataExposure")
    assert "ConnectionError" in crit[0].evidence["baseline_unavailable"]


def test_bola_owner_baseline_non_2xx_stays_critical_conservatively():
    c = _client(
        auth_pair=_pair(),
        responses=[
            _fake_response(status=500),                             # owner baseline: unexpected error
            _fake_response(status=200, text='{"item":"bob"}'),      # actor probe: 2xx
            _fake_response(status=200),
            _fake_response(status=403),
        ],
    )
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    crit = [f for f in findings if f.severity == Severity.CRITICAL]
    assert len(crit) == 1
    assert crit[0].evidence["baseline_unavailable"] == "owner_status_500"


def test_bola_unauth_probe_exception_stays_critical_conservatively():
    c = _client(auth_pair=_pair())
    c.request = MagicMock(side_effect=[
        _fake_response(status=200, text='{"item":"bob"}'),  # owner baseline
        _fake_response(status=200, text='{"item":"bob"}'),  # actor probe (== owner)
        requests.ConnectionError("boom"),                     # unauth probe blows up
        _fake_response(status=200),
        _fake_response(status=403),
    ])
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    crit = [f for f in findings if f.severity == Severity.CRITICAL]
    assert len(crit) == 1
    assert "ConnectionError" in crit[0].evidence["unauth_probe_unavailable"]


def test_bola_actor_control_probe_exception_stays_critical_conservatively():
    c = _client(auth_pair=_pair())
    c.request = MagicMock(side_effect=[
        _fake_response(status=200, text='{"item":"owner-view"}'),   # owner baseline
        _fake_response(status=200, text='{"item":"actor-view"}'),   # actor probe (differs from owner)
        _fake_response(status=403),                                   # unauth blocked
        requests.ConnectionError("boom"),                              # actor control blows up
        _fake_response(status=200),
        _fake_response(status=403),
    ])
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    crit = [f for f in findings if f.severity == Severity.CRITICAL]
    assert len(crit) == 1
    assert "ConnectionError" in crit[0].evidence["control_probe_unavailable"]


def test_bola_actor_control_probe_non_2xx_stays_critical_conservatively():
    c = _client(
        auth_pair=_pair(),
        responses=[
            _fake_response(status=200, text='{"item":"owner-view"}'),
            _fake_response(status=200, text='{"item":"actor-view"}'),
            _fake_response(status=403),
            _fake_response(status=500),   # actor control: unexpected error
            _fake_response(status=200),
            _fake_response(status=403),
        ],
    )
    findings = bola_rule.execute(c, _op("/orders/{id}"))
    crit = [f for f in findings if f.severity == Severity.CRITICAL]
    assert len(crit) == 1
    assert crit[0].evidence["control_probe_unavailable"] == "actor_own_status_500"


# ---- BOLA: non-GET/HEAD defense-in-depth guard -----------------------------

def test_bola_non_get_method_skips_diff_and_replays_only_once():
    """Defense-in-depth: applies_to() currently restricts BOLA to GET,
    so this is unreachable via execute() today — but `_probe_direction`
    itself guards against ever replaying probes against a method that
    might mutate state. On 2xx it falls straight back to CRITICAL with
    no comparison, issuing exactly ONE request."""
    op = _op("/orders/{id}", method="POST")
    c = _client(response=_fake_response(status=200, text="created"))
    findings = bola_rule._probe_direction(
        c, op, "POST /orders/{id}",
        actor_token="alice-token", actor_label="user_a",
        target_id=2, target_owner_label="user_b", owner_token="bob-token",
        actor_own_id=1, shared_patterns=[],
    )
    assert len(findings) == 1
    assert findings[0].severity == Severity.CRITICAL
    assert findings[0].rule_id.endswith("-CrossUserDataExposure")
    assert findings[0].evidence["baseline_diff_skipped"] == "non_get_method:POST"
    c.request.assert_called_once()


def test_bola_non_get_method_no_finding_on_non_2xx():
    op = _op("/orders/{id}", method="DELETE")
    c = _client(response=_fake_response(status=403))
    findings = bola_rule._probe_direction(
        c, op, "DELETE /orders/{id}",
        actor_token="alice-token", actor_label="user_a",
        target_id=2, target_owner_label="user_b", owner_token="bob-token",
        actor_own_id=1, shared_patterns=[],
    )
    assert findings == []
    c.request.assert_called_once()


# ---- FLA: applies_to -----------------------------------------------------

def test_fla_applies_to_admin_path():
    assert function_authz_rule.applies_to(_op("/admin/users")) is True


def test_fla_applies_to_safe_admin_path_too():
    """We can't tell from `applies_to` whether this is the vuln vs
    safe variant — the rule probes and lets the response tell us."""
    assert function_authz_rule.applies_to(_op("/safe/admin/users")) is True


def test_fla_skips_no_auth_op():
    assert function_authz_rule.applies_to(_op("/admin/users", requires_auth=False)) is False


# ---- FLA: no_auth_pair surfacing ----------------------------------------

def test_fla_no_pair_emits_info_on_admin_path_only():
    c = _client(auth_pair=None)
    findings = function_authz_rule.execute(c, _op("/admin/users"))
    assert len(findings) == 1
    assert findings[0].severity == Severity.INFO


def test_fla_no_pair_silent_on_non_admin_path():
    """Don't drown the output in 'skipped' findings on every endpoint."""
    c = _client(auth_pair=None)
    findings = function_authz_rule.execute(c, _op("/users"))
    assert findings == []


# ---- FLA: core behavior ---------------------------------------------------

def test_fla_low_priv_token_gets_2xx_yields_high_finding():
    c = _client(auth_pair=_pair(),
                response=_fake_response(status=200, text='{"users":[...]}'))
    findings = function_authz_rule.execute(c, _op("/admin/users"))
    high = [f for f in findings if f.severity == Severity.HIGH]
    assert len(high) == 1
    assert high[0].rule_id.endswith("NonAdminAccessGranted")
    assert high[0].evidence["low_priv_user"] == "user_a"


def test_fla_low_priv_token_gets_403_no_finding():
    c = _client(auth_pair=_pair(),
                response=_fake_response(status=403))
    findings = function_authz_rule.execute(c, _op("/admin/users"))
    assert findings == []


def test_fla_uses_user_a_token_by_default():
    """The default low-priv user is `user_a`."""
    c = _client(auth_pair=_pair(),
                response=_fake_response(status=403))
    function_authz_rule.execute(c, _op("/admin/users"))
    sent_token = c.request.call_args.kwargs["token"]
    assert sent_token == "alice-token"


def test_fla_honors_fla_low_priv_user_override():
    """If user specifies user_b as low-priv, that token is used."""
    pair = _pair(fla_low_priv_user="user_b")
    c = _client(auth_pair=pair, response=_fake_response(status=403))
    function_authz_rule.execute(c, _op("/admin/users"))
    sent_token = c.request.call_args.kwargs["token"]
    assert sent_token == "bob-token"


def test_fla_honors_custom_admin_paths():
    """Custom `fla_admin_paths` lets users mark non-`/admin/` paths
    as elevated."""
    pair = _pair(fla_admin_paths=["/internal/"])
    c = _client(auth_pair=pair, response=_fake_response(status=200))
    # /admin/users no longer matches
    findings_admin = function_authz_rule.execute(c, _op("/admin/users"))
    # /internal/reports DOES match
    findings_internal = function_authz_rule.execute(c, _op("/internal/reports"))
    assert findings_admin == []
    assert len(findings_internal) == 1


def test_fla_non_admin_path_no_findings_even_with_pair():
    c = _client(auth_pair=_pair(),
                response=_fake_response(status=200))
    findings = function_authz_rule.execute(c, _op("/users"))
    assert findings == []
    c.request.assert_not_called()


def test_fla_probe_exception_yields_info():
    import requests
    c = _client(auth_pair=_pair())
    c.request = MagicMock(side_effect=requests.ConnectionError("boom"))
    findings = function_authz_rule.execute(c, _op("/admin/users"))
    assert len(findings) == 1
    assert findings[0].severity == Severity.INFO
