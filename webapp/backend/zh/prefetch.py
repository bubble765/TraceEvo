"""预翻译：把词典吃不下、又会被展示的句子交模型整句翻译，落盘成缓存。

    python3 -m webapp.backend.zh.prefetch              # 全部病例
    python3 -m webapp.backend.zh.prefetch --cases 85   # 只做 case-85
    python3 -m webapp.backend.zh.prefetch --report     # 只报告覆盖率，不调用

**为什么必须是「预」翻译：** 运行时翻译会让同一个请求在不同时刻得到不同
结果（模型有温度），而界面上的中文一旦会随刷新变化，就没法作为演示材料 ——
「这一页讲的是什么」必须每次都一样。预翻译把不确定性一次性关在缓存文件里，
之后每次运行都是读同一个文件。

**为什么逐批落盘：** 1617 条约 135 次调用，中途遇到网关瞬断是常态。每批
写完就存盘，任务可以随时中断、随时续跑 —— 已完成的批次不会重做。

**译文校验：** 回来的每条都要过 `_accept()`。模型偶尔会把整段原样退回、
或者返回解释性文字。这类结果会被判为不合格并保留英文原文 —— 一个没翻译
的句子比一个被翻译成胡说八道的句子安全得多。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from typing import Dict, Iterable, List, Set

from .translator import (CACHE_PATH, dictionary, is_chinese_text,
                         is_fully_zh, load_cache, residue, save_cache)

GATEWAY = os.environ.get("TRACEEVO_LLM_BASE",
                         "http://localhost:18080/v1")
MODEL = os.environ.get("TRACEEVO_LLM_MODEL", "deepseek-v4-flash")
SESSION = os.environ.get("TRACEEVO_LLM_SESSION", "ses_traceevo_zh_prefetch")

BATCH = 12
MAX_CHARS = 2600          # 单条输入上限；超长的直接跳过（界面也不展示全文）
_JSON_ARR = re.compile(r"\[.*\]", re.S)

SYSTEM = (
    "你是医学文本翻译器。把用户给出的英文医学文本逐条翻译成简体中文，"
    "保持临床病历的语气，术语用国内通行译法。"
    "只输出一个 JSON 字符串数组，长度与输入相同，顺序一致，"
    "不要输出序号、解释或任何其他文字。"
)


# ------------------------------------------------------------------ 收集
def default_roots() -> List[str]:
    """系统**当前可浏览**的全部运行目录。

    从 `config.all_cases()` 推导，而不是写死一个路径。写死过一次，代价是：
    报表只扫了 held-out 那一个目录，于是「覆盖 100%」说的是**那一个目录**
    100%，而界面上还能点开另外 10 个病例 —— 它们的中文缺口不在任何一份
    报表里。覆盖率的分母必须和「用户能看到的范围」是同一个，否则报表越好看
    越有害。
    """
    from ..config import all_cases

    seen, out = set(), []
    for c in all_cases():
        from ..config import run_dir_for
        d = run_dir_for(c["caseId"])
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


def collect(roots, only_cases: Set[int] | None = None) -> Dict[str, List[str]]:
    """扫出所有**会被展示**的英文文本，按来源分类。

    只收展示位置。落在产物里但界面从不显示的字段（哈希、id、原始 payload
    的其余部分）不进这里 —— 翻译了也没人看，白白花调用。
    """
    import glob

    if isinstance(roots, str):
        roots = [roots]

    labels: Set[str] = set()
    queries: Set[str] = set()
    events: Set[str] = set()

    for root in roots:
        for p in sorted(glob.glob(os.path.join(root, "case-*_bindings.json"))):
            if only_cases and _case_no(p) not in only_cases:
                continue
            try:
                b = json.load(open(p, encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            for n in b.get("bindings") or []:
                if n.get("label"):
                    labels.add(str(n["label"]).strip())
            for r in b.get("records") or []:
                if r.get("query"):
                    queries.add(str(r["query"]).strip())

        for p in sorted(glob.glob(os.path.join(root, "case-*_events.jsonl"))):
            if only_cases and _case_no(p) not in only_cases:
                continue
            try:
                fh = open(p, encoding="utf-8")
            except OSError:
                continue
            with fh:
                for line in fh:
                    if not line.strip():
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    payload = e.get("payload") or {}
                    for k in ("response", "analysis", "report", "focus", "query"):
                        v = payload.get(k)
                        if isinstance(v, str) and v.strip():
                            events.add(v.strip()[:MAX_CHARS])

    return {"labels": sorted(labels), "queries": sorted(queries),
            "events": sorted(events)}


def _case_no(path: str) -> int:
    m = re.search(r"case-(\d+)_", os.path.basename(path))
    return int(m.group(1)) if m else -1


# 结构化数据的 repr（JSON 或 Python 字面量）。这类串不是「句子」，翻译它
# 既没有意义（读者看不到），又会把 JSON 碎片写进缓存。它们一度是从
# `_text_of` 的 `str(v)` 漏进来的 —— 源头已修，这里再挡一道，
# 免得将来别的采集路径又把它带回来。
_STRUCTURED = re.compile(r"^\s*[\[{]")


def looks_structured(s: str) -> bool:
    return bool(_STRUCTURED.match(s))


def pending(texts: Iterable[str], cache: Dict[str, str]) -> List[str]:
    """还需要整句翻译的。

    判据必须与 `translator.translate()` 的分流**完全一致** —— 否则会出现
    「预翻译做过的句子运行时仍走词典」或反之，两边各自觉得对方该管。
    这里就是照抄那条规则：>3 个英文词且不在缓存里。
    """
    from .translator import _SHORT_WORDS, _WORD

    out, seen = [], set()
    for t in texts:
        # 去重是必须的：labels / queries / events 三个来源会重复（同一句
        # 话既可能是节点标签又是检索 query），重复项既浪费调用次数，
        # 也会让「总数 - 待翻译」算出负数。
        if not t or len(t) > MAX_CHARS or t in seen:
            continue
        seen.add(t)
        if looks_structured(t):
            continue
        # 已经是中文的不必翻。模型自己产出的中文推理（「结合当前最可信假设
        # ……」）本来就在语料里，把它们排进翻译队列既浪费调用，也会让
        # 覆盖率报告显示一个根本不存在的缺口。
        if is_chinese_text(t):
            continue
        if t in cache:
            continue
        if len(_WORD.findall(t)) <= _SHORT_WORDS:
            continue          # 短语交给词典，术语一致性优先
        out.append(t)
    return out


# ------------------------------------------------------------------ 调用
# 网关前面有一层 Cloudflare，它按 User-Agent 做 bot 拦截。
# `Python-urllib/3.x` 会被挡下并返回 `403 error code: 1010`（没有正文解释，
# 看起来像鉴权失败，实际是 UA 被拉黑）。curl 与常见浏览器 UA 都放行。
# 这个坑值得写在代码里：403 会让人去查 token，而 token 根本没问题。
_UA = "curl/8.7.1"


def _post(payload: dict, timeout: int = 180) -> str:
    """POST 一个 chat 请求，返回首个 choice 的正文。

    **用 `http.client` 而不是 `urllib`，这是量出来的，不是风格偏好。**
    两者发出去的字节不同 —— urllib 把 `Host` 排在第 3 个头、并追加
    `Connection: close`；http.client 把 `Host` 放第一个。网关那个 router
    **只接受后者**：同一份 payload、同一个 UA、同一个 session，
    urllib 一律 `502 Bad Gateway`（连重试 4 次都一样），http.client 一律 200。
    抓包对比过请求头，差异就在顺序上。

    502 看起来像上游故障，会让人去查网关 —— 这也是它值得写在代码里的原因。
    管线（`gos/utils/public_function.py`）走的就是 http.client，所以这条路径
    本来就有一个能用的实现，没必要再养第二套。
    """
    import http.client
    from urllib.parse import urlsplit

    # `HTTPConnection` 要的是 `host[:port]`，不带 scheme —— 直接切字符串会把
    # `http://` 一起塞进去，报出来的是 `gaierror: nodename nor servname`，
    # 看起来像 DNS 挂了，其实是参数传错了。
    u = urlsplit(GATEWAY)
    host = u.netloc or u.path
    # key 优先取 TRACEEVO_API_KEY：与管线用的是同一个（`.env.local` 里就这一个），
    # 而 ANTHROPIC_API_KEY 可能是给别的用途准备的。
    key = (os.environ.get("TRACEEVO_API_KEY")
           or os.environ.get("ANTHROPIC_API_KEY") or "sk-local")
    conn = http.client.HTTPConnection(host, timeout=timeout)
    try:
        conn.request("POST", "/v1/chat/completions",
                     json.dumps(payload).encode("utf-8"),
                     {"Content-Type": "application/json",
                      "Authorization": f"Bearer {key}",
                      "x-opencode-session": SESSION,
                      "User-Agent": _UA})
        res = conn.getresponse()
        raw = res.read().decode("utf-8")
        if res.status != 200:
            raise OSError(f"HTTP {res.status} {res.reason}: {raw[:120]}")
        return json.loads(raw)["choices"][0]["message"]["content"]
    finally:
        conn.close()


def call_batch(batch: List[str], attempts: int = 5) -> List[str] | None:
    """一批翻译。失败指数退避 —— 网关偶发 Internal server error 与连接重置，
    这是已知的、可重试的故障，不该让整轮预翻译从头再来。"""
    user = json.dumps(batch, ensure_ascii=False)
    for i in range(attempts):
        try:
            content = _post({
                "model": MODEL,
                "messages": [{"role": "system", "content": SYSTEM},
                             {"role": "user", "content": user}],
                "max_tokens": 4096,
                "temperature": 0,
            })
            m = _JSON_ARR.search(content)
            if not m:
                raise ValueError(f"响应不是 JSON 数组: {content[:120]}")
            got = json.loads(m.group(0))
            if len(got) != len(batch):
                raise ValueError(f"长度不符：期望 {len(batch)}，得到 {len(got)}")
            return [str(x) for x in got]
        except Exception as e:  # noqa: BLE001
            wait = min(30, 2 ** i)
            print(f"    ! 第 {i + 1}/{attempts} 次失败（{type(e).__name__}: "
                  f"{str(e)[:80]}），{wait}s 后重试", file=sys.stderr)
            if i == attempts - 1:
                return None
            time.sleep(wait)
    return None


def _accept(src: str, out: str) -> bool:
    """译文合格吗。判据是「英文词真的变少了」。

    **不要用「中文占比」当判据。** 医学文本天然夹大量拉丁串（PR3-ANCA、
    FEV1 3.89 L、111% predicted），一条完全正确的译文里中文字符可能只占
    两成。早先这里是 `han < 25%` 就判不合格，实测卡掉的**全是合格译文**：

        PR3-ANCA was elevated and MPO-ANCA was negative
        → PR3-ANCA升高，MPO-ANCA阴性        （4/21 = 19%，被判不合格）

    而它想挡的两件事各自有更准的判据：原样退回由 `out == src` 挡，夹带
    解释由长度上界挡。剩下真正要判的是「到底翻了没有」，那就直接比
    `residue()` —— 源文里有多少个该翻的英文词、译文里还剩几个。
    """
    out = (out or "").strip()
    if not out or out == src.strip():
        return False
    if not re.search(r"[一-鿿]", out):
        return False                     # 一个汉字都没有，不可能是译文
    # 译文不该比原文还长很多（翻译腔或夹带解释的信号）
    if len(out) > max(80, len(src) * 3):
        return False
    return len(residue(out)) < len(residue(src))


# ------------------------------------------------------------------ 主流程
def run(roots, only_cases: Set[int] | None, report_only: bool,
        limit: int | None) -> int:
    groups = collect(roots, only_cases)
    everything = groups["labels"] + groups["queries"] + groups["events"]
    cache = load_cache(force=True)

    total = len(set(everything))
    todo = pending(everything, cache)
    covered = total - len(todo)
    print(f"展示文本去重 {total} 条：词典/缓存已覆盖 {covered} 条，"
          f"待翻译 {len(todo)} 条")
    for name, items in groups.items():
        n = len(pending(items, cache))
        print(f"   {name:8s} {len(items):5d} 条，待翻译 {n:5d}")

    if report_only or not todo:
        _report_coverage(groups, cache)
        return 0

    if limit:
        todo = todo[:limit]

    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    print(f"\n开始 {len(batches)} 批（每批 {BATCH} 条，模型 {MODEL}）")
    ok = fail = 0
    for bi, batch in enumerate(batches, 1):
        got = call_batch(batch)
        if got is None:
            fail += len(batch)
            print(f"  [{bi}/{len(batches)}] 整批失败，跳过")
            continue
        added = 0
        for src, out in zip(batch, got):
            if _accept(src, out):
                cache[src] = out.strip()
                added += 1
        ok += added
        if added:
            save_cache(cache)          # 逐批落盘：中断后可续跑
        print(f"  [{bi}/{len(batches)}] +{added}/{len(batch)}  "
              f"（累计 {len(cache)} 条）")

    print(f"\n完成：新增 {ok} 条，失败 {fail} 条，缓存共 {len(cache)} 条")
    print(f"缓存文件：{CACHE_PATH}")
    _report_coverage(groups, cache)
    return 0


def _report_coverage(groups: Dict[str, List[str]], cache: Dict[str, str]) -> None:
    """覆盖率报告。

    口径有两处刻意为之：

    **分母剔掉结构化碎片与已是中文的文本。** 不剔它们，覆盖率就会被一批
    读者永远看不到的 JSON 片段压低 —— 那是**报表的错**，不是翻译的缺口。

    **分子按 `translate()` 的分支分别判定，不混用一条判据。** 两个分支的
    「覆盖」含义本来就不同：

      * 短语（≤3 词）走**词典**，从不读缓存 ⇒ 覆盖 = `dictionary()` 之后
        没有英文残留。
      * 句子走**缓存**，缓存未命中才退回词典 ⇒ 覆盖 = 在缓存里。

    用一条判据套两支，必错其一。早先这里写的是「在缓存里就算覆盖，否则
    查词典」，看着像统一的判据，实际对短语这一支问了错的问题。
    """
    from .translator import _SHORT_WORDS, _WORD, translate

    print("\n=== 中文覆盖情况 ===")
    for name, items in groups.items():
        items = [t for t in items
                 if not looks_structured(t) and not is_chinese_text(t)]
        bad: List[str] = []
        for t in items:
            if len(_WORD.findall(t)) <= _SHORT_WORDS:
                if residue(dictionary(t)):
                    bad.append(t)
            elif t not in cache:
                bad.append(t)
        rate = (len(items) - len(bad)) / len(items) if items else 1.0
        print(f"  {name:8s} {len(items):5d} 条，仍有英文残留 {len(bad):4d} 条"
              f"（覆盖 {rate:.1%}）")
        for t in bad[:3]:
            print(f"      · {t[:88]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="预翻译展示文本到中文")
    ap.add_argument("--root", default="",
                    help="只扫这个目录；默认扫系统当前可浏览的全部运行目录")
    ap.add_argument("--cases", default="", help="只做这些病例号，逗号分隔")
    ap.add_argument("--report", action="store_true", help="只报告，不调用模型")
    ap.add_argument("--limit", type=int, default=None, help="最多翻译多少条")
    a = ap.parse_args()
    only = {int(x) for x in a.cases.split(",") if x.strip()} or None
    roots = [a.root] if a.root else default_roots()
    print(f"扫描 {len(roots)} 个运行目录")
    return run(roots, only, a.report, a.limit)


if __name__ == "__main__":
    raise SystemExit(main())
