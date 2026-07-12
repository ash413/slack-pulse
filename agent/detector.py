import json
import os
from anthropic import AsyncAnthropic

DETECTION_PROMPT = """\
You are Slack Pulse's detection engine. You'll be given a batch of recent \
Slack messages from one or more channels. Analyze them and flag ONLY things \
that clearly matter — don't flag routine chatter, lunch plans, or small talk.

Look for:
1. DUPLICATE_DISCUSSION — the same topic/issue being discussed in multiple \
   places or by multiple people without cross-awareness
2. MISSING_OWNER — a decision or question was raised but never explicitly \
   assigned to someone
3. READINESS_MISMATCH — a launch/deadline is proceeding while a dependency \
   is flagged as not ready, delayed, or unresolved
4. INCIDENT_PATTERN — a technical issue that resembles a known pattern worth \
   cross-referencing

Return ONLY valid JSON, no other text, in this exact format:
{
  "flags": [
    {
      "type": "DUPLICATE_DISCUSSION | MISSING_OWNER | READINESS_MISMATCH | INCIDENT_PATTERN",
      "summary": "one sentence describing what you noticed",
      "evidence": "brief quote or reference to the specific message(s)"
    }
  ]
}

If nothing meaningful stands out, return {"flags": []}.
"""

_client = AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))


async def detect_signals(messages: list[str]) -> list[dict]:
    """Analyze a batch of messages and return structured flags."""
    if not messages:
        return []

    joined = "\n".join(messages)

    response = await _client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1000,
        system=DETECTION_PROMPT,
        messages=[{"role": "user", "content": joined}],
    )

    text = "".join(
        block.text for block in response.content if hasattr(block, "text")
    )

    try:
        cleaned = text.strip().removeprefix("```json").removesuffix("```").strip()
        result = json.loads(cleaned)
        return result.get("flags", [])
    except (json.JSONDecodeError, AttributeError):
        return []