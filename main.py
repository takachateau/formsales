"""
フォーム営業ツール — FastAPI エントリーポイント
起動: uvicorn main:app --reload --port 8010
"""
import asyncio
from pathlib import Path
from fastapi import FastAPI, HTTPException, Path as FPath
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from database import init_db, get_conn
from collector import collect
from scraper import scrape_form
from generator import generate_message, generate_sales_text
from sender import submit_form

app = FastAPI(title="フォーム営業ツール", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def on_startup():
    init_db()


@app.get("/", response_class=HTMLResponse)
def index():
    html = (Path(__file__).parent / "ui.html").read_text(encoding="utf-8")
    return HTMLResponse(content=html)


# ─────────────────────────────────────────────
# リスト収集
# ─────────────────────────────────────────────

class CollectRequest(BaseModel):
    industry: str   # ec / sns / agency / d2c
    count: int = 20


@app.post("/collect")
def api_collect(req: CollectRequest):
    """Google CSEでターゲットURLを収集してDBに保存"""
    result = collect(req.industry, req.count)
    return result


# ─────────────────────────────────────────────
# 企業一覧
# ─────────────────────────────────────────────

@app.get("/companies")
def api_companies(status: str = None, industry: str = None, limit: int = 200):
    """収集済みの企業一覧を返す"""
    with get_conn() as conn:
        query = "SELECT * FROM target_companies WHERE 1=1"
        params = []
        if status:
            query += " AND status=?"
            params.append(status)
        if industry:
            query += " AND industry=?"
            params.append(industry)
        query += " ORDER BY industry, id DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


class DeleteRequest(BaseModel):
    ids: list[int]


@app.delete("/companies")
def api_companies_delete(req: DeleteRequest):
    """指定IDの企業を削除"""
    with get_conn() as conn:
        placeholders = ",".join("?" * len(req.ids))
        conn.execute(f"DELETE FROM target_companies WHERE id IN ({placeholders})", req.ids)
    return {"deleted": len(req.ids)}


# ─────────────────────────────────────────────
# フォーム解析 → メッセージ生成
# ─────────────────────────────────────────────

class PrepareRequest(BaseModel):
    company_id: int
    force: bool = False   # True のとき既存保存データを無視して再生成


@app.post("/prepare")
async def api_prepare(req: PrepareRequest):
    """指定企業のフォームを解析してAIでメッセージを生成する（未送信）"""
    import json as _json
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM target_companies WHERE id=?", (req.company_id,)
        ).fetchone()
    if not row:
        raise HTTPException(404, "企業が見つかりません")

    company = dict(row)

    # ── 保存済みデータがあれば即返す（force=False のとき）──
    if not req.force and company.get("saved_message") and company.get("saved_fields"):
        try:
            field_values = _json.loads(company["saved_fields"])
            field_defs   = _json.loads(company["saved_field_defs"]) if company.get("saved_field_defs") else []
            return {
                "company":      company,
                "contact_url":  company.get("contact_url"),
                "fields":       field_defs,
                "field_values": field_values,
                "message_body": company["saved_message"],
                "from_cache":   True,
            }
        except Exception:
            pass  # JSON parse 失敗時は再生成

    # ── フォーム解析 ──
    scrape_result = await scrape_form(company["url"], company_id=req.company_id)
    if scrape_result["error"]:
        return {"error": scrape_result["error"]}

    title           = company["title"] or company["domain"]
    industry        = company["industry"] or ""
    company_snippet = scrape_result.get("company_snippet", "")

    # 営業文を独立して生成（常に確保）
    message_body = await generate_sales_text(
        title, company["url"], industry, company_snippet
    )

    # フォームフィールドへの入力値を生成
    field_values = await generate_message(
        company_title=title,
        company_url=company["url"],
        industry=industry,
        fields=scrape_result["fields"],
        company_snippet=company_snippet,
    )

    # field_values 内に営業文が含まれていれば上書き（より文脈に合った文章を優先）
    msg_keywords = ['message', 'inquiry', 'content', 'body', 'naiyo',
                    'detail', 'text', 'お問い合わせ', '内容', 'メッセージ']
    for key, val in field_values.items():
        if isinstance(val, str) and any(k in key.lower() for k in msg_keywords):
            if len(val) > 50:
                message_body = val
                break

    # ── 生成結果をDBに保存 ──
    with get_conn() as conn:
        conn.execute(
            """UPDATE target_companies
               SET contact_url=?, saved_message=?, saved_fields=?, saved_field_defs=?, message_saved_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (
                scrape_result["contact_url"],
                message_body,
                _json.dumps(field_values, ensure_ascii=False),
                _json.dumps(scrape_result["fields"], ensure_ascii=False),
                req.company_id,
            )
        )

    # フォームスクリーンショットをbase64化
    import os as _os
    form_ss_b64 = None
    ss_path = scrape_result.get("form_screenshot")
    if ss_path and _os.path.exists(ss_path):
        import base64 as _b64
        with open(ss_path, "rb") as f:
            form_ss_b64 = "data:image/png;base64," + _b64.b64encode(f.read()).decode()

    return {
        "company":           company,
        "contact_url":       scrape_result["contact_url"],
        "fields":            scrape_result["fields"],
        "field_values":      field_values,
        "message_body":      message_body,
        "from_cache":        False,
        "form_screenshot":   form_ss_b64,
    }


# ─────────────────────────────────────────────
# フォームページ スクリーンショット
# ─────────────────────────────────────────────

class ScreenshotRequest(BaseModel):
    url: str
    company_id: int = 0

@app.post("/screenshot")
async def api_screenshot(req: ScreenshotRequest):
    """指定URLのフォームページをスクリーンショットして返す"""
    import base64 as _b64, os as _os
    from playwright.async_api import async_playwright

    # フォームページへのリンクキーワード（先頭ほど高スコア）
    FORM_LINK_KEYWORDS = [
        "お問い合わせフォーム", "フォームはこちら", "フォームへ",
        "お問い合わせはこちら", "問い合わせフォーム", "メールフォーム",
        "contact form", "inquiry form",
        "お問い合わせ", "問い合わせ", "contact", "inquiry",
    ]

    result = {"screenshot_base64": None, "error": None, "final_url": req.url}
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/120.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 900},
            )
            page = await context.new_page()
            page.set_default_timeout(20000)

            async def nav(url):
                try:
                    await page.goto(url, wait_until="networkidle", timeout=20000)
                except Exception:
                    try:
                        await page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    except Exception:
                        pass

            async def has_contact_form() -> bool:
                """textarea があればお問い合わせフォームとみなす"""
                try:
                    return await page.locator("textarea").count() > 0
                except Exception:
                    return False

            async def get_best_link():
                """現ページで最もフォームページらしいリンクのhrefを返す"""
                try:
                    items = await page.eval_on_selector_all(
                        "a[href]",
                        "els => els.map(e => ({href: e.href, text: (e.innerText||e.textContent||'').trim(), title: e.title||''}))"
                    )
                except Exception:
                    return None
                best_href, best_score = None, 0
                for item in items:
                    combined = (item.get("href","") + " " + item.get("text","") + " " + item.get("title","")).lower()
                    score = sum((len(FORM_LINK_KEYWORDS) - i) for i, kw in enumerate(FORM_LINK_KEYWORDS) if kw.lower() in combined)
                    if score > best_score:
                        best_score, best_href = score, item.get("href","")
                return best_href if best_score > 0 else None

            # Step1: 指定URLへ移動
            await nav(req.url)
            result["final_url"] = page.url

            # Step2: textareaが見つかるまで最大4ホップ辿る
            visited = {page.url}
            for _ in range(4):
                if await has_contact_form():
                    break
                link = await get_best_link()
                if not link or link in visited:
                    break
                visited.add(link)
                await nav(link)
                result["final_url"] = page.url

            ss_path = f"/tmp/formsales_formcheck_{req.company_id}.png"

            if await has_contact_form():
                # textareaを含むフォームを直撮り
                form_el = page.locator("form:has(textarea)").first
                if await form_el.count() == 0:
                    form_el = page.locator("textarea").first
                await form_el.scroll_into_view_if_needed()
                await page.wait_for_timeout(800)
                await form_el.screenshot(path=ss_path)
            else:
                await page.screenshot(path=ss_path, full_page=False)

            await browser.close()

            with open(ss_path, "rb") as f:
                result["screenshot_base64"] = "data:image/png;base64," + _b64.b64encode(f.read()).decode()

    except Exception as e:
        result["error"] = str(e)

    return result


# ─────────────────────────────────────────────
# フォーム送信
# ─────────────────────────────────────────────

class SendRequest(BaseModel):
    company_id: int
    contact_url: str
    field_values: dict
    dry_run: bool = True   # デフォルトは確認モード（実送信しない）


@app.post("/send")
async def api_send(req: SendRequest):
    """フォームに入力して送信する（dry_run=True のうちは送信ボタンを押さない）"""
    import base64, os
    result = await submit_form(
        contact_url=req.contact_url,
        field_values=req.field_values,
        company_id=req.company_id,
        dry_run=req.dry_run,
    )

    # スクリーンショットをbase64で返す
    ss_path = result.get("screenshot")
    if ss_path and os.path.exists(ss_path):
        with open(ss_path, "rb") as f:
            result["screenshot_base64"] = "data:image/png;base64," + base64.b64encode(f.read()).decode()

    if result["success"] and not req.dry_run:
        # ステータスを「送信済み」に更新
        with get_conn() as conn:
            conn.execute(
                "UPDATE target_companies SET status='sent' WHERE id=?",
                (req.company_id,)
            )

    return result


# ─────────────────────────────────────────────
# 送信ログ
# ─────────────────────────────────────────────

@app.get("/logs")
def api_logs(limit: int = 50):
    """送信ログを返す（企業情報付き）"""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                sl.*,
                tc.title   AS company_title,
                tc.domain  AS company_domain,
                tc.industry AS company_industry
            FROM send_logs sl
            LEFT JOIN target_companies tc ON sl.company_id = tc.id
            ORDER BY sl.id DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]


class ReplyRequest(BaseModel):
    reply_content: str
    reply_status: str = ""   # 空の場合はClaudeで自動判定


@app.post("/logs/{log_id}/reply")
async def api_log_reply(log_id: int, req: ReplyRequest):
    """返信内容を記録し、Claudeで返信種別を自動判定する"""
    from generator import client, SYSTEM_PROMPT

    status = req.reply_status
    if not status:
        # Claude で自動判定
        try:
            resp = client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=64,
                messages=[{
                    "role": "user",
                    "content": (
                        "以下のメール返信を読んで、次の4つのどれか1語だけ返してください。\n"
                        "auto_reply（自動返信・定型文）\n"
                        "human_reply（人間が書いた通常の返信）\n"
                        "meeting_needed（面談・詳細ヒアリング・商談の意向あり）\n"
                        "other（判断できない）\n\n"
                        f"--- 返信本文 ---\n{req.reply_content[:1000]}"
                    )
                }]
            )
            raw = resp.content[0].text.strip().lower()
            for s in ["auto_reply", "human_reply", "meeting_needed", "other"]:
                if s in raw:
                    status = s
                    break
            if not status:
                status = "other"
        except Exception as e:
            print(f"[Reply] 判定エラー: {e}")
            status = "other"

    with get_conn() as conn:
        conn.execute(
            "UPDATE send_logs SET reply_status=?, reply_content=? WHERE id=?",
            (status, req.reply_content, log_id)
        )
    return {"log_id": log_id, "reply_status": status}


# ─────────────────────────────────────────────
# ブラックリスト
# ─────────────────────────────────────────────

class BlacklistRequest(BaseModel):
    domain: str
    reason: str = ""


@app.post("/blacklist")
def api_blacklist_add(req: BlacklistRequest):
    """ドメインをブラックリストに追加"""
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO blacklist (domain, reason) VALUES (?,?)",
            (req.domain, req.reason)
        )
    return {"added": req.domain}


@app.get("/blacklist")
def api_blacklist_list():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM blacklist ORDER BY added_at DESC").fetchall()
    return [dict(r) for r in rows]


@app.get("/debug/search")
def api_debug_search(q: str = "Web制作会社 東京 お問い合わせ site:co.jp"):
    """Google CSE の動作確認用（デバッグ専用）"""
    import os, httpx
    api_key = os.getenv("GOOGLE_CSE_API_KEY", "")
    cx = os.getenv("GOOGLE_CSE_CX", "")
    if not api_key or not cx:
        return {"error": "環境変数未設定", "GOOGLE_CSE_API_KEY": bool(api_key), "GOOGLE_CSE_CX": bool(cx)}
    try:
        resp = httpx.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": api_key, "cx": cx, "q": q, "num": 5},
            timeout=10,
        )
        return {"status": resp.status_code, "body": resp.json()}
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8010, reload=True)
