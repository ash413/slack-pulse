import asyncio
from dataclasses import dataclass, field


@dataclass
class BufferedMessage:
    channel_id: str
    channel_name: str
    user: str
    text: str
    ts: str


class PulseBuffer:
    """Thread-safe in-memory buffer of recent messages across watched channels."""

    def __init__(self, max_size: int = 60, check_every: int = 3):
        self._messages: list[BufferedMessage] = []
        self._lock = asyncio.Lock()
        self._max_size = max_size
        self._check_every = check_every
        self._since_last_check = 0
        self._posted_signatures: set[str] = set()

    async def load_bulk(self, messages: list[BufferedMessage]) -> None:
        """Pre-load historical messages without triggering a detection check."""
        async with self._lock:
            self._messages.extend(messages)
            if len(self._messages) > self._max_size:
                self._messages = self._messages[-self._max_size :]

    async def add(self, msg: BufferedMessage) -> bool:
        """Add a message. Returns True if it's time to run detection."""
        async with self._lock:
            self._messages.append(msg)
            if len(self._messages) > self._max_size:
                self._messages = self._messages[-self._max_size :]
            self._since_last_check += 1
            if self._since_last_check >= self._check_every:
                self._since_last_check = 0
                return True
            return False

    async def snapshot(self) -> list[str]:
        async with self._lock:
            return [
                f"[#{m.channel_name}] {m.user}: {m.text}" for m in self._messages
            ]

    async def already_posted(self, signature: str) -> bool:
        async with self._lock:
            if signature in self._posted_signatures:
                return True
            self._posted_signatures.add(signature)
            return False


pulse_buffer = PulseBuffer()