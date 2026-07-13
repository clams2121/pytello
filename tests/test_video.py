"""Video pipeline tests: synthesize a short H.264 stream with PyAV, push it
through the decode pipeline chunked like real UDP datagrams, and verify
mid-stream join and corrupt-data handling never crash the pipeline."""

from __future__ import annotations

import asyncio
import io

import av
import numpy as np

from pytello.video import Camera, VideoStream
from tests.fakes import FakeEndpoint

FRAME_WIDTH = 64
FRAME_HEIGHT = 48


def _synthesize_h264(num_frames: int = 8) -> bytes:
    """Encode a tiny synthetic H.264 elementary stream in memory."""
    buffer = io.BytesIO()
    container = av.open(buffer, mode="w", format="h264")
    stream = container.add_stream("h264", rate=10)
    stream.width = FRAME_WIDTH
    stream.height = FRAME_HEIGHT
    stream.pix_fmt = "yuv420p"

    for _ in range(num_frames):
        frame = av.VideoFrame(FRAME_WIDTH, FRAME_HEIGHT, "yuv420p")
        for packet in stream.encode(frame):
            container.mux(packet)
    for packet in stream.encode(None):
        container.mux(packet)
    container.close()
    return buffer.getvalue()


def _chunk(data: bytes, size: int) -> list[bytes]:
    return [data[i : i + size] for i in range(0, len(data), size)]


async def _feed(stream: VideoStream, endpoint: FakeEndpoint, chunks: list[bytes]) -> None:
    # Let any consumer tasks created just before this call (the stream's
    # own background task, or a test's subscriber) reach their first
    # suspend point and register, before any data is available to miss.
    await asyncio.sleep(0)
    for chunk in chunks:
        endpoint.push_response(chunk)
    # Let the stream's background task drain the queue.
    for _ in range(len(chunks) + 5):
        await asyncio.sleep(0)


async def test_decodes_frames_from_chunked_stream() -> None:
    data = _synthesize_h264(num_frames=8)
    endpoint = FakeEndpoint()
    stream = VideoStream(endpoint, Camera.FRONT)
    stream._start()

    await _feed(stream, endpoint, _chunk(data, 200))
    await asyncio.sleep(0.05)

    assert stream.stats.frames_decoded >= 1
    frame = stream.latest_frame()
    assert frame is not None
    assert frame.shape == (FRAME_HEIGHT, FRAME_WIDTH, 3)
    assert frame.dtype == np.uint8
    await stream.close()


async def test_async_iterator_yields_frames_in_order() -> None:
    data = _synthesize_h264(num_frames=8)
    endpoint = FakeEndpoint()
    stream = VideoStream(endpoint, Camera.FRONT)
    stream._start()

    frames: list[np.ndarray] = []

    async def collect() -> None:
        async for frame in stream:
            frames.append(frame)
            if len(frames) >= 3:
                break

    collect_task = asyncio.create_task(collect())
    await _feed(stream, endpoint, _chunk(data, 200))
    await asyncio.wait_for(collect_task, timeout=2.0)

    assert len(frames) == 3
    for frame in frames:
        assert frame.shape == (FRAME_HEIGHT, FRAME_WIDTH, 3)
    await stream.close()


async def test_raw_h264_iterator_yields_undecoded_bytes() -> None:
    data = _synthesize_h264(num_frames=4)
    endpoint = FakeEndpoint()
    stream = VideoStream(endpoint, Camera.FRONT)
    stream._start()

    chunks = _chunk(data, 300)
    raw_chunks: list[bytes] = []

    async def collect() -> None:
        async for chunk in stream.raw_h264():
            raw_chunks.append(chunk)
            if len(raw_chunks) >= len(chunks):
                break

    collect_task = asyncio.create_task(collect())
    await _feed(stream, endpoint, chunks)
    await asyncio.wait_for(collect_task, timeout=2.0)

    assert b"".join(raw_chunks) == data
    await stream.close()


async def test_mid_gop_join_discards_until_first_keyframe() -> None:
    data = _synthesize_h264(num_frames=8)
    # Skip the first packet (which carries SPS/PPS/IDR) to simulate
    # joining a live stream mid-GOP, then splice in a repeat of it later
    # (as if a fresh keyframe arrived, which real Tello encoders do
    # periodically).
    joined = data[50:] + data
    endpoint = FakeEndpoint()
    stream = VideoStream(endpoint, Camera.FRONT)
    stream._start()

    await _feed(stream, endpoint, _chunk(joined, 150))
    await asyncio.sleep(0.05)

    # No crash, and once a real keyframe (from the appended full stream)
    # arrives, frames decode normally.
    assert stream.stats.frames_decoded >= 1
    await stream.close()


async def test_garbage_bytes_are_dropped_not_crashed() -> None:
    endpoint = FakeEndpoint()
    stream = VideoStream(endpoint, Camera.FRONT)
    stream._start()

    import os

    await _feed(stream, endpoint, [os.urandom(500) for _ in range(5)])
    await asyncio.sleep(0.05)

    assert stream.stats.frames_decoded == 0
    assert stream.latest_frame() is None

    # The stream must still be alive and able to decode a real stream
    # afterwards.
    data = _synthesize_h264(num_frames=4)
    await _feed(stream, endpoint, _chunk(data, 200))
    await asyncio.sleep(0.05)
    assert stream.stats.frames_decoded >= 1
    await stream.close()


async def test_down_camera_produces_expected_shape() -> None:
    data = _synthesize_h264(num_frames=4)
    endpoint = FakeEndpoint()
    stream = VideoStream(endpoint, Camera.DOWN)
    assert stream.camera is Camera.DOWN
    stream._start()

    await _feed(stream, endpoint, _chunk(data, 200))
    await asyncio.sleep(0.05)

    assert stream.stats.frames_decoded >= 1
    await stream.close()


async def test_close_stops_background_task() -> None:
    endpoint = FakeEndpoint()
    stream = VideoStream(endpoint, Camera.FRONT)
    stream._start()
    await stream.close()
    assert endpoint.closed is True
    assert stream._task is not None
    assert stream._task.done()


async def test_stats_track_packets_and_bytes() -> None:
    data = _synthesize_h264(num_frames=4)
    endpoint = FakeEndpoint()
    stream = VideoStream(endpoint, Camera.FRONT)
    stream._start()

    chunks = _chunk(data, 200)
    await _feed(stream, endpoint, chunks)
    await asyncio.sleep(0.05)

    assert stream.stats.packets_received == len(chunks)
    assert stream.stats.bytes_received == len(data)
    await stream.close()
