"""
メッセージ生成モジュール
Claude API でターゲット企業ごとにパーソナライズされた営業文を生成する。
"""
import os
import json
import re
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

# ─── 送信者情報 ───
SENDER = {
    "company":    "株式会社Chateau 2D",
    "last_name":  "金指",
    "first_name": "孝章",
    "name":       "金指孝章",
    "title":      "代表",
    "email":      "info@designresize.pro",
    "service":    "クリエイティブAIツール",
    "lp_url":     os.getenv("LP_URL", "https://www.designresize.pro/lp"),
    "tagline":    "1枚のデザイン・写真をアップロードするだけで、Instagram・X・HP・EC・広告など各プラットフォームのサイズに自動変換できるツール（DesignResize Pro）",
}

# ─── 業種別ペイン描写 ───
# 方針：
#   - 相手の実際のワークフローの中に存在する具体的な課題を描写する
#   - 「リサイズ」という単語より「〇〇の作業が発生する」という状況描写を優先
#   - 媒体名・フォーマット名・工程を具体的に挙げることで「自分のことだ」と思わせる
#   - 断言調・淡々としたプロの言葉で書く（感嘆符なし、過度な口語表現なし）
INDUSTRY_PAIN = {

    "creative": (
        "Webサイトやバナーを制作したあと、PC用・スマホ用・OGP・各SNS規格と"
        "同じデザインを何度もサイズ違いで作り直していませんか。"
        "1枚アップロードするだけで、各プラットフォームのサイズに自動変換できます。"
    ),

    "agency": (
        "1本のキャンペーンで、GDN・Meta・LINE・Xそれぞれの規定サイズに合わせて"
        "同じバナーをサイズ違いで作り直す作業が続いていませんか。"
        "修正が入るたびに全サイズ作り直す手間が、1ボタンで完結します。"
    ),

    "sns": (
        "同じ投稿ビジュアルを、フィード用・ストーリーズ用・リール用とサイズを変えて"
        "何度もデザインし直していませんか。"
        "1枚アップロードするだけで、Instagram・X・TikTokなど各サイズに自動対応できます。"
    ),

    "ec_agency": (
        "楽天・Amazon・Yahoo・自社ECは掲載サイズの規格がそれぞれ異なり、"
        "同じ商品画像をモールごとに作り直す作業が続いていませんか。"
        "1枚アップロードするだけで、全モールのサイズに自動変換できます。"
    ),

    "ec": (
        "商品写真を撮影したあと、ECサイト・Instagram・広告・LPと"
        "掲載先ごとにサイズを変えて何度も作り直していませんか。"
        "1枚アップロードするだけで、各プラットフォームのサイズに自動変換できます。"
    ),

    "d2c": (
        "ブランドのビジュアルをEC・SNS・LP・広告と展開するたびに、"
        "同じデザインを各サイズで作り直す作業が発生していませんか。"
        "1枚アップロードするだけで、Instagram・X・HP・ECなど全サイズに自動対応できます。"
    ),

    "video": (
        "映像を公開するたびに、YouTube用サムネイル・Instagramフィード・"
        "ストーリーズ・TikTokと、同じキービジュアルをサイズ違いで作り直していませんか。"
        "1枚アップロードするだけで、各プラットフォームのサイズに自動変換できます。"
    ),

    "food": (
        "新メニューや季節ごとの料理写真を、Instagram・公式サイト・"
        "デリバリーアプリ・Googleビジネスに掲載するたびサイズを作り直していませんか。"
        "1枚アップロードするだけで、各プラットフォームのサイズに自動変換できます。"
    ),

    "apparel": (
        "新作やシーズンビジュアルをEC・Instagram・Pinterest・広告バナーと"
        "展開するたびに、同じ写真をサイズ違いで作り直していませんか。"
        "1枚アップロードするだけで、各プラットフォームのサイズに自動変換できます。"
    ),

    "beauty_brand": (
        "商品ビジュアルをEC掲載・Instagram・広告入稿・PR素材と"
        "展開するたびに、SKUごとにサイズを作り直す作業が続いていませんか。"
        "1枚アップロードするだけで、各プラットフォームのサイズに自動変換できます。"
    ),

    "hotel": (
        "客室や施設の写真を、じゃらん・楽天トラベル・公式サイト・Instagram・"
        "Googleビジネスと掲載先ごとにサイズを作り直していませんか。"
        "1枚アップロードするだけで、各媒体のサイズに自動変換できます。"
    ),

    "sports": (
        "キャンペーンや新プログラムのビジュアルを、Instagram・ストーリーズ・"
        "LINEバナー・Webサイトとサイズを変えて何度も作り直していませんか。"
        "1枚アップロードするだけで、各プラットフォームのサイズに自動変換できます。"
    ),

    "realestate": (
        "物件写真をSUUMO・LIFULL HOME'S・自社サイト・SNSと掲載先ごとに"
        "サイズを変えて作り直す作業が、新規物件のたびに発生していませんか。"
        "1枚アップロードするだけで、各媒体のサイズに自動変換できます。"
    ),

    "event": (
        "イベントのキービジュアルをX・Instagram・ストーリーズ・TikTok・LP・"
        "フライヤーと、告知のたびにサイズを変えて作り直していませんか。"
        "1枚アップロードするだけで、各プラットフォームのサイズに自動変換できます。"
    ),
}

INDUSTRY_FALLBACK = (
    "画像・動画を媒体ごとに繰り返しリサイズする作業が、"
    "1回のアップロードで全フォーマット分まとめて完了するようになります。"
)

