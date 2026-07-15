"""The scheduled-run verdict contract.

The runner (never the playbook author) appends VERDICT_INSTRUCTION to every
scheduled turn; parse_verdict() then reads the LAST fenced JSON block of the
final answer. Alert-mode sending is gated on {"notify": true}.

Production callers may hold the SERVER-SIDE HTML-rendered answer (markdown
rendered with syntax-highlighted code boxes) instead of raw markdown — the
```json fence never survives rendering, though the JSON's characters do,
chopped into HTML tags with entities. parse_verdict_from_output() is the
entry point for callers that may be holding either form.
"""
from __future__ import annotations

import html
import json
import re
from typing import Optional

VERDICT_INSTRUCTION = (
    "At the very end of your answer, output your verdict as a fenced JSON block "
    "exactly in this form (it controls automated email delivery):\n"
    "```json\n"
    '{"notify": true or false, "subject": "<short email subject>", '
    '"summary": "<1-2 sentence summary>"}\n'
    "```\n"
    "Set \"notify\" to true only if the condition described above is met "
    "(for always-send digests, set it to true)."
)

_FENCED_JSON_RE = re.compile(r"```json\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)
# Fence-less fallback: the verdict contract is always a FLAT object (no nested
# braces), so a non-greedy no-brace character class safely delimits one object.
_FLAT_OBJECT_RE = re.compile(r'\{[^{}]*"notify"[^{}]*\}')


def _coerce_bool(value) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in ("true", "yes"):
            return True
        if lowered in ("false", "no"):
            return False
    return None


def _payload_to_verdict(payload) -> Optional[dict]:
    """Validate/normalize an already-json.loads'd payload into the verdict
    dict, or None if it doesn't satisfy the contract."""
    if not isinstance(payload, dict) or "notify" not in payload:
        return None
    notify = _coerce_bool(payload.get("notify"))
    if notify is None:
        return None
    subject = payload.get("subject")
    summary = payload.get("summary")
    return {
        "notify": notify,
        "subject": subject if isinstance(subject, str) and subject else None,
        "summary": summary if isinstance(summary, str) and summary else None,
    }


def _html_to_text(value: str) -> str:
    """Strip HTML tags and unescape entities — reconstructs the flat text
    content of a server-side HTML-rendered markdown answer."""
    return html.unescape(re.sub(r"<[^>]+>", "", value))


def parse_verdict(answer) -> Optional[dict]:
    """Return {'notify': bool, 'subject': str|None, 'summary': str|None} from
    `answer`, or None if absent/unparseable.

    First attempt: the last fenced ```json block (raw-markdown answers).
    Fallback: if that path finds nothing usable, scan for fence-less flat
    JSON objects (no nested braces, per the verdict contract) and use the
    last one — tried last-first — that validates.
    """
    if not answer or not isinstance(answer, str):
        return None

    blocks = _FENCED_JSON_RE.findall(answer)
    if blocks:
        try:
            payload = json.loads(blocks[-1])
        except (ValueError, TypeError):
            payload = None
        if payload is not None:
            verdict = _payload_to_verdict(payload)
            if verdict is not None:
                return verdict

    for candidate in reversed(_FLAT_OBJECT_RE.findall(answer)):
        try:
            payload = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        verdict = _payload_to_verdict(payload)
        if verdict is not None:
            return verdict
    return None


def parse_verdict_from_output(output) -> Optional[dict]:
    """Entry point for callers holding possibly-HTML-rendered output (the
    server-side rendered answer never contains a literal ```json fence)."""
    return parse_verdict(output) or parse_verdict(_html_to_text(output))
