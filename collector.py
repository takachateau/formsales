"""
リスト自動収集モジュール
DuckDuckGo検索でターゲット企業URLを収集し、
・重複除外
・ブラックリスト照合
・robots.txt チェック
・フォーム存在確認
を通過したURLだけをDBに保存する。
"""
import os
import httpx
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from ddgs import DDGS
from database import get_conn, init_db

# ────────────────────────────────────────────
# 業種別 検索クエリテンプレート
# 優先順位（リサイズツールとの親和性順）:
#   1. creative  … Web制作・デザイン会社（画像リサイズ需要◎ 常時多量）
#   2. sns       … SNS運用代行（複数SNS対応で毎日リサイズ）
#   3. agency    … デジタル広告代理店（バナー量産でリサイズ多）
#   4. ec        … 自社EC・通販（商品画像リサイズ需要○）
#   5. d2c       … D2Cブランド（商品画像+SNS兼用）
# ────────────────────────────────────────────
QUERY_TEMPLATES = {
    # ★最優先: Web制作・デザイン会社（毎日大量に画像リサイズが発生する）
    "creative": [
        "Web制作会社 中小企業 株式会社 お問い合わせフォーム -求人",
        "デザイン会社 UI UX 制作 中小 コンタクト -求人 -転職",
        "グラフィックデザイン ブランディング 会社 お問い合わせ",
        "映像制作 動画制作 会社 中小企業 お問い合わせ",
        "クリエイティブ制作会社 広告素材 バナー 株式会社 コンタクト",
    ],
    # ★高優先: SNS運用代行（毎日複数SNSに投稿＝毎日リサイズ）
    "sns": [
        "SNS運用代行 株式会社 サービス お問い合わせ -求人 -転職",
        "Instagram運用代行 中小企業向け 会社 お問い合わせフォーム",
        "TikTok YouTube 運用代行 マーケティング会社 コンタクト",
        "SNSマーケティング支援 BtoB 中小 お問い合わせ",
        "SNSコンサルティング 投稿代行 株式会社 コンタクト",
    ],
    # ★高優先: デジタル広告代理店（バナー量産でリサイズ多）
    "agency": [
        "デジタルマーケティング支援 中小企業 株式会社 お問い合わせ -大手",
        "Web広告運用代行 中小 会社 お問い合わせフォーム",
        "リスティング広告 SNS広告 代理店 中小企業 コンタクト",
        "インターネット広告代理店 中小 株式会社 お問い合わせ",
        "運用型広告 バナー制作 代理店 お問い合わせフォーム",
    ],
    # 中優先: 自社EC
    "ec": [
        "自社ECサイト 通販 中小企業 お問い合わせフォーム",
        "オリジナルブランド 通販サイト 株式会社 お問い合わせ",
        "アパレル 食品 雑貨 自社通販 中小企業 コンタクト",
        "ネットショップ運営 自社ブランド 株式会社 お問い合わせ",
    ],
    # 中優先: D2C
    "d2c": [
        "D2Cブランド 自社EC 化粧品 スキンケア お問い合わせ -大手",
        "D2C 食品 健康食品 サプリ 通販会社 コンタクト",
        "オリジナルコスメ アパレル D2C 中小 お問い合わせフォーム",
        "ライフスタイルブランド 自社通販 株式会社 お問い合わせ",
    ],

    # EC運営代行・ECコンサル（商品画像を複数モール用に変換する業務が日常）
    "ec_agency": [
        "EC運営代行 楽天 Amazon 株式会社 お問い合わせ",
        "ECコンサルティング 通販運営支援 会社 コンタクト",
        "ネットショップ運営代行 商品撮影 画像制作 お問い合わせ",
        "ECサイト構築 運営 中小企業支援 株式会社 お問い合わせフォーム",
    ],

    # 飲食チェーン・フードブランド（料理写真をInstagram・Google・デリバリーアプリ・公式サイト用に毎回変換）
    "food": [
        "飲食チェーン 複数店舗 SNS運用 公式 株式会社 お問い合わせ",
        "カフェ レストラン 食品ブランド 自社SNS 公式サイト お問い合わせ",
        "フードブランド 食品メーカー 公式Instagram 会社 コンタクト",
        "飲食 グルメ 公式アカウント運営 株式会社 お問い合わせフォーム",
    ],

    # アパレル・ファッションブランド（商品写真をEC・SNS・広告・LPと全方面で使う）
    "apparel": [
        "アパレルブランド 自社EC SNS公式 株式会社 お問い合わせ",
        "ファッションブランド Instagram 公式 商品画像 会社 コンタクト",
        "セレクトショップ 自社通販 SNS運用 株式会社 お問い合わせフォーム",
        "レディースファッション メンズ 自社ブランド 公式 お問い合わせ",
    ],

    # 美容・コスメブランド（商品ビジュアルをEC・SNS・PR・広告で使い回す）
    "beauty_brand": [
        "コスメブランド スキンケア 自社EC Instagram 公式 お問い合わせ",
        "化粧品メーカー SNS公式 商品撮影 中小 株式会社 コンタクト",
        "美容 ヘアケア ボディケア D2C 自社ブランド お問い合わせフォーム",
        "コスメ 美容液 ファンデーション 自社通販 SNS 会社 お問い合わせ",
    ],

    # ホテル・旅館・観光施設（客室・料理・施設写真をOTA・SNS・公式サイトで別サイズ掲載）
    "hotel": [
        "ホテル 旅館 公式サイト Instagram 複数施設 株式会社 お問い合わせ",
        "観光施設 リゾート 宿泊施設 SNS公式 会社 コンタクト",
        "グランピング アウトドア施設 公式Instagram 会社 お問い合わせフォーム",
        "ブティックホテル 旅館 自社SNS 公式 お問い合わせ",
    ],

    # スポーツ・フィットネス事業会社（施設写真・プログラム紹介をSNS・LP・広告で展開）
    "sports": [
        "フィットネスクラブ スポーツジム 複数店舗 公式SNS 株式会社 お問い合わせ",
        "ヨガスタジオ ピラティス スポーツスクール 自社SNS 会社 コンタクト",
        "スポーツブランド アウトドア 公式Instagram 事業会社 お問い合わせフォーム",
        "総合スポーツクラブ 施設運営 SNS公式 株式会社 お問い合わせ",
    ],

    # 不動産会社（物件写真をSUUMO・HOME'S・SNS・自社サイトで別フォーマット掲載）
    "realestate": [
        "不動産会社 物件写真 SNS公式 Instagram 株式会社 お問い合わせ",
        "不動産 賃貸 売買 自社SNS 複数媒体掲載 会社 コンタクト",
        "デベロッパー マンション 物件撮影 SNS運用 株式会社 お問い合わせフォーム",
        "不動産仲介 リノベーション 公式Instagram 事業会社 お問い合わせ",
    ],

    # イベント・エンタメ会社（告知ビジュアルをSNS各種・LP・チラシ用と大量に展開）
    "event": [
        "イベント企画会社 告知 SNS公式 株式会社 お問い合わせ",
        "エンタメ 音楽 ライブイベント 公式SNS 会社 コンタクト",
        "展示会 フェス イベント運営 ビジュアル制作 株式会社 お問い合わせフォーム",
        "イベントプロデュース 公式Instagram TikTok 事業会社 お問い合わせ",
    ],

    # 映像制作会社（納品・公開時にYouTube/Instagram/TikTok向けサムネ・KV全フォーマット書き出しが必要）
    "video": [
        "映像制作会社 動画制作 株式会社 お問い合わせ -求人 -転職",
        "動画制作 YouTube Instagram TikTok 制作プロダクション コンタクト",
        "映像プロダクション CM MV 広告映像 中小 お問い合わせフォーム",
        "動画編集 映像ディレクション 会社 株式会社 コンタクト -求人",
        "映像コンテンツ制作 SNS動画 サムネイル制作 会社 お問い合わせ",
    ],
}

