"""Video stream handling: raw H.264 capture, PyAV decode pipeline, and the
three frame-access patterns described in the design brief.

Decoding is done with PyAV (the ``av`` package) rather than
``cv2.VideoCapture("udp://...")``, which is the classic source of gray
frames and multi-second latency in other Tello libraries. ``opencv-python``
is never imported by this module -- it is only used by the display
examples.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from enum import Enum

import av
import numpy as np
from numpy.typing import NDArray

from pytello.transport import Endpoint

logger = logging.getLogger("pytello.video")


class Camera(Enum):
    """Which camera a video stream reads from.

    Only one camera may stream at a time -- selecting one is exclusive
    with the other. ``DOWN`` requires SDK 3.0 (Tello EDU with firmware
    >= v02.05.01.17, or RoboMaster TT); see
    :meth:`pytello.aio.client.TelloClient.select_camera_source`.
    """

    FRONT = "front"
    DOWN = "down"


@dataclass
class VideoStats:
    """Running counters for a :class:`VideoStream`.

    Corrupt or out-of-order H.264 data is dropped rather than crashing the
    pipeline; these counters are how a caller notices it's happening.
    """

    packets_received: int = 0
    bytes_received: int = 0
    frames_decoded: int = 0
    frames_dropped: int = 0


class VideoStream:
    """Owns the video UDP endpoint and PyAV decode pipeline for one active
    camera stream.

    Construct via :meth:`pytello.aio.client.TelloClient.start_video`, not
    directly -- it needs to coordinate ``streamon``/``downvision`` on the
    command channel first.

    Three access patterns work concurrently against the same stream:

    - :meth:`latest_frame` -- the most recent decoded frame, non-blocking;
      ideal for a control loop that just wants "the current picture."
    - ``async for frame in stream:`` -- an async iterator of every frame,
      in order, as a BGR :class:`numpy.ndarray`.
    - :meth:`raw_h264` -- an async iterator of the raw elementary-stream
      bytes as received, undecoded, for piping elsewhere (ffmpeg,
      recording) without decode overhead.
    """

    def __init__(self, endpoint: Endpoint, camera: Camera) -> None:
        """Wrap an already-open video :class:`~pytello.transport.Endpoint`.
        Does not start receiving until :meth:`_start` is called by
        ``TelloClient.start_video``."""
        self._endpoint = endpoint
        self.camera = camera
        self._codec = av.CodecContext.create("h264", "r")
        self._latest_frame: NDArray[np.uint8] | None = None
        self._stats = VideoStats()
        self._got_keyframe = False
        self._frame_subscribers: list[asyncio.Queue[NDArray[np.uint8]]] = []
        self._raw_subscribers: list[asyncio.Queue[bytes]] = []
        self._task: asyncio.Task[None] | None = None
        self._closed = False

    def _start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="pytello-video")

    async def _run(self) -> None:
        while True:
            data = await self._endpoint.receive()
            self._stats.packets_received += 1
            self._stats.bytes_received += len(data)
            self._publish_raw(data)
            self._decode(data)

    def _publish_raw(self, data: bytes) -> None:
        for queue in self._raw_subscribers:
            queue.put_nowait(data)

    def _decode(self, data: bytes) -> None:
        try:
            packets = self._codec.parse(data)
        except av.FFmpegError as exc:
            self._stats.frames_dropped += 1
            logger.warning("Dropped corrupt H.264 data (parse failed): %s", exc)
            return

        for packet in packets:
            if not self._got_keyframe:
                if not packet.is_keyframe:
                    # Mid-GOP join: discard inter-frame packets until the
                    # first keyframe, which is expected to carry its own
                    # SPS/PPS on this stream.
                    continue
                self._got_keyframe = True
                logger.debug("Video stream synced at first keyframe (mid-GOP join)")
            try:
                frames = self._codec.decode(packet)
            except av.FFmpegError as exc:
                self._stats.frames_dropped += 1
                logger.warning("Dropped undecodable H.264 packet: %s", exc)
                continue
            for frame in frames:
                array = frame.to_ndarray(format="bgr24").astype(np.uint8, copy=False)
                self._stats.frames_decoded += 1
                self._latest_frame = array
                self._publish_frame(array)

    def _publish_frame(self, array: NDArray[np.uint8]) -> None:
        # Unlike latest_frame() (a single overwritten slot), the async
        # iterator promises every decoded frame in order, so this queue
        # is unbounded rather than drop-oldest.
        for queue in self._frame_subscribers:
            queue.put_nowait(array)

    def latest_frame(self) -> NDArray[np.uint8] | None:
        """Most recent decoded frame as a BGR ``numpy`` array, or ``None``
        if no frame has decoded yet. Non-blocking."""
        return self._latest_frame

    @property
    def stats(self) -> VideoStats:
        """Running packet/frame/drop counters for this stream."""
        return self._stats

    def __aiter__(self) -> AsyncGenerator[NDArray[np.uint8], None]:
        """Support ``async for frame in stream:`` -- see the class docstring."""
        queue: asyncio.Queue[NDArray[np.uint8]] = asyncio.Queue()
        self._frame_subscribers.append(queue)
        return self._frame_iterator(queue)

    async def _frame_iterator(
        self, queue: asyncio.Queue[NDArray[np.uint8]]
    ) -> AsyncGenerator[NDArray[np.uint8], None]:
        try:
            while True:
                yield await queue.get()
        finally:
            self._frame_subscribers.remove(queue)

    async def raw_h264(self) -> AsyncGenerator[bytes, None]:
        """Async iterator of raw elementary-stream bytes as received,
        undecoded -- for piping to ffmpeg, recording to disk, etc."""
        queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._raw_subscribers.append(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._raw_subscribers.remove(queue)

    async def close(self) -> None:
        """Stop receiving and release the underlying socket. Safe to call
        more than once."""
        if self._closed:
            return
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._endpoint.close()
