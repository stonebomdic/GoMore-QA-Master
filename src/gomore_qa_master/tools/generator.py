from ..config import PROJECT_ROOT
from ..runners import get_runner
from ..security import validate_filename


def generate_test(
    description: str,
    filename: str,
    url: str | None = None,
    module: dict | None = None,
    business_context: str | None = None,
) -> str:
    """Pass url/module/business_context through only to runners that declare
    them in `generation_context_fields` — narrow-signature runners
    (jest/cypress/go_test) would TypeError on extra kwargs."""
    ok, result = validate_filename(filename, PROJECT_ROOT)
    if not ok:
        return f"error: {result}"
    runner = get_runner()
    context = {"url": url, "module": module, "business_context": business_context}
    extra = {k: v for k, v in context.items()
             if k in runner.generation_context_fields}
    return runner.generate_test(description, filename, **extra)


def codegen(url: str, output: str = "recorded_test.py") -> str:
    ok, result = validate_filename(output, PROJECT_ROOT)
    if not ok:
        return f"error: {result}"
    return get_runner().codegen(url, output)
