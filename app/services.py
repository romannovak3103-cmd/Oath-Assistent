"""Слой бизнес-логики (services): правила, валидация, конфликты встреч.
Ничего не знает ни про LLM, ни про HTTP. Ошибки пользователя — ServiceError."""
import os
from datetime import datetime
from zoneinfo import ZoneInfo

from . import storage

TZ = ZoneInfo(os.getenv("TIMEZONE", "Europe/Moscow"))
TYPES = ("task", "meeting", "reminder")
STATUSES = ("open", "done")
CONFLICT_MINUTES = 60


class ServiceError(Exception):
    """Понятная ошибка, текст которой можно показать модели и пользователю."""


def now():
    return datetime.now(TZ)


def _public(item):
    return {k: item[k] for k in ("id", "type", "title", "due_at", "status")}


def _check_type(value, field="type"):
    if value is not None and value not in TYPES:
        raise ServiceError(f"{field} должен быть одним из: {', '.join(TYPES)}")
    return value


def _check_status(value):
    if value is not None and value not in STATUSES:
        raise ServiceError(f"status должен быть одним из: {', '.join(STATUSES)}")
    return value


def _item_id(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        raise ServiceError("id должен быть числом")


def normalize_due(value):
    """'YYYY-MM-DD' или 'YYYY-MM-DDTHH:MM' (в т.ч. с пробелом, секундами, поясом) -> каноничная строка."""
    if value in (None, ""):
        return None
    s = str(value).strip().replace(" ", "T")
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    try:
        if len(s) == 10:
            datetime.strptime(s, "%Y-%m-%d")
            return s
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise ServiceError(
            f"Неверный формат даты «{value}». Нужен ISO: YYYY-MM-DD или YYYY-MM-DDTHH:MM")
    if dt.tzinfo:
        dt = dt.astimezone(TZ).replace(tzinfo=None)
    return dt.strftime("%Y-%m-%dT%H:%M")


def _date_only(value, field):
    s = normalize_due(value)
    if not s:
        raise ServiceError(f"Нужна дата {field} в формате YYYY-MM-DD")
    return s[:10]


def _has_time(due_at):
    return bool(due_at) and "T" in due_at


def find_conflicts(user_id, due_at, exclude_id=None, minutes=CONFLICT_MINUTES):
    """Открытые встречи, которые начинаются ближе чем через `minutes` от due_at."""
    if not _has_time(due_at):
        return []
    t = datetime.fromisoformat(due_at)
    out = []
    for r in storage.list_range(user_id, due_at[:10], due_at[:10], type_="meeting", status="open"):
        if r["id"] == exclude_id or not _has_time(r["due_at"]):
            continue
        if abs((datetime.fromisoformat(r["due_at"]) - t).total_seconds()) < minutes * 60:
            out.append(r)
    return out


def _with_conflicts(user_id, key, item):
    res = {key: _public(item)}
    if item["type"] == "meeting":
        c = find_conflicts(user_id, item["due_at"], exclude_id=item["id"])
        if c:
            res["conflicts"] = [_public(x) for x in c]
    return res


def create_item(user_id, type, title, due_at=None):
    _check_type(type)
    title = (title or "").strip()
    if not title:
        raise ServiceError("Нужно название")
    due = normalize_due(due_at)
    if type == "meeting" and not _has_time(due):
        raise ServiceError("Для встречи нужны дата и время. Спроси пользователя.")
    item = storage.create(user_id, type, title, due, now().strftime("%Y-%m-%dT%H:%M"))
    return _with_conflicts(user_id, "created", item)


def list_items(user_id, date_from, date_to, type=None, status=None):
    d1, d2 = _date_only(date_from, "date_from"), _date_only(date_to, "date_to")
    if d1 > d2:
        d1, d2 = d2, d1
    rows = storage.list_range(user_id, d1, d2, _check_type(type), _check_status(status))
    return {"items": [_public(r) for r in rows], "count": len(rows)}


def list_all(user_id):
    return [_public(r) | {"created_at": r["created_at"]} for r in storage.list_all(user_id)]


def _stem(word):
    w = word.lower().strip()
    return w[:-2] if len(w) > 5 else w  # грубый «стемминг»: ловит падежи и окончания


def search_items(user_id, keywords, type=None):
    if isinstance(keywords, str):
        keywords = keywords.split()
    stems = list(dict.fromkeys(_stem(k) for k in (keywords or []) if str(k).strip()))
    if not stems:
        raise ServiceError("Нужны ключевые слова для поиска")
    rows = storage.search(user_id, stems, _check_type(type))
    return {"items": [_public(r) for r in rows], "count": len(rows)}


def update_item(user_id, id, title=None, due_at=None, status=None):
    item_id = _item_id(id)
    item = storage.get(user_id, item_id)
    if not item:
        raise ServiceError("Запись не найдена")
    fields = {}
    if title is not None:
        if not title.strip():
            raise ServiceError("Название не может быть пустым")
        fields["title"] = title.strip()
    if due_at not in (None, ""):
        due = normalize_due(due_at)
        if item["type"] == "meeting" and not _has_time(due):
            raise ServiceError("Для встречи нужны дата и время. Спроси пользователя.")
        fields["due_at"] = due
    if status is not None:
        fields["status"] = _check_status(status)
    if not fields:
        raise ServiceError("Нечего менять: укажи title, due_at или status")
    return _with_conflicts(user_id, "updated", storage.update(user_id, item_id, **fields))


def delete_item(user_id, id):
    if not storage.delete(user_id, _item_id(id)):
        raise ServiceError("Запись не найдена")
    return {"deleted": True}