FORM_KEYWORDS = ["お問い合わせ", "contact", "問い合わせ", "コンタクト", "inquiry", "<form"]

# 除外ドメイン（大手・プラットフォーム・ブログ・Q&A・海外）
SKIP_DOMAINS = {
    # 大手ECプラットフォーム
    "amazon.co.jp", "amazon.com", "rakuten.co.jp", "yahoo.co.jp",
    "mercari.com", "zozotown.com", "qoo10.jp", "shopify.com",
    "thebase.com", "stores.jp", "makeshop.jp", "futureshop.jp",
    "squareup.com", "square.com", "colormestore.com",
    # 大手ブランド
    "newbalance.jp", "nike.com", "adidas.co.jp", "uniqlo.com",
    "muji.com", "nitori-net.jp", "loft.co.jp",
    # SNS・動画
    "instagram.com", "tiktok.com", "youtube.com", "twitter.com", "x.com",
    "facebook.com", "linkedin.com", "pinterest.com", "line.me",
    # ブログ・メディア・マーケティング記事サイト
    "note.com", "ameblo.jp", "livedoor.jp", "fc2.com", "jugem.jp",
    "hatena.ne.jp", "wordpress.com", "wix.com", "jimdo.com",
    "medium.com", "substack.com", "blogger.com",
    "liskul.com", "ferret-plus.com", "webtan.impress.co.jp",
    "markezine.jp", "itmedia.co.jp", "nenshu.jp",
    # Q&A・まとめ
    "chiebukuro.yahoo.co.jp", "detail.chiebukuro.yahoo.co.jp",
    "quora.com", "reddit.com", "okwave.jp",
    "zhihu.com", "naver.com", "pixiv.net",
    # ニュース・比較サイト
    "nikkei.com", "asahi.com", "yomiuri.co.jp", "mainichi.jp",
    "kakaku.com", "kurashiru.com", "hikaku.kurashiru.com",
    "mybest.jp", "rankingoo.net", "price.com",
    # 求人
    "indeed.com", "recruit.co.jp", "mynavi.jp", "rikunabi.com",
    "wantedly.com", "doda.jp", "en-japan.com",
    # プレスリリース・行政・協会
    "prtimes.jp", "atpress.ne.jp", "dreamnews.jp",
    "meti.go.jp", "chusho.meti.go.jp", "j-net21.smrj.go.jp",
    # その他
    "google.com", "google.co.jp", "bing.com", "wikipedia.org",
    "github.com", "qiita.com", "zenn.dev",
    "ebisumart.com", "cuenote.jp", "fril.jp", "netshop.impress.co.jp",
}

