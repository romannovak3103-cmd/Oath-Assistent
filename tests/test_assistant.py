import json

import pytest

from app import services
from app.assistant import Assistant, system_prompt
from app.llm import (LLMError, ToolCall, Turn, to_anthropic_messages, to_openai_messages)

U = "u1"


class FakeLLM:
    """Отдаёт заранее заготовленные ответы и запоминает, что ему присылали."""
    def __init__(self, *turns):
        self.turns, self.calls = list(turns), []

    def complete(self, system, messages, tools):
        self.calls.append((system, list(messages)))
        t = self.turns.pop(0)
        if isinstance(t, Exception):
            raise t
        return t


def call(name, **args):
    return Turn("", [ToolCall(f"id_{name}", name, args)])


def test_tool_loop_creates_item_and_replies():
    llm = FakeLLM(call("create_item", type="task", title="Позвонить Ивану", due_at="2026-11-01T10:00"),
                  Turn("Записал на завтра в 10:00."))
    assert Assistant(llm).chat(U, "s", "Напомни завтра в 10 позвонить Ивану") == "Записал на завтра в 10:00."
    assert services.list_items(U, "2026-11-01", "2026-11-01")["count"] == 1
    # модель получила результат инструмента как JSON
    tool_msg = llm.calls[1][1][-1]
    assert tool_msg["role"] == "tool"
    assert json.loads(tool_msg["results"][0]["content"])["created"]["title"] == "Позвонить Ивану"


def test_tool_error_goes_back_to_model():
    llm = FakeLLM(call("create_item", type="meeting", title="Созвон", due_at="2026-11-02"),
                  Turn("Во сколько встреча?"))
    assert Assistant(llm).chat(U, "s", "Созвон во вторник") == "Во сколько встреча?"
    result = json.loads(llm.calls[1][1][-1]["results"][0]["content"])
    assert "error" in result and services.list_all(U) == []


def test_bad_tool_args_do_not_crash():
    llm = FakeLLM(Turn("", [ToolCall("1", "create_item", {"title": "Без типа"}),
                            ToolCall("2", "nope", {}),
                            ToolCall("3", "delete_item", {"id": "x", "лишний": 1})]),
                  Turn("Не вышло."))
    assert Assistant(llm).chat(U, "s", "тест") == "Не вышло."
    results = [json.loads(r["content"]) for r in llm.calls[1][1][-1]["results"]]
    assert all("error" in r for r in results)


def test_context_between_messages():
    llm = FakeLLM(Turn("Привет!"), Turn("И снова привет."))
    a = Assistant(llm)
    a.chat(U, "s", "Привет")
    a.chat(U, "s", "Ещё раз")
    assert [m["role"] for m in llm.calls[1][1]] == ["user", "assistant", "user"]


def test_sessions_are_separate():
    llm = FakeLLM(Turn("a"), Turn("b"))
    a = Assistant(llm)
    a.chat("u1", "s", "x")
    a.chat("u2", "s", "y")
    assert len(llm.calls[1][1]) == 1


def test_failed_call_rolls_back_history():
    llm = FakeLLM(call("create_item", type="task", title="X"), LLMError("упала"), Turn("ок"))
    a = Assistant(llm)
    with pytest.raises(LLMError):
        a.chat(U, "s", "первый")
    assert a.sessions[f"{U}:s"] == []
    a.chat(U, "s", "второй")
    assert [m["role"] for m in llm.calls[2][1]] == ["user"]


def test_window_starts_with_user_message():
    a = Assistant(FakeLLM(), max_window=3)
    msgs = [{"role": "user", "text": "1"}, {"role": "assistant", "text": "", "tool_calls": [1]},
            {"role": "tool", "results": []}, {"role": "assistant", "text": "x", "tool_calls": []},
            {"role": "user", "text": "2"}]
    assert [m["role"] for m in a._window(msgs)] == ["user"]


def test_empty_reply_is_stored_non_empty():
    a = Assistant(FakeLLM(Turn("  ")))
    assert a.chat(U, "s", "x") == "Готово."
    assert a.sessions[f"{U}:s"][-1]["text"] == "Готово."


def test_infinite_tool_loop_is_stopped():
    llm = FakeLLM(*[call("list_items", date_from="2026-11-01", date_to="2026-11-01") for _ in range(10)])
    assert "Не получилось" in Assistant(llm).chat(U, "s", "x")
    assert len(llm.calls) == 6


def test_system_prompt_has_calendar_and_timezone():
    p = system_prompt()
    assert "(сегодня)" in p and "(завтра)" in p and "Europe/Moscow" in p


def test_provider_message_conversion():
    msgs = [{"role": "user", "text": "Привет"},
            {"role": "assistant", "text": "", "tool_calls": [ToolCall("c1", "list_items", {"date_from": "2026-11-01"})]},
            {"role": "tool", "results": [{"id": "c1", "name": "list_items", "content": "{}"}]},
            {"role": "assistant", "text": "Пусто", "tool_calls": []}]
    a = to_anthropic_messages(msgs)
    assert a[1]["content"][0] == {"type": "tool_use", "id": "c1", "name": "list_items", "input": {"date_from": "2026-11-01"}}
    assert a[2]["content"][0]["tool_use_id"] == "c1"
    o = to_openai_messages("sys", msgs)
    assert o[0] == {"role": "system", "content": "sys"}
    assert o[2]["tool_calls"][0]["function"]["name"] == "list_items" and o[2]["content"] is None
    assert o[3] == {"role": "tool", "tool_call_id": "c1", "content": "{}"}
