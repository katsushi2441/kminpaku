<?php
/**
 * Kurage 民泊できる場所チェック（kminpaku）
 *
 * 住所を入れると、その場所の用途地域と、その自治体の民泊の上乗せ条例を返す。
 *   /                住所で調べる
 *   /check?q=住所     判定
 *   /jorei           上乗せ条例のある自治体の一覧
 *   /jorei/{自治体}   条例1件
 *   /data /about /api?q= /robots.txt /sitemap.xml
 *
 * **この画面がしないこと**: 営業できるかを断定しない。条例を要約・解釈しない。
 * 出すのは「その住所の用途地域」「条例に書いてある文（原文）」「窓口で確かめてください」まで。
 * 収録していない自治体は「区域外」と書かず「未収録」と書く。時点と出典を必ず添える。
 *
 * heteml に置くときは、その階層の .htaccess に `AddHandler php-script .php` が要る（既定はPHP5.6）。
 * PHP 8 + PDO SQLite だけで動く。外部のAIやAPIには何も送らない（住所検索の国土地理院を除く）。
 */
declare(strict_types=1);
mb_internal_encoding('UTF-8');

$SITE = 'Kurage 民泊できる場所チェック';
$SELF = '/kminpaku.php';
$DIR  = __DIR__ . '/kminpaku_data';
$OGP  = 'https://kurage.exbridge.jp/images/ogp/kminpaku.png';
$STORE = 'https://kappstore.exbridge.jp/app.php?id=c36227d2b62b8517&ref=kminpaku';
$GSI  = 'https://msearch.gsi.go.jp/address-search/AddressSearch';
$PREF_NAMES = ['北海道', '青森県', '岩手県', '宮城県', '秋田県', '山形県', '福島県', '茨城県', '栃木県', '群馬県', '埼玉県', '千葉県', '東京都', '神奈川県', '新潟県', '富山県', '石川県', '福井県', '山梨県', '長野県', '岐阜県', '静岡県', '愛知県', '三重県', '滋賀県', '京都府', '大阪府', '兵庫県', '奈良県', '和歌山県', '鳥取県', '島根県', '岡山県', '広島県', '山口県', '徳島県', '香川県', '愛媛県', '高知県', '福岡県', '佐賀県', '長崎県', '熊本県', '大分県', '宮崎県', '鹿児島県', '沖縄県'];
// 住宅宿泊事業法で、条例による制限が置かれやすい用途地域
$RESIDENTIAL = ['第一種低層住居専用地域', '第二種低層住居専用地域', '第１種低層住居専用地域', '第２種低層住居専用地域',
                '第一種中高層住居専用地域', '第二種中高層住居専用地域', '第１種中高層住居専用地域', '第２種中高層住居専用地域',
                '田園住居地域'];

function h($s) { return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
function n($v) { return number_format((int)$v); }
function jd($j) { $a = json_decode((string)$j, true); return is_array($a) ? $a : []; }

try {
    $db = new PDO('sqlite:' . $DIR . '/kminpaku.sqlite');
    $db->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
    $db->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);
} catch (Exception $e) {
    http_response_code(503); header('Content-Type: text/plain; charset=UTF-8');
    echo "データベースがありません。scripts/build_db.py で作って kminpaku_data/ に置いてください。"; exit;
}
$META = []; foreach ($db->query('SELECT k, v FROM meta') as $r) { $META[$r['k']] = $r['v']; }
$SCALE = (float)($META['scale'] ?? 10000000);
$PREFS = jd($META['prefs'] ?? '[]');

$path = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH) ?: '/';
if (strpos($path, $SELF) === 0) { $path = substr($path, strlen($SELF)); }
$path = '/' . trim(rawurldecode((string)$path), '/');
$path = $path === '/' ? '' : $path;

