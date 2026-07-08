from app.clients.llm import _parse_list


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
