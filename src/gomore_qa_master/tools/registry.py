"""Tool registry — 每個 MCP tool 一筆 ToolSpec，dispatch 與 list_tools 的
單一事實來源（P2）。

新增 tool 的步驟只有一處：在 server.py 寫 handler 並 `register(...)`，
description/inputSchema 放進 schemas.py 的兩個 dict。list_tools 順序 ==
註冊順序。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .schemas import TOOL_DESCRIPTIONS, TOOL_SCHEMAS


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    handler: Callable[[dict], Any]
    # blocking=True 的 handler 由 dispatch offload 到 worker thread（P1），
    # 涵蓋 subprocess / 網路 / 磁碟 I/O（含 P0 verify_plan 的產物檔案讀取）。
    blocking: bool = False


REGISTRY: dict[str, ToolSpec] = {}


def register(name: str, handler: Callable[[dict], Any], *, blocking: bool = False) -> ToolSpec:
    if name not in TOOL_DESCRIPTIONS or name not in TOOL_SCHEMAS:
        raise KeyError(f"tool {name!r} 缺 description/schema（tools/schemas.py）")
    spec = ToolSpec(
        name=name,
        description=TOOL_DESCRIPTIONS[name],
        input_schema=TOOL_SCHEMAS[name],
        handler=handler,
        blocking=blocking,
    )
    REGISTRY[name] = spec
    return spec
