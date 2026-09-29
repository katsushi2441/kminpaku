#!/usr/bin/env python3
"""kminpakuを 390px（スマホ）と 1280px で撮って outputs/ に置く。横はみ出しは scrollWidth で実測。
  /usr/bin/python3 scripts/shot_senkyoku.py https://xb4g.com/giin/senkyoku/kanagawa-11
（空のプロファイルで開く。browser_agent/chrome-profile は使わない）"""
import os, sys
from playwright.sync_api import sync_playwright
url = sys.argv[1]
out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "outputs")
with sync_playwright() as p:
    b = p.chromium.launch()
    for w in (390, 1280):
        pg = b.new_page(viewport={"width": w, "height": 900}, device_scale_factor=1)
        pg.goto(url, wait_until="networkidle", timeout=60000)
        sw = pg.evaluate("document.documentElement.scrollWidth")
        f = os.path.join(out, f"kminpaku_{w}.png")
        pg.screenshot(path=f, full_page=True)
        print(w, "scrollWidth", sw, "→", f, "(はみ出し)" if sw > w else "")
    b.close()
