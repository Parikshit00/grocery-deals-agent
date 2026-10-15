import asyncio
from types import SimpleNamespace

from app.clients import llm
from app.clients.llm import last_json_object, parse_items
from app.schemas.basket import Item


def test_parse_items_objects_and_units():
    content = (
        '[{"name": "Hackfleisch", "qty": 500, "unit": "g"}, {"name": "Milch", "qty": 1, '
        '"unit": "l"}, {"name": "Zwiebeln", "qty": 2, "unit": "stk"}, {"name": "Mehl", '
        '"qty": 0.5, "unit": "kg"}, {"name": "Oregano", "qty": null, "unit": null}]'
    )
    assert parse_items(content) == [
        Item(name="Hackfleisch", qty=500, unit="g"),
        Item(name="Milch", qty=1000, unit="ml"),
        Item(name="Zwiebeln", qty=2, unit="stk"),
        Item(name="Mehl", qty=500, unit="g"),
        Item(name="Oregano"),
    ]


def test_parse_items_odd_units_and_names():
    # unknown unit -> one pack; bare count -> packs; empty names and junk dropped
    content = '[{"name": "Olivenöl", "qty": 2, "unit": "EL"}, {"name": "Eier", "qty": 6}, ' \
        '{"name": ""}, 7, "Butter"]'
    assert parse_items(content) == [
        Item(name="Olivenöl"), Item(name="Eier", qty=6), Item(name="Butter")]


def test_parse_items_takes_last_array_after_prose():
    # Answers come last; prose may restate the prompt example or contain loose brackets.
    prose = (
        'Example was [{"name": "Hackfleisch", "qty": 500, "unit": "g"}], maybe [a, b].\n'
        '```json\n[{"name": "Kartoffeln", "qty": 1000, "unit": "g"}]\n```'
    )
    assert parse_items(prose) == [Item(name="Kartoffeln", qty=1000, unit="g")]


def test_parse_items_none():
    assert parse_items("no list here") == []


def test_last_object_after_reasoning():
    content = (
        'Checking {"note": "intermediate"} ... the element is the store button.\n'
        '{"action": "click", "el": 7}'
    )
    assert last_json_object(content) == {"action": "click", "el": 7}


def test_last_object_nested_braces():
    content = 'x {"accept": {"lidl": ["o1"]}, "reason": "s {with} braces"}'
    assert last_json_object(content)["accept"] == {"lidl": ["o1"]}


def test_last_object_none_and_invalid_skipped():
    assert last_json_object("no json here") is None
    assert last_json_object('{broken json} then {"action": "done"}') == {"action": "done"}


def test_recipe_caps_thinking_then_retries_without_it_on_length(monkeypatch):
    calls: list[dict] = []

    def chunk(content, finish, think=""):
        delta = SimpleNamespace(content=content, reasoning=think)
        return SimpleNamespace(choices=[SimpleNamespace(delta=delta, finish_reason=finish)])

    async def create(**kw):
        calls.append(kw["extra_body"])
        events = ([chunk("", "length", "hmm")] if len(calls) == 1
                  else [chunk('[{"name": "Hackfleisch", "qty": 500, "unit": "g"}]', "stop")])

        async def gen():
            for e in events:
                yield e
        return gen()

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(llm, "get_client", lambda: client)
    thoughts: list[str] = []
    items = asyncio.run(llm.decompose_recipe("Lasagne", 4, thoughts.append))
    assert calls == [{"thinking_token_budget": llm.RECIPE_THINKING_TOKENS}, llm.NO_THINK]
    assert thoughts == ["hmm"] and [i.name for i in items] == ["Hackfleisch"]