SYSTEM_PROMPT = """
あなたはBtoB向けフォーム営業の文章を書く専門家です。
受け取った担当者が「自社の状況をわかっている」「読んでみようか」と感じる、
自然な引きのある文章を書いてください。

【文体の原則】
- 読み手が「自分のことだ」と感じる書き出しで始める
- プロフェッショナルで読みやすい。口語的すぎず、堅苦しすぎず
- 感嘆符（！）は使わない
- 「〜ですね」「〜ぜひ」「〜是非」などの押しつけがましい言い回しを避ける
- 短文を積み重ねるリズムで書く

【構成（必ずこの順番で、各ブロックの間は必ず改行を入れること）】
1. 【冒頭フック・必ず相手の会社名を入れる】
   「（会社名）さんは、〜していませんか。」のように、相手の会社名を冒頭に自然に入れたうえで、
   その会社が日常的に直面している作業の煩わしさを描写した問いかけ1〜2文。
   例：「〇〇株式会社さんは、SNS投稿のたびに同じデザインをInstagram用・X用・ストーリーズ用とサイズ違いで作り直していませんか。」
   ※「突然のご連絡」「〜に関してです」「はじめまして」などの書き出しは禁止。
   ※会社名は省略せず正式名称で入れること。

2. 【解決の提示】（改行してから）
   DesignResize Proを使えば、1枚アップロードするだけでInstagram・X・HP・EC・広告など
   各プラットフォームのサイズに自動変換できる、という旨を1〜2文で。

3. 【送信者】（改行してから）
   会社名・名前を1行で。

4. 【リンク】（改行してから）
   LPリンク＋「詳細はこちらからご確認いただけます」

【守ること】
- 全体150〜250字
- 必ず相手の会社名を冒頭に入れる（省略・変形不可）
- 各ブロックの間は必ず改行（\n）を入れて読みやすくする
- 課題描写は具体的な媒体名・作業シーンを使う
- LPリンク以外のURLは記載しない
- 面談・商談の打診はしない
- 使用禁止：「ご多忙の折」「弊社サービスをご紹介」「ご検討ください」「お役に立てれば」「いかがでしょうか」「突然のご連絡」「はじめまして」「〜に関してです」
"""


def build_prompt(company_title: str, company_url: str,
                 industry: str, fields: list[dict],
                 company_snippet: str = "") -> str:

    pain = INDUSTRY_PAIN.get(industry, INDUSTRY_FALLBACK)
    field_names = [f"{f.get('label') or f.get('name', '')}({f.get('type', '')})"
                   for f in fields]
    fields_str = "、".join(field_names) if field_names else "不明"

    snippet_section = f"""
【相手企業のHP概要（冒頭パーソナライズに必ず使うこと）】
{company_snippet}
→ この情報をもとに、冒頭1文に「この会社・このサービス宛て」と伝わる具体的な言及を入れること。
""" if company_snippet else ""

    return f"""
以下の企業のお問い合わせフォームに送る営業文を作ってください。

【相手企業】
- 社名: {company_title}
- URL: {company_url}
- 業種: {industry}
{snippet_section}
【送信者】
- {SENDER['company']} {SENDER['title']}の{SENDER['name']}と申します
- 提案: {SENDER['tagline']}
- LP: {SENDER['lp_url']}

【この業種特有の課題（必ずこのトーンで組み込むこと）】
{pain}

【フォームフィールド一覧】
{fields_str}

フォームの各フィールドに入れる値をJSONで返してください。
- 氏名（フルネーム）系: 「{SENDER['name']}」
- 姓（セイ・苗字・ファミリーネーム）系: 「{SENDER['last_name']}」
- 名（メイ・名前・ファーストネーム）系: 「{SENDER['first_name']}」
- 会社名系: 「{SENDER['company']}」
- メール系: 「{SENDER['email']}」
- 電話系: 「（省略可なら空欄のまま、必須なら 03-0000-0000）」
- お問い合わせ内容・メッセージ系: 上記ルールに従った150〜220字の営業文
- その他: 適切な値

出力はJSONのみ（説明文不要）:
{{
  "フィールドname": "入力値"
}}
"""


async def generate_sales_text(company_title: str, company_url: str,
                              industry: str,
                              company_snippet: str = "") -> str:
    """
    営業文本文だけを生成して返す（フォームフィールド不要）。
    プレビュー表示・フォールバック用。
    """
    pain = INDUSTRY_PAIN.get(industry, INDUSTRY_FALLBACK)

    snippet_section = f"""
【相手企業のHP概要（冒頭パーソナライズに必ず使うこと）】
{company_snippet}
→ この情報をもとに、冒頭1文に「この会社・このサービス宛て」と伝わる具体的な言及を入れること。
""" if company_snippet else ""

    prompt = f"""
以下の企業に送る営業文を1つ書いてください。

【相手企業】
- 社名: {company_title}
- URL: {company_url}
- 業種: {industry}
{snippet_section}
【送信者】
- {SENDER['company']} 代表の{SENDER['name']}と申します
- 提案: {SENDER['tagline']}
- LP: {SENDER['lp_url']}

【この業種特有の課題（必ずこのトーンで組み込むこと）】
{pain}

営業文のみ出力（JSONや説明文は不要）:
"""
    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as e:
        print(f"[Generator] 営業文生成エラー: {e}")
        return ""


async def generate_message(company_title: str, company_url: str,
                            industry: str, fields: list[dict],
                            company_snippet: str = "") -> dict:
    """
    Claude API でフォーム入力値を生成する。
    戻り値: { field_name: value, ... }
    """
    if not fields:
        return {}

    prompt = build_prompt(company_title, company_url, industry, fields, company_snippet)

    try:
        response = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()

        match = re.search(r"\{[\s\S]+\}", text)
        if match:
            return json.loads(match.group())
        return {}

    except Exception as e:
        print(f"[Generator] エラー: {e}")
        return {}
