"""展示层中文化：词典优先，整句缓存兜底。

**这一层不参与任何判定。** 审计、门控、tier 分级全部跑在英文原文上；这里
只决定「界面上显示什么字」。所以改词表、改缓存都不会让任何一个判定结果
发生变化 —— 这是它敢放在展示层的前提。

三级回退，顺序是刻意的：

  1. **词典**（`terms.py`，821 条）。确定性、无需模型、术语一致。
     术语一致性是有价值的：同一个 `retinal vein occlusion` 在全文里
     必须是同一个中文说法，否则读者会以为是两个不同的概念。整句翻译
     做不到这一点（同一术语在不同句子里可能被译成不同措辞）。
  2. **整句缓存**（`data/zh_cache.json`）。只有词典**覆盖不全**的句子
     才走这里 —— 即替换后仍残留英文单词的那些。缓存由 `prefetch.py`
     一次性生成并落盘，运行时只读文件，不联网、结果恒定。
  3. **原文**。缓存里也没有就显示英文。这是**故意**的：一个未翻译的
     英文句子在界面上一眼可见，而一个编造的中文词看不出来。
     缺什么补什么，比猜一个要好。

`residue()` 是这套机制的眼睛：它回答「这句话还有没有英文残留」。
词典层判断该不该走缓存、自检脚本判断覆盖率，都用它。
"""
from __future__ import annotations

import json
import os
import re
from typing import Dict, Iterable, List

from .terms import TERMS

_DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "data")
CACHE_PATH = os.path.join(_DATA, "zh_cache.json")

# 英文停用连接词：只在**两侧都是中文**时删除（"胸痛 及 气促" → "胸痛气促"）。
# 含英文残留时不删 —— 否则 "National Institute of Health" 会被拆成
# "的/位于" 这种既非中文也非英文的东西。
_STOP_WORDS = (
    "due to", "and", "or", "with", "of", "the", "in", "on", "to", "from",
    "at", "for", "was", "were", "is", "are", "be", "been", "as", "by",
    "than", "then", "but", "if", "when", "while", "a", "an",
)

# 按词条长度降序 + 词边界。长度降序是为了让 "white blood cell" 先于
# "blood" 命中；没有这一条，"blood" 会把长词拆坏。
_COMPILED = sorted(
    ((re.compile(r"(?<![A-Za-z])" + re.escape(en) + r"(?![A-Za-z])", re.I), zh)
     for en, zh in TERMS.items()),
    key=lambda p: -len(p[0].pattern),
)

_STOP_PAT = "|".join(re.escape(w) for w in _STOP_WORDS)
# 单个停用词不够用：'in the'、'of the'、'is not' 这类**连续**停用词序列
# 在译文里成串出现，一次只匹配一个会把剩下的留在句中。所以这里允许
# 「停用词 + 任意空白/顿号 + 停用词」重复出现。
_STOP_SEQ = rf"(?:{_STOP_PAT})(?:[\s,，、]*(?:{_STOP_PAT}))*"

# 中文之间的停用词序列。两侧的标点一并吞掉 —— 否则会留下
# 「高血压, 高脂血症、 多发卒中」这种半截标点。
_STOP_BETWEEN_ZH = re.compile(
    rf"(?<=[一-鿿])[\s,，、]*{_STOP_SEQ}[\s,，、]*(?=[一-鿿])", re.I)
_LEADING = re.compile(rf"^(?:{_STOP_SEQ})[\s,，、]+", re.I)
_TRAILING = re.compile(rf"[\s,，、]+(?:{_STOP_SEQ})$", re.I)
_WS = re.compile(r"\s+")
# 中文之间残留的英文逗号统一成顿号，避免中英标点混排
_ZH_COMMA = re.compile(r"(?<=[一-鿿])\s*,\s*(?=[一-鿿])")
# 两个汉字之间的空格：英文分词留下的痕迹，中文里不该有。
# 只匹配「汉字 空格 汉字」，不碰汉字与数字/拉丁字母的边界。
_ZH_SPACE = re.compile(r"([一-鿿])\s+([一-鿿])")
# 连续顿号折叠
_DUP_PUNCT = re.compile(r"[、,，]{2,}")


def _drop_seq(m: "re.Match[str]") -> str:
    """删除停用词序列；若序列里本来有分隔标点，用一个顿号顶上。

    全删会让「高血压 高脂血症 多发卒中」粘成一串 —— 原句里的逗号承载了
    并列关系，删词时不该把它一起删掉。
    """
    return "、" if re.search(r"[,，、]", m.group(0)) else ""

# 残留检测：长度 >=2 的拉丁字母串。单字母不算（"A" 可能是分级，"x" 可能是乘号）。
_EN_WORD = re.compile(r"[A-Za-z]{2,}")

