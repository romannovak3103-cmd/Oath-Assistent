"""Слой хранения: SQLite. Никакой логики LLM здесь нет."""
import os
import sqlite3
from datetime import datetime

DB_PATH = os.getenv("DB_PATH", "assistant.db")


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with _conn() as c:
        c.execute("""CREATE TABLE IF NOT EXISTS items(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            type TEXT NOT NULL CHECK(type IN ('task','meeting','reminder')),
            title TEXT NOT NULL,
            due_at TEXT,               -- 'YYYY-MM-DD' или 'YYYY-MM-DDTHH:MM' (локальное время)
            created_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','done'))
        )""")
        c.execute("CREATE INDEX IF NOT EXISTS idx_user_due ON items(user_id, due_at)")


def create(user_id, type_, title, due_at=None):
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO items(user_id,type,title,due_at,created_at) VALUES(?,?,?,?,?)",
            (user_id, type_, title, due_at, datetime.now().isoformat(timespec="minutes")),
        )
        return get(user_id, cur.lastrowid)


def get(user_id, item_id):
    with _conn() as c:
        r = c.execute("SELECT * FROM items WHERE id=? AND user_id=?", (item_id, user_id)).fetchone()
        return dict(r) if r else None


def list_range(user_id, date_from, date_to, type_=None, status=None):
    sql = "SELECT * FROM items WHERE user_id=? AND due_at IS NOT NULL AND substr(due_at,1,10) BETWEEN ? AND ?"
    args = [user_id, date_from, date_to]
    if type_:
        sql += " AND type=?"; args.append(type_)
    if status:
        sql += " AND status=?"; args.append(status)
    with _conn() as c:
        return [dict(r) for r in c.execute(sql + " ORDER BY due_at", args)]


def _stem(word):
    w = word.lower().strip()
    return w[:-2] if len(w) > 5 else w  # грубое «стемминг»: ловит падежи и окончания


def search(user_id, keywords, type_=None):
    """Нечёткий поиск: ключевые слова (LLM даёт синонимы и формы) ищутся по основам.
    Python lower() корректно работает с кириллицей, в отличие от SQLite LOWER()."""
    stems = [_stem(k) for k in keywords if k.strip()]
    with _conn() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM items WHERE user_id=?", (user_id,))]
    scored = []
    for r in rows:
        if type_ and r["type"] != type_:
            continue
        title = r["title"].lower()
        score = sum(1 for s in stems if s in title)
        if score:
            scored.append((score, r))
    scored.sort(key=lambda x: (-x[0], x[1]["due_at"] or ""), reverse=False)
    return [r for _, r in scored][:20]


def update(user_id, item_id, **fields):
    allowed = {k: v for k, v in fields.items() if k in ("title", "due_at", "status", "type") and v is not None}
    if not allowed:
        return get(user_id, item_id)
    sets = ", ".join(f"{k}=?" for k in allowed)
    with _conn() as c:
        c.execute(f"UPDATE items SET {sets} WHERE id=? AND user_id=?", (*allowed.values(), item_id, user_id))
    return get(user_id, item_id)


def delete(user_id, item_id):
    with _conn() as c:
        return c.execute("DELETE FROM items WHERE id=? AND user_id=?", (item_id, user_id)).rowcount > 0


def conflicts(user_id, due_at, exclude_id=None, minutes=60):
    """Встречи, которые начинаются ближе чем через `minutes` от due_at."""
    if not due_at or "T" not in due_at:
        return []
    t = datetime.fromisoformat(due_at)
    out = []
    for r in list_range(user_id, due_at[:10], due_at[:10], type_="meeting", status="open"):
        if r["id"] == exclude_id or "T" not in (r["due_at"] or ""):
            continue
        if abs((datetime.fromisoformat(r["due_at"]) - t).total_seconds()) < minutes * 60:
            out.append(r)
    return out