# ブログ・記事URLのパスパターン（これが含まれるURLは除外）
SKIP_URL_PATHS = [
    "/blog/", "/column/", "/media/", "/article/", "/news/",
    "/useful_info", "/library/", "/knowledge/", "/howto/",
    "/magazine/", "/post-", "/entry/", "/archives/",
]

# 海外サイト判定（これらのTLDは除外）
FOREIGN_TLDS = {".com.cn", ".cn", ".kr", ".tw", ".vn", ".th", ".id",
                ".ru", ".de", ".fr", ".it", ".es", ".nl", ".pl"}


def extract_domain(url: str) -> str:
    return urlparse(url).netloc.lower().lstrip("www.")


def is_skip_domain(domain: str) -> bool:
    """大手・ブログ・海外・行政サイトを除外"""
    if domain in SKIP_DOMAINS:
        return True
    for skip in SKIP_DOMAINS:
        if domain.endswith("." + skip) or domain == skip:
            return True
    for tld in FOREIGN_TLDS:
        if domain.endswith(tld):
            return True
    # 行政・公共機関
    if domain.endswith(".lg.jp") or domain.endswith(".go.jp") or domain.endswith(".ed.jp"):
        return True
    return False


def is_article_url(url: str) -> bool:
    """ブログ記事・コラムURLを除外"""
    path = urlparse(url).path.lower()
    return any(pat in path for pat in SKIP_URL_PATHS)


def is_blacklisted(domain: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM blacklist WHERE domain=?", (domain,)
        ).fetchone()
    return row is not None


def is_duplicate(domain: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM target_companies WHERE domain=?", (domain,)
        ).fetchone()
    return row is not None


def check_robots(url: str) -> bool:
    """robots.txt でアクセス禁止されていないか確認（禁止→False）"""
    try:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        rp = RobotFileParser()
        rp.set_url(robots_url)
        rp.read()
        return rp.can_fetch("*", url)
    except Exception:
        return True  # 取得できなければ許可扱い


def check_has_form(url: str) -> bool:
    """HTMLにフォーム関連キーワードが含まれているか確認"""
    try:
        resp = httpx.get(url, timeout=8, follow_redirects=True,
                         headers={"User-Agent": "Mozilla/5.0"})
        html = resp.text.lower()
        return any(kw.lower() in html for kw in FORM_KEYWORDS)
    except Exception:
        return False


def search_ddg(query: str, num: int = 10) -> list[dict]:
    """DuckDuckGo で検索（ローカル用フォールバック）"""
    try:
        results = DDGS().text(
            query + " site:co.jp OR site:jp",
            region="jp-jp",
            max_results=num * 2,
        )
        return [{"link": r["href"], "title": r.get("title", "")} for r in results]
    except Exception as e:
        print(f"[DDG] 検索エラー: {e}")
        return []


