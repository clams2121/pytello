"""Synchronous facade over the async core.

:class:`Tello` owns a background thread running its own asyncio event
loop and delegates every call to :class:`~pytello.aio.client.TelloClient`
via ``asyncio.run_coroutine_threadsafe``, blocking the calling thread
until the coroutine completes. Method names and semantics are identical
to the async client; this is what most users should start with.
"""

from __future__ import annotations

import asyncio
import atexit
import logging
import signal
import threading
from collections.abc import Awaitable, Callable, Iterator
from types import FrameType, TracebackType
from typing import Any, TypeVar

from pytello.aio.client import (
    DEFAULT_COMMAND_PORT,
    DEFAULT_CONNECT_RETRIES,
    DEFAULT_DRONE_HOST,
    DEFAULT_KEEPALIVE_INTERVAL_S,
    DEFAULT_STATE_PORT,
    DEFAULT_STATE_STALE_TIMEOUT_S,
    DEFAULT_VIDEO_PORT,
    TelloClient,
)
from pytello.capabilities import Capabilities
from pytello.protocol import FlipDirection, TelloState
from pytello.video import Camera, VideoStats, VideoStream

logger = logging.getLogger("pytello.sync")

_T = TypeVar("_T")

CoroRunner = Callable[[Awaitable[_T]], _T]


async def _await_it(awaitable: Awaitable[_T]) -> _T:
    """Normalize any awaitable (a plain coroutine, or an async generator's
    ``__anext__()``) into a genuine coroutine object that
    ``run_coroutine_threadsafe`` can schedule."""
    return await awaitable


class SyncVideoStream:
    """Synchronous facade over :class:`pytello.video.VideoStream`.

    Construct via :meth:`Tello.start_video`, not directly.
    """

    def __init__(self, async_stream: VideoStream, call: CoroRunner[Any]) -> None:
        """Wrap an already-started :class:`~pytello.video.VideoStream`,
        submitting its coroutines through ``call`` (the owning
        :class:`Tello`'s background event loop)."""
        self._async_stream = async_stream
        self._call = call

    @property
    def camera(self) -> Camera:
        """Which camera this stream is reading from."""
        return self._async_stream.camera

    @property
    def stats(self) -> VideoStats:
        """Running packet/frame/drop counters for this stream."""
        return self._async_stream.stats

    def latest_frame(self) -> Any:
        """Most recent decoded frame as a BGR ``numpy`` array, or ``None``
        if no frame has decoded yet. Non-blocking."""
        return self._async_stream.latest_frame()

    def __iter__(self) -> Iterator[Any]:
        """Blocking iterator of every decoded frame, in order."""
        async_iter = self._async_stream.__aiter__()
        try:
            while True:
                try:
                    yield self._call(async_iter.__anext__())
                except StopAsyncIteration:
                    return
        finally:
            self._call(async_iter.aclose())

    def raw_h264(self) -> Iterator[bytes]:
        """Blocking iterator of raw elementary-stream bytes as received,
        undecoded -- for piping to ffmpeg, recording to disk, etc."""
        async_iter = self._async_stream.raw_h264()
        try:
            while True:
                try:
                    yield self._call(async_iter.__anext__())
                except StopAsyncIteration:
                    return
        finally:
            self._call(async_iter.aclose())

    def close(self) -> None:
        """Stop receiving and release the underlying socket."""
        self._call(self._async_stream.close())


