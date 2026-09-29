#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用途地域データ（国土数値情報 A29 第2.1版）の**自治体ごとの利用条件**を構造化する。

  /usr/bin/python3 scripts/parse_a29_conditions.py

**なぜ要るか。** A29 は全国そろっているように見えるが、自治体ごとに条件が違う（2026-09-23 実測）:
  1. オープンデータ公開可
  2. 条件を付して公開可 … 「有償利用不可」「再配信不可」などが付く
  3. 公開不可
  ※資料の提供なし … そもそも収録されていない

**有償の製品に「有償利用不可」の自治体を入れてはいけない。** また「再配信不可」の自治体の
データは配布物に同梱できない。ここを混ぜると規約違反になる。判定できない自治体は
「データがない」ではなく「この自治体は再配布が認められていない」と書いて区別する。

出典: 令和元年度 国土数値情報（用途地域）整備業務 公開に関する利用条件
      https://nlftp.mlit.go.jp/ksj/gml/datalist/Situation_of_the_data_collection_RestrictedZoneData.pdf

出力: data/a29_conditions.json
"""
from __future__ import annotations

import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TXT = os.path.join(ROOT, "data", "a29_municipal_conditions.txt")
OUT = os.path.join(ROOT, "data", "a29_conditions.json")
SRC = ("令和元年度 国土数値情報（用途地域）整備業務 公開に関する利用条件",
       "https://nlftp.mlit.go.jp/ksj/gml/datalist/Situation_of_the_data_collection_RestrictedZoneData.pdf")
ROW = re.compile(r"^(\d{5})\s+(\S+?)\s+(\S+?)\s+(\S+)\s*(.*)$")


def main() -> int:
    if not os.path.exists(TXT):
        print(f"{TXT} がない。先に PDF を落として pdftotext -layout", file=sys.stderr)
        return 1
    items = []
    for ln in open(TXT, encoding="utf-8"):
        m = ROW.match(ln.rstrip())
        if not m:
            continue
        code, pref, city, _kana, rest = m.groups()
        # 公開可否
        if "公開不可" in rest:
            level, usable_paid, redistribute = "公開不可", False, False
        elif "条件を付して公開可" in rest:
            level = "条件つき"
            usable_paid = "有償利用不可" not in rest
            redistribute = "再配信不可" not in rest
        elif "オープンデータ公開可" in rest:
            level, usable_paid, redistribute = "オープンデータ", True, True
        elif "資料の提供なし" in rest:
            level, usable_paid, redistribute = "資料なし", False, False
        else:
            level, usable_paid, redistribute = "不明", False, False
        conds = [c for c in ("有償利用不可", "再配信不可", "改変不可", "出典明示")
                 if c in rest]
        items.append({
            "code": code, "pref": pref, "city": city, "level": level,
            "usable_in_paid_product": usable_paid, "redistributable": redistribute,
            "conditions": conds, "raw": re.sub(r"\s{2,}", " ", rest).strip(),
        })
    if not items:
        print("!! 1件も取れなかった。PDFの体裁が変わった可能性がある", file=sys.stderr)
        return 1
    n = len(items)
    agg: dict[str, int] = {}
    for it in items:
        agg[it["level"]] = agg.get(it["level"], 0) + 1
    json.dump({"source": SRC[0], "source_url": SRC[1], "count": n,
               "summary": agg,
               "note": "usable_in_paid_product が false の自治体は、有償の製品に入れない。"
                       "redistributable が false の自治体は、配布物に同梱しない。",
               "items": items},
              open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"{n:,}自治体 → data/a29_conditions.json")
    for k, v in sorted(agg.items(), key=lambda x: -x[1]):
        print(f"  {k:12} {v:5,} ({v / n * 100:.1f}%)")
    paid = sum(1 for i in items if i["usable_in_paid_product"])
    redis = sum(1 for i in items if i["redistributable"])
    print(f"  → 有償の製品に入れられる {paid:,} ({paid / n * 100:.1f}%) / 同梱配布できる {redis:,} ({redis / n * 100:.1f}%)")
    ng = [i for i in items if not i["usable_in_paid_product"]]
    print(f"  入れられない自治体の例: {[i['pref'] + i['city'] for i in ng[:8]]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
