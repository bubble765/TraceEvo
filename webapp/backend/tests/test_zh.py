"""中文化层测试。

这一层不参与任何判定，但它有自己的一套逻辑（最长匹配、词边界、停用词删除、
缓存回退），而那套逻辑出错时的表现是**读起来别扭但看起来正常**的中文 ——
比报错难发现得多，所以要有测试钉住。

断言分三类：
    词典行为   最长匹配不被拆坏、词边界正确、中文间停用词被删
    回退契约   词典吃不下时不留空白、不编造、缓存缺失不崩
    覆盖程度   整份语料的实际覆盖率（回归下限，不是「必须 100%」）
"""
from __future__ import annotations

import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from webapp.backend.zh import (dictionary, is_fully_zh, residue,  # noqa: E402
                               translate, translate_doc)


# ------------------------------------------------------------------ 词典
def test_longest_match_wins():
    """长词必须优先 —— 'blood' 不能把 'white blood cell' 拆坏。"""
    assert "白细胞" in dictionary("White blood cell count")
    assert dictionary("blood loss") != dictionary("white blood cell")


def test_word_boundary_respected():
    """词边界：'CT' 不应命中 'CCTA' 这类更长串的内部。"""
    # 'ct' 在词典里，但 'CCTA' 整体不该被拆
    out = dictionary("CCTA")
    assert "CT" not in out or out == "CCTA", f"CCTA 被拆坏：{out}"


def test_case_insensitive():
    assert dictionary("CHEST PAIN") == dictionary("chest pain")


def test_stopwords_removed_between_chinese():
    """中文之间的英文停用词应被删除，且保留并列关系。"""
    out = dictionary("hypertension and hyperlipidemia")
    assert "and" not in out
    assert "高血压" in out and "高脂血症" in out


def test_consecutive_stopwords_removed():
    """连续停用词（'in the'、'of the'）要一次删净。

    只删单个停用词会把 'the' 留在句中 —— 那是界面上最刺眼的一种残留，
    因为它夹在两个中文词之间，读起来像排版事故。
    """
    out = dictionary("vision loss in the right eye")
    assert " in " not in f" {out} " and " the " not in f" {out} "
    assert "视力下降" in out


def test_leading_trailing_stopwords_removed():
    out = dictionary("of the chest pain")
    assert not out.lower().startswith("of")
    assert "胸痛" in out


# ------------------------------------------------------------------ 回退
def test_unknown_sentence_keeps_english_not_blank():
    """未命中缓存的句子必须**保留英文原样**，不能返回空串，也不能编造。

    一个未翻译的英文句子在界面上一眼可见；一个空白格或一个编造的中文词
    看不出来。缺什么补什么，比猜一个要好。
    """
    s = "Zzz qqq wwww vvvv uuuu"
    out = translate(s)
    assert out, "不得返回空串"
    assert any(w in out.lower() for w in ("zzz", "qqq")), \
        f"未命中部分应保留原样，实际得到：{out}"


def test_short_terms_use_dictionary():
    """短语走词典（术语一致），因此不受缓存内容影响。"""
    assert translate("chest pain") == "胸痛"


def test_residue_reports_what_is_missing():
    """residue() 是「还差哪些词」的答案，补词典时照它补。

    它的契约是**列出所有拉丁词**（白名单除外），不做停用词豁免 ——
    停用词的删除是 `dictionary()` 的职责（且只在两侧均为中文时删）。
    一个夹在中文与英文残片之间的 `and` 是真正的残留信号：它说明这句话
    没被译完。在这里豁免它，就等于把「还差一个词」从报告里抹掉。
    """
    r = residue("胸痛 Zzz")
    assert "zzz" in r
    assert residue("胸痛、发热") == [], "纯中文不应报残留"
    # 白名单里的缩写不算残留
    assert "oct" not in residue("OCT 检查")
    assert "mmhg" not in residue("血压 120 mmHg")


def test_dictionary_removes_stopwords_but_residue_does_not():
    """两者的分工，用同一个输入钉住 —— 免得日后有人「顺手」把停用词
    豁免塞进 residue，那会让真实的残留被静默吞掉。"""
    assert "and" not in dictionary("chest pain and fever")
    assert "and" in residue("胸痛 and Zzz")


def test_residue_exempts_latin_that_is_supposed_to_stay_latin():
    """`residue()` 报的是「没翻译的」，不是「出现了拉丁字母」。

    三类拉丁串在医学文本里本来就该保留：白名单缩写、全大写缩写、
    含数字的（化验值/基因位点）。把它们算作残留，会让一段通顺的中文报告
    被报成未翻译，然后照着这份报告去补词典、去重翻 —— 解决一个不存在的问题。
    """
    zh = "PR3-ANCA 升高，MPO-ANCA 阴性，FEV1 占预计值 111%，HER2 阳性"
    assert residue(zh) == [], f"不该报残留：{residue(zh)}"
    # 但真正没翻的普通英文词仍要报出来 —— 豁免不能宽到把缺口一起吞掉
    assert residue("PR3-ANCA 升高 and Zzz") == ["and", "zzz"]
    assert residue("胸痛 with 发热") == ["with"]


def test_is_fully_zh():
    assert is_fully_zh("chest pain")
    assert not is_fully_zh("Zzz qqq wwww")


