"""Слой хранения (storage): SQLite. Только запросы к БД, без бизнес-правил и без LLM."""
import os
import sqlite3

DB_PATH = os.getenv("DB_PATH", "assistant.db")


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    # SQLite LOWER()/LIKE понимают регистр только у латиницы. Подключаем свою
    # функцию, чтобы обычный SQL LIKE работал и с кириллицей.
    c.create_function("py_lower", 1, lambda s: s.lower() if isinstance(s, str) else s, deterministic=True)
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


def create(user_id, type_, title, due_at, created_at):
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO items(user_id,type,title,due_at,created_at) VALUES(?,?,?,?,?)",
            (user_id, type_, title, due_at, created_at),
        )
        new_id = cur.lastrowid
    return get(user_id, new_id)


def get(user_id, item_id):
    with _conn() as c:
        r = c.execute("SELECT * FROM items WHERE id=? AND user_id=?", (item_id, user_id)).fetchone()
        return dict(r) if r else None


def list_all(user_id):
    """Все записи пользователя: сначала со сроком (по дате), затем без срока."""
    with _conn() as c:
        rows = c.execute(
            "SELECT * FROM items WHERE user_id=? ORDER BY due_at IS NULL, due_at, id", (user_id,))
        return [dict(r) for r in rows]


def list_range(user_id, date_from, date_to, type_=None, status=None):
    """Записи со сроком в периоде [date_from; date_to] включительно (даты 'YYYY-MM-DD')."""
    sql = "SELECT * FROM items WHERE user_id=? AND due_at IS NOT NULL AND substr(due_at,1,10) BETWEEN ? AND ?"
    args = [user_id, date_from, date_to]
    if type_:
        sql += " AND type=?"
        args.append(type_)
    if status:
        sql += " AND status=?"
        args.append(status)
    with _conn() as c:
        return [dict(r) for r in c.execute(sql + " ORDER BY due_at, id", args)]


def _escape_like(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def search(user_id, stems, type_=None, limit=20):
    """Поиск по названию через SQL LIKE. `stems` — уже нормализованные основы слов.
    Чем больше основ встретилось в названии, тем выше запись в выдаче."""
    stems = [s for s in stems if s]
    if not stems:
        return []
    score = " + ".join("(py_lower(title) LIKE ? ESCAPE '\\')" for _ in stems)
    patterns = [f"%{_escape_like(s)}%" for s in stems]
    sql = f"SELECT *, ({score}) AS score FROM items WHERE user_id=?"
    args = [*patterns, user_id]
    if type_:
        sql += " AND type=?"
        args.append(type_)
    sql += " AND score > 0 ORDER BY score DESC, due_at IS NULL, due_at DESC, id DESC LIMIT ?"
    args.append(limit)
    with _conn() as c:
        rows = []
        for r in c.execute(sql, args):
            d = dict(r)
            d.pop("score", None)
            rows.append(d)
        return rows


def update(user_id, item_id, **fields):
    allowed = {k: v for k, v in fields.items() if k in ("title", "due_at", "status", "type") and v is not None}
    if allowed:
        sets = ", ".join(f"{k}=?" for k in allowed)
        with _conn() as c:
            c.execute(f"UPDATE items SET {sets} WHERE id=? AND user_id=?", (*allowed.values(), item_id, user_id))
    return get(user_id, item_id)


def delete(user_id, item_id):
    with _conn() as c:
        return c.execute("DELETE FROM items WHERE id=? AND user_id=?", (item_id, user_id)).rowcount > 0