def search_serpapi(query: str, num: int = 10) -> list[dict]:
    """SerpAPI で検索（Railway本番用）"""
    try:
        from serpapi import GoogleSearch
        params = {
            "q": query + " site:co.jp OR site:jp",
            "hl": "ja",
            "gl": "jp",
            "num": min(num * 2, 10),
            "api_key": os.getenv("SERPAPI_KEY", ""),
        }
        search = GoogleSearch(params)
        results = search.get_dict().get("organic_results", [])
        return [{"link": r.get("link", ""), "title": r.get("title", "")} for r in results]
    except Exception as e:
        print(f"[SerpAPI] 検索エラー: {e}")
        return []


def search(query: str, num: int = 10) -> list[dict]:
    """SerpAPIが設定されていればSerpAPI、なければDDGを使う"""
    if os.getenv("SERPAPI_KEY"):
        return search_serpapi(query, num)
    return search_ddg(query, num)


def is_japanese_domain(domain: str) -> bool:
    """日本のドメインかどうか判定"""
    jp_tlds = (".co.jp", ".jp", ".ne.jp", ".or.jp", ".ac.jp", ".gr.jp")
    if any(domain.endswith(t) for t in jp_tlds):
        return True
    # .com でも日本語サイトの可能性があるのでそのままは通す
    # 明らかに海外のものはSKIP_DOMAINSで除外済み
    return True


def collect(industry: str, count: int = 30) -> dict:
    """
    指定業種のURLをcount件収集してDBに保存する。
    戻り値: { saved: int, skipped: int, details: list }
    """
    init_db()

    templates = QUERY_TEMPLATES.get(industry)
    if not templates:
        return {"error": f"未知の業種: {industry}. 選択肢: {list(QUERY_TEMPLATES.keys())}"}

    saved = 0
    skipped = 0
    details = []

    for query in templates:
        if saved >= count:
            break

        print(f"[Search] 検索: {query}")
        items = search(query, num=count)

        for item in items:
            if saved >= count:
                break

            url   = item.get("link", "")
            title = item.get("title", "")
            domain = extract_domain(url)

            # --- フィルタリング ---
            if not url.startswith("http"):
                skipped += 1
                continue
            if is_skip_domain(domain):
                details.append({"url": url, "result": "大手/ブログ/海外除外"})
                skipped += 1
                continue
            if is_article_url(url):
                details.append({"url": url, "result": "記事URL除外"})
                skipped += 1
                continue
            # .jpドメイン以外は日本語コンテンツか確認
            if not any(domain.endswith(t) for t in (".co.jp", ".jp", ".ne.jp")):
                try:
                    r = httpx.get(url, timeout=5, follow_redirects=True,
                                  headers={"User-Agent": "Mozilla/5.0"})
                    # 日本語文字が含まれていなければ除外
                    if not any(0x3000 <= ord(c) <= 0x9FFF for c in r.text[:2000]):
                        details.append({"url": url, "result": "海外サイト除外"})
                        skipped += 1
                        continue
                except Exception:
                    pass
            if is_blacklisted(domain):
                details.append({"url": url, "result": "BL除外"})
                skipped += 1
                continue
            if is_duplicate(domain):
                details.append({"url": url, "result": "重複"})
                skipped += 1
                continue
            if not check_robots(url):
                details.append({"url": url, "result": "robots除外"})
                skipped += 1
                continue

            has_form = check_has_form(url)

            # DB保存
            try:
                with get_conn() as conn:
                    conn.execute("""
                        INSERT INTO target_companies
                            (url, domain, title, industry, keyword, has_form, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (url, domain, title, industry, query,
                          1 if has_form else 0,
                          "ready" if has_form else "no_form"))
                saved += 1
                details.append({"url": url, "result": "保存済み", "has_form": has_form})
                print(f"  ✓ {domain} (フォーム={'あり' if has_form else 'なし'})")
            except Exception as e:
                skipped += 1
                details.append({"url": url, "result": f"DBエラー: {e}"})

    return {"saved": saved, "skipped": skipped, "details": details}


if __name__ == "__main__":
    # テスト実行: EC業種を10件収集
    result = collect("ec", count=10)
    print(f"\n収集完了: 保存={result['saved']} / スキップ={result['skipped']}")