# 这些词**本来就不该翻译**：缩写、专有名词、单位、已被目标读者当作术语的
# 拉丁串。列在这里而不是从残留里排除，是为了让「允许保留的英文」是一个
# 可审阅的清单 —— 每加一个都有人要想一下。
KEEP_EN = {
    "oct", "ct", "mri", "x", "ray", "ecg", "ekg", "crp", "bun", "mmhg",
    "um", "lm", "cm", "mm", "kg", "mg", "ml", "dl", "iu", "hba1c",
    "nginx", "kafka", "kubernetes", "redis", "mysql", "http", "api", "url",
    "json", "csv", "sql", "gc", "cpu", "gpu", "id", "kb", "rag", "llm",
    "gos", "v", "i", "ii", "iii",
    # 分级/编号类
    "l1", "l2", "l3", "p0", "p1", "p2", "p3", "a0", "a1", "a2", "a3",
    "a4", "a5", "b0", "b1", "b2", "b3",
}


def dictionary(text: str) -> str:
    """术语级替换。未命中的部分保留英文原样。"""
    s = str(text or "")
    if not s:
        return ""
    for pat, zh in _COMPILED:
        s = pat.sub(zh, s)
    s = _STOP_BETWEEN_ZH.sub(_drop_seq, s)
    s = _LEADING.sub("", s)
    s = _TRAILING.sub("", s)
    s = _ZH_COMMA.sub("、", s)
    s = _DUP_PUNCT.sub("、", s)
    s = _WS.sub(" ", s).strip()
    # 逐词替换后，相邻的两个中文词之间会留下原文的空格：
    # "Syndesmotic ankle injury" → "下胫腓联合 踝关节 损伤"。中文之间不写
    # 空格，那三个空格纯粹是英文分词留下的痕迹。
    #
    # **只在两侧都是汉字时才收掉。** 不碰「汉字-数字/拉丁」的边界 ——
    # "视力下降 1.5年"、"评分 3 分" 里的空格是有意义的，一起收掉会把
    # 数字和单位粘成一团。汉字范围取 CJK 统一表意文字主体区，够用且不误伤。
    s = _ZH_SPACE.sub(r"\1\2", s)
    return s


def _is_latin_ok(token: str) -> bool:
    """这个拉丁串是**本来就该保留的**吗。

    三类，都是医学文本里的常态，不是「没翻译」：
      * 白名单里的缩写与单位（OCT / mmHg / CRP）
      * 全大写缩写（PR3-ANCA / CRPC / HER2 / FDG-PET）—— 临床上就是写拉丁
      * 含数字的（FEV1 / CD207 / BRAF-V600E / 20/200）

    不豁免它们，`residue()` 会把一段通顺的中文报告报成「未翻译」，而
    照着这个报告去补词典、去重翻，是在解决一个不存在的问题。
    """
    if token.lower() in KEEP_EN:
        return True
    if any(ch.isdigit() for ch in token):
        return True
    return token.isupper()


def residue(text: str) -> List[str]:
    """文本里仍然残留的英文单词（去重、小写、按出现顺序）。

    这就是「还差哪些词」的答案。补词典时照它补即可。
    """
    out: List[str] = []
    for m in _EN_WORD.finditer(text or ""):
        raw = m.group(0)
        if _is_latin_ok(raw):
            continue
        w = raw.lower()
        if w not in out:
            out.append(w)
    return out


# 已经是中文的判据：CJK 字符占非空白字符的三分之一以上。
# 阈值取 1/3 而不是 1/2：中文里夹拉丁缩写很常见（「患者 HER2 阳性，
# FDG-PET/CT 显示高代谢」），按 1/2 会把这类句子判成英文。
_CJK = re.compile(r"[一-鿿]")


def is_chinese_text(text: str) -> bool:
    s = re.sub(r"\s", "", str(text or ""))
    if not s:
        return False
    return len(_CJK.findall(s)) / len(s) >= 0.34


def is_fully_zh(text: str) -> bool:
    """词典能否独立吃下这句话。"""
    return not residue(dictionary(text))


# ------------------------------------------------------------------ 缓存
_CACHE: Dict[str, str] = {}
_LOADED = False


