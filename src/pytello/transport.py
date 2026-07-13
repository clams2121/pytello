"""UDP sockets only: binding, sending, and receiving datagrams.

This module has no protocol knowledge -- it moves bytes and enforces the
source-IP allowlist described in the security requirements (any inbound
datagram whose source is not the configured drone address is dropped and
logged, never processed).

:class:`Endpoint` is a structural interface (``typing.Protocol``) rather
than a base class so that tests can supply an in-process fake that speaks
the same shape without opening a real socket.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Protocol

logger = logging.getLogger("pytello.transport")


class Endpoint(Protocol):
    """The shape :mod:`pytello.aio.client` depends on for a UDP channel."""

    async def open(self) -> None:
        """Bind the underlying socket. Idempotent no-op for fakes that are
        already open."""
        ...

    def send(self, data: bytes) -> None:
        """Send a datagram to the configured remote address."""
        ...

    async def receive(self) -> bytes:
        """Wait for and return the next accepted inbound datagram."""
        ...

    def poll(self) -> bytes | None:
        """Return the next queued datagram without waiting, or ``None``."""
        ...

    def close(self) -> None:
        """Release the underlying socket. Safe to call more than once."""
        ...

    @property
    def dropped_count(self) -> int:
        """Number of inbound datagrams rejected for a spoofed source IP."""
        ...


@dataclass(frozen=True)
class EndpointAddress:
    """Local bind port plus the drone's remote host/port for one channel."""

    local_port: int
    remote_host: str
    remote_port: int


class _FilteringProtocol(asyncio.DatagramProtocol):
    """``asyncio.DatagramProtocol`` that enforces the source-IP allowlist.

    Any datagram whose source IP does not match ``allowed_source_ip`` is
    counted and dropped without being queued -- it never reaches parsing.
    """

    def __init__(self, queue: asyncio.Queue[bytes], allowed_source_ip: str, channel_name: str) -> None:
        self._queue = queue
        self._allowed_source_ip = allowed_source_ip
        self._channel_name = channel_name
        self.dropped_count = 0

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        source_ip = addr[0]
        if source_ip != self._allowed_source_ip:
            self.dropped_count += 1
            logger.warning(
                "Dropped spoofed datagram on %s channel: source %s, expected %s",
                self._channel_name,
                source_ip,
                self._allowed_source_ip,
            )
            return
        self._queue.put_nowait(data)

    def error_received(self, exc: Exception) -> None:
        logger.warning("Socket error on %s channel: %s", self._channel_name, exc)


class UdpEndpoint:
    """Real ``asyncio`` UDP socket implementing :class:`Endpoint`."""

    def __init__(self, address: EndpointAddress, channel_name: str) -> None:
        self._address = address
        self._channel_name = channel_name
        self._queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _FilteringProtocol | None = None

    async def open(self) -> None:
        if self._transport is not None:
            return
        loop = asyncio.get_running_loop()
        transport, protocol_instance = await loop.create_datagram_endpoint(
            lambda: _FilteringProtocol(self._queue, self._address.remote_host, self._channel_name),
            local_addr=("0.0.0.0", self._address.local_port),
        )
        self._transport = transport
        assert isinstance(protocol_instance, _FilteringProtocol)
        self._protocol = protocol_instance
        logger.debug(
            "Opened %s channel: local port %d, remote %s:%d",
            self._channel_name,
            self._address.local_port,
            self._address.remote_host,
            self._address.remote_port,
        )

    def send(self, data: bytes) -> None:
        if self._transport is None:
            raise RuntimeError(f"{self._channel_name} endpoint is not open; call open() first")
        self._transport.sendto(data, (self._address.remote_host, self._address.remote_port))

    async def receive(self) -> bytes:
        return await self._queue.get()

    def poll(self) -> bytes | None:
        try:
            return self._queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def close(self) -> None:
        if self._transport is not None:
            self._transport.close()
            self._transport = None
            self._protocol = None

    @property
    def dropped_count(self) -> int:
        return self._protocol.dropped_count if self._protocol is not None else 0
