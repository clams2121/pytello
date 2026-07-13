"""In-process test double for :class:`pytello.transport.Endpoint`.

Not a public API -- exists purely so :mod:`test_client` can script drone
responses (including delayed ACKs and dropped responses) without opening
real sockets.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable


class FakeEndpoint:
    """A scriptable stand-in for a UDP channel.

    ``handler(data, endpoint)`` is invoked synchronously on every
    :meth:`send`; it decides what (if anything) to queue as a response,
    optionally after a delay, by calling :meth:`push_response`.
    """

    def __init__(self, handler: Callable[[bytes, FakeEndpoint], None] | None = None) -> None:
        self.sent: list[bytes] = []
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._handler = handler
        self.closed = False
        self.dropped_count = 0

    async def open(self) -> None:
        return None

    def send(self, data: bytes) -> None:
        self.sent.append(data)
        if self._handler is not None:
            self._handler(data, self)

    def push_response(self, data: bytes, delay: float = 0.0) -> None:
        if delay <= 0:
            self._queue.put_nowait(data)
        else:
            loop = asyncio.get_event_loop()
            loop.call_later(delay, self._queue.put_nowait, data)

    async def receive(self) -> bytes:
        return await self._queue.get()

    def poll(self) -> bytes | None:
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def close(self) -> None:
        self.closed = True


def scripted(
    responses: dict[str, bytes | str | None], delays: dict[str, float] | None = None
) -> Callable[[bytes, FakeEndpoint], None]:
    """Build a handler that replies per the first word of the sent command.

    A mapping to ``None`` means "drop this response" (simulates a timeout).
    Commands not present in ``responses`` get an immediate ``ok``.
    """
    delays = delays or {}

    def handler(data: bytes, endpoint: FakeEndpoint) -> None:
        text = data.decode("ascii")
        keyword = text.split(maxsplit=1)[0]
        if keyword in responses:
            response = responses[keyword]
            if response is None:
                return
        else:
            response = "ok"
        payload = response.encode("ascii") if isinstance(response, str) else response
        endpoint.push_response(payload, delay=delays.get(keyword, 0.0))

    return handler
