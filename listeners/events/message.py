from logging import Logger

from slack_bolt.context.async_context import AsyncBoltContext
from slack_bolt.context.say.async_say import AsyncSay
from slack_bolt.context.say_stream.async_say_stream import AsyncSayStream
from slack_bolt.context.set_status.async_set_status import AsyncSetStatus
from slack_sdk.web.async_client import AsyncWebClient

from agent import AgentDeps, run_agent
from thread_context import session_store
from listeners.views.feedback_builder import build_feedback_blocks

from agent.buffer import pulse_buffer, BufferedMessage
from agent.detector import detect_signals


async def handle_message(
    client: AsyncWebClient,
    context: AsyncBoltContext,
    event: dict,
    logger: Logger,
    say: AsyncSay,
    say_stream: AsyncSayStream,
    set_status: AsyncSetStatus,
):
    """Handle messages sent to the agent via DM or in threads the bot is part of."""
    # Skip message subtypes (edits, deletes, etc.) and bot messages.
    if event.get("subtype"):
        return
    if event.get("bot_id"):
        return

    is_dm = event.get("channel_type") == "im"
    is_thread_reply = event.get("thread_ts") is not None

    if is_dm:
        pass
    elif is_thread_reply:
        # Channel thread replies are handled only if the bot is already engaged
        session = session_store.get_session(context.channel_id, event["thread_ts"])
        if session is None:
            return
    else:
        # Top-level channel messages: buffer for passive detection, don't reply
        await handle_passive_message(client, context, event, logger)
        return

    try:
        channel_id = context.channel_id
        text = event.get("text", "")
        thread_ts = event.get("thread_ts") or event["ts"]

        # Get session ID for conversation context
        existing_session_id = session_store.get_session(channel_id, thread_ts)

        # Set assistant thread status with loading messages
        await set_status(
            status="Thinking...",
            loading_messages=[
                "Teaching the hamsters to type faster…",
                "Untangling the internet cables…",
                "Consulting the office goldfish…",
                "Polishing up the response just for you…",
                "Convincing the AI to stop overthinking…",
            ],
        )

        # Run the agent with deps for tool access
        user_id = context.user_id
        deps = AgentDeps(
            client=client,
            user_id=user_id,
            channel_id=channel_id,
            thread_ts=thread_ts,
            message_ts=event["ts"],
            user_token=context.user_token,
        )
        response_text, new_session_id = await run_agent(
            text, session_id=existing_session_id, deps=deps
        )

        # Stream response in thread with feedback buttons
        streamer = await say_stream()
        await streamer.append(markdown_text=response_text)
        feedback_blocks = build_feedback_blocks()
        await streamer.stop(blocks=feedback_blocks)

        # Store session ID for future context
        if new_session_id:
            session_store.set_session(channel_id, thread_ts, new_session_id)

    except Exception as e:
        logger.exception(f"Failed to handle message: {e}")
        await say(
            text=f":warning: Something went wrong! ({e})",
            thread_ts=event.get("thread_ts") or event.get("ts"),
        )

async def handle_passive_message(
    client: AsyncWebClient,
    context: AsyncBoltContext,
    event: dict,
    logger: Logger,
):
    try:
        channel_id = context.channel_id
        text = event.get("text", "")
        if not text:
            return

        channel_info = await client.conversations_info(channel=channel_id)
        channel_name = channel_info["channel"]["name"]

        user_id = context.user_id
        should_check = await pulse_buffer.add(
            BufferedMessage(
                channel_id=channel_id,
                channel_name=channel_name,
                user=user_id,
                text=text,
                ts=event["ts"],
            )
        )

        if not should_check:
            return

        messages = await pulse_buffer.snapshot()
        flags = await detect_signals(messages)

        for flag in flags:
            signature = f"{flag['type']}:{flag['summary']}"
            if await pulse_buffer.already_posted(signature):
                continue

            emoji = {
                "DUPLICATE_DISCUSSION": "🔁",
                "MISSING_OWNER": "🎯",
                "READINESS_MISMATCH": "⚠️",
                "INCIDENT_PATTERN": "🔍",
            }.get(flag["type"], "💡")

            await client.chat_postMessage(
                channel=channel_id,
                text=(
                    f"{emoji} *Slack Pulse noticed something*\n"
                    f"{flag['summary']}\n"
                    f"> {flag['evidence']}"
                ),
            )
    except Exception as e:
        logger.exception(f"Failed passive message handling: {e}")