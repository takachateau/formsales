"""
ローカル SQLite DB管理
target_companies: リスト自動収集で溜まったURL
send_logs:        送信ログ
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "formsales.db"

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS target_companies (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            url         TEXT NOT NULL,
            domain      TEXT NOT NULL,
            title       TEXT,
            industry    TEXT,
            keyword     TEXT,
            has_form    INTEGER DEFAULT 0,
            status      TEXT DEFAULT 'pending',
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_domain
            ON target_companies(domain);

        CREATE TABLE IF NOT EXISTS send_logs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id      INTEGER REFERENCES target_companies(id),
            url             TEXT,
            message         TEXT,
            result          TEXT,
            reply_status    TEXT DEFAULT 'none',
            reply_content   TEXT,
            sent_at         DATETIME DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS blacklist (
            domain      TEXT PRIMARY KEY,
            reason      TEXT,
            added_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        );
        """)
        # マイグレーション: 既存DBに新カラムを追加
        for table, col, definition in [
            ("send_logs",        "reply_status",    "TEXT DEFAULT 'none'"),
            ("send_logs",        "reply_content",   "TEXT"),
            # 生成済み営業文の保存
            ("target_companies", "contact_url",     "TEXT"),
            ("target_companies", "saved_message",   "TEXT"),
            ("target_companies", "saved_fields",    "TEXT"),
            ("target_companies", "message_saved_at","DATETIME"),
            ("target_companies", "saved_field_defs","TEXT"),
        ]:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {definition}")
            except Exception:
                pass  # 既に存在する場合はスキップ
    print(f"[DB] initialized: {DB_PATH}")

if __name__ == "__main__":
    init_db()
