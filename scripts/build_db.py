#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用途地域と上乗せ条例を SQLite にまとめる（PHP 1ファイルから引ける形）。

  /usr/bin/python3 scripts/build_db.py                 # 取得ずみの都道府県ぜんぶ
  /usr/bin/python3 scripts/build_db.py --pref 東京都    # 都道府県を絞る

**都道府県ごとに1ファイルにする。** 全国を1本にすると、判定1回のために全国分を開くことになる。
買う人はふつう1つの地域しか要らないので、要る県だけ置けばいい作りにする。
  php/kminpaku_data/kminpaku.sqlite        … 条例・収録状況・時点（軽い。必ず置く）
  php/kminpaku_data/youto_<都道府県>.sqlite … 用途地域の面（要る県だけ置く）

**面の座標は int32 に詰める。** GeoJSON のまま持つと全国で約1.9GB になる（2026-09-23 実測）。
  1. ダグラス・ポイカーで 1m まで間引く（点が38%に減る）
  2. 経度・緯度を 1e-7 度単位の int32 で詰める（1点8バイト、精度約1cm）
  → 全国で約88MB。PHP 側は unpack('l*') で読む。

**R*Tree は使わない。** heteml の PHP に同梱された SQLite には rtree モジュールが無く、
`no such module: rtree` で落ちる（2026-09-23 本番で実測）。買い切りで配る先の環境も選べない。
代わりに外接矩形を普通の列で持ち、minx に索引を張る。都道府県ごとに1ファイルで最大でも
1万面ほどなので、これで十分速い（本番で実測 0.1秒前後）。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sqlite3
import struct
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = "/mnt/data/kminpaku/raw/youto"
OUTDIR = os.path.join(ROOT, "php", "kminpaku_data")
DATA = os.path.join(ROOT, "data")
SCALE = 10_000_000          # 1e-7 度 ≒ 1cm
TOL = 1e-5                  # 間引きの許容 ≒ 1m

YOUTO_SCHEMA = """
CREATE TABLE youto (
  id INTEGER PRIMARY KEY, city TEXT, citycode TEXT,
  name TEXT, code INTEGER, bcr TEXT, far TEXT,
  minx REAL, maxx REAL, miny REAL, maxy REAL, geom BLOB);
CREATE INDEX youto_city ON youto(citycode);
CREATE INDEX youto_box ON youto(minx, maxx);
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
"""
MAIN_SCHEMA = """
CREATE TABLE coverage (
  pref TEXT, citycode TEXT, city TEXT, n INTEGER, PRIMARY KEY(pref, citycode));
CREATE INDEX coverage_city ON coverage(city);
CREATE TABLE jorei (
  id INTEGER PRIMARY KEY, kind TEXT, govt TEXT, jorei TEXT,
  promulgated TEXT, enforced TEXT, type TEXT,
  zones TEXT, periods TEXT, limits TEXT, conditions TEXT, raw TEXT,
  list_asof TEXT, limits_asof TEXT, list_source TEXT, limits_source TEXT);
CREATE INDEX jorei_govt ON jorei(govt);
CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
"""


def simplify(ring: list, tol: float) -> list:
    """ダグラス・ポイカー。閉じた輪なので、減りすぎたら元に戻す。"""
    if len(ring) < 5:
        return ring

    def dp(pl):
        if len(pl) < 3:
            return pl
        x1, y1 = pl[0]; x2, y2 = pl[-1]
        dx, dy = x2 - x1, y2 - y1
        d2 = dx * dx + dy * dy
        best, bi = 0.0, 0
        for i in range(1, len(pl) - 1):
            x, y = pl[i]
            if d2 == 0:
                dd = (x - x1) ** 2 + (y - y1) ** 2
            else:
                t = max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / d2))
                dd = (x - (x1 + t * dx)) ** 2 + (y - (y1 + t * dy)) ** 2
            if dd > best:
                best, bi = dd, i
        if math.sqrt(best) > tol:
            return dp(pl[:bi + 1])[:-1] + dp(pl[bi:])
        return [pl[0], pl[-1]]

    r = dp(ring)
    return r if len(r) >= 4 else ring


def pack(geom: dict) -> tuple[bytes, float, float, float, float]:
    """面を詰める。形式:
         [面の数 u16]( [輪の数 u16]( [点の数 u32] (lon,lat の int32 × 点数) ) )
    """
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    out = [struct.pack("<H", len(polys))]
    xs: list[float] = []
    ys: list[float] = []
    for poly in polys:
        out.append(struct.pack("<H", len(poly)))
        for ring in poly:
            r = simplify([(float(p[0]), float(p[1])) for p in ring], TOL)
            out.append(struct.pack("<I", len(r)))
            for x, y in r:
                out.append(struct.pack("<ll", round(x * SCALE), round(y * SCALE)))
                xs.append(x); ys.append(y)
    return b"".join(out), min(xs), max(xs), min(ys), max(ys)


