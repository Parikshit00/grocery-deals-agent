from app.clients.llm import _parse_list, last_json_object


def test_parse_json_array():
    assert _parse_list('["Butter", "Milch"]') == ["Butter", "Milch"]


def test_parse_json_with_preamble():
    assert _parse_list('Sure: ["Eier", "Mehl"] done') == ["Eier", "Mehl"]


def test_parse_fallback_comma_separated():
    assert _parse_list("Butter, Milch, Eier") == ["Butter", "Milch", "Eier"]


def test_parse_reasoning_prose_without_think_tag():
    # The reasoning model emits no </think> and restates the prompt example; the parser
    # must take the LAST valid array, not the first-[..last-] span.
    prose = (
        'The example was ["Rindersteak", "Kartoffeln", "Butter"]. For this dish I need '
        'potatoes and sausages, maybe [a, b] loosely.\n["Kartoffeln", "Würstchen"]'
    )
    assert _parse_list(prose) == ["Kartoffeln", "Würstchen"]


def test_parse_with_think_tag():
    assert _parse_list('reasoning here </think> ["Milch", "Eier"]') == ["Milch", "Eier"]


def test_parse_no_array_splits_last_line_only():
    assert _parse_list("long reasoning first line\nBrot, Milch, Eier") == [
        "Brot",
        "Milch",
        "Eier",
    ]


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