class Tello:
    """Synchronous facade over the Tello UDP text-command SDK.

    Owns a background thread running its own asyncio event loop for the
    lifetime of the object. Every method blocks the calling thread until
    the underlying coroutine completes; see
    :class:`pytello.aio.client.TelloClient` for full parameter and
    exception documentation, which is identical here.

    Is a context manager: ``with Tello() as drone:`` connects on entry
    and, on exit, attempts a bounded-timeout landing if airborne before
    releasing sockets. If ``install_safety_handlers`` is left ``True``
    (the default) and this is used from the main thread, a SIGINT handler
    and an ``atexit`` hook provide the same safety-net landing even if the
    process is interrupted or exits without a clean ``close()``.
    """

    def __init__(
        self,
        drone_host: str = DEFAULT_DRONE_HOST,
        *,
        command_port: int = DEFAULT_COMMAND_PORT,
        state_port: int = DEFAULT_STATE_PORT,
        video_port: int = DEFAULT_VIDEO_PORT,
        keepalive_interval: float = DEFAULT_KEEPALIVE_INTERVAL_S,
        state_stale_timeout: float = DEFAULT_STATE_STALE_TIMEOUT_S,
        connect_retries: int = DEFAULT_CONNECT_RETRIES,
        on_state: Callable[[TelloState], None] | None = None,
        on_connection_lost: Callable[[BaseException], None] | None = None,
        install_safety_handlers: bool = True,
    ) -> None:
        """Start the background event loop and construct the underlying
        :class:`~pytello.aio.client.TelloClient`. Does not touch the
        network -- call :meth:`connect` (or use as a context manager) for
        that. Parameters match ``TelloClient.__init__`` except
        ``install_safety_handlers``, which enables the SIGINT/atexit
        safety-net landing described in the class docstring.
        """
        self._loop = asyncio.new_event_loop()
        loop_ready = threading.Event()
        self._thread = threading.Thread(
            target=self._run_loop, args=(loop_ready,), name="pytello-loop", daemon=True
        )
        self._thread.start()
        loop_ready.wait()

        self._client = TelloClient(
            drone_host,
            command_port=command_port,
            state_port=state_port,
            video_port=video_port,
            keepalive_interval=keepalive_interval,
            state_stale_timeout=state_stale_timeout,
            connect_retries=connect_retries,
            on_state=on_state,
            on_connection_lost=on_connection_lost,
        )
        self._closed = False
        self._previous_sigint_handler: Callable[[int, FrameType | None], Any] | int | None = None
        self._signal_handler_installed = False
        if install_safety_handlers:
            atexit.register(self._atexit_land)
            self._install_signal_handler()

    def _run_loop(self, loop_ready: threading.Event) -> None:
        asyncio.set_event_loop(self._loop)
        loop_ready.set()
        self._loop.run_forever()

    def _call(self, awaitable: Awaitable[_T]) -> _T:
        return asyncio.run_coroutine_threadsafe(_await_it(awaitable), self._loop).result()

    def _install_signal_handler(self) -> None:
        if threading.current_thread() is not threading.main_thread():
            return
        try:
            self._previous_sigint_handler = signal.signal(signal.SIGINT, self._handle_sigint)
        except (ValueError, OSError):
            # Not in the main thread of the main interpreter, or the
            # platform doesn't support this signal -- nothing to do.
            return
        self._signal_handler_installed = True

    def _handle_sigint(self, signum: int, frame: FrameType | None) -> None:
        logger.warning("SIGINT received; attempting a safety-net landing before exiting")
        try:
            self.close()
        except Exception:
            # Best-effort only: this is the last chance to land before the
            # process dies from the re-raised KeyboardInterrupt below, so
            # log loudly rather than let a landing failure mask it.
            logger.exception("Safety-net landing during SIGINT handling failed")
        if self._signal_handler_installed and self._previous_sigint_handler is not None:
            signal.signal(signal.SIGINT, self._previous_sigint_handler)
        raise KeyboardInterrupt

    def _atexit_land(self) -> None:
        if self._closed:
            return
        logger.warning("Process exiting with pytello.Tello still open; attempting safety-net landing")
        try:
            self.close()
        except Exception:
            # Best-effort only: atexit callbacks that raise are reported by
            # the interpreter but don't stop shutdown, so log loudly here
            # rather than rely on that.
            logger.exception("Safety-net landing during atexit failed")

    def __enter__(self) -> Tello:
        """Call :meth:`connect` and return ``self``."""
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Call :meth:`close`, regardless of whether the block raised."""
        self.close()

    # ----------------------------------------------------------------
    # Properties
    # ----------------------------------------------------------------

    @property
    def capabilities(self) -> Capabilities:
        """See :attr:`pytello.aio.client.TelloClient.capabilities`."""
        return self._client.capabilities

    @property
    def is_flying(self) -> bool:
        """See :attr:`pytello.aio.client.TelloClient.is_flying`."""
        return self._client.is_flying

    @property
    def latest_state(self) -> TelloState | None:
        """See :attr:`pytello.aio.client.TelloClient.latest_state`."""
        return self._client.latest_state

    @property
    def command_dropped_count(self) -> int:
        """See :attr:`pytello.aio.client.TelloClient.command_dropped_count`."""
        return self._client.command_dropped_count

    # ----------------------------------------------------------------
    # Connection lifecycle
    # ----------------------------------------------------------------

    def connect(self) -> None:
        """See :meth:`pytello.aio.client.TelloClient.connect`."""
        self._call(self._client.connect())

    def close(self) -> None:
        """Land if airborne (bounded timeout), stop background tasks and
        sockets, and shut down the background event loop. Safe to call
        more than once."""
        if self._closed:
            return
        self._closed = True
        try:
            self._call(self._client.close())
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._thread.join(timeout=10.0)
            self._loop.close()

    # ----------------------------------------------------------------
    # Flight commands
    # ----------------------------------------------------------------

    def takeoff(self) -> None:
        """See :meth:`pytello.aio.client.TelloClient.takeoff`."""
        self._call(self._client.takeoff())

    def land(self) -> None:
        """See :meth:`pytello.aio.client.TelloClient.land`."""
        self._call(self._client.land())

    def emergency(self) -> None:
        """See :meth:`pytello.aio.client.TelloClient.emergency`."""
        self._call(self._client.emergency())

    def up(self, cm: int) -> None:
        """See :meth:`pytello.aio.client.TelloClient.up`."""
        self._call(self._client.up(cm))

    def down(self, cm: int) -> None:
        """See :meth:`pytello.aio.client.TelloClient.down`."""
        self._call(self._client.down(cm))

    def left(self, cm: int) -> None:
        """See :meth:`pytello.aio.client.TelloClient.left`."""
        self._call(self._client.left(cm))

    def right(self, cm: int) -> None:
        """See :meth:`pytello.aio.client.TelloClient.right`."""
        self._call(self._client.right(cm))

    def forward(self, cm: int) -> None:
        """See :meth:`pytello.aio.client.TelloClient.forward`."""
        self._call(self._client.forward(cm))

    def back(self, cm: int) -> None:
        """See :meth:`pytello.aio.client.TelloClient.back`."""
        self._call(self._client.back(cm))

    def cw(self, degrees: int) -> None:
        """See :meth:`pytello.aio.client.TelloClient.cw`."""
        self._call(self._client.cw(degrees))

    def ccw(self, degrees: int) -> None:
        """See :meth:`pytello.aio.client.TelloClient.ccw`."""
        self._call(self._client.ccw(degrees))

    def flip(self, direction: FlipDirection) -> None:
        """See :meth:`pytello.aio.client.TelloClient.flip`."""
        self._call(self._client.flip(direction))

    def go(self, x: int, y: int, z: int, speed: int, mid: int | None = None) -> None:
        """See :meth:`pytello.aio.client.TelloClient.go`."""
        self._call(self._client.go(x, y, z, speed, mid))

    def curve(
        self,
        x1: int,
        y1: int,
        z1: int,
        x2: int,
        y2: int,
        z2: int,
        speed: int,
        mid: int | None = None,
    ) -> None:
        """See :meth:`pytello.aio.client.TelloClient.curve`."""
        self._call(self._client.curve(x1, y1, z1, x2, y2, z2, speed, mid))

    def rc(self, left_right: int, forward_back: int, up_down: int, yaw: int) -> None:
        """See :meth:`pytello.aio.client.TelloClient.rc`."""
        self._call(self._client.rc(left_right, forward_back, up_down, yaw))

    def set_speed(self, speed: int) -> None:
        """See :meth:`pytello.aio.client.TelloClient.set_speed`."""
        self._call(self._client.set_speed(speed))

    # ----------------------------------------------------------------
    # Reads
    # ----------------------------------------------------------------

    def get_speed(self) -> float:
        """See :meth:`pytello.aio.client.TelloClient.get_speed`."""
        return self._call(self._client.get_speed())

    def get_battery(self) -> int:
        """See :meth:`pytello.aio.client.TelloClient.get_battery`."""
        return self._call(self._client.get_battery())

    def get_flight_time(self) -> int:
        """See :meth:`pytello.aio.client.TelloClient.get_flight_time`."""
        return self._call(self._client.get_flight_time())

    def get_wifi_snr(self) -> str:
        """See :meth:`pytello.aio.client.TelloClient.get_wifi_snr`."""
        return self._call(self._client.get_wifi_snr())

    def get_height(self) -> int:
        """See :meth:`pytello.aio.client.TelloClient.get_height`."""
        return self._call(self._client.get_height())

    def get_temperature(self) -> str:
        """See :meth:`pytello.aio.client.TelloClient.get_temperature`."""
        return self._call(self._client.get_temperature())

    def get_attitude(self) -> str:
        """See :meth:`pytello.aio.client.TelloClient.get_attitude`."""
        return self._call(self._client.get_attitude())

    def get_barometer(self) -> float:
        """See :meth:`pytello.aio.client.TelloClient.get_barometer`."""
        return self._call(self._client.get_barometer())

    def get_acceleration(self) -> str:
        """See :meth:`pytello.aio.client.TelloClient.get_acceleration`."""
        return self._call(self._client.get_acceleration())

    def get_tof(self) -> int:
        """See :meth:`pytello.aio.client.TelloClient.get_tof`."""
        return self._call(self._client.get_tof())

    # ----------------------------------------------------------------
    # Mission pads (SDK 2.0+)
    # ----------------------------------------------------------------

    def mission_pad_on(self) -> None:
        """See :meth:`pytello.aio.client.TelloClient.mission_pad_on`."""
        self._call(self._client.mission_pad_on())

    def mission_pad_off(self) -> None:
        """See :meth:`pytello.aio.client.TelloClient.mission_pad_off`."""
        self._call(self._client.mission_pad_off())

    def mission_pad_direction(self, direction: int) -> None:
        """See :meth:`pytello.aio.client.TelloClient.mission_pad_direction`."""
        self._call(self._client.mission_pad_direction(direction))

    # ----------------------------------------------------------------
    # Video
    # ----------------------------------------------------------------

    def stream_on(self) -> None:
        """See :meth:`pytello.aio.client.TelloClient.stream_on`."""
        self._call(self._client.stream_on())

    def stream_off(self) -> None:
        """See :meth:`pytello.aio.client.TelloClient.stream_off`."""
        self._call(self._client.stream_off())

    def select_camera_source(self, down: bool) -> None:
        """See :meth:`pytello.aio.client.TelloClient.select_camera_source`."""
        self._call(self._client.select_camera_source(down))

    def start_video(self, camera: Camera = Camera.FRONT) -> SyncVideoStream:
        """See :meth:`pytello.aio.client.TelloClient.start_video`. Returns a
        :class:`SyncVideoStream` rather than the async
        :class:`~pytello.video.VideoStream`."""
        async_stream = self._call(self._client.start_video(camera))
        return SyncVideoStream(async_stream, self._call)

    def stop_video(self) -> None:
        """See :meth:`pytello.aio.client.TelloClient.stop_video`."""
        self._call(self._client.stop_video())
