"""The scheduled-run verdict contract.

The runner (never the playbook author) appends VERDICT_INSTRUCTION to every
scheduled turn; parse_verdict() then reads the LAST fenced JSON block of the
final answer. Alert-mode sending is gated on {"notify": true}.
"""
from __future__ import annotations

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


def parse_verdict(answer) -> Optional[dict]:
    """Return {'notify': bool, 'subject': str|None, 'summary': str|None} from the
    last fenced JSON block of `answer`, or None if absent/unparseable."""
    if not answer or not isinstance(answer, str):
        return None
    blocks = _FENCED_JSON_RE.findall(answer)
    if not blocks:
        return None
    try:
        payload = json.loads(blocks[-1])
    except (ValueError, TypeError):
        return None
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
