#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""判定の当たりを、分かっている住所で確かめる。build_db.py のあとに必ず流す。
  /usr/bin/python3 scripts/selftest.py [http://127.0.0.1:18383]
"""
import sys, json, urllib.parse, urllib.request
BASE = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:18383'
# 住所 → 期待する用途地域の一部 / 条例のある自治体か
CASES = [
    ("東京都大田区羽田1-1-1", "商業", True),
    ("東京都世田谷区成城6-1-1", "住居専用", True),
    ("大阪市中央区本町1-1-1", "商業", True),
    ("京都市中京区寺町通御池上る上本能寺前町488", "商業", True),   # 「京都府」を「京都」と切る不具合の見張り
    ("北海道札幌市中央区北1条西2丁目", "商業", True),
    ("名古屋市瑞穂区内浜町34-9", "", True),
    ("沖縄県那覇市泉崎1-1-1", "", True),
    ("福岡市中央区天神1-8-1", "商業", False),
]
ng = 0
for q, want, want_jorei in CASES:
    u = BASE + '/kminpaku.php/api?q=' + urllib.parse.quote(q)
    try:
        d = json.load(urllib.request.urlopen(u, timeout=40))
    except Exception as e:
        print(f'NG {q}: {e}'); ng += 1; continue
    y = d.get('youto', {})
    name = y.get('name') or y.get('status')
    has = bool(d.get('jorei'))
    ok = (want in (name or '')) and (has == want_jorei) if want else (y.get('status') == '該当' and has == want_jorei)
    print(f'{"ok" if ok else "NG"} {q[:28]:30} {name} / 条例{"あり" if has else "なし"}')
    if not ok: ng += 1
print(f'\n{len(CASES) - ng}/{len(CASES)} 一致')
sys.exit(1 if ng else 0)
