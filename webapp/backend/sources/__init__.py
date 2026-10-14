"""数据源工厂。

两个实现，交出**同一个** `ArtifactBundle` 协议，上层代码不分叉：

  * `artifact`  —— 落盘的运行产物（封存的 held-out 与真实分析都是它）
  * `showcase`  —— 手写的展示轨迹（见 `showcase.py` 里那条边界说明）

分发放在这里、按病例号进行：调用方（`services/case.py`）只管 `load(id)`，
不需要知道这个 id 背后是跑出来的还是编排的 —— 需要知道的地方只有一处，
就是审计层，而它读的是 `bundle.kind`。

第二个实现是照着第一阶段留的接口加的（原文：「第二阶段加入 live 时，
**上层代码不需要改**」）—— 这次加 showcase，`services/` 里确实一行没动。
"""
from __future__ import annotations

from typing import Any, Dict, List

from . import showcase
from .artifact import ArtifactSource
from .base import ArtifactBundle, DataSource

_ARTIFACT: DataSource = ArtifactSource()


class _Dispatch:
    """按病例号选数据源。展示轨迹优先 —— 它的 id 是保留的（`case-ankle`）。"""

    def load(self, case_id: str) -> ArtifactBundle:
        if showcase.find(case_id):
            return showcase.ShowcaseSource().load(case_id)
        return _ARTIFACT.load(case_id)

    def available(self) -> List[Dict[str, Any]]:
        # **这里是唯一一处拼病例列表的地方。** `config.all_cases` 只管
        # 产物型病例（封存块 + 真实运行），展示轨迹由它的数据源自己加 ——
        # 两边各加一次会让同一个 id 出现两条、形状还不一样（见
        # `showcase.case_refs` 的说明）。
        return _ARTIFACT.available() + showcase.case_refs()


_SOURCE: DataSource = _Dispatch()


def get_source() -> DataSource:
    return _SOURCE


def showcases() -> List[Dict[str, Any]]:
    """展示轨迹登记表（catalogue 原始形状）。"""
    return showcase.catalogue()


__all__ = ["ArtifactBundle", "ArtifactSource", "DataSource", "get_source",
           "showcases"]
