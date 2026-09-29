#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""国のとりまとめPDF（令和3年4月1日時点）から、自治体ごとの制限を取り出す。

  /usr/bin/python3 scripts/parse_jorei_pdf.py

**行単位で「区域」「期間」に割らない。** PDFの表は見出しが欄の縦中央に置かれ、本文は
見出し行の上下にまたがって折り返す（2026-09-23 実測）。行で割ると「の地域」「い日を除く
期間」のような切れ端になる。条例の内容を取り違えると、営業できない場所を「できる」と
見せる事故になる。

そこで欄の左右を実測で分け、**折り返しを繋いでから**区域と期間の対に戻す:
  1. 右端の「趣旨」欄を落とす（本文行で趣旨が始まる位置の最小値で切る）
  2. 見出し列（区域・期間・①②③）と本文列を、本文の書き出し位置で分ける
  3. 本文を順に溜め、見出しが来たらその塊の名前にする。次の見出しか空行で塊を閉じる
原文（raw）も必ず残す。画面には出典PDFへのリンクと時点（令和3年4月1日）を出す。

出力: data/jorei_limits.json
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TXT = os.path.join(ROOT, "data", "jorei_pdf.txt")
OUT = os.path.join(ROOT, "data", "jorei_limits.json")
PDF_URL = "https://www.mlit.go.jp/kankocho/minpaku/content/001418351.pdf"
HEAD = re.compile(r"^(.+?)における主な条例内容")
SP = "[\\s　]"

# 判定に使う語（条例の評価ではなく、語が書いてあるかどうか）
ZONES = {
    "住居専用地域": ["低層住居専用地域", "中高層住居専用地域", "住居専用地域"],
    "住居地域": ["第一種住居地域", "第二種住居地域", "準住居地域"],
    "田園住居地域": ["田園住居地域", "田園居住地域"],
    "学校等の周囲": ["小学校", "中学校", "学校"],
    "児童福祉施設等の周囲": ["児童福祉施設", "保育所", "こども園", "幼稚園"],
    "文教地区": ["文教地区"],
    "国立公園等": ["国立公園", "国定公園", "国民保養温泉地"],
    "景観地区": ["景観地区"],
    "全域": ["区内全域", "市内全域", "町内全域", "県内全域", "全域"],
}
PERIODS = {
    "日曜": ["日曜日"], "土曜": ["土曜日"], "休日・祝日": ["休日"],
    "年末年始": ["年末年始"], "学校の休業日": ["授業を行わない日", "休業日"],
}


def flat(s: str) -> str:
    return re.sub(SP + "+", "", s)


def cut_shushi(lines: list[str]) -> list[str]:
    """右端の「趣旨」欄を落とす。

    見出し行の「趣旨」の位置（例: 48）と、本文行で趣旨が始まる位置（例: 41）はずれる。
    見出しで切ると趣旨の文が残る（2026-09-23 実測）。本文行を見て、
    4つ以上の空白のあとに始まる右寄りの塊の開始位置を集め、その最小値で切る。
    """
    cands = []
    for ln in lines:
        last = None
        for m in re.finditer(r" {4,}(?=\S)", ln):
            last = m
        if last and last.end() > 25:
            cands.append(last.end())
    if not cands:
        return [ln.rstrip() for ln in lines]
    col = min(cands)
    return [(ln[:col] if len(ln) > col else ln).rstrip() for ln in lines]


def rebuild(lines: list[str]) -> list[dict]:
    """折り返しを繋いで、区域／期間の対に戻す。"""
    head_re = re.compile("^" + SP + "*(?:[①-⑳]" + SP + "*)?(?:区域|期間)?" + SP + "*")
    # 表より下の「※…に該当する場合に適用」以降は列が違うので、ここで切る
    tbl = []
    for ln in lines:
        if ln.lstrip().startswith("※上記") or re.match(r"^[\s\u3000]*※[^\n]*適用", ln):
            break
        tbl.append(ln)
    lines = tbl
    starts = [m.end() for ln in lines
              for m in [head_re.match(ln)] if m and m.end() < len(ln) and m.end() > 0]
    if not starts:
        return []
    col = min(starts)
    cells: list[dict] = []
    buf: list[str] = []
    label: str | None = None

    def close() -> None:
        nonlocal buf, label
        t = flat("".join(buf))
        if label and t:
            cells.append({"kind": label, "text": t})
        buf, label = [], None

    for ln in lines:
        if not ln.strip():
            close()
            continue
        head, text = ln[:col], ln[col:].strip()
        # 本文の無い ①②③ の行は、欄と欄の境目（縦中央に置かれた通し番号）
        if re.search("[①-⑳]", head) and not text:
            close()
            continue
        m = re.search(r"(区域|期間)", head)
        if m:
            if label:                      # 既に名前の付いた塊があれば、そこで閉じる
                close()
            label = m.group(1)
        if text and not re.fullmatch("[①-⑳※" + SP + "]*", text):
            buf.append(text)
    close()
    return cells


def main() -> int:
    if not os.path.exists(TXT):
        print("data/jorei_pdf.txt がない。先に scripts/fetch_jorei.py", file=sys.stderr)
        return 1
    lines = open(TXT, encoding="utf-8").read().splitlines()
    blocks: dict[str, list[str]] = {}
    cur = None
    for ln in lines:
        m = HEAD.match(ln.strip())
        if m:
            cur = m.group(1).strip()
            blocks[cur] = []
            continue
        if cur is not None:
            blocks[cur].append(ln)

    out = []
    for govt, body in blocks.items():
        body = cut_shushi(body)
        keep: list[str] = []
        for ln in body:
            t = ln.rstrip()
            if not t.strip():
                if keep and keep[-1] != "":
                    keep.append("")
                continue
            if "事業の実施制限" in t:        # 表の見出し行
                continue
            keep.append(t)
        raw = "\n".join(keep).strip("\n")
        limits = rebuild(keep)
        f = flat(raw)
        cond = []
        m = re.search(r"※[^\n]*適用(.*)$", raw, re.S)
        if m:
            cond = [flat(c) for c in re.findall(
                r"（[０-９\\d一二三四五六七八九]）(.+?)(?=（[０-９\\d一二三四五六七八九]）|$)",
                m.group(1), re.S)]
        out.append({
            "govt": govt,
            "zones": [z for z, ws in ZONES.items() if any(w in f for w in ws)],
            "periods": [p for p, ws in PERIODS.items() if any(w in f for w in ws)],
            "limits": limits,
            "conditions": cond,
            "raw": raw,
            "asof": "令和3年4月1日時点",
            "source": "観光庁「民泊の実施制限に関する地方公共団体の条例のとりまとめについて」",
            "source_url": PDF_URL,
        })
    json.dump({"asof": "令和3年4月1日時点", "count": len(out),
               "note": "zones/periods は語の一致で付けた目印。判断の根拠は limits と raw（原文）と出典PDF。",
               "items": out},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{len(out)}自治体 → data/jorei_limits.json")
    print(f"  区域と期間の対が取れた {sum(1 for o in out if o['limits'])} / 区域の語 {sum(1 for o in out if o['zones'])}")
    for o in out[:3]:
        print(f"\n▼ {o['govt']}  区域={o['zones']}  期間={o['periods']}")
        for it in o["limits"]:
            print(f"    {it['kind']}: {it['text'][:70]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