// ── 判定 ───────────────────────────────────────────────
function geocode($q) {
    global $GSI;
    $ctx = stream_context_create(['http' => ['timeout' => 12, 'header' => "User-Agent: kminpaku/1.0\r\n"]]);
    $j = @file_get_contents($GSI . '?q=' . rawurlencode($q), false, $ctx);
    if ($j === false) return null;
    $items = json_decode($j, true);
    if (!is_array($items) || !$items) return null;
    $best = null; $bs = -1;
    foreach ($items as $it) {
        $t = $it['properties']['title'] ?? '';
        $s = (mb_strpos($t, $q) !== false ? 4 : 0) + (mb_strpos($t, $q) === 0 ? 2 : 0) - mb_strlen($t) / 100;
        if ($s > $bs) { $bs = $s; $best = $it; }
    }
    if (!$best) return null;
    return ['lon' => (float)$best['geometry']['coordinates'][0],
            'lat' => (float)$best['geometry']['coordinates'][1],
            'title' => (string)($best['properties']['title'] ?? '')];
}

/** 詰めた面（int32）の中に点があるか。形式は scripts/build_db.py の pack() と対。 */
function in_packed(string $b, float $lon, float $lat, float $scale): bool {
    $x = $lon * $scale; $y = $lat * $scale;
    $off = 0; $len = strlen($b);
    if ($len < 2) return false;
    $np = unpack('v', substr($b, $off, 2))[1]; $off += 2;
    for ($p = 0; $p < $np; $p++) {
        if ($off + 2 > $len) return false;
        $nr = unpack('v', substr($b, $off, 2))[1]; $off += 2;
        $inside = false; $hole = false;
        for ($r = 0; $r < $nr; $r++) {
            if ($off + 4 > $len) return false;
            $nc = unpack('V', substr($b, $off, 4))[1]; $off += 4;
            $need = $nc * 8;
            if ($off + $need > $len) return false;
            $pts = unpack('l*', substr($b, $off, $need)); $off += $need;
            $in = false;
            for ($i = 0; $i < $nc; $i++) {
                $x1 = $pts[$i * 2 + 1]; $y1 = $pts[$i * 2 + 2];
                $j = ($i + 1) % $nc;
                $x2 = $pts[$j * 2 + 1]; $y2 = $pts[$j * 2 + 2];
                if (($y1 > $y) != ($y2 > $y)) {
                    $xx = ($x2 - $x1) * ($y - $y1) / ($y2 - $y1) + $x1;
                    if ($x < $xx) $in = !$in;
                }
            }
            if ($r === 0) { $inside = $in; if (!$in) break; }
            elseif ($in) { $hole = true; break; }
        }
        if ($inside && !$hole) return true;
    }
    return false;
}

