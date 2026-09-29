#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""配布ZIP（kappstore 同梱物）を作る。

  /usr/bin/python3 scripts/make_zip.py
  /usr/bin/python3 scripts/make_zip.py --pref 東京都 --pref 大阪府   # 用途地域を絞る

**同梱の LICENSE と README は必ずこのプロジェクトのものを入れる。**
他製品からコピーしたまま別製品の記述が残っていた前例がある（LICENSE の同梱データの節）。
最後に中身を並べて、製品名が混ざっていないか目で確かめられるように出す。

用途地域は都道府県ごとに1ファイルなので、全部入れると約57MBになる。
買う人が要る県だけでよければ --pref で絞る。
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOP = "kurage-minpaku-check"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pref", action="append", default=[])
    a = ap.parse_args()
    data = ROOT / "php" / "kminpaku_data"
    files = [
        (ROOT / "php" / "LICENSE", "LICENSE"),
        (ROOT / "php" / "README.md", "README.md"),
        (ROOT / "php" / "kminpaku.php", "kminpaku.php"),
        (data / "kminpaku.sqlite", "kminpaku_data/kminpaku.sqlite"),
        (data / ".htaccess", "kminpaku_data/.htaccess"),
        (ROOT / "outputs" / "kminpaku_1200x630.png", "images/ogp/kminpaku.png"),
        (ROOT / "scripts" / "fetch_youto.py", "scripts/fetch_youto.py"),
        (ROOT / "scripts" / "fetch_jorei.py", "scripts/fetch_jorei.py"),
        (ROOT / "scripts" / "parse_jorei_pdf.py", "scripts/parse_jorei_pdf.py"),
        (ROOT / "scripts" / "parse_a29_conditions.py", "scripts/parse_a29_conditions.py"),
        (ROOT / "scripts" / "build_db.py", "scripts/build_db.py"),
        (ROOT / "scripts" / "selftest.py", "scripts/selftest.py"),
        (ROOT / "docs" / "DATA.md", "docs/DATA.md"),
    ]
    youto = sorted(data.glob("youto_*.sqlite"))
    if a.pref:
        youto = [p for p in youto if p.stem[len("youto_"):] in a.pref]
    files += [(p, f"kminpaku_data/{p.name}") for p in youto]

    missing = [str(s) for s, _ in files if not s.exists()]
    if missing:
        print("!! 無いファイル:", *missing, sep="\n  ", file=sys.stderr)
        return 1
    out = ROOT / "outputs" / f"{TOP}.zip"
    out.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for src, name in files:
            z.write(src, f"{TOP}/{name}")
    print(f"→ {out}  {out.stat().st_size / 1e6:.1f}MB  用途地域 {len(youto)}都道府県")
    with zipfile.ZipFile(out) as z:
        for n in z.namelist():
            if "/youto_" not in n:
                print("   ", n)
        print(f"    …ほか 用途地域 {len(youto)}ファイル")
    return 0


if __name__ == "__main__":
    sys.exit(main())
