#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""住所を入れると、その場所で民泊ができるか（どの制度が使えるか）を返す。判定の中身の試作。

  /usr/bin/python3 scripts/judge.py "東京都大田区羽田1-1-1"
  /usr/bin/python3 scripts/judge.py "大阪市中央区本町1-1-1" --json

返すもの:
  1. 用途地域（都市計画決定GISデータ・令和7年度版）と建蔽率・容積率
  2. その自治体（と都道府県）の上乗せ条例の有無と、区域・期間の制限の原文
  3. 3つの制度（住宅宿泊事業法・特区民泊・旅館業法の簡易宿所）のうち、どれが検討できるか

**しないこと**: 営業の可否を断定しない。条例の解釈をしない。要約しない。
出すのは「その住所の用途地域」と「条例に書いてある文」と「窓口はどこか」まで。
最後の判断は自治体の窓口に確かめてもらう。データの時点と出典を必ず添える。
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
import unicodedata
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAW = "/mnt/data/kminpaku/raw/youto"
DATA = os.path.join(ROOT, "data")
GSI = "https://msearch.gsi.go.jp/address-search/AddressSearch"
PREF_NAMES = ['北海道', '青森県', '岩手県', '宮城県', '秋田県', '山形県', '福島県', '茨城県', '栃木県', '群馬県', '埼玉県', '千葉県', '東京都', '神奈川県', '新潟県', '富山県', '石川県', '福井県', '山梨県', '長野県', '岐阜県', '静岡県', '愛知県', '三重県', '滋賀県', '京都府', '大阪府', '兵庫県', '奈良県', '和歌山県', '鳥取県', '島根県', '岡山県', '広島県', '山口県', '徳島県', '香川県', '愛媛県', '高知県', '福岡県', '佐賀県', '長崎県', '熊本県', '大分県', '宮崎県', '鹿児島県', '沖縄県']
UA = {"User-Agent": "kminpaku/0.1 (+https://kurage.exbridge.jp/; contact info@exbridge.jp)"}
SRC_YOUTO = ("国土交通省 都市局「都市計画決定GISデータ（全国）」令和7年度版",
             "https://www.mlit.go.jp/toshi/tosiko/toshi_tosiko_tk_000087.html")
DISCLAIM = ("掲載データは、建築確認申請や不動産重要事項説明等の手続に用いることを保証するもの"
            "ではなく、参考情報として利用を想定しています（出典サイトの記載）。")
# 住宅宿泊事業法で、条例による制限が置かれやすい用途地域
RESIDENTIAL = ("第一種低層住居専用地域", "第二種低層住居専用地域",
               "第一種中高層住居専用地域", "第二種中高層住居専用地域", "田園住居地域")


def norm(s: str) -> str:
    return unicodedata.normalize("NFKC", re.sub(r"[\s　]", "", s or ""))


def geocode(q: str):
    import requests
    r = requests.get(GSI, params={"q": q}, timeout=15, headers=UA)
    r.raise_for_status()
    items = r.json()
    if not items:
        return None
    def score(it):
        t = it.get("properties", {}).get("title", "")
        return (q in t, t.startswith(q), -len(t))
    it = max(items, key=score)
    lon, lat = it["geometry"]["coordinates"]
    return {"lon": float(lon), "lat": float(lat), "title": it["properties"].get("title", "")}


def in_ring(x: float, y: float, ring: list) -> bool:
    inside = False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > y) != (y2 > y):
            xx = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < xx:
                inside = not inside
    return inside


def in_poly(x: float, y: float, geom: dict) -> bool:
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    for poly in polys:
        if not poly:
            continue
        if in_ring(x, y, poly[0]) and not any(in_ring(x, y, h) for h in poly[1:]):
            return True
    return False


def youto_at(lon: float, lat: float, pref: str, title: str = ""):
    """用途地域を引く。

    都道府県の zip の中は「27_大阪府/27100_大阪市/27100_youto.geojson」のように
    市区町村ごとに1ファイル。**政令市は区ごとに分かれず市で1本**なので、区名で探すと当たらない
    （2026-09-23 実測。大阪市中央区で「区域外」と誤判定した）。
    住所の文字列から市区町村名を拾ってファイルを絞り、見つからなければ全ファイルを見る。
    """
    z = os.path.join(RAW, f"{pref}.zip")
    if not os.path.exists(z):
        return {"status": "未収録", "reason": f"{pref} の用途地域データを取り込んでいません",
                "hint": f"/usr/bin/python3 scripts/fetch_youto.py --pref {pref}"}
    body = title[len(pref):] if title.startswith(pref) else title
    cands = re.findall(r"[^\s]+?[市区町村]", body)          # 「大阪市」「中央区」など
    with zipfile.ZipFile(z) as f:
        names = [n for n in f.namelist() if n.endswith("_youto.geojson")]
        # 住所に出てくる市区町村名を、長いものから当てる
        order = []
        for c in sorted(set(cands), key=len, reverse=True):
            order += [n for n in names if c in n and n not in order]
        order += [n for n in names if n not in order]
        for n in order:
            try:
                d = json.loads(f.read(n).decode("utf-8"))
            except Exception:
                continue
            for ft in d.get("features") or []:
                g = ft.get("geometry")
                if g and in_poly(lon, lat, g):
                    p = ft["properties"]
                    return {"status": "該当", "name": p.get("YoutoName"), "code": p.get("YoutoCode"),
                            "bcr": p.get("BCR"), "far": p.get("FAR"),
                            "city": p.get("Cityname"), "citycode": p.get("Citycode"), "pref": p.get("Pref")}
    return {"status": "区域外",
            "reason": "用途地域の指定がない場所です（市街化調整区域・都市計画区域外など）。"
                      "データに載っていない自治体の可能性もあります"}


