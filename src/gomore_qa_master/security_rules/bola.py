"""OWASP API1 (BOLA / IDOR) + OWASP API5 (Function Level Authz).

PR-4 of the v0.8.0 rollout. Two rules in one module because they
share the "low-priv token sees something it shouldn't" diff
machinery — only the selector differs.

  BOLARule          — for each GET endpoint with exactly one path
                      parameter, probe with user-A's token using
                      user-B's object id. 2xx alone is NOT enough to
                      call this CRITICAL — see "BOLA 4-probe diff"
                      below for how false positives are ruled out.

  FunctionAuthzRule — for each operation whose path matches a known
                      admin-shaped pattern (default: "/admin/"),
                      probe with the LOW-PRIV token. 2xx = the
                      server granted admin access to a non-admin
                      user.

Both rules carry `requires_auth_pair = True`. When the runner
doesn't have an `AuthPair`, they skip with INFO findings rather
than firing false positives off the default-token probe.

BOLA 4-probe diff (A′ — supersedes the single-baseline-diff design)
---------------------------------------------------------------------

A first pass at BOLA fired CRITICAL on 2xx alone: "actor's own token
fetched target's object id". In a POC run against a real spec, every
one of 6 such CRITICAL findings turned out to be a false positive —
the "object" was a resource both users legitimately see the same way
(shared/public content), not something object-level authz should have
gated per-owner.

A follow-up design added ONE baseline probe (target owner's own token,
same path) and downgraded to INFO whenever its content byte-matched
the actor's. That over-corrected: it can't tell "genuinely shared
content" apart from "the textbook BOLA bug where the endpoint returns
the SAME raw record to anyone" — which is, definitionally, identical
content. A security review caught it turning a real, dogfood-verified
leak into an INFO false negative, and rejected it.

The current design (A′) never collapses "content matches" straight to
a dismissal. Instead, once the actor's probe returns 2xx,
`_probe_direction` runs up to three MORE probes against the exact same
`request_path` (all methods hardcoded to `"GET"` — see the
`op.method` guard below) before drawing any conclusion:

  1. owner baseline    — target owner's own token (done first; if this
                         errors or comes back non-2xx, fail CLOSED:
                         stay CRITICAL rather than guess).
  2. actor probe       — actor's own token (already have this: 2xx is
                         the pre-condition for everything below; a
                         non-2xx here still means "no finding", as
                         always).
  3. unauth probe      — no token at all (`token=None`). Tells us
                         whether the spec's declared auth requirement
                         is even enforced.
  4. actor control     — actor's own token against actor's OWN first
                         owned id (`bola_test_ids[actor]`'s first
                         entry). Tells us what the actor's "normal"
                         response looks like, so we can tell "the
                         endpoint just always returns MY data
                         regardless of the path id" apart from a real
                         cross-user leak.

All four bodies are compared via `_fingerprint()` — not raw text: it
JSON-decodes where possible, strips known-volatile fields (timestamps,
request ids, ...), and hashes the normalized form (sha256, not md5 —
md5 is rejected outright under FIPS-mode OpenSSL and buys nothing here
since this is an equality fingerprint, not a security boundary).

Declared-shared override (checked FIRST, right after the actor's 2xx —
before any of the owner/unauth/control probe *results* are judged):
`AuthPair.bola_shared_endpoints` is a list of glob patterns matched
whole against `op.path` via `fnmatch.fnmatch` (NOT substrings — see
`_first_glob_match`). A match always emits INFO `-DeclaredShared`,
regardless of what the owner/unauth probes come back with — including
if they error or come back non-2xx, which is recorded as
`"unavailable"` in evidence rather than tripping the fail-closed
CRITICAL path below. It's an explicit escape hatch for resources a
human has already confirmed are intentionally shared (read-shared,
write-owned resources are the classic case) — narrower than silently
trusting "content happened to match."

Endpoints that are shared to any logged-in user but NOT to anonymous
callers are the one shape this diff genuinely can't distinguish from a
real per-owner leak by content alone (both look like "actor ==
owner"). `bola_shared_endpoints` is the intended way to declare those
out; `bola_test_ids` must otherwise map to objects that really are
private per-user, or expect `-CrossUserDataExposure` on them.

Verdict (only reached when `op.path` does NOT match a declared-shared
pattern):

  - unauth got 2xx AND fingerprint == actor's        → **HIGH**
    `-PublicContent`. This is NOT a dismissal: the spec declares auth
    required and it isn't enforced at all — anyone, not just the
    actor, can read this object. That's a `broken_auth`-shaped defect
    layered under a BOLA probe, and treating it as low-severity would
    be a regression versus the pre-diff design (which was at least
    CRITICAL on bare 2xx). If this object is genuinely meant to be
    public, declare it via `bola_shared_endpoints` instead — THAT is
    the only path to an INFO verdict here.
  - unauth was blocked (non-2xx, OR 2xx with different content) AND
    actor's fingerprint == owner's                    → CRITICAL
    `-CrossUserDataExposure`. This is the real signature of a leak:
    an anonymous caller can't get in, but the actor's own token
    produces exactly what the legitimate owner would see.
  - unauth was blocked AND actor's fingerprint == actor's own-object
    fingerprint (control)                             → INFO
    `-ScopedView`. Positive proof the endpoint ignores the path id
    and just returns the caller's own data (e.g. derived from a JWT
    `sub` claim) — there's no cross-user id-selection happening at
    all, so there's nothing for BOLA to catch here.
  - unauth was blocked AND the content doesn't match owner OR actor's
    own control                                       → CRITICAL
    `-CrossUserDataExposure`, `evidence["diff_class"] = "partial_or_
    volatile"`. Ambiguous — could be a partial leak, could be a
    fingerprint miss on some volatile field we don't strip yet. Ship
    it CRITICAL for a human to look at rather than silently drop it.
  - ANY of probes 1/3/4 raises, or the owner baseline (1) comes back
    non-2xx                                           → CRITICAL,
    fail-closed, conservative. We never let an investigative probe's
    own failure make a real finding disappear. (The actor's OWN probe,
    #2, keeps its pre-existing contract: a request error there means
    we never even confirmed a hit, so it stays an INFO `-ProbeFailed`,
    same as before this redesign.)

Known limitation: `APIClient` doesn't set `requests`' `trust_env=False`,
so a `~/.netrc` entry for the target host could inject credentials
into the "unauthenticated" probe (#3) and defeat it. Not fixing this
by disabling `trust_env` — that also turns off environment-configured
proxy support, which isn't a worthwhile trade for closing a local-
machine-config edge case.

Discovery strategy decision (PRD §7.3)
--------------------------------------

For BOLA we picked **explicit config** — `auth_pair.bola_test_ids`
maps each user to the ids of objects they own. The alternative
strategies were:

  (a) Explicit config           ← picked
  (b) Auto-seed via POST endpoints
  (c) Discover via "list me's objects" endpoints

(a) is the simplest, deterministic, and has zero side effects
during a security scan. (b)/(c) are deferred to v0.8.1 — they
involve mutating server state or assuming spec-quality patterns
that aren't universal.

Multi-parameter paths
---------------------

For PR-4 the BOLA rule applies only to paths with exactly ONE path
parameter. Paths like `/users/{user_id}/orders/{order_id}` need a
richer substitution strategy (which id swaps to user-B's, which
stays?) and are filed for v0.8.1. The rule emits an INFO finding
on multi-param paths so users know they were skipped.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import re

from .base import APIClient, Finding, OperationContext, Severity

_DEFAULT_FLA_ADMIN_PATHS = ["/admin/", "/admin"]

# Only methods it's safe to replay 3-4 times against the same path
# without side effects. BOLA's applies_to() currently restricts to
# GET, so this is a defense-in-depth guard against a future looser
# applies_to() — see `BOLARule._probe_direction`.
_SAFE_REPLAY_METHODS = ("GET", "HEAD")

# Fields stripped before fingerprinting a JSON response body — known
# sources of harmless per-request noise (timestamps, request ids)
# that would otherwise defeat an equality comparison between two
# probes of the same logical resource. Extend this set as new noisy
# fields turn up in real specs.
_VOLATILE_JSON_FIELDS = frozenset({
    "timestamp", "request_id", "server_time", "generated_at",
    "trace_id", "correlation_id", "expires_at", "nonce",
})


def _first_glob_match(path: str, patterns: list[str]) -> str | None:
    """First pattern in `patterns` that `fnmatch.fnmatch`-matches the
    WHOLE `path` — used for `bola_shared_endpoints`. Deliberately glob
    (`fnmatch`), not substring: a substring match on a bare "/" or "*"
    pattern would silently match every path in the scan, and BOLA's
    runner-side validation (see `runners.api_security.run_scan`)
    rejects non-string / empty entries but can't catch "technically a
    valid pattern that matches everything" — glob at least makes the
    intent explicit and greppable in the config."""
    for p in patterns:
        if fnmatch.fnmatch(path, p):
            return p
    return None


def _count_path_params(path: str) -> int:
    return len(re.findall(r"\{[^{}]+\}", path))


def _substitute_first_path_param(path: str, value: int | str) -> str:
    """Replace the first `{...}` token with `value`. Leaves the rest
    alone — used as a sanity check; BOLA only acts on single-param
    paths so the "leaves the rest alone" branch is unused for now."""
    return re.sub(r"\{[^{}]+\}", str(value), path, count=1)


def _matches_admin_pattern(path: str, patterns: list[str]) -> bool:
    return any(p in path for p in patterns)


def _truncate(s: str, n: int) -> str:
    if len(s) <= n:
        return s
    return s[:n] + f"... ({len(s)} total bytes)"


def _strip_volatile(value):
    """Recursively drop `_VOLATILE_JSON_FIELDS` keys from dicts nested
    anywhere in `value`. Lists/scalars pass through unchanged (besides
    recursing into list elements)."""
    if isinstance(value, dict):
        return {k: _strip_volatile(v) for k, v in value.items()
                if k not in _VOLATILE_JSON_FIELDS}
    if isinstance(value, list):
        return [_strip_volatile(v) for v in value]
    return value


def _fingerprint(resp) -> str:
    """A stable equality fingerprint of a response body.

    Used across the BOLA 4-probe diff (see module docstring) to
    compare bodies from the owner-baseline / actor / unauth / actor-
    control probes. Not a security boundary — just a cheap way to say
    "these two responses are the same logical content."

    JSON bodies are decoded, volatile fields (timestamps, request ids,
    ...) stripped, and re-serialized with sorted keys and no
    incidental whitespace before hashing — so unrelated per-request
    noise doesn't defeat what should be an "identical resource"
    comparison. Non-JSON bodies fall back to a whitespace-stripped
    hash of the raw text.

    sha256, not md5: md5 is rejected outright under FIPS-mode OpenSSL
    builds, and buys nothing here since this isn't a security
    boundary — just an equality check.
    """
    text = resp.text
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        normalized = text.strip()
    else:
        normalized = json.dumps(_strip_volatile(parsed), sort_keys=True,
                                 separators=(",", ":"))
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


# ---- BOLA (OWASP API1) ----------------------------------------------------

class BOLARule:
    """Broken Object Level Authorization scanner.

    For each (auth-required GET, single path-param) operation:
      For each (user-A, user-B) ordered pair in `auth_pair`:
        probe path-with-user-B-id with user-A's token.
        if 2xx → run the 4-probe diff (module docstring) to decide
        between CRITICAL (`-CrossUserDataExposure`) and one of the
        INFO outcomes (`-PublicContent`, `-ScopedView`,
        `-DeclaredShared`).
    """

    id: str = "OWASP-API1-BOLA"
    severity: Severity = Severity.HIGH  # default; per-finding CRITICAL
    requires_auth_pair: bool = True

    def applies_to(self, op: OperationContext) -> bool:
        if not op.requires_auth:
            return False
        if op.method.upper() != "GET":
            return False
        # Single path parameter only. Multi-param paths punt to v0.8.1.
        return _count_path_params(op.path) == 1

    def execute(self, client: APIClient, op: OperationContext) -> list[Finding]:
        endpoint = f"{op.method.upper()} {op.path}"
        pair = client.auth_pair
        if pair is None:
            return [_skip_finding(self.id, endpoint, "no_auth_pair_provided",
                                   "Provide `auth.alt_user_token` to enable BOLA probing.")]
        if not pair.bola_test_ids:
            return [_skip_finding(self.id, endpoint, "no_bola_test_ids",
                                   "Provide `auth.bola_test_ids` mapping each user to ids of "
                                   "objects they own.")]

        user_a_ids = pair.bola_test_ids.get("user_a", [])
        user_b_ids = pair.bola_test_ids.get("user_b", [])
        if not user_a_ids or not user_b_ids:
            return [_skip_finding(self.id, endpoint, "incomplete_bola_test_ids",
                                   "bola_test_ids must list at least one id under both "
                                   "`user_a` and `user_b`.")]

        shared_patterns = pair.bola_shared_endpoints or []
        findings: list[Finding] = []

        # Direction 1: user_a's token tries to read user_b's first object.
        findings.extend(self._probe_direction(
            client, op, endpoint,
            actor_token=pair.user_a_token, actor_label="user_a",
            target_id=user_b_ids[0], target_owner_label="user_b",
            owner_token=pair.user_b_token, actor_own_id=user_a_ids[0],
            shared_patterns=shared_patterns,
        ))
        # Direction 2: user_b's token tries to read user_a's first object.
        findings.extend(self._probe_direction(
            client, op, endpoint,
            actor_token=pair.user_b_token, actor_label="user_b",
            target_id=user_a_ids[0], target_owner_label="user_a",
            owner_token=pair.user_a_token, actor_own_id=user_b_ids[0],
            shared_patterns=shared_patterns,
        ))
        return findings

    def _probe_direction(
        self, client: APIClient, op: OperationContext, endpoint: str,
        *, actor_token: str, actor_label: str,
        target_id: int, target_owner_label: str, owner_token: str,
        actor_own_id: int, shared_patterns: list[str],
    ) -> list[Finding]:
        request_path = _substitute_first_path_param(op.path, target_id)

        if op.method.upper() not in _SAFE_REPLAY_METHODS:
            # Defense-in-depth: applies_to() currently restricts BOLA to
            # GET, so this is unreachable in production today. If that
            # filter is ever loosened, replaying 3 extra probes against
            # a mutating method (POST/PUT/PATCH/DELETE) could cause real
            # side effects — fall back to the old single-probe check
            # instead: actor 2xx = CRITICAL, no comparison attached.
            return self._probe_single_no_diff(
                client, op, endpoint, request_path,
                actor_token=actor_token, actor_label=actor_label,
                target_id=target_id, target_owner_label=target_owner_label,
            )

        title = (f"{actor_label} can read {target_owner_label}'s object id={target_id} — "
                 f"missing object-level authorization check")
        remediation_hint = (
            "Compare the caller's identity to the object's owner before "
            "returning. Reject with 403 (or 404 to avoid id-enumeration) "
            "when the caller is not the owner. Don't rely on the spec's "
            "`security` declaration alone — it tells you 'someone is "
            "logged in,' not 'this specific user owns this object.'"
        )

        # ---- Probe 1: owner baseline (done first) -------------------------
        try:
            owner_resp = client.request("GET", request_path, token=owner_token)
        except Exception as e:
            owner_resp = None
            owner_unavailable = f"{type(e).__name__}: {e}"
        else:
            owner_unavailable = (None if 200 <= owner_resp.status_code < 300
                                  else f"owner_status_{owner_resp.status_code}")

        # ---- Probe 2: actor probe ------------------------------------------
        try:
            actor_resp = client.request("GET", request_path, token=actor_token)
        except Exception as e:
            # No hit was ever confirmed — this is a plain probe failure,
            # not evidence of anything. Unlike probes 1/3/4 below, this
            # does NOT fail closed to CRITICAL.
            return [Finding(
                rule_id=f"{self.id}-ProbeFailed",
                severity=Severity.INFO,
                endpoint=endpoint,
                title=f"BOLA probe failed: {actor_label} → {target_owner_label}'s id={target_id}",
                evidence={"actor": actor_label, "target_id": target_id,
                          "error": f"{type(e).__name__}: {e}"},
                remediation_hint="Verify the endpoint is reachable.",
            )]

        if not (200 <= actor_resp.status_code < 300):
            return []  # no hit — owner baseline's outcome is moot

        base_evidence = {
            "actor": actor_label,
            "target_owner": target_owner_label,
            "target_id": target_id,
            "probed_path": request_path,
            "status_code": actor_resp.status_code,
            "response_body_preview": _truncate(actor_resp.text, 500),
        }

        # ---- Declared-shared override — checked FIRST, right after the ----
        # actor's 2xx, before any owner/unauth probe RESULT is judged. An
        # explicit user declaration must win even if the owner baseline or
        # unauth probe subsequently errors — a declared-shared endpoint
        # must never fall through to the fail-closed CRITICAL paths below.
        # Fingerprints that can't be obtained are recorded as the literal
        # string "unavailable" rather than causing this branch to bail.
        matched_pattern = _first_glob_match(op.path, shared_patterns)
        if matched_pattern is not None:
            actor_fp = _fingerprint(actor_resp)
            owner_fp = ("unavailable" if owner_unavailable is not None
                        else _fingerprint(owner_resp))
            try:
                unauth_resp_ds = client.request("GET", request_path, token=None)
            except Exception:
                unauth_fp = "unavailable"
            else:
                unauth_fp = (_fingerprint(unauth_resp_ds)
                             if 200 <= unauth_resp_ds.status_code < 300 else "unavailable")
            return [Finding(
                rule_id=f"{self.id}-DeclaredShared",
                severity=Severity.INFO,
                endpoint=endpoint,
                title=(f"{target_owner_label}'s object id={target_id} is declared shared "
                       f"via `auth.bola_shared_endpoints` — skipping BOLA judgement"),
                evidence={
                    **base_evidence,
                    "actor_fingerprint": actor_fp,
                    "owner_fingerprint": owner_fp,
                    "unauth_fingerprint": unauth_fp,
                    "declared_shared": True,
                    "matched_pattern": matched_pattern,
                },
                remediation_hint=(
                    "This path matched an entry in `auth.bola_shared_endpoints`, so "
                    "it's treated as an intentionally shared resource and not judged "
                    "for object-level authorization. Remove it from that list (or "
                    "narrow the pattern) if it should actually be gated per-owner."
                ),
            )]

        if owner_unavailable is not None:
            return [Finding(
                rule_id=f"{self.id}-CrossUserDataExposure",
                severity=Severity.CRITICAL,
                endpoint=endpoint,
                title=title,
                evidence={**base_evidence, "baseline_unavailable": owner_unavailable},
                remediation_hint=remediation_hint,
            )]

        actor_fp = _fingerprint(actor_resp)
        owner_fp = _fingerprint(owner_resp)

        # ---- Probe 3: unauth probe (no Authorization header at all) -------
        try:
            unauth_resp = client.request("GET", request_path, token=None)
        except Exception as e:
            return [Finding(
                rule_id=f"{self.id}-CrossUserDataExposure",
                severity=Severity.CRITICAL,
                endpoint=endpoint,
                title=title,
                evidence={**base_evidence,
                          "unauth_probe_unavailable": f"{type(e).__name__}: {e}"},
                remediation_hint=remediation_hint,
            )]

        unauth_2xx = 200 <= unauth_resp.status_code < 300
        unauth_fp = _fingerprint(unauth_resp) if unauth_2xx else None
        unauth_public = unauth_2xx and unauth_fp == actor_fp

        if unauth_public:
            # NOT a dismissal — HIGH, not INFO. The spec declares auth
            # required and it isn't enforced at all: anyone, not just
            # the actor, can read this object. Silently downgrading
            # this to INFO would be a false-negative regression versus
            # the pre-diff design (which was at least CRITICAL on bare
            # 2xx). If this object is genuinely meant to be public, the
            # only path to an INFO verdict is declaring it via
            # `bola_shared_endpoints` (checked above).
            return [Finding(
                rule_id=f"{self.id}-PublicContent",
                severity=Severity.HIGH,
                endpoint=endpoint,
                title=(f"Object id={target_id} at this endpoint is readable without "
                       f"authentication — the spec's declared auth requirement isn't "
                       f"enforced"),
                evidence={
                    **base_evidence,
                    "actor_fingerprint": actor_fp,
                    "unauth_fingerprint": unauth_fp,
                    "unauth_status_code": unauth_resp.status_code,
                },
                remediation_hint=(
                    "An unauthenticated request returned the same content as an "
                    "authenticated one, even though the spec declares `security` on "
                    "this operation. Enforce authentication on this endpoint — track "
                    "it under `broken_auth` as well, since the underlying defect is "
                    "that auth enforcement is missing entirely, not merely that "
                    "object ownership isn't checked. If this object is genuinely "
                    "meant to be public, add its path to "
                    "`auth.bola_shared_endpoints` to declare that intentionally "
                    "instead of leaving this HIGH finding unresolved."
                ),
            )]

        # ---- Probe 4: actor control (actor's own object) -------------------
        own_path = _substitute_first_path_param(op.path, actor_own_id)
        try:
            actor_own_resp = client.request("GET", own_path, token=actor_token)
        except Exception as e:
            return [Finding(
                rule_id=f"{self.id}-CrossUserDataExposure",
                severity=Severity.CRITICAL,
                endpoint=endpoint,
                title=title,
                evidence={**base_evidence,
                          "control_probe_unavailable": f"{type(e).__name__}: {e}"},
                remediation_hint=remediation_hint,
            )]

        if not (200 <= actor_own_resp.status_code < 300):
            return [Finding(
                rule_id=f"{self.id}-CrossUserDataExposure",
                severity=Severity.CRITICAL,
                endpoint=endpoint,
                title=title,
                evidence={**base_evidence,
                          "control_probe_unavailable":
                              f"actor_own_status_{actor_own_resp.status_code}"},
                remediation_hint=remediation_hint,
            )]

        actor_own_fp = _fingerprint(actor_own_resp)

        if actor_fp == owner_fp:
            return [Finding(
                rule_id=f"{self.id}-CrossUserDataExposure",
                severity=Severity.CRITICAL,
                endpoint=endpoint,
                title=title,
                evidence={
                    **base_evidence,
                    "actor_fingerprint": actor_fp,
                    "owner_fingerprint": owner_fp,
                    "unauth_blocked": True,
                    "unauth_status_code": unauth_resp.status_code,
                },
                remediation_hint=remediation_hint,
            )]

        if actor_fp == actor_own_fp:
            return [Finding(
                rule_id=f"{self.id}-ScopedView",
                severity=Severity.INFO,
                endpoint=endpoint,
                title=(f"{actor_label}'s request for {target_owner_label}'s object "
                       f"id={target_id} returned {actor_label}'s OWN data instead — "
                       f"the endpoint appears to ignore the path id"),
                evidence={
                    **base_evidence,
                    "actor_fingerprint": actor_fp,
                    "owner_fingerprint": owner_fp,
                    "actor_own_fingerprint": actor_own_fp,
                    "actor_own_id": actor_own_id,
                },
                remediation_hint=(
                    "No action needed for this direction: the server appears to "
                    "derive the response from the caller's identity (e.g. a JWT "
                    "`sub` claim) rather than from the path parameter, so the "
                    "object id in the URL doesn't actually select cross-user data."
                ),
            )]

        return [Finding(
            rule_id=f"{self.id}-CrossUserDataExposure",
            severity=Severity.CRITICAL,
            endpoint=endpoint,
            title=title,
            evidence={
                **base_evidence,
                "actor_fingerprint": actor_fp,
                "owner_fingerprint": owner_fp,
                "actor_own_fingerprint": actor_own_fp,
                "diff_class": "partial_or_volatile",
            },
            remediation_hint=(
                remediation_hint + " This response matched neither the owner's "
                "baseline nor the actor's own control response — it may be a "
                "partial leak or a volatile field this scan doesn't strip yet. "
                "Manually compare the three responses before dismissing."
            ),
        )]

    def _probe_single_no_diff(
        self, client: APIClient, op: OperationContext, endpoint: str, request_path: str,
        *, actor_token: str, actor_label: str, target_id: int, target_owner_label: str,
    ) -> list[Finding]:
        """Pre-4-probe-diff fallback for methods other than GET/HEAD.

        See the `_SAFE_REPLAY_METHODS` guard in `_probe_direction`.
        """
        try:
            resp = client.request(op.method, request_path, token=actor_token)
        except Exception as e:
            return [Finding(
                rule_id=f"{self.id}-ProbeFailed",
                severity=Severity.INFO,
                endpoint=endpoint,
                title=f"BOLA probe failed: {actor_label} → {target_owner_label}'s id={target_id}",
                evidence={"actor": actor_label, "target_id": target_id,
                          "error": f"{type(e).__name__}: {e}"},
                remediation_hint="Verify the endpoint is reachable.",
            )]

        if not (200 <= resp.status_code < 300):
            return []

        return [Finding(
            rule_id=f"{self.id}-CrossUserDataExposure",
            severity=Severity.CRITICAL,
            endpoint=endpoint,
            title=(f"{actor_label} can read {target_owner_label}'s object id={target_id} — "
                   f"missing object-level authorization check"),
            evidence={
                "actor": actor_label,
                "target_owner": target_owner_label,
                "target_id": target_id,
                "probed_path": request_path,
                "status_code": resp.status_code,
                "response_body_preview": _truncate(resp.text, 500),
                "baseline_diff_skipped": f"non_get_method:{op.method.upper()}",
            },
            remediation_hint=(
                "Compare the caller's identity to the object's owner before "
                "returning. Reject with 403 (or 404 to avoid id-enumeration) "
                "when the caller is not the owner."
            ),
        )]


# ---- Function Level Authorization (OWASP API5) ---------------------------

class FunctionAuthzRule:
    """Broken Function Level Authorization scanner.

    For each auth-required operation whose path matches an admin
    pattern, probe with the LOW-PRIV token. 2xx = admin function
    accessible to non-admin users.
    """

    id: str = "OWASP-API5-FunctionAuthz"
    severity: Severity = Severity.HIGH
    requires_auth_pair: bool = True

    def applies_to(self, op: OperationContext) -> bool:
        # Path-pattern selection. Production scanners would also honor
        # spec-declared scopes (e.g. OAuth2 `admin` scope) but our
        # fixture and most simple specs lack them. Path-pattern is a
        # safe default; an `fla_admin_paths` override per-scan lets
        # callers tune this. Actual filtering happens in execute() so we
        # can surface "no auth_pair" INFO findings consistently.
        return op.requires_auth

    def execute(self, client: APIClient, op: OperationContext) -> list[Finding]:
        endpoint = f"{op.method.upper()} {op.path}"
        pair = client.auth_pair
        if pair is None:
            # FLA only fires for admin paths. Don't emit "skipped" on
            # every non-admin endpoint; only on admin paths where we
            # would have probed.
            patterns = _DEFAULT_FLA_ADMIN_PATHS
            if not _matches_admin_pattern(op.path, patterns):
                return []
            return [_skip_finding(self.id, endpoint, "no_auth_pair_provided",
                                   "Provide `auth.alt_user_token` to enable FLA probing on "
                                   "admin-shaped paths.")]

        patterns = pair.fla_admin_paths or _DEFAULT_FLA_ADMIN_PATHS
        if not _matches_admin_pattern(op.path, patterns):
            return []  # not an admin path — nothing to probe

        # Pick the low-priv token. Default: user_a.
        low_priv_token = (pair.user_a_token if pair.fla_low_priv_user == "user_a"
                          else pair.user_b_token)
        low_priv_label = pair.fla_low_priv_user

        # Substitute any path params with "1" — same approach as
        # headers_misconfig. Function-level authz finding is about
        # WHO can call the endpoint, not what they pass to it.
        from .headers_misconfig import _resolve_path
        request_path = _resolve_path(op.path)

        try:
            resp = client.request(op.method, request_path, token=low_priv_token)
        except Exception as e:
            return [Finding(
                rule_id=f"{self.id}-ProbeFailed",
                severity=Severity.INFO,
                endpoint=endpoint,
                title="FLA probe failed — request error",
                evidence={"low_priv_user": low_priv_label,
                          "error": f"{type(e).__name__}: {e}"},
                remediation_hint="Verify the endpoint is reachable.",
            )]

        if 200 <= resp.status_code < 300:
            return [Finding(
                rule_id=f"{self.id}-NonAdminAccessGranted",
                severity=Severity.HIGH,
                endpoint=endpoint,
                title=(f"Admin-shaped endpoint accessible to {low_priv_label} "
                       f"(no role check)"),
                evidence={
                    "low_priv_user": low_priv_label,
                    "matched_pattern": next((p for p in patterns if p in op.path), ""),
                    "status_code": resp.status_code,
                    "response_body_preview": _truncate(resp.text, 500),
                },
                remediation_hint=(
                    "Check the caller's role / scope claim before honoring the "
                    "request. Reject with 403 when the role doesn't include "
                    "elevated privileges. The fact that a token is valid is "
                    "NOT sufficient authorization for admin-only endpoints."
                ),
            )]
        return []


# ---- shared helper -------------------------------------------------------

def _skip_finding(rule_id: str, endpoint: str, reason: str, hint: str) -> Finding:
    return Finding(
        rule_id=f"{rule_id}-Skipped",
        severity=Severity.INFO,
        endpoint=endpoint,
        title=f"{rule_id} skipped: {reason}",
        evidence={"reason": reason},
        remediation_hint=hint,
    )


# Singletons for easy registration.
bola_rule = BOLARule()
function_authz_rule = FunctionAuthzRule()
