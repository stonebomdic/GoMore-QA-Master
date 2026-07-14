"""P1 — dispatch 邊界 offload：阻塞型 tool 不得卡住 event loop。

用 fake runner（run_tests 內 time.sleep）+ asyncio.gather 併發一慢一快，
斷言快的完成時間戳早於慢的 sleep 時長（證明慢的沒有霸佔 loop），
且 offload 後回傳值與同步呼叫逐字相同。
"""
from __future__ import annotations

import asyncio
import json
import time

from gomore_qa_master import server

SLOW_S = 0.3


def _fake_run_tests(**kwargs):
    time.sleep(SLOW_S)
    return {"ok": True, "kwargs_seen": sorted(kwargs)}


def test_slow_run_tests_does_not_block_fast_tool(monkeypatch):
    monkeypatch.setattr(server.runner, "run_tests", _fake_run_tests)

    done_at: dict[str, float] = {}

    async def timed(key: str, name: str, args: dict):
        result = await server._dispatch(name, args)
        done_at[key] = time.monotonic()
        return result

    async def main():
        t0 = time.monotonic()
        slow, fast = await asyncio.gather(
            timed("slow", "run_tests", {}),
            timed("fast", "get_runner_info", {}),
        )
        return t0, slow, fast

    t0, slow_res, _fast_res = asyncio.run(main())

    # 快的 tool 必須在慢的 sleep 完成之前就回來 —— 同步 dispatch 會讓
    # 快的被卡到 t0 + SLOW_S 之後才開始跑。
    assert done_at["fast"] - t0 < SLOW_S, (
        f"get_runner_info took {done_at['fast'] - t0:.3f}s — "
        f"event loop was blocked by run_tests"
    )
    assert done_at["fast"] < done_at["slow"]

    # offload 不得改變回傳內容：與直接同步呼叫逐字相同。
    payload = json.loads(slow_res[0].text)
    direct = _fake_run_tests(filter=None, headed=False, browser="chromium")
    assert payload == direct


def test_offloaded_tools_return_value_unchanged(monkeypatch):
    """list_tests 經 offload 後輸出逐字不變。"""
    monkeypatch.setattr(server.runner, "list_tests", lambda: "tests/test_a.py::test_x")
    result = asyncio.run(server._dispatch("list_tests", {}))
    assert result[0].text == "tests/test_a.py::test_x"
