"""The async Tello client: connection lifecycle, command serialization,
timeouts, keepalive, capability detection, and the flight command surface.

Every public coroutine here either succeeds or raises one of the typed
exceptions in :mod:`pytello.exceptions`. Nothing is swallowed silently.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable
from types import TracebackType

from pytello import protocol
from pytello.capabilities import (
    Capabilities,
    build_capabilities,
    detect_sdk_version,
    require_camera_switching,
    require_mission_pads,
)
from pytello.exceptions import (
    TelloCommandError,
    TelloConnectionError,
    TelloError,
    TelloNotFlyingError,
    TelloTimeoutError,
)
from pytello.protocol import FlipDirection, ParsedResponse, ResponseKind, TelloState
from pytello.transport import Endpoint, EndpointAddress, UdpEndpoint

logger = logging.getLogger("pytello.client")

DEFAULT_DRONE_HOST = "192.168.10.1"
DEFAULT_COMMAND_PORT = 8889
DEFAULT_STATE_PORT = 8890
DEFAULT_VIDEO_PORT = 11111

#: Interval at which a benign keepalive command is sent while flying, to
#: stay ahead of the drone's 15s auto-land watchdog.
DEFAULT_KEEPALIVE_INTERVAL_S = 10.0

#: How long state telemetry may go quiet while flying before the
#: connection is presumed lost.
DEFAULT_STATE_STALE_TIMEOUT_S = 15.0

#: Bounded retries for the initial "command" SDK-mode handshake -- the
#: drone occasionally answers the first post-boot packet with garbage.
DEFAULT_CONNECT_RETRIES = 3

_FAILURE_KINDS = frozenset(
    {ResponseKind.ERROR, ResponseKind.OUT_OF_RANGE, ResponseKind.UNKNOWN_COMMAND, ResponseKind.GARBAGE}
)


class TelloClient:
    """Async client for the Tello UDP text-command SDK.

    Async users should prefer ``async with TelloClient(...) as client:`` so
    that a bounded-timeout landing is attempted automatically if the drone
    is airborne when the block exits. There is no reliable way to run
    async cleanup from ``atexit`` or a signal handler, so process-level
    safety-net handling (SIGINT, atexit) lives in the synchronous facade,
    :class:`pytello.Tello`, which most users should use directly.
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
        command_endpoint: Endpoint | None = None,
        state_endpoint: Endpoint | None = None,
    ) -> None:
        """Construct a client. This does not touch the network -- call
        :meth:`connect` (or use as an async context manager) to do that.

        Args:
            drone_host: IP address of the drone's control link. Also the
                only source IP accepted on any channel; all other inbound
                datagrams are dropped and logged.
            command_port: UDP port for text commands and their responses.
            state_port: UDP port the drone broadcasts state telemetry on.
            video_port: UDP port the drone streams raw H.264 on.
            keepalive_interval: Seconds of command-channel silence, while
                flying, before a benign ``battery?`` keepalive is sent to
                stay ahead of the drone's 15s auto-land watchdog.
            state_stale_timeout: Seconds without a state packet, while
                flying, before the connection is presumed lost.
            connect_retries: Bounded retry count for the initial SDK-mode
                handshake.
            on_state: Optional callback invoked with each parsed
                :class:`~pytello.protocol.TelloState` as it arrives.
            on_connection_lost: Optional callback invoked once, from the
                background watchdog, if the connection is presumed lost
                while flying.
            command_endpoint: Advanced/testing hook -- inject a pre-built
                :class:`~pytello.transport.Endpoint` instead of opening a
                real UDP socket. Leave as ``None`` in normal use.
            state_endpoint: Same, for the state telemetry channel.
        """
        self._drone_host = drone_host
        self._command_port = command_port
        self._state_port = state_port
        self._video_port = video_port
        self._keepalive_interval = keepalive_interval
        self._state_stale_timeout = state_stale_timeout
        self._connect_retries = connect_retries
        self._on_state = on_state
        self._on_connection_lost = on_connection_lost

        self._command_endpoint: Endpoint | None = command_endpoint
        self._state_endpoint: Endpoint | None = state_endpoint
        self._owns_command_endpoint = command_endpoint is None
        self._owns_state_endpoint = state_endpoint is None

        self._command_lock = asyncio.Lock()
        self._connected = False
        self._flying = False
        self._capabilities: Capabilities | None = None

        self._last_command_monotonic: float = 0.0
        self._latest_state: TelloState | None = None
        self._last_state_monotonic: float | None = None

        self._keepalive_task: asyncio.Task[None] | None = None
        self._state_task: asyncio.Task[None] | None = None
        self._state_watchdog_task: asyncio.Task[None] | None = None
        self._connection_lost_error: BaseException | None = None

    # ----------------------------------------------------------------
    # Properties
    # ----------------------------------------------------------------

    @property
    def capabilities(self) -> Capabilities:
        """Feature flags for the connected drone. Raises
        :class:`TelloConnectionError` if :meth:`connect` has not completed."""
        if self._capabilities is None:
            raise TelloConnectionError("Not connected yet; call connect() first")
        return self._capabilities

    @property
    def is_flying(self) -> bool:
        """Best-effort local tracking of airborne state (set by
        takeoff/land/emergency; not polled from the drone)."""
        return self._flying

    @property
    def latest_state(self) -> TelloState | None:
        """Most recently received state telemetry sample, or ``None``
        before the first one arrives."""
        return self._latest_state

    @property
    def command_dropped_count(self) -> int:
        """Datagrams dropped on the command channel for a spoofed source IP."""
        return self._command_endpoint.dropped_count if self._command_endpoint is not None else 0

    # ----------------------------------------------------------------
    # Connection lifecycle
    # ----------------------------------------------------------------

    async def connect(self) -> None:
        """Enter SDK mode, detect capabilities, and start background tasks.

        Raises:
            TelloConnectionError: if the drone never acknowledges SDK-mode
                entry after ``connect_retries`` attempts.
        """
        if self._connected:
            return

        if self._command_endpoint is None:
            self._command_endpoint = UdpEndpoint(
                EndpointAddress(
                    local_port=self._command_port,
                    remote_host=self._drone_host,
                    remote_port=self._command_port,
                ),
                channel_name="command",
            )
        if self._owns_command_endpoint:
            await self._command_endpoint.open()

        await self._enter_sdk_mode()

        sdk_response = await self._query_optional_unknown(protocol.cmd_sdk_query())
        sdk_version = detect_sdk_version(sdk_response)

        battery_response = await self._execute(protocol.cmd_battery_query())
        serial_number = await self._query_optional_unknown(protocol.cmd_sn_query())

        self._capabilities = build_capabilities(sdk_version, serial_number=serial_number)
        logger.info(
            "Connected to Tello: SDK %s, battery %s%%, serial %s",
            sdk_version.value,
            battery_response.value,
            serial_number or "unknown",
        )

        if self._state_endpoint is None:
            self._state_endpoint = UdpEndpoint(
                EndpointAddress(
                    local_port=self._state_port,
                    remote_host=self._drone_host,
                    remote_port=self._state_port,
                ),
                channel_name="state",
            )
        if self._owns_state_endpoint:
            await self._state_endpoint.open()

        self._state_task = asyncio.create_task(self._state_loop(), name="pytello-state")
        self._state_watchdog_task = asyncio.create_task(
            self._state_watchdog_loop(), name="pytello-state-watchdog"
        )
        self._keepalive_task = asyncio.create_task(self._keepalive_loop(), name="pytello-keepalive")

        self._connected = True

    async def _enter_sdk_mode(self) -> None:
        last_error: Exception | None = None
        command = protocol.cmd_enter_sdk_mode()
        for attempt in range(1, self._connect_retries + 1):
            try:
                response = await self._send_and_wait(command)
            except TelloTimeoutError as exc:
                last_error = exc
                logger.warning(
                    "SDK-mode entry attempt %d/%d timed out", attempt, self._connect_retries
                )
                continue
            if response.kind is ResponseKind.OK:
                return
            logger.warning(
                "SDK-mode entry attempt %d/%d got unexpected response %r "
                "(known firmware quirk: garbage after boot)",
                attempt,
                self._connect_retries,
                response.raw,
            )
            last_error = TelloCommandError(command, response.raw)
        raise TelloConnectionError(
            "Drone never acknowledged SDK-mode entry ('command') after "
            f"{self._connect_retries} attempts. Troubleshooting: (1) is this device connected "
            "to the TELLO-XXXXXX Wi-Fi network? (2) is another app or device already connected "
            "to the drone? (3) power-cycle the drone -- it can return garbage on the first "
            "packet after boot."
        ) from last_error

    async def close(self) -> None:
        """Land if airborne (bounded timeout), stop background tasks, and
        close owned sockets. Safe to call more than once."""
        if not self._connected:
            return

        if self._flying:
            try:
                await asyncio.wait_for(
                    self._execute(protocol.cmd_land()), timeout=protocol.MEDIUM_TIMEOUT_S
                )
            except TelloError as exc:
                logger.error(
                    "Failed to land cleanly during shutdown (%s); cutting motors as a last resort",
                    exc,
                )
                await self.emergency()
            finally:
                self._flying = False

        for task in (self._keepalive_task, self._state_watchdog_task, self._state_task):
            if task is not None:
                task.cancel()
        for task in (self._keepalive_task, self._state_watchdog_task, self._state_task):
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError):
                    await task

        if self._owns_command_endpoint and self._command_endpoint is not None:
            self._command_endpoint.close()
        if self._owns_state_endpoint and self._state_endpoint is not None:
            self._state_endpoint.close()

        self._connected = False

    async def __aenter__(self) -> TelloClient:
        await self.connect()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    # ----------------------------------------------------------------
    # Command serialization core
    # ----------------------------------------------------------------

    async def _send_and_wait(self, command: str, *, timeout: float | None = None) -> ParsedResponse:
        if self._connection_lost_error is not None:
            raise TelloConnectionError("Connection to drone was lost") from self._connection_lost_error
        assert self._command_endpoint is not None

        effective_timeout = timeout if timeout is not None else protocol.command_timeout(command)

        async with self._command_lock:
            self._drain_stale_responses()
            self._command_endpoint.send(command.encode("ascii"))
            self._last_command_monotonic = time.monotonic()

            if effective_timeout is None:
                return ParsedResponse(kind=ResponseKind.OK, raw="")

            try:
                raw = await asyncio.wait_for(self._command_endpoint.receive(), timeout=effective_timeout)
            except TimeoutError as exc:
                raise TelloTimeoutError(command, effective_timeout) from exc

        return protocol.parse_response(raw)

    def _drain_stale_responses(self) -> None:
        assert self._command_endpoint is not None
        while True:
            stale = self._command_endpoint.poll()
            if stale is None:
                return
            logger.warning(
                "Discarding stale response received after a previous command's timeout: %r", stale
            )

    async def _execute(self, command: str, *, timeout: float | None = None) -> ParsedResponse:
        """Send ``command``, wait for its response, and raise
        :class:`TelloCommandError` if the drone rejected it."""
        response = await self._send_and_wait(command, timeout=timeout)
        if response.kind in _FAILURE_KINDS:
            raise TelloCommandError(command, response.raw)
        return response

    async def _query_optional_unknown(self, command: str) -> str | None:
        """Send a query where ``unknown command`` is an expected, meaningful
        answer (the firmware doesn't support it) rather than a failure."""
        response = await self._send_and_wait(command)
        if response.kind is ResponseKind.UNKNOWN_COMMAND:
            return None
        if response.kind in _FAILURE_KINDS:
            raise TelloCommandError(command, response.raw)
        return response.value

    def _require_flying(self) -> None:
        if not self._flying:
            raise TelloNotFlyingError(
                "This command requires the drone to be airborne; call takeoff() first"
            )

    # ----------------------------------------------------------------
    # Background tasks
    # ----------------------------------------------------------------

    async def _keepalive_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(1.0)
                if not self._flying or self._connection_lost_error is not None:
                    continue
                if self._command_lock.locked():
                    continue  # an in-flight command already resets the drone's watchdog
                elapsed = time.monotonic() - self._last_command_monotonic
                if elapsed < self._keepalive_interval:
                    continue
                try:
                    await self._execute(protocol.cmd_battery_query())
                except TelloError as exc:
                    logger.warning("Keepalive command failed: %s", exc)
        except asyncio.CancelledError:
            raise

    async def _state_loop(self) -> None:
        assert self._state_endpoint is not None
        try:
            while True:
                raw = await self._state_endpoint.receive()
                try:
                    state = protocol.parse_state(raw)
                except ValueError as exc:
                    logger.warning("Discarding malformed state packet: %s", exc)
                    continue
                self._latest_state = state
                self._last_state_monotonic = time.monotonic()
                if self._on_state is not None:
                    self._on_state(state)
        except asyncio.CancelledError:
            raise

    async def _state_watchdog_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(1.0)
                if not self._flying or self._connection_lost_error is not None:
                    continue
                if self._last_state_monotonic is None:
                    continue
                age = time.monotonic() - self._last_state_monotonic
                if age > self._state_stale_timeout:
                    self._handle_connection_lost(
                        TelloConnectionError(
                            f"No state telemetry received for {age:.1f}s while flying "
                            f"(stale threshold {self._state_stale_timeout:.1f}s); "
                            "connection presumed lost."
                        )
                    )
        except asyncio.CancelledError:
            raise

    def _handle_connection_lost(self, error: BaseException) -> None:
        if self._connection_lost_error is not None:
            return
        self._connection_lost_error = error
        logger.error("Connection to drone lost: %s", error)
        if self._on_connection_lost is not None:
            self._on_connection_lost(error)

    # ----------------------------------------------------------------
    # Flight commands
    # ----------------------------------------------------------------

    async def takeoff(self) -> None:
        """Auto takeoff to roughly 1m. Requires SDK 1.3+.

        Raises:
            TelloCommandError: if the drone rejects takeoff (e.g. low battery).
            TelloTimeoutError: if no response arrives in time.
        """
        await self._execute(protocol.cmd_takeoff())
        self._flying = True

    async def land(self) -> None:
        """Auto land.

        Raises:
            TelloCommandError: if the drone rejects the command.
            TelloTimeoutError: if no response arrives in time.
        """
        await self._execute(protocol.cmd_land())
        self._flying = False

    async def emergency(self) -> None:
        """Immediately stop all motors. Bypasses command serialization --
        sent without waiting for the lock or for any in-flight command to
        finish, since this is the drone's panic stop. Does not raise on
        drone rejection; this is a best-effort, fire-immediately signal.
        """
        assert self._command_endpoint is not None
        self._command_endpoint.send(protocol.cmd_emergency().encode("ascii"))
        self._flying = False

    async def up(self, cm: int) -> None:
        """Ascend ``cm`` centimeters (20-500). Requires the drone to be flying."""
        self._require_flying()
        await self._execute(protocol.cmd_up(cm))

    async def down(self, cm: int) -> None:
        """Descend ``cm`` centimeters (20-500). Requires the drone to be flying."""
        self._require_flying()
        await self._execute(protocol.cmd_down(cm))

    async def left(self, cm: int) -> None:
        """Move left ``cm`` centimeters (20-500). Requires the drone to be flying."""
        self._require_flying()
        await self._execute(protocol.cmd_left(cm))

    async def right(self, cm: int) -> None:
        """Move right ``cm`` centimeters (20-500). Requires the drone to be flying."""
        self._require_flying()
        await self._execute(protocol.cmd_right(cm))

    async def forward(self, cm: int) -> None:
        """Move forward ``cm`` centimeters (20-500). Requires the drone to be flying."""
        self._require_flying()
        await self._execute(protocol.cmd_forward(cm))

    async def back(self, cm: int) -> None:
        """Move backward ``cm`` centimeters (20-500). Requires the drone to be flying."""
        self._require_flying()
        await self._execute(protocol.cmd_back(cm))

    async def cw(self, degrees: int) -> None:
        """Rotate clockwise ``degrees`` (1-360). Requires the drone to be flying."""
        self._require_flying()
        await self._execute(protocol.cmd_cw(degrees))

    async def ccw(self, degrees: int) -> None:
        """Rotate counter-clockwise ``degrees`` (1-360). Requires the drone to be flying."""
        self._require_flying()
        await self._execute(protocol.cmd_ccw(degrees))

    async def flip(self, direction: FlipDirection) -> None:
        """Flip in ``direction``. Requires the drone to be flying and
        battery above the drone's own internal threshold (it will reject
        the command with :class:`TelloCommandError` otherwise)."""
        self._require_flying()
        await self._execute(protocol.cmd_flip(direction))

    async def go(self, x: int, y: int, z: int, speed: int, mid: int | None = None) -> None:
        """Fly to relative offset ``(x, y, z)`` cm at ``speed`` cm/s.

        Each of x/y/z must be in -500..500 cm, and may not all lie within
        -20..20 cm simultaneously. ``speed`` must be 10-100 cm/s. If
        ``mid`` is given, coordinates are relative to that mission pad
        (requires SDK 2.0+ mission pad detection to be enabled first via
        :meth:`mission_pad_on`).
        """
        self._require_flying()
        await self._execute(protocol.cmd_go(x, y, z, speed, mid))

    async def curve(
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
        """Fly a curve through relative waypoint 1 to relative waypoint 2 at
        ``speed`` cm/s. See :meth:`go` for coordinate/speed ranges."""
        self._require_flying()
        await self._execute(protocol.cmd_curve(x1, y1, z1, x2, y2, z2, speed, mid))

    async def rc(self, left_right: int, forward_back: int, up_down: int, yaw: int) -> None:
        """Send one RC control-stick frame; each channel is -100..100.

        Unlike every other command, the drone does not acknowledge ``rc``
        -- this returns as soon as the datagram is sent. Callers driving a
        control loop should call this repeatedly (e.g. at 10-50 Hz); doing
        so also serves as the flight keepalive.
        """
        await self._send_and_wait(protocol.cmd_rc(left_right, forward_back, up_down, yaw))

    async def set_speed(self, speed: int) -> None:
        """Set the drone's default movement speed to ``speed`` cm/s (10-100)."""
        await self._execute(protocol.cmd_speed_set(speed))

    # ----------------------------------------------------------------
    # Reads
    # ----------------------------------------------------------------

    async def get_speed(self) -> float:
        """Query the current default movement speed in cm/s."""
        response = await self._execute(protocol.cmd_speed_query())
        return float(response.value) if response.value else 0.0

    async def get_battery(self) -> int:
        """Query remaining battery percentage (0-100)."""
        response = await self._execute(protocol.cmd_battery_query())
        return int(response.value) if response.value else 0

    async def get_flight_time(self) -> int:
        """Query current flight time in seconds."""
        response = await self._execute(protocol.cmd_time_query())
        return int(response.value) if response.value else 0

    async def get_wifi_snr(self) -> str:
        """Query the Wi-Fi signal-to-noise ratio, as reported by the drone."""
        response = await self._execute(protocol.cmd_wifi_query())
        return response.value or ""

    async def get_height(self) -> int:
        """Query current height above takeoff point, in cm."""
        response = await self._execute(protocol.cmd_height_query())
        return int(response.value) if response.value else 0

    async def get_temperature(self) -> str:
        """Query internal temperature range, as reported by the drone (deg C)."""
        response = await self._execute(protocol.cmd_temp_query())
        return response.value or ""

    async def get_attitude(self) -> str:
        """Query IMU attitude data (pitch/roll/yaw), as reported by the drone."""
        response = await self._execute(protocol.cmd_attitude_query())
        return response.value or ""

    async def get_barometer(self) -> float:
        """Query barometric altitude in meters."""
        response = await self._execute(protocol.cmd_baro_query())
        return float(response.value) if response.value else 0.0

    async def get_acceleration(self) -> str:
        """Query IMU acceleration data, as reported by the drone."""
        response = await self._execute(protocol.cmd_acceleration_query())
        return response.value or ""

    async def get_tof(self) -> int:
        """Query time-of-flight distance sensor reading, in cm."""
        response = await self._execute(protocol.cmd_tof_query())
        return int(response.value) if response.value else 0

    # ----------------------------------------------------------------
    # Mission pads (SDK 2.0+)
    # ----------------------------------------------------------------

    async def mission_pad_on(self) -> None:
        """Enable mission pad detection. Requires SDK 2.0+ (Tello EDU/TT).

        Raises:
            TelloUnsupportedCapability: on a standard Tello (SDK 1.3).
        """
        require_mission_pads(self.capabilities)
        await self._execute(protocol.cmd_mission_pad_on())

    async def mission_pad_off(self) -> None:
        """Disable mission pad detection."""
        await self._execute(protocol.cmd_mission_pad_off())

    async def mission_pad_direction(self, direction: int) -> None:
        """Set which camera(s) detect mission pads: 0=down, 1=front, 2=both.
        Requires SDK 2.0+ (Tello EDU/TT)."""
        require_mission_pads(self.capabilities)
        await self._execute(protocol.cmd_mission_pad_direction(direction))

    # ----------------------------------------------------------------
    # Video control (control-channel commands; decode lives in video.py)
    # ----------------------------------------------------------------

    async def stream_on(self) -> None:
        """Start the raw H.264 video stream on the video UDP port."""
        await self._execute(protocol.cmd_stream_on())

    async def stream_off(self) -> None:
        """Stop the video stream."""
        await self._execute(protocol.cmd_stream_off())

    async def select_camera_source(self, down: bool) -> None:
        """Switch the active camera. ``down=True`` selects the down-facing
        320x240 grayscale camera; ``down=False`` selects the front-facing
        960x720 color camera. Only one camera streams at a time.

        Raises:
            TelloUnsupportedCapability: selecting the down camera requires
                SDK 3.0 (Tello EDU with firmware >= v02.05.01.17, or
                RoboMaster TT); a standard Tello answers ``unknown command``.
        """
        if down:
            require_camera_switching(self.capabilities)
        await self._execute(protocol.cmd_downvision(down))
