from app.core.llm import _parse_list


def test_parse_json_array():
    assert _parse_list('["Butter", "Milch"]') == ["Butter", "Milch"]


def test_parse_json_with_preamble():
    assert _parse_list('Sure: ["Eier", "Mehl"] done') == ["Eier", "Mehl"]


def test_parse_fallback_comma_separated():
    assert _parse_list("Butter, Milch, Eier") == ["Butter", "Milch", "Eier"]