def load_cache(force: bool = False) -> Dict[str, str]:
    global _CACHE, _LOADED
    if _LOADED and not force:
        return _CACHE
    try:
        with open(CACHE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        _CACHE = {str(k): str(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError):
        # 缓存缺失或损坏都退回空表：翻译是增强，不是依赖项。一个坏掉的
        # 缓存文件不该让整个系统起不来 —— 它会静默降级到「词典 + 原文」。
        _CACHE = {}
    _LOADED = True
    return _CACHE


def save_cache(cache: Dict[str, str]) -> None:
    os.makedirs(_DATA, exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=1, sort_keys=True)


def cache_stats() -> Dict[str, int]:
    return {"entries": len(load_cache())}


# 短术语的判定阈值。见 `translate()` 的说明。
_SHORT_WORDS = 3
_WORD = re.compile(r"[A-Za-z]+")


def translate(text: str) -> str:
    """中文化主入口。运行时**不联网**。

    两条路径，按文本长度分：

    **短语（≤3 个英文词）走词典。** 短语不存在语序问题，而术语一致性恰恰
    在这里最重要 —— `retinal vein occlusion` 在全文里必须是同一个说法，
    否则读者会以为是两个不同的病。模型逐句翻译做不到这种一致。

    **句子（>3 个词）走缓存，退回词典。** 词典是逐词替换，中文和英文的
    词序不同，替换完就是「无 临床实验室证据眼内炎症 被发现」这种词序错乱
    的产物 —— 每个词都对，整句读不通。整句翻译（缓存）没有这个问题。
    缓存未命中时仍退回词典：能读懂的半成品好过一个空白。
    """
    s = str(text or "").strip()
    if not s:
        return ""
    if len(_WORD.findall(s)) <= _SHORT_WORDS:
        return dictionary(s)

    cached = load_cache().get(s)
    if cached:
        return cached
    # 句子且无缓存 —— 退回词典。它可能带英文残留或语序失真，但都是
    # **看得见**的；运行 prefetch 即可补齐。
    return dictionary(s)


# ------------------------------------------------------------------ 固定样板
# `traceevo.evolution.memory_formatter` 生成的注入块里有若干**固定句子**。
# 它们不是医学内容，是模板骨架，所以按整句精确匹配处理 —— 交给词典会被
# 逐词拆开（"process guidance" 之类的组合词根本不在词典里），交给模型
# 又是在为一个恒定不变的字符串反复付费。
BOILERPLATE = {
    "[Trusted Prior Experience]": "[可信历史经验]",
    "[Failure Prevention]": "[失败预防]",
    "The following items are process guidance from previously verified executions.":
        "以下条目来自既往已验证执行的过程指引。",
    "They are NOT patient facts and MUST NOT be treated as evidence.":
        "它们不是患者事实，不得当作证据使用。",
    "Use them only to guide planning, tool/evidence checks, and collaboration.":
        "仅用于指导规划、工具与证据核查、以及协作分工。",
    "The current patient's evidence always has priority.":
        "当前患者的证据始终优先。",
}

# 条目前缀。"Pattern to avoid: X." 的骨架是固定的，只有 X 需要翻译。
_ITEM_PREFIX = (
    ("Pattern to avoid:", "需规避的模式："),
    ("Avoid:", "规避："),
    ("Check:", "检查："),
)


def translate_doc(text: str) -> str:
    """整块文本（多行、可能带编号）的中文化。

    逐行处理并**保留原有结构**（空行、编号、缩进）—— 这是一段会被注入
    提示词的文本，结构本身就是内容的一部分；把它压成一段话会让「哪条是
    哪条」消失。
    """
    if not text:
        return ""
    out: List[str] = []
    for line in str(text).splitlines():
        stripped = line.strip()
        if not stripped:
            out.append("")
            continue
        if stripped in BOILERPLATE:
            out.append(BOILERPLATE[stripped])
            continue
        # "1. Pattern to avoid: X." -> 保留编号，翻译骨架与内容
        m = re.match(r"^(\d+\.\s*)(.*)$", stripped)
        num, body = (m.group(1), m.group(2)) if m else ("", stripped)
        for en, zh in _ITEM_PREFIX:
            if body.startswith(en):
                out.append(num + zh + " " + translate(body[len(en):].strip()))
                break
        else:
            out.append(num + translate(body))
    return "\n".join(out)


def translate_many(texts: Iterable[str]) -> Dict[str, str]:
    """批量翻译。返回 {原文: 译文}，供一次性渲染多个字段。"""
    out: Dict[str, str] = {}
    for t in texts:
        s = str(t or "").strip()
        if s and s not in out:
            out[s] = translate(s)
    return out


def coverage(texts: Iterable[str]) -> Dict[str, object]:
    """覆盖率自检：哪些句子还残留英文。供 prefetch 与测试使用。"""
    total = 0
    bad: List[Dict[str, object]] = []
    for t in texts:
        s = str(t or "").strip()
        if not s:
            continue
        total += 1
        r = residue(translate(s))
        if r:
            bad.append({"text": s, "residue": r})
    return {
        "total": total,
        "clean": total - len(bad),
        "rate": (total - len(bad)) / total if total else 1.0,
        "uncovered": bad,
    }
