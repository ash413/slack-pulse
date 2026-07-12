import asyncio
import logging
import os

from dotenv import load_dotenv
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient

from listeners import register_listeners
from agent.buffer import pulse_buffer, BufferedMessage

load_dotenv(dotenv_path=".env", override=False)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = AsyncApp(
    token=os.environ.get("SLACK_BOT_TOKEN"),
    client=AsyncWebClient(
        base_url=os.environ.get("SLACK_API_URL", "https://slack.com/api"),
        token=os.environ.get("SLACK_BOT_TOKEN"),
    ),
)

register_listeners(app)

async def load_channel_history():
    """Pre-load the pulse buffer with existing channel history on startup,
    so detection has real context from seeded conversations, not just live messages."""
    try:
        team_id = os.environ.get("SLACK_TEAM_ID", "T0BGSGQJ11P")

        channels_resp = await app.client.users_conversations(
            types="public_channel", limit=200, team_id=team_id
        )
        channels = channels_resp.get("channels", [])

        for channel in channels:
            channel_id = channel["id"]
            channel_name = channel["name"]

            history_resp = await app.client.conversations_history(
                channel=channel_id, limit=100
            )
            messages = history_resp.get("messages", [])

            buffered = []
            for msg in reversed(messages):  # oldest first
                if msg.get("subtype") or msg.get("bot_id"):
                    continue
                text = msg.get("text", "")
                if not text:
                    continue
                buffered.append(
                    BufferedMessage(
                        channel_id=channel_id,
                        channel_name=channel_name,
                        user=msg.get("user", "unknown"),
                        text=text,
                        ts=msg["ts"],
                    )
                )

            if buffered:
                await pulse_buffer.load_bulk(buffered)
                logger.info(
                    f"Loaded {len(buffered)} historical messages from #{channel_name}"
                )

    except Exception as e:
        logger.exception(f"Failed to load channel history: {e}")


async def main():
    await load_channel_history()
    handler = AsyncSocketModeHandler(app, os.environ.get("SLACK_APP_TOKEN"))
    await handler.start_async()


if __name__ == "__main__":
    asyncio.run(main())