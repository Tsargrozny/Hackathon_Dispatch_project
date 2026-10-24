"""Voice escalation: Claude writes/understands the words, Twilio carries the call.

Both integrations are optional. Without keys the system falls back to a
template script, keyword matching, and the browser "coordinator phone" page.
"""
from __future__ import annotations

import json
import os
import re
from xml.sax.saxutils import escape

import httpx

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"


def claude_enabled() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


def twilio_enabled() -> bool:
    return all(os.getenv(k) for k in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER",
                                      "COORDINATOR_PHONE", "PUBLIC_URL"))


async def _claude(prompt: str, max_tokens: int = 220) -> str | None:
    if not claude_enabled():
        return None
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(
                ANTHROPIC_URL,
                headers={"x-api-key": os.environ["ANTHROPIC_API_KEY"], "anthropic-version": "2023-06-01",
                         "content-type": "application/json"},
                json={"model": os.getenv("ANTHROPIC_MODEL", "claude-haiku-5-5"), "max_tokens": max_tokens,
                      "messages": [{"role": "user", "content": prompt}]},
            )
            r.raise_for_status()
            return "".join(b.get("text", "") for b in r.json().get("content", [])).strip() or None
    except Exception as e:  # never let the LLM break the demo
        print(f"[claude] falling back to template: {e}")
        return None


async def make_script(snapshot: dict, options: list[dict], reason: str, fallback: str) -> str:
    opts = [{k: o[k] for k in ("key", "label", "eta_min", "margin_min", "summary")} for o in options]
    prompt = f"""You are the voice of an organ-transport monitoring system calling an organ procurement
coordinator who may have just been woken up. Write what the phone call should SAY, under 70 words.
Plain spoken English, no markdown, no lists, no emojis. Times as words like "two hours ten minutes".
Lead with the organ, its destination and the problem. Give each option's consequence in one short clause
(remaining safe time "to spare" = margin). End with exactly: "Say {', '.join(o['key'] for o in opts[:-1])}, or {opts[-1]['key']}."

Problem: {reason}
Shipment: {snapshot['organ_label']} {snapshot['shipment_id']} to {snapshot['destination']['name']}.
Current leg: {snapshot['leg']['name']}. Temperature {snapshot['temp_c']} C.
Options (minutes): {json.dumps(opts)}"""
    text = await _claude(prompt)
    return text or fallback


KEYWORDS = {
    "charter": ["charter", "jet", "plane", "fly", "private"],
    "reroute": ["reroute", "re-route", "route", "redirect", "davis", "sacramento", "switch", "other center"],
    "hold": ["hold", "keep", "continue", "stay", "wait", "no change"],
}


async def interpret(utterance: str, options: list[dict]) -> str | None:
    valid = [o["key"] for o in options]
    u = (utterance or "").lower()
    hits = [k for k in valid if any(re.search(rf"\b{re.escape(w)}\b", u) for w in KEYWORDS.get(k, [k]))]
    if len(hits) == 1:
        return hits[0]
    text = await _claude(
        f"A transplant coordinator was offered these options: {json.dumps([{k: o[k] for k in ('key', 'label')} for o in options])}. "
        f'They replied: "{utterance}". Reply with only the key they chose, or "none" if unclear.', max_tokens=10)
    if text:
        t = text.strip().strip('".').lower()
        if t in valid:
            return t
    return None


async def place_call(escalation_id: str) -> tuple[bool, str]:
    sid, token = os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
    url = os.environ["PUBLIC_URL"].rstrip("/") + f"/voice/twiml?eid={escalation_id}"
    try:
        async with httpx.AsyncClient(timeout=15, auth=(sid, token)) as client:
            r = await client.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Calls.json",
                                  data={"To": os.environ["COORDINATOR_PHONE"], "From": os.environ["TWILIO_FROM_NUMBER"],
                                        "Url": url, "Method": "POST"})
            if r.status_code >= 300:
                return False, f"Twilio {r.status_code}: {r.text[:200]}"
            return True, r.json().get("sid", "")
    except Exception as e:
        return False, str(e)


def twiml_gather(script: str, action: str, hints: list[str]) -> str:
    return (f'<?xml version="1.0" encoding="UTF-8"?><Response>'
            f'<Gather input="speech" action="{escape(action)}" method="POST" speechTimeout="auto" '
            f'hints="{escape(", ".join(hints))}" language="en-US">'
            f'<Say voice="Polly.Joanna">{escape(script)}</Say></Gather>'
            f'<Say voice="Polly.Joanna">No answer heard. The alert stays open on the coordinator dashboard. Goodbye.</Say></Response>')


def twiml_say(text: str) -> str:
    return (f'<?xml version="1.0" encoding="UTF-8"?><Response>'
            f'<Say voice="Polly.Joanna">{escape(text)}</Say></Response>')
