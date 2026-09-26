from ..config import RUNNER_NAME
from .base import TestRunner
from .cypress import CypressRunner
from .go_test import GoTestRunner
from .jest import JestRunner
from .maestro import MaestroRunner
from .newman import NewmanRunner
from .pytest_playwright import PytestPlaywrightRunner
from .schemathesis import SchemathesisRunner

REGISTRY: dict[str, type[TestRunner]] = {
    "pytest": PytestPlaywrightRunner,
    "pytest-playwright": PytestPlaywrightRunner,
    "playwright": PytestPlaywrightRunner,
    "jest": JestRunner,
    "cypress": CypressRunner,
    "go": GoTestRunner,
    "go-test": GoTestRunner,
    "maestro": MaestroRunner,
    "mobile": MaestroRunner,
    "schemathesis": SchemathesisRunner,
    "api": SchemathesisRunner,
    "newman": NewmanRunner,
    "postman": NewmanRunner,
}


def get_runner() -> TestRunner:
    cls = REGISTRY.get(RUNNER_NAME)
    if not cls:
        raise ValueError(
            f"未知的 QA_RUNNER: {RUNNER_NAME}。可用: {sorted(REGISTRY)}"
        )
    return cls()


__all__ = ["REGISTRY", "TestRunner", "get_runner"]