def build_pref(pref: str, zpath: str) -> tuple[int, list]:
    out = os.path.join(OUTDIR, f"youto_{pref}.sqlite")
    if os.path.exists(out):
        os.remove(out)
    db = sqlite3.connect(out)
    db.executescript(YOUTO_SCHEMA)
    fid = 0
    cov = []
    with zipfile.ZipFile(zpath) as z:
        for nm in sorted(n for n in z.namelist() if n.endswith("_youto.geojson")):
            try:
                d = json.loads(z.read(nm).decode("utf-8"))
            except Exception as e:
                print(f"  ! {nm}: {e}", file=sys.stderr)
                continue
            rows = []
            city = citycode = ""
            for ft in d.get("features") or []:
                g = ft.get("geometry") or {}
                if not g.get("coordinates"):
                    continue
                p = ft.get("properties") or {}
                city = p.get("Cityname") or city
                citycode = p.get("Citycode") or citycode
                fid += 1
                blob, x1, x2, y1, y2 = pack(g)
                rows.append((fid, p.get("Cityname"), p.get("Citycode"), p.get("YoutoName"),
                             p.get("YoutoCode"), p.get("BCR"), p.get("FAR"), x1, x2, y1, y2, blob))
            if rows:
                db.executemany("INSERT INTO youto VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)
                cov.append((pref, citycode, city, len(rows)))
    db.executemany("INSERT INTO meta VALUES (?,?)",
                   [("pref", pref), ("scale", str(SCALE)), ("tolerance_deg", str(TOL)),
                    ("built", __import__("datetime").date.today().isoformat())])
    db.commit()
    db.execute("VACUUM")
    db.close()
    return fid, cov


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pref", action="append", default=[])
    a = ap.parse_args()
    zips = sorted(f for f in os.listdir(RAW) if f.endswith(".zip"))
    if a.pref:
        zips = [f for f in zips if f[:-4] in a.pref]
    if not zips:
        print(f"{RAW} に zip がない。先に scripts/fetch_youto.py", file=sys.stderr)
        return 1
    os.makedirs(OUTDIR, exist_ok=True)
    allcov = []
    total = 0
    for zf in zips:
        pref = zf[:-4]
        n, cov = build_pref(pref, os.path.join(RAW, zf))
        sz = os.path.getsize(os.path.join(OUTDIR, f"youto_{pref}.sqlite"))
        print(f"  {pref}: {n:,}面 {sz / 1e6:.1f}MB", flush=True)
        allcov += cov
        total += n

    main_db = os.path.join(OUTDIR, "kminpaku.sqlite")
    if os.path.exists(main_db):
        os.remove(main_db)
    db = sqlite3.connect(main_db)
    db.executescript(MAIN_SCHEMA)
    db.executemany("INSERT OR REPLACE INTO coverage VALUES (?,?,?,?)", allcov)

    lst = json.load(open(os.path.join(DATA, "jorei_list.json"), encoding="utf-8"))
    lim = json.load(open(os.path.join(DATA, "jorei_limits.json"), encoding="utf-8"))
    lm = {re.sub(r"\s", "", i["govt"]): i for i in lim["items"]}

    def find(g: str):
        g = re.sub(r"\s", "", g)
        if g in lm:
            return lm[g]
        for k, v in lm.items():
            if k.endswith(g) or g.endswith(k):
                return v
        return {}

    for i, r in enumerate(lst["items"], 1):
        L = find(r["govt"])
        db.execute("INSERT INTO jorei VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (i, r["kind"], r["govt"], r["jorei"], r["promulgated"], r["enforced"], r.get("type", ""),
                    json.dumps(L.get("zones", []), ensure_ascii=False),
                    json.dumps(L.get("periods", []), ensure_ascii=False),
                    json.dumps(L.get("limits", []), ensure_ascii=False),
                    json.dumps(L.get("conditions", []), ensure_ascii=False),
                    L.get("raw", ""), lst["asof"], L.get("asof", ""), lst["source"], L.get("source_url", "")))
    meta = {
        "youto_source": "国土交通省 都市局「都市計画決定GISデータ（全国）」令和7年度版",
        "youto_source_url": "https://www.mlit.go.jp/toshi/tosiko/toshi_tosiko_tk_000087.html",
        "youto_note": "掲載データは、建築確認申請や不動産重要事項説明等の手続に用いることを保証するものではなく、参考情報として利用を想定しています（出典サイトの記載）。",
        "jorei_list_asof": lst["asof"], "jorei_list_source": lst["source"],
        "jorei_limits_asof": lim["asof"],
        "jorei_limits_source": "観光庁「民泊の実施制限に関する地方公共団体の条例のとりまとめについて」",
        "prefs": json.dumps([z[:-4] for z in zips], ensure_ascii=False),
        "scale": str(SCALE),
        "built": __import__("datetime").date.today().isoformat(),
    }
    db.executemany("INSERT INTO meta VALUES (?,?)", list(meta.items()))
    db.commit()
    db.execute("VACUUM")
    print(f"\n→ {OUTDIR}")
    print(f"  用途地域 {total:,}面 / 市区町村 {len(allcov):,} / 条例 {len(lst['items'])}")
    tot = sum(os.path.getsize(os.path.join(OUTDIR, f)) for f in os.listdir(OUTDIR) if f.endswith(".sqlite"))
    print(f"  合計 {tot / 1e6:.1f}MB（本体 {os.path.getsize(main_db) / 1e6:.1f}MB）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
