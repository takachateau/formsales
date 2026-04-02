"""
フォーム送信モジュール
Playwright でお問い合わせフォームに値を入力して送信する。
送信結果（成功/失敗/要確認）をDBに記録する。
"""
import asyncio
from playwright.async_api import async_playwright
from database import get_conn

CONTACT_HINTS = [
    "contact", "inquiry", "お問い合わせ", "問い合わせ", "toiawase",
    "form", "message", "support", "feedback", "kontakt",
]

SUBMIT_TEXTS = [
    '送信する', '送信', '確認する', '確認', '次へ進む', '次へ',
    '申し込む', '登録する', '送る', 'お問い合わせを送る',
    '入力内容を確認する', '入力内容を確認', '確認画面へ進む', '確認画面へ',
    '問い合わせる', '問い合わせを送る', 'Send', 'Submit', 'Confirm', 'Next',
]


async def _find_form(page) -> bool:
    """現在ページにフォームが存在するか確認"""
    try:
        count = await page.locator("form input, form textarea").count()
        return count > 0
    except Exception:
        return False


async def _navigate_to_form(page, base_url: str) -> bool:
    """お問い合わせページへのリンクを探してクリック"""
    try:
        links = await page.eval_on_selector_all(
            "a[href]",
            "els => els.map(e => ({href: e.href, text: e.innerText}))"
        )
        for link in links:
            href = link.get("href", "").lower()
            text = link.get("text", "").lower()
            if any(h in href or h in text for h in CONTACT_HINTS):
                await page.goto(link["href"], wait_until="networkidle", timeout=20000)
                return True
    except Exception:
        pass
    return False


async def submit_form(contact_url: str, field_values: dict,
                      company_id: int, dry_run: bool = True) -> dict:
    result = {"success": False, "result": "", "screenshot": None}

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36"
            )
            page = await context.new_page()
            page.set_default_timeout(20000)

            # ページ読み込み（networkidle で JS レンダリング完了まで待つ）
            try:
                await page.goto(contact_url, wait_until="networkidle", timeout=20000)
            except Exception:
                await page.goto(contact_url, wait_until="domcontentloaded", timeout=15000)

            # フォームが見つからない場合、お問い合わせリンクを探してナビゲート
            if not await _find_form(page):
                navigated = await _navigate_to_form(page, contact_url)
                if navigated:
                    # 再度フォームチェック
                    await asyncio.sleep(1)
                    if not await _find_form(page):
                        result["result"] = "フォームが見つかりませんでした"
                        screenshot_path = f"/tmp/formsales_{company_id}_noform.png"
                        await page.screenshot(path=screenshot_path, full_page=False)
                        result["screenshot"] = screenshot_path
                        await browser.close()
                        _log_send(company_id, contact_url, str(field_values), result["result"])
                        return result

            # フィールドへの入力
            filled = 0
            for name, value in field_values.items():
                if not value:
                    continue
                try:
                    sel = f"[name='{name}'], [id='{name}']"
                    el = page.locator(sel).first
                    if await el.count() == 0:
                        continue
                    await el.wait_for(state="visible", timeout=3000)
                    tag = await el.evaluate("e => e.tagName.toLowerCase()")
                    input_type = await el.evaluate("e => (e.type||'').toLowerCase()")

                    if tag == "select":
                        await el.select_option(label=value)
                    elif input_type in ("radio", "checkbox"):
                        pass  # ラジオ・チェックはスキップ
                    else:
                        await el.fill(str(value))
                    filled += 1
                except Exception:
                    pass

            # ── 入力後スクリーンショット（フォーム要素を直接撮影）──
            screenshot_path = f"/tmp/formsales_{company_id}_filled.png"
            try:
                form_el = page.locator("form:has(textarea)").first
                if await form_el.count() == 0:
                    form_el = page.locator("form").first
                await form_el.scroll_into_view_if_needed()
                await page.wait_for_timeout(600)
                await form_el.screenshot(path=screenshot_path)
            except Exception:
                await page.screenshot(path=screenshot_path, full_page=False)
            result["screenshot"] = screenshot_path

            if dry_run:
                result["result"] = f"DRY_RUN: {filled}フィールド入力完了"
                result["success"] = True
            else:
                clicked = False

                # 1. type=submit を優先
                for sel in ["button[type='submit']", "input[type='submit']"]:
                    try:
                        btn = page.locator(sel).first
                        await btn.wait_for(state="visible", timeout=5000)
                        await btn.click()
                        clicked = True
                        break
                    except Exception:
                        pass

                # 2. テキストマッチ
                if not clicked:
                    for text in SUBMIT_TEXTS:
                        try:
                            btn = page.locator(
                                f"button:has-text('{text}'), input[value='{text}']"
                            ).first
                            if await btn.count() > 0:
                                await btn.wait_for(state="visible", timeout=3000)
                                await btn.click()
                                clicked = True
                                break
                        except Exception:
                            pass

                # 3. フォーム内の最後のボタン（JSフォールバック）
                if not clicked:
                    try:
                        await page.evaluate("""
                            () => {
                                const forms = document.querySelectorAll('form');
                                for (const form of forms) {
                                    const btns = form.querySelectorAll(
                                        'button, input[type=submit], input[type=button]'
                                    );
                                    if (btns.length > 0) {
                                        btns[btns.length - 1].click();
                                        return true;
                                    }
                                }
                                return false;
                            }
                        """)
                        clicked = True
                    except Exception:
                        pass

                if not clicked:
                    raise Exception("送信ボタンが見つかりませんでした")

                # ── 送信後：サンキューページを待つ ──
                THANK_YOU = [
                    "ありがとうございました", "ありがとうございます",
                    "送信完了", "完了しました", "受け付けました",
                    "お問い合わせを受け付け", "送信が完了",
                    "thank you", "success", "received", "submitted",
                ]
                # まず networkidle を待つ
                try:
                    await page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:
                    pass

                # サンキューテキストが出るまで最大10秒ポーリング
                for _ in range(20):
                    content = await page.content()
                    if any(h in content.lower() for h in THANK_YOU):
                        break
                    await page.wait_for_timeout(500)

                # 送信後スクリーンショット（ビューポートのみ・ページ先頭に戻す）
                try:
                    await page.evaluate("window.scrollTo(0, 0)")
                    await page.wait_for_timeout(300)
                except Exception:
                    pass
                screenshot_path = f"/tmp/formsales_{company_id}_sent.png"
                await page.screenshot(path=screenshot_path, full_page=False)
                result["screenshot"] = screenshot_path

                content = await page.content()
                if any(h in content.lower() for h in THANK_YOU):
                    result["result"] = "送信成功（完了ページ確認済み）"
                    result["success"] = True
                else:
                    result["result"] = f"送信操作完了（画面を確認してください） URL:{page.url}"
                    result["success"] = True

            await browser.close()

    except Exception as e:
        result["result"] = f"エラー: {e}"

    _log_send(company_id, contact_url, str(field_values), result["result"])
    return result


def _log_send(company_id: int, url: str, message: str, result: str):
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO send_logs (company_id, url, message, result) VALUES (?,?,?,?)",
                (company_id, url, message, result)
            )
    except Exception as e:
        print(f"[Sender] ログ記録エラー: {e}")
