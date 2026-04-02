"""
フォームスクレイパーモジュール
Playwright でターゲットサイトのお問い合わせフォームを解析し、
フィールド情報（name, type, label, required など）を返す。
"""
import re
from typing import Optional
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup


CONTACT_PATH_HINTS = [
    "contact", "inquiry", "お問い合わせ", "問い合わせ", "kontakt",
    "toiawase", "form", "message", "support", "feedback",
]


def _score_path(href: str) -> int:
    """お問い合わせページらしさのスコア（高いほど優先）"""
    h = href.lower()
    for hint in CONTACT_PATH_HINTS:
        if hint in h:
            return 1
    return 0


async def find_contact_url(page, base_url: str) -> Optional[str]:
    """トップページのリンクからお問い合わせページのURLを探す"""
    links = await page.eval_on_selector_all(
        "a[href]",
        "els => els.map(e => ({href: e.href, text: e.innerText}))"
    )
    candidates = [l for l in links if _score_path(l.get("href", ""))]
    if candidates:
        return candidates[0]["href"]
    return None


async def get_visible_fields(page) -> list[dict]:
    """
    Playwright で現在ページの「表示されているフィールド」だけを取得する。
    textareaを含むフォームを優先し、なければ最もフィールド数が多いフォームを対象とする。
    """
    return await page.evaluate("""
        () => {
            // textareaを含むフォームを優先、なければフィールド数最大のフォームを選ぶ
            const forms = Array.from(document.querySelectorAll('form'));
            if (forms.length === 0) return [];

            let target = forms.find(f => f.querySelector('textarea'));
            if (!target) {
                target = forms.reduce((a, b) =>
                    a.querySelectorAll('input,textarea,select').length >=
                    b.querySelectorAll('input,textarea,select').length ? a : b
                );
            }

            const fields = [];
            const seen = new Set();
            const els = target.querySelectorAll('input, textarea, select');

            for (const el of els) {
                // 非表示要素をスキップ（offsetParent チェックは除外 - fixed要素で誤検知するため）
                const style = window.getComputedStyle(el);
                if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') continue;
                const rect = el.getBoundingClientRect();
                if (rect.width === 0 && rect.height === 0) continue;

                const type = (el.type || el.tagName.toLowerCase()).toLowerCase();
                if (['hidden','submit','button','reset','image'].includes(type)) continue;

                const name = el.name || el.id || '';
                if (!name || seen.has(name)) continue;
                seen.add(name);

                // ラベル取得
                let label = '';
                if (el.id) {
                    const lbl = document.querySelector('label[for="' + el.id + '"]');
                    if (lbl) label = lbl.innerText.trim();
                }
                if (!label) label = el.getAttribute('aria-label') || el.placeholder || el.getAttribute('title') || '';

                // select のオプション取得
                let options = [];
                if (el.tagName.toLowerCase() === 'select') {
                    options = Array.from(el.options).map(o => ({
                        value: o.value,
                        label: o.text.trim()
                    })).filter(o => o.label);
                }

                fields.push({
                    name:     name,
                    type:     el.tagName.toLowerCase() === 'textarea' ? 'textarea'
                              : el.tagName.toLowerCase() === 'select'  ? 'select'
                              : type,
                    label:    label,
                    required: el.required || el.getAttribute('aria-required') === 'true',
                    options:  options,
                });
            }
            return fields;
        }
    """)


def _extract_company_snippet(html: str) -> str:
    """トップページから企業概要テキストを抽出する（営業文パーソナライズ用）"""
    soup = BeautifulSoup(html, "html.parser")

    # 1. OGP/meta description を優先
    for sel in [
        {"property": "og:description"},
        {"name": "description"},
        {"property": "og:title"},
    ]:
        tag = soup.find("meta", attrs=sel)
        if tag and tag.get("content", "").strip():
            text = tag["content"].strip()
            if len(text) > 10:
                return text[:300]

    # 2. h1 + 直後のリードコピーをフォールバック
    parts = []
    h1 = soup.find("h1")
    if h1:
        parts.append(h1.get_text(strip=True))
    for tag in soup.find_all(["p", "h2"], limit=5):
        t = tag.get_text(strip=True)
        if len(t) > 20:
            parts.append(t[:100])
            break
    if parts:
        return " / ".join(parts)[:300]

    return ""


async def scrape_form(url: str, company_id: int = 0) -> dict:
    """
    指定URLのお問い合わせフォームを解析する。
    戻り値: { contact_url, fields, company_snippet, form_screenshot, error }
    """
    result = {
        "contact_url": None,
        "fields": [],
        "company_snippet": "",
        "form_screenshot": None,  # フォームページのスクリーンショットパス
        "error": None,
    }

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            page.set_default_timeout(15000)

            # トップページを開く
            await page.goto(url, wait_until="domcontentloaded")
            top_html = await page.content()

            # トップページから企業概要を抽出
            result["company_snippet"] = _extract_company_snippet(top_html)

            # お問い合わせページを探す
            contact_url = await find_contact_url(page, url)
            if not contact_url:
                contact_url = url

            result["contact_url"] = contact_url

            # お問い合わせページを開く
            if contact_url != url:
                try:
                    await page.goto(contact_url, wait_until="networkidle", timeout=15000)
                except Exception:
                    await page.goto(contact_url, wait_until="domcontentloaded", timeout=15000)

            fields = await get_visible_fields(page)
            result["fields"] = fields

            # フォームページのスクリーンショットを撮る（証拠）
            try:
                # フォームが見えるようにスクロール
                try:
                    await page.eval_on_selector("form", "el => el.scrollIntoView()")
                except Exception:
                    pass
                ss_path = f"/tmp/formsales_form_{company_id or 'tmp'}.png"
                await page.screenshot(path=ss_path, full_page=False)
                result["form_screenshot"] = ss_path
            except Exception:
                pass

            await browser.close()

    except Exception as e:
        result["error"] = str(e)

    return result
