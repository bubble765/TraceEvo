"""展示层中文化。

对外只有 `translate()` 一个入口。它的契约是：**任何输入都有输出，且输出
永远不依赖网络**。词典覆盖不到、缓存也没有的句子，它会返回带着英文残留
的半成品 —— 而不是抛错，也不是编一个中文词。
"""
from .translator import (BOILERPLATE, CACHE_PATH, KEEP_EN, cache_stats,
                         coverage, dictionary, is_chinese_text, is_fully_zh,
                         residue, save_cache, translate, translate_doc,
                         translate_many)

__all__ = [
    "BOILERPLATE", "CACHE_PATH", "KEEP_EN", "cache_stats", "coverage",
    "dictionary", "is_chinese_text", "is_fully_zh", "residue",
    "save_cache", "translate",
    "translate_doc", "translate_many",
]
