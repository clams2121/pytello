"""Asyncio core of pytello. :class:`pytello.aio.client.TelloClient` is the
real implementation; :class:`pytello.Tello` is a synchronous facade over it.
"""

from pytello.aio.client import TelloClient

__all__ = ["TelloClient"]
