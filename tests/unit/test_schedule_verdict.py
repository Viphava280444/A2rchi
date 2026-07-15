"""parse_verdict: last fenced JSON block wins; anything malformed → None."""
from src.interfaces.playbook_scheduler.verdict import (
    VERDICT_INSTRUCTION,
    parse_verdict,
    parse_verdict_from_output,
)


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


# ---------------------------------------------------------------- live-smoke finding
# ChatWrapper.__call__ can return the SERVER-SIDE HTML-rendered answer (markdown
# rendered with syntax-highlighted code boxes), not raw markdown — the ```json
# fence never survives rendering, but the JSON's characters all survive as HTML
# text content, chopped into <span>s with &quot; entities.

def _html_wrap(sentence, notify_json_text):
    """Replicate the live sample's rendering: a <p> sentence, then a
    Pygments-style highlighted block with the JSON chopped into spans and
    HTML entities, exactly like the production sample tail."""
    return (
        f"<p>{sentence}</p>\n"
        '<div class="highlight"><pre><span></span><span class="o">{</span>'
        + notify_json_text +
        '<span class="o">}</span>\n</pre></div>'
    )


HTML_VERDICT_FALSE = _html_wrap(
    "Everything checked out fine this run.",
    '<span class="s2">&quot;notify&quot;</span>:<span class="w"> </span>'
    '<span class="kc">false</span>,<span class="w"> </span>'
    '<span class="s2">&quot;subject&quot;</span>:<span class="w"> </span>'
    '<span class="s2">&quot;Scheduler smoke check: all quiet&quot;</span>,'
    '<span class="w"> </span><span class="s2">&quot;summary&quot;</span>:'
    '<span class="w"> </span><span class="s2">&quot;No issues detected; '
    'monitoring indicates normal operation.&quot;</span>',
)


def test_parses_html_rendered_verdict():
    v = parse_verdict_from_output(HTML_VERDICT_FALSE)
    assert v["notify"] is False
    assert v["subject"] == "Scheduler smoke check: all quiet"
    assert v["summary"] == (
        "No issues detected; monitoring indicates normal operation."
    )


def test_flat_object_fallback_last_wins():
    text = (
        'Status check one: {"notify": false, "subject": "first"} looks fine.\n'
        'Status check two: {"notify": true, "subject": "second"} needs attention.'
    )
    v = parse_verdict(text)
    assert v["notify"] is True
    assert v["subject"] == "second"


def test_fenced_path_still_first():
    text = (
        _wrap('{"notify": true, "subject": "fenced wins"}')
        + '\nmore text {"notify": false, "subject": "flat loses"}\n'
    )
    v = parse_verdict(text)
    assert v["notify"] is True
    assert v["subject"] == "fenced wins"
