"""比對 CRUD 寫入值與讀回值。"""
from __future__ import annotations


def diff_readback(written: dict, readback: dict) -> list[str]:
    """回傳「寫入值與讀回值不符」的 key 清單。

    只檢查 written 內的 key（server 自行新增的欄位不算不符）。
    """
    return [k for k, v in written.items() if readback.get(k) != v]
