"""Диалоговый слой: системный промпт, история сессий и цикл «модель → инструменты → модель»."""
import json
import os
from collections import OrderedDict
from datetime import timedelta

from . import services
from .tools import TOOLS, run_tool

WEEKDAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
MAX_STEPS = 6          # защита от бесконечного цикла инструментов
MAX_SESSIONS = 200     # сколько сессий держим в памяти процесса
MAX_STORED = 60        # сообщений на сессию
FALLBACK = "Не получилось обработать запрос, попробуйте сформулировать иначе."


def system_prompt():
    now = services.now()
    days = "\n".join(
        f"{d:%Y-%m-%d} — {WEEKDAYS[d.weekday()]}"
        + (" (сегодня)" if i == 0 else " (завтра)" if i == 1 else "")
        for i, d in ((i, now + timedelta(days=i)) for i in range(15)))
    return f"""Ты голосовой ассистент для личных задач, напоминаний и встреч. Отвечай по-русски, коротко и естественно: ответ будет озвучен, поэтому без markdown, списков со звёздочками и ID.
Сейчас: {now:%Y-%m-%d %H:%M}, {WEEKDAYS[now.weekday()]}. Часовой пояс {services.TZ.key}.
Календарь на ближайшие дни (бери даты отсюда, не считай сам):
{days}
Правила:
- Относительные даты («завтра», «в пятницу», «через два часа», «на следующей неделе») считай от текущего момента. «В пятницу» = ближайшая будущая пятница. «На эту неделю» = с понедельника по воскресенье текущей недели.
- Любой запрос про планы, задачи, встречи или поиск выполняй вызовом инструмента, а не ответом по памяти.
- Не хватает обязательных данных (например, время встречи) — задай ОДИН короткий уточняющий вопрос и ничего не создавай.
- Задача без срока допустима. «До конца недели» = дата воскресенья. Утро=09:00, день=14:00, вечер=19:00, если время не названо точно.
- Тип: встреча (люди, созвон, митинг) = meeting; «напомни» = reminder; остальное = task.
- Запросы «Что у меня сегодня?», «Какие планы?», «Покажи встречи» — один и тот же list_items.
- «Перенеси её», «отметь выполненной», «удали это» — речь про запись из недавнего диалога; бери её id из результатов инструментов. Если подходит несколько записей, уточни.
- Для поиска старых записей используй search_items, не list_items.
- Если результат содержит conflicts, запись создана, но предупреди о пересечении со встречей.
- Если инструмент вернул error, исправь вызов или объясни пользователю, чего не хватает.
- Только факты из результатов инструментов, ничего не выдумывай. Даты в ответе называй по-человечески («завтра в 10:00», «в пятницу, 31 октября»)."""


class Assistant:
    def __init__(self, llm, max_window=None):
        self.llm = llm
        self.max_window = max_window or int(os.getenv("MAX_HISTORY", "30"))
        self.sessions: "OrderedDict[str, list]" = OrderedDict()

    def _history(self, key):
        if key in self.sessions:
            self.sessions.move_to_end(key)
        else:
            self.sessions[key] = []
            while len(self.sessions) > MAX_SESSIONS:
                self.sessions.popitem(last=False)
        return self.sessions[key]

    def _window(self, msgs):
        """Последние сообщения для модели; всегда начинаются с реплики пользователя,
        чтобы не разорвать пару «вызов инструмента — результат»."""
        w = msgs[-self.max_window:]
        while w and w[0]["role"] != "user":
            w = w[1:]
        return w

    def chat(self, user_id, session_id, text):
        msgs = self._history(f"{user_id}:{session_id}")
        start = len(msgs)
        msgs.append({"role": "user", "text": text})
        try:
            for _ in range(MAX_STEPS):
                turn = self.llm.complete(system_prompt(), self._window(msgs), TOOLS)
                reply = turn.text.strip()
                if not turn.tool_calls:
                    reply = reply or "Готово."  # пустой ответ нельзя класть в историю
                msgs.append({"role": "assistant", "text": reply, "tool_calls": turn.tool_calls})
                if not turn.tool_calls:
                    return reply
                results = [{"id": c.id, "name": c.name,
                            "content": json.dumps(run_tool(user_id, c.name, c.args), ensure_ascii=False)}
                           for c in turn.tool_calls]
                msgs.append({"role": "tool", "results": results})
            return FALLBACK
        except Exception:
            del msgs[start:]  # не оставляем в истории оборванный обмен
            raise
        finally:
            if len(msgs) > MAX_STORED:
                del msgs[:len(msgs) - MAX_STORED]
