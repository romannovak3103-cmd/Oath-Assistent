import pytest

from app import services as s
from app.services import ServiceError

U = "u1"


def titles(res):
    return [i["title"] for i in res["items"]]


def test_task_without_date():
    r = s.create_item(U, "task", "Купить молоко")
    assert r["created"]["due_at"] is None and r["created"]["status"] == "open"


def test_meeting_requires_time():
    with pytest.raises(ServiceError):
        s.create_item(U, "meeting", "Созвон", "2026-11-02")
    with pytest.raises(ServiceError):
        s.create_item(U, "meeting", "Созвон")
    assert s.create_item(U, "meeting", "Созвон", "2026-11-02T10:00")["created"]["due_at"] == "2026-11-02T10:00"


@pytest.mark.parametrize("raw,expected", [
    ("2026-11-02 10:00", "2026-11-02T10:00"),
    ("2026-11-02T10:00:30", "2026-11-02T10:00"),
    ("2026-11-02T07:00:00Z", "2026-11-02T10:00"),   # UTC -> Москва
    ("2026-11-02", "2026-11-02"),
])
def test_due_normalized(raw, expected):
    assert s.normalize_due(raw) == expected


@pytest.mark.parametrize("bad", ["завтра", "2026-13-40", "10:00", "2026-11-02T25:00"])
def test_bad_date_rejected(bad):
    with pytest.raises(ServiceError):
        s.create_item(U, "task", "X", bad)


def test_bad_type_and_empty_title():
    with pytest.raises(ServiceError):
        s.create_item(U, "event", "X")
    with pytest.raises(ServiceError):
        s.create_item(U, "task", "   ")


def test_conflicts():
    s.create_item(U, "meeting", "Первая", "2026-11-02T10:00")
    near = s.create_item(U, "meeting", "Вторая", "2026-11-02T10:30")
    assert [c["title"] for c in near["conflicts"]] == ["Первая"]
    far = s.create_item(U, "meeting", "Третья", "2026-11-02T12:00")
    assert "conflicts" not in far
    # перенос на занятое время тоже даёт предупреждение, но не конфликтует сам с собой
    moved = s.update_item(U, far["created"]["id"], due_at="2026-11-02T10:15")
    assert {c["title"] for c in moved["conflicts"]} == {"Первая", "Вторая"}


def test_list_period_inclusive_and_sorted():
    s.create_item(U, "task", "Вне", "2026-11-04")
    s.create_item(U, "task", "Поздно", "2026-11-03T18:00")
    s.create_item(U, "task", "Рано", "2026-11-02T08:00")
    s.create_item(U, "task", "Без срока")
    assert titles(s.list_items(U, "2026-11-02", "2026-11-03")) == ["Рано", "Поздно"]
    # перепутанные границы исправляются
    assert s.list_items(U, "2026-11-03", "2026-11-02")["count"] == 2


def test_list_filters():
    s.create_item(U, "meeting", "Встреча", "2026-11-02T10:00")
    done = s.create_item(U, "task", "Задача", "2026-11-02")["created"]["id"]
    s.update_item(U, done, status="done")
    assert titles(s.list_items(U, "2026-11-02", "2026-11-02", type="meeting")) == ["Встреча"]
    assert titles(s.list_items(U, "2026-11-02", "2026-11-02", status="done")) == ["Задача"]


def test_search_cyrillic_case_and_forms():
    s.create_item(U, "task", "Подписать ДОГОВОР аренды")
    s.create_item(U, "task", "Купить молоко")
    assert titles(s.search_items(U, ["договора"])) == ["Подписать ДОГОВОР аренды"]
    assert titles(s.search_items(U, ["Аренду", "контракт"])) == ["Подписать ДОГОВОР аренды"]
    assert s.search_items(U, ["самолёт"])["count"] == 0


def test_search_ranks_by_matches_and_filters_type():
    s.create_item(U, "task", "Отчёт по практике")
    s.create_item(U, "meeting", "Обсудить отчёт и практику с куратором", "2026-11-05T12:00")
    assert titles(s.search_items(U, ["отчёт", "практик"]))[0].startswith("Обсудить")
    assert titles(s.search_items(U, ["отчёт"], type="task")) == ["Отчёт по практике"]


def test_search_like_wildcards_are_literal():
    s.create_item(U, "task", "Скидка 50% на курс")
    s.create_item(U, "task", "Другое")
    assert titles(s.search_items(U, ["%"])) == ["Скидка 50% на курс"]
    assert titles(s.search_items(U, ["_"])) == []
    with pytest.raises(ServiceError):
        s.search_items(U, [])


def test_update_and_delete():
    i = s.create_item(U, "task", "Старое", "2026-11-02")["created"]["id"]
    assert s.update_item(U, i, title="Новое", due_at="2026-11-09")["updated"]["title"] == "Новое"
    assert s.update_item(U, i, status="done")["updated"]["status"] == "done"
    with pytest.raises(ServiceError):
        s.update_item(U, i)                  # нечего менять
    with pytest.raises(ServiceError):
        s.update_item(U, 999, title="X")     # нет такой записи
    assert s.delete_item(U, i) == {"deleted": True}
    with pytest.raises(ServiceError):
        s.delete_item(U, i)


def test_cannot_strip_time_from_meeting():
    m = s.create_item(U, "meeting", "Встреча", "2026-11-02T10:00")["created"]["id"]
    with pytest.raises(ServiceError):
        s.update_item(U, m, due_at="2026-11-03")


def test_id_as_string_is_accepted():
    i = s.create_item(U, "task", "X")["created"]["id"]
    assert s.delete_item(U, str(i)) == {"deleted": True}
    with pytest.raises(ServiceError):
        s.delete_item(U, "abc")


def test_users_are_isolated():
    i = s.create_item("a", "task", "Секрет", "2026-11-02")["created"]["id"]
    assert s.list_items("b", "2026-11-01", "2026-11-30")["count"] == 0
    assert s.search_items("b", ["секрет"])["count"] == 0
    with pytest.raises(ServiceError):
        s.update_item("b", i, title="Взлом")
    with pytest.raises(ServiceError):
        s.delete_item("b", i)


def test_list_all_includes_undated_last():
    s.create_item(U, "task", "Без срока")
    s.create_item(U, "task", "Со сроком", "2026-11-02")
    assert [i["title"] for i in s.list_all(U)] == ["Со сроком", "Без срока"]