function youto_at(float $lon, float $lat, string $pref) {
    global $DIR, $SCALE;
    $f = $DIR . '/youto_' . $pref . '.sqlite';
    if (!is_file($f)) {
        return ['status' => '未収録', 'reason' => $pref . ' の用途地域データを置いていません'];
    }
    $y = new PDO('sqlite:' . $f);
    $y->setAttribute(PDO::ATTR_DEFAULT_FETCH_MODE, PDO::FETCH_ASSOC);
    // R*Tree は使わない。heteml の SQLite に rtree モジュールが無く落ちる（2026-09-23 本番で実測）
    $st = $y->prepare('SELECT name, code, bcr, far, city, citycode, geom FROM youto
                       WHERE minx <= ? AND maxx >= ? AND miny <= ? AND maxy >= ?');
    $st->execute([$lon, $lon, $lat, $lat]);
    foreach ($st as $row) {
        if (in_packed($row['geom'], $lon, $lat, $SCALE)) {
            unset($row['geom']);
            $row['status'] = '該当'; $row['pref'] = $pref;
            return $row;
        }
    }
    return ['status' => '区域外',
            'reason' => '用途地域の指定がない場所です（市街化調整区域・都市計画区域外など）。'
                      . 'この自治体のデータが出典に載っていない場合もあります'];
}

function jorei_for(PDO $db, string $pref, string $city): array {
    $out = [];
    $st = $db->prepare('SELECT * FROM jorei');
    $st->execute();
    foreach ($st as $r) {
        $g = preg_replace('/\s+/u', '', $r['govt']);
        if (($city !== '' && ($g === $city || mb_substr($g, -mb_strlen($city)) === $city))
            || ($pref !== '' && $g === $pref)) {
            $r['zones'] = jd($r['zones']); $r['periods'] = jd($r['periods']);
            $r['limits'] = jd($r['limits']); $r['conditions'] = jd($r['conditions']);
            $out[] = $r;
        }
    }
    return $out;
}

function check(PDO $db, string $q): array {
    global $RESIDENTIAL;
    $g = geocode($q);
    if (!$g) return ['status' => 'not_found', 'query' => $q];
    // 「京都府京都市…」を最短一致で切ると「京都」になる（2026-09-23 実測）。47の名前で照合する
    $pref = '';
    foreach ($GLOBALS['PREF_NAMES'] as $p) { if (mb_strpos($g['title'], $p) === 0) { $pref = $p; break; } }
    $y = youto_at($g['lon'], $g['lat'], $pref);
    $city = (string)($y['city'] ?? '');
    return [
        'status' => 'ok', 'query' => $q, 'address' => $g['title'],
        'lat' => $g['lat'], 'lon' => $g['lon'], 'pref' => $pref,
        'youto' => $y,
        'residential' => in_array((string)($y['name'] ?? ''), $RESIDENTIAL, true),
        'jorei' => jorei_for($db, $pref, $city),
        'checked_at' => date('Y-m-d H:i'),
    ];
}

// ── 画面の部品 ─────────────────────────────────────────
function head_html($title, $desc, $canon) {
    global $SITE, $OGP, $SELF;
    $base = 'https://kurage.exbridge.jp' . $SELF;
    echo '<!doctype html><html lang="ja"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">';
    echo '<title>' . h($title) . '</title><meta name="description" content="' . h($desc) . '">';
    echo '<link rel="canonical" href="' . h($base . $canon) . '">';
    echo '<meta property="og:title" content="' . h($title) . '"><meta property="og:description" content="' . h($desc) . '">'
       . '<meta property="og:type" content="website"><meta property="og:image" content="' . h($OGP) . '">'
       . '<meta property="og:site_name" content="' . h($SITE) . '"><meta property="og:url" content="' . h($base . $canon) . '">';
    echo '<meta property="og:locale" content="ja_JP">';
    echo '<meta name="twitter:card" content="summary_large_image"><meta name="twitter:image" content="' . h($OGP) . '">';
    echo '<style>'
       . ':root{--ink:#12202f;--mut:#5d6b7a;--teal:#0a9a8f;--teal-d:#087f76;--teal-l:#e6f4f2;--line:#dfe7ec;--bg:#f5f8fa;--red:#c0392b;--red-l:#fdecea;--amber:#b7791f;--amber-l:#fdf6e3}'
       . '*{box-sizing:border-box}html{color-scheme:light}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.85 "Noto Sans JP",system-ui,sans-serif}a,.src{overflow-wrap:anywhere}'
       . 'a{color:var(--teal-d)}.wrap{width:min(920px,100% - 32px);margin:0 auto}'
       . 'header.top{background:#fff;border-bottom:1px solid var(--line)}header.top .wrap{display:flex;align-items:center;gap:14px;flex-wrap:wrap;min-height:56px}'
       . '.brand{font-weight:800;color:var(--ink);text-decoration:none}nav a{color:var(--mut);text-decoration:none;font-size:14px;font-weight:600;margin-right:12px}'
       . 'main{padding:22px 0 44px}h1{font-size:clamp(21px,4vw,28px);line-height:1.4;margin:0 0 10px}h2{font-size:18px;margin:26px 0 8px;border-left:5px solid var(--teal);padding-left:10px}'
       . '.lead{color:var(--mut);margin:0 0 16px}'
       . '.panel{background:#fff;border:1px solid var(--line);border-radius:14px;padding:16px;margin:14px 0}'
       . '.form{display:flex;gap:10px;flex-wrap:wrap}input[type=text]{flex:1 1 260px;min-width:0;font-size:17px;padding:12px 14px;border:2px solid var(--line);border-radius:10px}'
       . '.btn{display:inline-block;background:var(--teal);color:#fff;border:0;border-radius:10px;padding:12px 20px;font:inherit;font-weight:700;text-decoration:none;cursor:pointer}'
       . '.btn.ghost{background:#fff;color:var(--teal-d);border:1px solid var(--line)}'
       . '.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,210px),1fr));gap:12px}'
       . '.card{border:1px solid var(--line);border-radius:12px;padding:14px;background:#fff;min-width:0}'
       . '.card .k{font-size:12.5px;color:var(--mut);font-weight:700}.card .v{font-size:21px;font-weight:800;line-height:1.35;overflow-wrap:anywhere}.card .s{font-size:13px;color:var(--mut);margin-top:4px}'
       . '.card.warn{background:var(--amber-l);border-color:#e6c98b}.card.none{background:var(--teal-l);border-color:#a9ddd6}'
       . '.tag{display:inline-block;font-size:12px;font-weight:700;border-radius:999px;padding:2px 10px;border:1px solid var(--line);background:var(--bg);color:var(--mut);margin:0 6px 4px 0}'
       . '.lim{border-left:3px solid var(--teal);padding:2px 0 2px 12px;margin:10px 0}.lim .k{font-size:12.5px;color:var(--mut);font-weight:700}'
       . '.note{background:var(--amber-l);border-left:4px solid var(--amber);padding:10px 12px;border-radius:8px;font-size:14px;margin:10px 0}'
       . '.src{font-size:12.5px;color:var(--mut)}table.t{border-collapse:collapse;width:100%;font-size:14px}table.t th,table.t td{border-bottom:1px solid var(--line);padding:7px 8px;text-align:left}'
       . '.tscroll{overflow-x:auto}'
       . '</style></head><body><header class="top"><div class="wrap"><a class="brand" href="' . h($SELF . '/') . '">' . h($SITE) . '</a><nav>';
    foreach (['/' => '住所で調べる', '/jorei' => '上乗せ条例', '/data' => 'データ', '/about' => 'このサイトについて'] as $u => $t) {
        echo '<a href="' . h($SELF . $u) . '">' . h($t) . '</a>';
    }
    echo '</nav></div></header><main><div class="wrap">';
}
function foot_html() {
    global $META, $SELF;
    echo '<h2>出典</h2><div class="panel src">'
       . '<p>用途地域: ' . h($META['youto_source'] ?? '') . '<br><a href="' . h($META['youto_source_url'] ?? '') . '">' . h($META['youto_source_url'] ?? '') . '</a></p>'
       . '<p>' . h($META['youto_note'] ?? '') . '</p>'
       . '<p>上乗せ条例の一覧: ' . h($META['jorei_list_source'] ?? '') . '（' . h($META['jorei_list_asof'] ?? '') . '）<br>'
       . '制限の中身: ' . h($META['jorei_limits_source'] ?? '') . '（' . h($META['jorei_limits_asof'] ?? '') . '）</p>'
       . '<p>判定は公開データを住所の代表点で照らした参考情報です。公的な証明ではありません。'
       . '最後は必ず自治体の窓口（保健所・都市計画課）でご確認ください。</p></div>';
    if ($GLOBALS['STORE']) {
        echo '<div class="panel"><p>このシステムを自分のサーバーに置いて使う（ソースコード同梱・MIT License）</p>'
           . '<p><a class="btn" href="' . h($GLOBALS['STORE']) . '">オンプレミス版を見る</a></p></div>';
    }
    // 再販パートナー募集の枠（中身は kurage_web/partner-bar.js）。当社の公開先でだけ読む（配布版を置いたサイトからは当社へ通信しない）
    echo '</div></main>';
    if (($_SERVER['HTTP_HOST'] ?? '') === 'kurage.exbridge.jp') echo '<script src="https://kurage.exbridge.jp/partner-bar.js" defer></script>';
    echo '</body></html>';
}
function search_form($q = '') {
    global $SELF;
    echo '<form class="form" method="get" action="' . h($SELF . '/check') . '">'
       . '<input type="text" name="q" value="' . h($q) . '" placeholder="住所（例: 東京都大田区羽田1-1-1）" aria-label="住所">'
       . '<button class="btn" type="submit">調べる</button></form>';
}

// ── robots / sitemap / api ─────────────────────────────
if ($path === '/robots.txt') {
    header('Content-Type: text/plain; charset=UTF-8');
    echo "User-agent: *\nAllow: /\nSitemap: https://kurage.exbridge.jp{$SELF}/sitemap.xml\n"; exit;
}
if ($path === '/sitemap.xml') {
    header('Content-Type: application/xml; charset=UTF-8');
    $base = 'https://kurage.exbridge.jp' . $SELF; $lm = $META['built'] ?? date('Y-m-d');
    echo '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">';
    foreach (['/', '/jorei', '/data', '/about'] as $u) { echo '<url><loc>' . h($base . $u) . '</loc><lastmod>' . $lm . '</lastmod></url>'; }
    foreach ($db->query('SELECT govt FROM jorei ORDER BY id') as $r) {
        echo '<url><loc>' . h($base . '/jorei/' . rawurlencode($r['govt'])) . '</loc><lastmod>' . $lm . '</lastmod></url>';
    }
    echo '</urlset>'; exit;
}
if ($path === '/api') {
    header('Content-Type: application/json; charset=UTF-8');
    $q = trim((string)($_GET['q'] ?? ''));
    if ($q === '') { http_response_code(400); echo json_encode(['error' => 'q が要ります'], JSON_UNESCAPED_UNICODE); exit; }
    $r = check($db, $q);
    $r['note'] = '参考情報です。営業の可否を示すものではありません。自治体の窓口で確認してください。';
    echo json_encode($r, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES | JSON_PRETTY_PRINT); exit;
}

// ── 判定の画面 ─────────────────────────────────────────
if ($path === '/check') {
    $q = trim((string)($_GET['q'] ?? ''));
    $r = $q === '' ? null : check($db, $q);
    $t = $q === '' ? '住所で調べる' : $q . ' で民泊ができるか（用途地域と上乗せ条例）';
    head_html($t . '｜' . $SITE, '住所を入れると、その場所の用途地域と、その自治体の民泊の上乗せ条例（区域・期間の制限）を原文で返します。', '/check');
    echo '<h1>' . h($q === '' ? '住所で調べる' : $q) . '</h1>';
    search_form($q);
    if ($r && $r['status'] === 'not_found') {
        echo '<div class="panel"><p>住所を特定できませんでした。番地まで入れるか、市区町村名から書いてみてください。</p></div>';
    } elseif ($r) {
        $y = $r['youto'];
        echo '<p class="lead">判定に使った住所: ' . h($r['address']) . '（' . number_format($r['lat'], 5) . ', ' . number_format($r['lon'], 5) . '）／ ' . h($r['checked_at']) . '</p>';
        echo '<h2>用途地域</h2><div class="grid">';
        if ($y['status'] === '該当') {
            echo '<div class="card' . ($r['residential'] ? ' warn' : '') . '"><div class="k">用途地域</div><div class="v">' . h($y['name']) . '</div>'
               . '<div class="s">' . h(($y['pref'] ?? '') . ($y['city'] ?? '')) . '</div></div>'
               . '<div class="card"><div class="k">建蔽率</div><div class="v">' . h($y['bcr']) . '%</div></div>'
               . '<div class="card"><div class="k">容積率</div><div class="v">' . h($y['far']) . '%</div></div>';
        } else {
            echo '<div class="card"><div class="k">用途地域</div><div class="v">' . h($y['status']) . '</div><div class="s">' . h($y['reason'] ?? '') . '</div></div>';
        }
        echo '</div>';
        if ($r['residential']) {
            echo '<div class="note">住居専用系の地域です。住宅宿泊事業法では、この区分に条例で区域・期間の制限が置かれていることが多くあります。下の条例の文をご確認ください。</div>';
        }
        echo '<h2>上乗せ条例</h2>';
        if ($r['jorei']) {
            foreach ($r['jorei'] as $j) {
                echo '<div class="panel"><div style="font-weight:800">' . h($j['govt']) . '『' . h($j['jorei']) . '』</div>'
                   . '<div class="src">' . h($j['promulgated']) . ' / ' . h($j['enforced']) . '（一覧の時点 ' . h($j['list_asof']) . '）</div>';
                if ($j['limits']) {
                    echo '<p class="src" style="margin-top:10px">制限の中身（' . h($j['limits_asof']) . '・原文のまま）</p>';
                    foreach ($j['limits'] as $it) {
                        echo '<div class="lim"><div class="k">' . h($it['kind']) . '</div>' . h($it['text']) . '</div>';
                    }
                } else {
                    echo '<p class="src" style="margin-top:10px">制限の中身は、収録したとりまとめに載っていません。自治体の条例本文でご確認ください。</p>';
                }
                if ($j['conditions']) {
                    echo '<p class="src">この制限が当てはまる場合</p><ul class="src">';
                    foreach ($j['conditions'] as $c) echo '<li>' . h($c) . '</li>';
                    echo '</ul>';
                }
                echo '</div>';
            }
        } else {
            echo '<div class="panel"><p>この自治体・都道府県の上乗せ条例は、収録した一覧（' . h($META['jorei_list_asof'] ?? '') . '）にありません。</p>'
               . '<p class="src">一覧に無いことは「制限がない」という意味ではありません。制定されたばかりの条例や、旅館業法・特区民泊の側の制限は別にあります。必ず自治体の窓口でご確認ください。</p></div>';
        }
        echo '<h2>3つの制度</h2><div class="panel"><p class="src">民泊には3つの制度があり、それぞれ窓口と手続きが違います。この画面はどれが使えるかを断定しません。</p>'
           . '<div class="tscroll"><table class="t"><tr><th>制度</th><th>手続き</th><th>日数</th><th>区域</th></tr>'
           . '<tr><td>住宅宿泊事業法（民泊新法）</td><td>届出</td><td>年間180日まで</td><td>条例で区域・期間がさらに制限されることがある</td></tr>'
           . '<tr><td>国家戦略特別区域法（特区民泊）</td><td>認定</td><td>上限なし</td><td>認定区域に限られる（大阪市・東京都大田区ほか）</td></tr>'
           . '<tr><td>旅館業法（簡易宿所）</td><td>許可</td><td>上限なし</td><td>用途地域の制限が別にかかる</td></tr>'
           . '</table></div></div>';
    }
    foot_html(); exit;
}

// ── 条例の一覧 ─────────────────────────────────────────
if ($path === '/jorei') {
    head_html('民泊の上乗せ条例がある自治体の一覧｜' . $SITE, '住宅宿泊事業法の上乗せ条例を定めている自治体を、条例名・公布日・施行日つきで並べました。', '/jorei');
    $rows = $db->query('SELECT * FROM jorei ORDER BY kind, id')->fetchAll();
    echo '<h1>上乗せ条例がある自治体</h1><p class="lead">' . n(count($rows)) . '自治体（一覧の時点 ' . h($META['jorei_list_asof'] ?? '') . '）。'
       . '住宅宿泊事業法18条で、都道府県・保健所設置市などは条例で実施する区域と期間を制限できます。</p>';
    echo '<div class="tscroll"><table class="t"><tr><th>自治体</th><th>条例</th><th>施行</th></tr>';
    foreach ($rows as $r) {
        echo '<tr><td><a href="' . h($SELF . '/jorei/' . rawurlencode($r['govt'])) . '">' . h($r['govt']) . '</a></td>'
           . '<td>' . h($r['jorei']) . '</td><td class="src">' . h(mb_substr($r['enforced'], 0, 30)) . '</td></tr>';
    }
    echo '</table></div>'; foot_html(); exit;
}
if (preg_match('#^/jorei/(.+)$#u', $path, $m)) {
    $st = $db->prepare('SELECT * FROM jorei WHERE govt = ?'); $st->execute([$m[1]]);
    $r = $st->fetch();
    if (!$r) { http_response_code(404); head_html('見つかりません｜' . $SITE, '', '/jorei'); echo '<h1>その自治体の条例は収録していません</h1>'; foot_html(); exit; }
    head_html($r['govt'] . 'の民泊条例（区域・期間の制限）｜' . $SITE, $r['govt'] . '『' . $r['jorei'] . '』の区域・期間の制限を原文のまま載せています。', '/jorei/' . rawurlencode($r['govt']));
    echo '<h1>' . h($r['govt']) . '『' . h($r['jorei']) . '』</h1>';
    echo '<p class="lead">' . h($r['promulgated']) . ' / ' . h($r['enforced']) . '</p>';
    $lim = jd($r['limits']);
    if ($lim) {
        echo '<h2>区域と期間の制限（' . h($r['limits_asof']) . '・原文のまま）</h2><div class="panel">';
        foreach ($lim as $it) echo '<div class="lim"><div class="k">' . h($it['kind']) . '</div>' . h($it['text']) . '</div>';
        echo '</div>';
    } else {
        echo '<div class="panel"><p>制限の中身は、収録したとりまとめに載っていません。自治体の条例本文でご確認ください。</p></div>';
    }
    foreach (jd($r['conditions']) as $c) { }
    if (jd($r['conditions'])) {
        echo '<h2>この制限が当てはまる場合</h2><div class="panel"><ul>';
        foreach (jd($r['conditions']) as $c) echo '<li>' . h($c) . '</li>';
        echo '</ul></div>';
    }
    echo '<div class="panel"><p><a class="btn ghost" href="' . h($SELF . '/check') . '">住所で調べる</a></p></div>';
    foot_html(); exit;
}

// ── データ・このサイト ─────────────────────────────────
if ($path === '/data') {
    head_html('収録しているデータ｜' . $SITE, '用途地域と上乗せ条例の、収録範囲と時点。', '/data');
    echo '<h1>収録しているデータ</h1>';
    $tot = (int)$db->query('SELECT sum(n) FROM coverage')->fetchColumn();
    $cities = (int)$db->query('SELECT count(*) FROM coverage')->fetchColumn();
    echo '<div class="grid">'
       . '<div class="card"><div class="k">用途地域</div><div class="v">' . n($tot) . '<span style="font-size:14px">面</span></div><div class="s">' . n($cities) . '市区町村</div></div>'
       . '<div class="card"><div class="k">上乗せ条例</div><div class="v">' . n((int)$db->query('SELECT count(*) FROM jorei')->fetchColumn()) . '<span style="font-size:14px">自治体</span></div><div class="s">' . h($META['jorei_list_asof'] ?? '') . '</div></div>'
       . '<div class="card"><div class="k">都道府県</div><div class="v">' . n(count($PREFS)) . '<span style="font-size:14px">/47</span></div><div class="s">' . h(implode('・', array_slice($PREFS, 0, 6))) . (count($PREFS) > 6 ? ' ほか' : '') . '</div></div>'
       . '</div>';
    echo '<h2>収録している市区町村</h2><div class="tscroll"><table class="t"><tr><th>都道府県</th><th>市区町村</th><th class="n">面</th></tr>';
    foreach ($db->query('SELECT * FROM coverage ORDER BY pref, citycode') as $c) {
        echo '<tr><td>' . h($c['pref']) . '</td><td>' . h($c['city']) . '</td><td>' . n($c['n']) . '</td></tr>';
    }
    echo '</table></div>';
    echo '<div class="note">ここに無い市区町村は「区域外」ではなく<strong>未収録</strong>です。出典側に載っていない自治体もあります。</div>';
    foot_html(); exit;
}
if ($path === '/about') {
    head_html('このサイトについて｜' . $SITE, '何をして、何をしないか。', '/about');
    echo '<h1>このサイトについて</h1>';
    echo '<div class="panel"><p>住所を入れると、その場所の<strong>用途地域</strong>と、その自治体の<strong>民泊の上乗せ条例</strong>を返します。'
       . '民泊には住宅宿泊事業法・特区民泊・旅館業法の簡易宿所の3つの制度があり、窓口も条件も別々で、住所から横断して引ける場所がありません。'
       . 'そこを1画面にしたものです。</p></div>';
    echo '<h2>しないこと</h2><div class="panel"><ul>'
       . '<li>営業できる・できないを断定しない</li>'
       . '<li>条例を要約・解釈しない。出すのは条例に書いてある文のまま</li>'
       . '<li>収録していない自治体を「区域外」と書かない。「未収録」と書く</li>'
       . '<li>外部のAIやAPIに住所を送らない（住所の位置を引く国土地理院の検索だけ使います）</li>'
       . '</ul></div>';
    foot_html(); exit;
}

// ── トップ ─────────────────────────────────────────────
head_html($SITE . '｜住所で民泊の用途地域と上乗せ条例を調べる',
    '住所を入れると、その場所の用途地域（建蔽率・容積率つき）と、その自治体の民泊の上乗せ条例（区域・期間の制限）を原文で返します。', '/');
echo '<h1>この住所で民泊ができるか、用途地域と条例から調べます</h1>';
echo '<p class="lead">民泊には住宅宿泊事業法（届出・年180日）、特区民泊（認定）、旅館業法の簡易宿所（許可）の3つがあります。'
   . '窓口も条件も別々で、住所から横断して引ける場所がありません。ここでは用途地域と、その自治体の上乗せ条例を1画面に並べます。</p>';
echo '<div class="panel">'; search_form('');
echo '<p class="src" style="margin-top:8px">例: ';
foreach (['東京都大田区羽田1-1-1', '大阪市中央区本町1-1-1', '東京都世田谷区成城6-1-1'] as $ex) {
    echo '<a href="' . h($SELF . '/check?q=' . rawurlencode($ex)) . '">' . h($ex) . '</a>　';
}
echo '</p></div>';
echo '<h2>返ってくるもの</h2><div class="grid">'
   . '<div class="card"><div class="k">用途地域</div><div class="v" style="font-size:17px">名称・建蔽率・容積率</div><div class="s">都市計画決定GISデータ（令和7年度版）</div></div>'
   . '<div class="card"><div class="k">上乗せ条例</div><div class="v" style="font-size:17px">区域・期間の制限</div><div class="s">条例に書いてある文のまま。要約しません</div></div>'
   . '<div class="card"><div class="k">3つの制度</div><div class="v" style="font-size:17px">届出・認定・許可</div><div class="s">どれを検討できるか、窓口はどこか</div></div>'
   . '</div>';
echo '<div class="note">この画面は営業の可否を判定するものではありません。調べる前の下ごしらえです。最後は必ず自治体の窓口でご確認ください。</div>';
foot_html();
