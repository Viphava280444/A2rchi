"""parse_verdict: last fenced JSON block wins; anything malformed → None."""
from src.interfaces.playbook_scheduler.verdict import VERDICT_INSTRUCTION, parse_verdict


def _wrap(payload):
    return f"Some analysis text.\n\n```json\n{payload}\n```\n"


def test_parses_notify_true():
    v = parse_verdict(_wrap('{"notify": true, "subject": "80% failures", "summary": "bad"}'))
    assert v == {"notify": True, "subject": "80% failures", "summary": "bad"}


def test_parses_notify_false():
    v = parse_verdict(_wrap('{"notify": false}'))
    assert v == {"notify": False, "subject": None, "summary": None}


def test_last_fenced_block_wins():
    text = _wrap('{"notify": false}') + "\nmore text\n" + _wrap('{"notify": true}')
    assert parse_verdict(text)["notify"] is True


def test_missing_block_returns_none():
    assert parse_verdict("no json here at all") is None


def test_malformed_json_returns_none():
    assert parse_verdict(_wrap('{"notify": tru')) is None


def test_non_dict_returns_none():
    assert parse_verdict(_wrap('[1, 2, 3]')) is None


def test_missing_notify_key_returns_none():
    assert parse_verdict(_wrap('{"subject": "x"}')) is None


def test_string_notify_is_coerced():
    assert parse_verdict(_wrap('{"notify": "true"}'))["notify"] is True
    assert parse_verdict(_wrap('{"notify": "false"}'))["notify"] is False


def test_empty_and_none_inputs():
    assert parse_verdict("") is None
    assert parse_verdict(None) is None


def test_uncoercible_notify_returns_none():
    for v in ('"banana"', '5', 'null', '[1]'):
        assert parse_verdict(_wrap('{"notify": ' + v + '}')) is None


def test_wrong_typed_or_empty_subject_summary_become_none():
    v = parse_verdict(_wrap('{"notify": true, "subject": 123, "summary": ""}'))
    assert v == {"notify": True, "subject": None, "summary": None}


def test_instruction_mentions_the_contract():
    assert "```json" in VERDICT_INSTRUCTION and '"notify"' in VERDICT_INSTRUCTION
    assert "only if the condition" in VERDICT_INSTRUCTION
    assert "always-send digests" in VERDICT_INSTRUCTION
