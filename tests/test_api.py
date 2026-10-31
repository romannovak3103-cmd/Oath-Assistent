from fastapi.testclient import TestClient

from app import main
from app.assistant import Assistant
from app.llm import Turn


class Echo:
    def complete(self, system, messages, tools):
        return Turn("Привет, " + messages[-1]["text"])


client = TestClient(main.app)


def setup_function(_):
    main.app.dependency_overrides[main.get_assistant] = lambda: Assistant(Echo())


def teardown_function(_):
    main.app.dependency_overrides.clear()


def test_chat_ok():
    r = client.post("/api/chat", json={"message": " мир "}, headers={"X-User-Id": "u"})
    assert r.status_code == 200 and r.json() == {"reply": "Привет, мир"}


def test_chat_empty_message():
    assert client.post("/api/chat", json={"message": "  "}).status_code == 400


def test_items_endpoint_returns_undated():
    from app import services
    services.create_item("u", "task", "Без срока")
    services.create_item("u", "task", "Со сроком", "2026-11-02")
    r = client.get("/api/items", headers={"X-User-Id": "u"})
    assert [i["title"] for i in r.json()] == ["Со сроком", "Без срока"]


def test_missing_key_gives_readable_error(monkeypatch):
    main.app.dependency_overrides.clear()
    monkeypatch.setattr(main, "_assistant", None)
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = client.post("/api/chat", json={"message": "x"})
    assert r.status_code == 500 and r.json()["detail"]


def test_static_index_served():
    assert "Ассистент" in client.get("/").text
