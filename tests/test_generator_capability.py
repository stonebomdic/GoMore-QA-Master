"""P3 Task 4 — runner 能力以屬性宣告，generator 不再反射偵測簽名。

能力契約：TestRunner.generation_context_fields（frozenset）宣告
generate_test 額外接受哪些 context kwargs；generator 只轉發宣告過的欄位。
"""
from __future__ import annotations

import inspect as _inspect
from pathlib import Path

import pytest

from gomore_qa_master.runners.base import TestRunner
from gomore_qa_master.runners.pytest_playwright import PytestPlaywrightRunner
from gomore_qa_master.tools import generator


class _NarrowRunner(TestRunner):
    """jest/cypress/go 型 — 窄簽名，不吃 context kwargs。"""
    name = "narrow"

    def __init__(self):
        self.seen: dict | None = None

    def list_tests(self): return ""
    def run_tests(self, filter=None, **kwargs): return {}
    def run_failed(self): return {}
    def get_report_summary(self): return {}
    def get_failure_details(self, test_id=None): return []

    def generate_test(self, description: str, filename: str) -> str:
        self.seen = {"description": description, "filename": filename}
        return f"narrow:{filename}"


class _WideRunner(_NarrowRunner):
    """pytest 型 — 宣告吃 url/module/business_context。"""
    name = "wide"
    generation_context_fields = frozenset({"url", "module", "business_context"})

    def generate_test(self, description, filename, url=None, module=None,
                      business_context=None):
        self.seen = {"url": url, "module": module,
                     "business_context": business_context}
        return f"wide:{filename}"


@pytest.fixture
def use_runner(monkeypatch):
    def _use(r):
        monkeypatch.setattr(generator, "get_runner", lambda: r)
        return r
    return _use


def test_base_declares_empty_context_fields():
    assert TestRunner.generation_context_fields == frozenset()


def test_pytest_runner_declares_context_fields():
    assert PytestPlaywrightRunner.generation_context_fields == frozenset(
        {"url", "module", "business_context"})


def test_narrow_runner_does_not_receive_context(use_runner):
    r = use_runner(_NarrowRunner())
    msg = generator.generate_test(
        "desc", "test_x.py", url="http://a", module={"name": "m"},
        business_context="ctx")
    assert msg == "narrow:test_x.py"  # 無 TypeError
    assert r.seen == {"description": "desc", "filename": "test_x.py"}


def test_wide_runner_receives_declared_context(use_runner):
    r = use_runner(_WideRunner())
    msg = generator.generate_test(
        "desc", "test_x.py", url="http://a", module={"name": "m"},
        business_context="ctx")
    assert msg == "wide:test_x.py"
    assert r.seen == {"url": "http://a", "module": {"name": "m"},
                      "business_context": "ctx"}


def test_generator_no_longer_uses_inspect():
    src = Path(_inspect.getsourcefile(generator)).read_text(encoding="utf-8")
    assert "import inspect" not in src, (
        "能力偵測應改讀 runner.generation_context_fields 屬性")
