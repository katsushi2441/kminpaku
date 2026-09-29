#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""民泊（住宅宿泊事業）の上乗せ条例を、自治体ごとの一覧として落とす。

  /usr/bin/python3 scripts/fetch_jorei.py

**なぜ国のPDFを正典にしないか。** 観光庁の「民泊の実施制限に関する地方公共団体の条例の
とりまとめ」は令和3年4月1日時点・58自治体で止まっている（2026-09-23 実測）。その後に
墨田区・葛飾区（令和7年12月）、江戸川区・高槻市（令和8年3月）などが制定していて、
古い表をそのまま見せると「制限なし」と読める自治体が出る。これは事故になる。

なので **一般財団法人 地方自治研究機構の追跡ページ（令和8年4月1日時点・62自治体）を
一覧の出どころにし、制限の中身は国のPDFから取る**。両方の時点を data に残し、
画面には必ず「いつ時点のものか」と条例名・公布日・施行日を出す。

出力: data/jorei_list.json（自治体・条例名・公布日・施行日・類型・出典）
     data/jorei_pdf.txt（国のPDFの本文。制限の中身を読む元）
"""
from __future__ import annotations

import html
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
RILG = "https://www.rilg.or.jp/htdocs/img/reiki/071_minpaku.htm"
PDF = "https://www.mlit.go.jp/kankocho/minpaku/content/001418351.pdf"
# 表の並び（RILG のページ内の順序）。表ごとに自治体の種別が違う
KINDS = ["特別区", "市", "都道府県", "その他"]


def get(url: str, out: str | None = None) -> str:
    cmd = ["curl", "-sS", "--compressed", "--max-time", "60", "-A", UA]
    if out:
        cmd += ["-o", out, "-w", "%{http_code}"]
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    if out:
        if r.stdout.strip() != "200":
            raise RuntimeError(f"{url}: HTTP {r.stdout.strip()}")
        return out
    return r.stdout


def cells(tr: str) -> list[str]:
    return [html.unescape(re.sub(r"<[^>]+>", "", c)).replace("　", " ").strip()
            for c in re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", tr)]


def main() -> int:
    os.makedirs(DATA, exist_ok=True)
    s = get(RILG)
    rows = []
    for i, t in enumerate(re.findall(r"(?is)<table.*?</table>", s)):
        kind = KINDS[i] if i < len(KINDS) else "その他"
        for tr in re.findall(r"(?is)<tr.*?</tr>", t):
            c = cells(tr)
            if len(c) < 4 or not c[0] or "条例" not in c[1]:
                continue
            rows.append({
                "kind": kind,
                "govt": c[0],
                "jorei": c[1],
                "promulgated": c[2],
                "enforced": c[3],
                "type": c[4] if len(c) > 4 else "",
                "source": RILG,
            })
    if not rows:
        print("!! 条例を1件も取れなかった。RILG のページ構造が変わった可能性がある", file=sys.stderr)
        return 1
    json.dump({"asof": "令和8年4月1日時点（出典ページの記載）", "source": RILG, "count": len(rows), "items": rows},
              open(os.path.join(DATA, "jorei_list.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"条例 {len(rows)}件 → data/jorei_list.json")
    for k in KINDS:
        n = sum(1 for r in rows if r["kind"] == k)
        if n:
            print(f"  {k}: {n}")

    # 制限の中身は国のPDFから（令和3年4月1日時点）
    pdf = os.path.join(DATA, "jorei_torimatome_r3.pdf")
    get(PDF, pdf)
    txt = os.path.join(DATA, "jorei_pdf.txt")
    subprocess.run(["pdftotext", "-layout", pdf, txt], check=True)
    print(f"国のとりまとめ（令和3年4月1日時点）→ {txt} "
          f"{len(open(txt, encoding='utf-8').read().splitlines()):,}行")
    return 0


if __name__ == "__main__":
    sys.exit(main())
