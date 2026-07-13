"""Scanner 家族 registry（P3）— run_tests/runner 之外的第二個工具家族。

新增 scanner：實作一個帶 `name` 與 `scan(...)` 的類別，註冊進 SCANNERS。
server 的 scan 類 tool handler 一律經此 registry 取得實例。
"""
from .api_security import ApiSecurityScanner

SCANNERS: dict[str, object] = {
    "api_security": ApiSecurityScanner(),
}
