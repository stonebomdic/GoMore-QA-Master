"""由 OpenAPI spec 切出 mutating 測試範圍。

POST/PUT/PATCH 分三桶：
  runnable        — 有 requestBody schema 且不在黑名單
  no_body_schema  — mutating 但無 requestBody schema（fuzz 價值低）
  blacklisted     — path 命中副作用黑名單
DELETE 一律不入任何桶。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

MUTATING_METHODS = ("post", "put", "patch")


def load_spec(path: str) -> dict:
    text = Path(path).read_text()
    if path.endswith((".yaml", ".yml")):
        import yaml
        return yaml.safe_load(text)
    return json.loads(text)


def _has_request_body(op: dict) -> bool:
    content = ((op or {}).get("requestBody") or {}).get("content") or {}
    return any("schema" in (c or {}) for c in content.values())


def build_scope(spec: dict, blacklist_patterns: list[str]) -> dict:
    compiled = [re.compile(p) for p in blacklist_patterns]
    runnable, no_body, blacklisted = [], [], []
    for path, item in (spec.get("paths") or {}).items():
        for method, op in (item or {}).items():
            if method.lower() not in MUTATING_METHODS:
                continue
            entry = {"method": method.upper(), "path": path}
            if any(c.search(path) for c in compiled):
                blacklisted.append(entry)
            elif _has_request_body(op):
                runnable.append(entry)
            else:
                no_body.append(entry)
    return {"runnable": runnable, "no_body_schema": no_body, "blacklisted": blacklisted}