# ------------------------------------------------------------------ 样板
def test_boilerplate_translated():
    """memory_formatter 的固定骨架必须整体中文化，且保留编号结构。"""
    doc = ("[Failure Prevention]\n\n"
           "The following items are process guidance from previously "
           "verified executions.\n"
           "They are NOT patient facts and MUST NOT be treated as evidence.\n\n"
           "1. Pattern to avoid: UNSUPPORTED_EVIDENCE.\n"
           "2. Avoid: Do not treat an unsupported agent assertion as "
           "clinical evidence.\n")
    out = translate_doc(doc)
    assert "[失败预防]" in out
    assert "不是患者事实" in out
    assert "需规避的模式" in out
    assert "1." in out and "2." in out, "编号必须保留"


def test_empty_input_is_safe():
    for f in (translate, dictionary, translate_doc):
        assert f("") == ""
        assert f(None) == ""          # type: ignore[arg-type]


# ------------------------------------------------------------------ 覆盖
# ------------------------------------------------------------------ 预翻译
def test_accept_takes_latin_dense_translations():
    """译文合格与否，判据是「英文词真的变少了」，不是「中文占多大比例」。

    医学文本里拉丁串本来就多，一条完全正确的译文可能只有两成汉字。
    按比例卡，卡掉的全是合格译文 —— 下面是实测被误杀的那两条。
    """
    from webapp.backend.zh.prefetch import _accept

    assert _accept("PR3-ANCA was elevated and MPO-ANCA was negative",
                   "PR3-ANCA升高，MPO-ANCA阴性")
    assert _accept(
        "Spirometry shows FEV1 3.89 L (111% predicted), FVC 5.74 L",
        "肺功能检查示FEV1 3.89 L（占预计值111%），FVC 5.74 L")
    # 三种该拒的：原样退回、纯英文、夹带解释的长文
    assert not _accept("chest pain", "chest pain")
    assert not _accept("chest pain and fever", "chest pain and fever")
    assert not _accept("chest pain", "胸痛。" + "解释" * 200)


def test_pending_never_queues_what_the_dictionary_branch_handles():
    """`pending()` 与 `translate()` 的分流必须一致。

    短语（≤3 个英文词）在运行时**永远**走词典、从不读缓存。所以预翻译
    不该给它们排队 —— 排了也白排，译文写进缓存后运行时根本不会读，
    而报表若按「在缓存里就算覆盖」来算，还会报出一个界面并不存在的覆盖率。
    两处各自觉得对方该管，是这类分层最容易出的错。
    """
    from webapp.backend.zh.prefetch import pending

    cache = {"Some long sentence about a patient with fever": "某位发热患者的",
             "chest pain": "胸痛（永远读不到）"}
    todo = pending(["chest pain", "acute pulmonary embolism",
                    "Some long sentence about a patient with fever"], cache)
    assert todo == [], f"短语与已缓存句都不该排队：{todo}"


def _residues(bundle):
    bad = []
    for n in bundle.nodes:
        raw = n.get("label") or ""
        if not raw:
            continue
        r = residue(translate(raw))
        if r:
            bad.append((n.get("node_id"), raw[:60], r))
    return bad


def test_case85_labels_translate_to_chinese_readable(c):
    """case-85 的每个节点标签都必须能译出中文，且不留英文残片。

    **这是回归下限，不是「必须 100%」。** 语料是多科室的，词典永远补不全；
    真正保证全中文的是 `zh_cache.json`（由 `zh/prefetch.py` 生成）。
    """
    bad = _residues(c.bundle)
    assert not bad, "case-85 仍有英文残留：\n" + "\n".join(
        f"  {a}: {b} → {d}" for a, b, d in bad)


def test_demo_case_labels_are_clean():
    """**演示病例**的标签不留英文残片。

    盯的是 `config.DEMO_CASE` 指向的那个，不是写死的某个号 —— 演示病例会换
    （现在是手写的 `webapp/showcase/ankle.json`），把号码焊在这里的话，
    换演示时这条会继续对着旧病例通过，而新演示是花的也没人知道。
    """
    from webapp.backend import config
    from webapp.backend.services import case as case_svc
    demo = config.case_task(config.DEMO_CASE)
    bundle = case_svc.load(demo).bundle
    # 先证明这条测试**真的看到了东西**：读不到节点时 `_residues` 返回空表，
    # 测试会一声不响地通过 —— 那正是它最该报的情况。
    assert len(bundle.nodes) > 0, \
        f"演示病例 {demo} 一个节点都没读到，这条测试会空过"
    bad = _residues(bundle)
    assert not bad, f"演示病例 {demo} 仍有英文残留：\n" + "\n".join(
        f"  {a}: {b} → {d}" for a, b, d in bad)


def test_chinese_layer_does_not_touch_judgements(c):
    """**中文化不得改变任何判定。**

    审计、门控、tier 全部跑在英文原文上；这一层只决定显示什么字。
    如果哪天改词表能让某个节点的 tier 变化，那说明翻译跑到了判定链路上 ——
    那是一个严重的耦合，必须在这里被拦下。
    """
    a = c.audit()
    assert a.tierCounts == {
        "P0_DIRECT_GROUNDED": 6, "P1_SOFT_GROUNDED": 0,
        "P2_DERIVED": 7, "P3_UNSUPPORTED_ASSERTION": 2,
    }
    for t in a.tiers:
        # 度量是数值，与语言无关；标签是中文。两者必须同时成立。
        assert t.tierLabel, f"{t.nodeId} 缺中文等级标签"
        assert isinstance(t.tokenCoverage, (int, float))
