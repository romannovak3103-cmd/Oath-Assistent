"""Описание инструментов для LLM и их вызов. Тонкий адаптер между llm и services."""
from . import services

TYPE_ENUM = {"type": "string", "enum": ["task", "meeting", "reminder"]}
DUE_DESC = "Локальное время ISO: 'YYYY-MM-DD' (только дата) или 'YYYY-MM-DDTHH:MM'"

# Формат нейтральный (JSON Schema); провайдер в llm.py приводит его к API Claude или OpenAI.
TOOLS = [
    {"name": "create_item",
     "description": "Создать задачу, напоминание или встречу. Для встречи ВСЕГДА нужно и дата, и время: если времени нет, не вызывай инструмент, а спроси пользователя.",
     "parameters": {"type": "object", "properties": {
         "type": TYPE_ENUM,
         "title": {"type": "string", "description": "Короткое название без даты и слов-команд"},
         "due_at": {"type": "string", "description": DUE_DESC}},
         "required": ["type", "title"]}},
    {"name": "list_items",
     "description": "Показать задачи и встречи за период (включительно). «Что у меня сегодня» = date_from=date_to=сегодня.",
     "parameters": {"type": "object", "properties": {
         "date_from": {"type": "string", "description": "YYYY-MM-DD"},
         "date_to": {"type": "string", "description": "YYYY-MM-DD"},
         "type": TYPE_ENUM, "status": {"type": "string", "enum": ["open", "done"]}},
         "required": ["date_from", "date_to"]}},
    {"name": "search_items",
     "description": "Найти старые записи (в любом периоде, в том числе прошлом) по смыслу. Передай 2-6 ключевых слов: основные слова из запроса плюс синонимы и однокоренные формы.",
     "parameters": {"type": "object", "properties": {
         "keywords": {"type": "array", "items": {"type": "string"}}, "type": TYPE_ENUM},
         "required": ["keywords"]}},
    {"name": "update_item",
     "description": "Изменить запись: перенести (due_at), переименовать (title) или отметить выполненной (status='done'). id бери из предыдущих результатов.",
     "parameters": {"type": "object", "properties": {
         "id": {"type": "integer"}, "title": {"type": "string"},
         "due_at": {"type": "string", "description": DUE_DESC},
         "status": {"type": "string", "enum": ["open", "done"]}},
         "required": ["id"]}},
    {"name": "delete_item",
     "description": "Удалить запись по id.",
     "parameters": {"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}},
]

_HANDLERS = {
    "create_item": services.create_item,
    "list_items": services.list_items,
    "search_items": services.search_items,
    "update_item": services.update_item,
    "delete_item": services.delete_item,
}
_ALLOWED = {t["name"]: set(t["parameters"]["properties"]) for t in TOOLS}


def run_tool(user_id, name, args):
    """Выполняет инструмент. Любая ошибка возвращается как {"error": ...}, чтобы модель
    могла исправиться сама (например, переспросить или поправить формат даты)."""
    handler = _HANDLERS.get(name)
    if not handler:
        return {"error": f"Неизвестный инструмент {name}"}
    args = {k: v for k, v in (args if isinstance(args, dict) else {}).items() if k in _ALLOWED[name]}
    try:
        return handler(user_id, **args)
    except services.ServiceError as e:
        return {"error": str(e)}
    except TypeError:
        return {"error": "Не хватает обязательных аргументов инструмента"}
