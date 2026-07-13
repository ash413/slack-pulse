import json
import os

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)
from claude_agent_sdk.types import McpHttpServerConfig

from agent.agent import GITHUB_MCP_URL, NOTION_MCP_URL, SLACK_MCP_URL

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

## TOOLS
You have access to GitHub and Notion search tools. Use them when relevant:
- For INCIDENT_PATTERN flags, search GitHub for a related open issue in the \
  default repo and include a link/reference if found.
- For any flag referencing a decision, architecture choice, or past incident, \
  search Notion for a related doc and include a link/reference if found.
Only use tools when they'd meaningfully strengthen a flag — don't search for \
every message, and don't let tool use block returning results if nothing \
relevant turns up quickly.

Return ONLY valid JSON as your FINAL message, no other text surrounding it, \
in this exact format:
{
  "flags": [
    {
      "type": "DUPLICATE_DISCUSSION | MISSING_OWNER | READINESS_MISMATCH | INCIDENT_PATTERN",
      "summary": "one sentence describing what you noticed",
      "evidence": "brief quote or reference to the specific message(s)",
      "cross_reference": "optional: GitHub issue link or Notion doc link/title if found, else omit"
    }
  ]
}

If nothing meaningful stands out, return {"flags": []}.
"""


def _build_mcp_servers() -> tuple[dict, list[str]]:
    """Build the MCP server config for detection, mirroring agent.py's setup."""
    mcp_servers: dict = {}
    allowed_tools: list[str] = []

    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        mcp_servers["github"] = McpHttpServerConfig(
            type="http",
            url=GITHUB_MCP_URL,
            headers={"Authorization": f"Bearer {github_token}"},
        )
        allowed_tools.append("mcp__github__*")

    notion_auth_token = os.environ.get("NOTION_SIDECAR_AUTH_TOKEN")
    if notion_auth_token:
        mcp_servers["notion"] = McpHttpServerConfig(
            type="http",
            url=NOTION_MCP_URL,
            headers={"Authorization": f"Bearer {notion_auth_token}"},
        )
        allowed_tools.append("mcp__notion__*")

    return mcp_servers, allowed_tools


async def detect_signals(messages: list[str]) -> list[dict]:
    """Analyze a batch of messages and return structured flags, using GitHub \
    and Notion tools to cross-reference when relevant."""
    if not messages:
        return []

    joined = "\n".join(messages)
    mcp_servers, allowed_tools = _build_mcp_servers()

    print(f"[detector] Starting detection with {len(messages)} messages, tools: {allowed_tools}")

    options = ClaudeAgentOptions(
        system_prompt=DETECTION_PROMPT,
        mcp_servers=mcp_servers,
        allowed_tools=allowed_tools,
        permission_mode="bypassPermissions",
    )

    response_parts: list[str] = []

    async with ClaudeSDKClient(options) as client:
        await client.query(joined)

        async for message in client.receive_response():
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        response_parts.append(block.text)
            if isinstance(message, ResultMessage):
                pass  # no session tracking needed for one-shot detection

    text = "\n".join(response_parts) if response_parts else ""
    print(f"[detector] Raw response: {text[:500]}")

    try:
        # Extract the JSON object even if Claude added narration before/after it
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise json.JSONDecodeError("No JSON object found", text, 0)

        json_str = text[start : end + 1]
        result = json.loads(json_str)
        flags = result.get("flags", [])
        print(f"[detector] Parsed {len(flags)} flags")
        return flags
    except (json.JSONDecodeError, AttributeError) as e:
        print(f"[detector] Failed to parse response as JSON: {e}")
        return []