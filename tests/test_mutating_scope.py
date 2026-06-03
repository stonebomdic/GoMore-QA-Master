from scripts.mutating_scope import build_scope


def _op_with_body():
    return {"requestBody": {"content": {"application/json": {"schema": {"type": "object"}}}}}


def test_build_scope_splits_three_buckets_and_drops_delete():
    spec = {
        "paths": {
            "/mood-diary": {"post": _op_with_body()},
            "/auth/email/login": {"post": _op_with_body()},
            "/iap/purchase": {"post": _op_with_body()},
            "/ping": {"post": {}},                       # 無 requestBody schema
            "/widgets/{id}": {"delete": {}, "get": {}},  # DELETE / GET 不入桶
        }
    }
    scope = build_scope(spec, blacklist_patterns=[r"^/auth/", r"^/iap/"])

    assert {"method": "POST", "path": "/mood-diary"} in scope["runnable"]
    assert {"method": "POST", "path": "/auth/email/login"} in scope["blacklisted"]
    assert {"method": "POST", "path": "/iap/purchase"} in scope["blacklisted"]
    assert {"method": "POST", "path": "/ping"} in scope["no_body_schema"]

    flat = scope["runnable"] + scope["no_body_schema"] + scope["blacklisted"]
    assert all(e["method"] in {"POST", "PUT", "PATCH"} for e in flat)  # 無 DELETE/GET
