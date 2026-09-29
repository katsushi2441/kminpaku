#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用途地域（都市計画決定GISデータ・全国）を都道府県ごとに落とす。

  /usr/bin/python3 scripts/fetch_youto.py --pref 東京都
  /usr/bin/python3 scripts/fetch_youto.py --all

**国土数値情報 A29 を使わない。** A29 第2.1版（2019年度）は自治体ごとに利用条件が違い、
1,213自治体のうち **有償利用不可・公開不可・回答なしが201（16.6%）** ある（2026-09-23 実測）。
大阪市・京都市・大田区・渋谷区・豊島区・八王子市・那覇市など、民泊で肝心な自治体が入って
いない。有償の製品に入れると規約違反になる。

代わりに **国土交通省 都市局「都市計画決定GISデータ 全国」**（令和7年度版）を使う:
  https://www.mlit.go.jp/toshi/tosiko/toshi_tosiko_tk_000087.html
  - 国土交通省の責任で全国分を整備し、無償で提供している
  - 自治体ごとの有償利用不可・再配信不可という条件が付いていない
  - 令和7年度版。A29（2019年度）より6年新しい
  - ただし **「建築確認申請や不動産重要事項説明等の手続に用いることを保証するものではなく、
    参考情報として利用を想定」** と明記されている。画面に必ずこのまま書く。
  - 「地方公共団体がGISデータを保有していない等の理由で掲載されていないデータがある」
    → 収録されていない自治体は「区域外」ではなく「未収録」と書く（data/youto_coverage.json）

落とすもの: 都道府県ごとの GeoJSON zip → /mnt/data/kminpaku/raw/youto/<都道府県>.zip
一覧ページ（zipのURL）は毎回読み直す。URLはコンテンツ番号で、貼り替えられることがある。
"""
from __future__ import annotations

import argparse
import html
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = "/mnt/data/kminpaku/raw/youto"
LIST = "https://www.mlit.go.jp/toshi/tosiko/toshi_tosiko_tk_000182.html"
BASE = "https://www.mlit.go.jp"
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")
WAIT = 2.0


def get(url: str, out: str | None = None) -> str:
    cmd = ["curl", "-sS", "--compressed", "--max-time", "600", "-A", UA]
    if out:
        cmd += ["-o", out, "-w", "%{http_code}"]
    cmd.append(url)
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=700)
    if out:
        code = r.stdout.strip()
        if code != "200":
            raise RuntimeError(f"{url}: HTTP {code}")
        return out
    return r.stdout


def links() -> dict[str, str]:
    """都道府県 → GeoJSON zip の URL。一覧ページを毎回読む。"""
    s = get(LIST)
    out = {}
    for tr in re.findall(r"(?is)<tr.*?</tr>", s):
        cells = re.findall(r"(?is)<t[dh][^>]*>(.*?)</t[dh]>", tr)
        if len(cells) < 4:
            continue
        pref = html.unescape(re.sub(r"<[^>]+>", "", cells[0])).strip()
        if not pref.endswith(("都", "道", "府", "県")):
            continue
        m = re.search(r'href="([^"]+\.zip)"', cells[3])      # 4列目 = GeoJSON形式
        if m:
            out[pref] = m.group(1) if m.group(1).startswith("http") else BASE + m.group(1)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pref", action="append", default=[])
    ap.add_argument("--all", action="store_true")
    a = ap.parse_args()
    os.makedirs(RAW, exist_ok=True)
    urls = links()
    print(f"一覧に {len(urls)}都道府県")
    if not urls:
        print("!! zip のURLが取れない。一覧ページの体裁が変わった可能性がある", file=sys.stderr)
        return 1
    targets = list(urls) if a.all else [p for p in a.pref if p in urls]
    if not targets:
        print("--pref <都道府県> か --all を指定する。使える名前:", ", ".join(list(urls)[:8]), "…")
        return 1
    for pref in targets:
        dst = os.path.join(RAW, f"{pref}.zip")
        if os.path.exists(dst) and os.path.getsize(dst) > 1000:
            print(f"  {pref}: 取得ずみ（{os.path.getsize(dst) / 1e6:.1f}MB）")
            continue
        print(f"  {pref}: {urls[pref]}", flush=True)
        get(urls[pref], dst)
        print(f"     → {dst} {os.path.getsize(dst) / 1e6:.1f}MB", flush=True)
        time.sleep(WAIT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
