"""共享 fixture。

`case` 是**会话级**的：它读的是封存 artifact，在一次测试运行里不会变，
而且 `services.audit.derive` 自带 mtime 缓存 —— 多个测试文件各读一次
既浪费也会掩盖缓存本身的问题。
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DEMO = "case-85"


@pytest.fixture(scope="session")
def case():
    from webapp.backend.services import case as case_svc
    return case_svc.load(DEMO)


@pytest.fixture(scope="session")
def c(case):
    """别名。测试里 `c.audit()` 比 `case.audit()` 读起来更像主语。"""
    return case