def jorei_for(pref: str, city: str):
    lst = json.load(open(os.path.join(DATA, "jorei_list.json"), encoding="utf-8"))
    lim = json.load(open(os.path.join(DATA, "jorei_limits.json"), encoding="utf-8"))
    limmap = {norm(i["govt"]): i for i in lim["items"]}

    def limits_of(g: str):
        """一覧は「大田区」、とりまとめPDFは「東京都大田区」のように表記がずれる。"""
        if g in limmap:
            return limmap[g]
        for k, v in limmap.items():
            if k.endswith(g) or g.endswith(k):
                return v
        return {}

    nc, np_ = norm(city), norm(pref)
    hits = []
    for r in lst["items"]:
        g = norm(r["govt"])
        # 市区町村名が空のときに endswith("") で全件当たるのを防ぐ（大阪市で千代田区の条例を出した）
        if (nc and (g == nc or g.endswith(nc))) or (np_ and g == np_):
            d = dict(r)
            L = limits_of(g)
            d["limits"] = L.get("limits", [])
            d["zones"] = L.get("zones", [])
            d["raw"] = L.get("raw", "")
            d["limits_asof"] = L.get("asof", "")
            hits.append(d)
    return hits, lst["asof"]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("address")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    g = geocode(a.address)
    if not g:
        print("住所を特定できませんでした"); return 1
    # 「京都府京都市…」を最短一致で切ると「京都」になる。47の名前で照合する
    pref = next((p for p in PREF_NAMES if g["title"].startswith(p)), "")
    y = youto_at(g["lon"], g["lat"], pref, g["title"])
    city = y.get("city") or ""
    jorei, jorei_asof = jorei_for(pref, city) if city or pref else ([], "")

    res = {
        "query": a.address,
        "address": g["title"],
        "lat": g["lat"], "lon": g["lon"],
        "youto": y,
        "youto_source": {"name": SRC_YOUTO[0], "url": SRC_YOUTO[1], "note": DISCLAIM},
        "jorei": jorei,
        "jorei_asof": jorei_asof,
        "residential_zone": y.get("name") in RESIDENTIAL,
    }
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1)); return 0

    print(f"住所: {g['title']}  ({g['lat']:.5f}, {g['lon']:.5f})")
    if y["status"] == "該当":
        print(f"用途地域: {y['name']}（建蔽率 {y['bcr']}% / 容積率 {y['far']}%）  {y['pref']}{y['city']}")
        if res["residential_zone"]:
            print("  ※ 住居専用系の地域です。条例で民泊の区域・期間が制限されることが多い区分です")
    else:
        print(f"用途地域: {y['status']} — {y.get('reason', '')}")
    print(f"  出典: {SRC_YOUTO[0]}")
    print(f"  {DISCLAIM}")
    print()
    if jorei:
        for j in jorei:
            print(f"上乗せ条例: {j['govt']}『{j['jorei']}』")
            print(f"  {j['promulgated']} / {j['enforced']}（一覧の時点 {jorei_asof}）")
            if j["limits"]:
                print(f"  制限の中身（{j['limits_asof']}・観光庁のとりまとめより。原文）:")
                for it in j["limits"]:
                    print(f"    {it['kind']}: {it['text']}")
            else:
                print("  制限の中身は、このとりまとめには載っていません。自治体の条例本文で確認してください")
            print()
    else:
        print(f"上乗せ条例: この自治体・都道府県のものは、収録した一覧（{jorei_asof}）にありません")
        print("  ※ 一覧にないことは「制限がない」という意味ではありません。自治体の窓口で確かめてください")
    print()
    print("使える制度（どれも自治体の窓口で確認が要ります）")
    print("  1. 住宅宿泊事業法（民泊新法）… 届出。年間180日まで。条例で区域・期間がさらに制限されることがある")
    print("  2. 国家戦略特別区域法（特区民泊）… 認定。区域が限られる（大阪市・東京都大田区ほか）")
    print("  3. 旅館業法（簡易宿所）… 許可。日数の上限なし。用途地域の制限が別にかかる")
    return 0


if __name__ == "__main__":
    sys.exit(main())
