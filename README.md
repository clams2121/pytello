# pytello-core

A clean-room, production-quality Python interface library for the
DJI/Ryze Tello drone family, built directly against the documented UDP
text-command SDK. Package name is `pytello-core`; the importable package
is `pytello`.

**Design philosophy: fail loud.** If the drone rejects a command, times
out, or the connected firmware lacks a capability, you get a specific
typed exception with a clear message -- never a silent no-op, a swallowed
error, or a guess.

Python 3.11+. Linux (Ubuntu) is the primary target; the library avoids
anything platform-specific where practical.

## Install

```bash
pip install pytello-core
# Only needed for the example scripts (video display, keyboard control):
pip install pytello-core[examples]
```

Core dependencies are `av` (PyAV, for H.264 decode) and `numpy`.
`opencv-python` is never imported by the library itself.

## Quickstart

```python
from pytello import Tello

with Tello() as drone:
    print(f"Battery: {drone.get_battery()}%")
    drone.takeoff()
    drone.cw(180)
    drone.forward(50)
    drone.back(50)
    drone.land()
```

That's it -- `Tello` is a synchronous facade; `with` connects on entry
and, if the drone is still airborne when the block exits (including on
an exception), attempts a bounded-timeout landing before releasing
sockets. Building an asyncio application instead? Use
`pytello.aio.TelloClient` directly -- identical method names and
semantics, `await`ed.

See `examples/` for five complete runnable scripts (basic flight, front
camera, down camera / model detection, telemetry-only, keyboard RC), and
`hardware_tests/` for manual scripts to run against real hardware,
ordered safest-first.

## Which model do you have?

Most owners don't actually know. Two ways to find out:

1. Run `examples/03_down_camera.py`. If it starts streaming the down
   camera, you have a Tello EDU on firmware >= v02.05.01.17, or a
   RoboMaster TT. If it prints an explanation and exits, you have a
   standard Tello (or an EDU on older firmware).
2. Or just connect and check `drone.capabilities.sdk_version` -- the
   library detects this automatically on every `connect()`.

## Model / SDK compatibility

| Model | SDK reported | Camera switching | Mission pads |
|---|---|---|---|
| Tello (standard) | 1.3 (`sdk?` -> `unknown command`) | No | No |
| Tello EDU, firmware < v02.05.01.17 | 2.0 | No | Yes |
| Tello EDU, firmware >= v02.05.01.17 | 3.0 | Yes | Yes |
| RoboMaster TT | 3.0 | Yes | Yes |

Calling a method that needs a capability your drone doesn't have raises
`TelloUnsupportedCapability` with a message explaining exactly why --
never a silent no-op. See `docs/protocol.md` for the full protocol
reference, including a few places where this library's behavior is a
documented simplification or where the official SDK PDFs disagree with
each other (notably: `downvision`/camera switching is not actually
documented in any of the three official PDFs this project could fetch --
see that file for details).

## Video

```python
from pytello import Tello, Camera

with Tello() as drone:
    stream = drone.start_video(camera=Camera.FRONT)
    frame = stream.latest_frame()  # BGR numpy array, or None before the first frame
    stream.close()
```

Three ways to consume a stream, usable concurrently:

- `stream.latest_frame()` -- most recent decoded frame, non-blocking.
  Best for a control loop that just wants "the current picture."
- `for frame in stream:` (sync) / `async for frame in stream:` (async) --
  every decoded frame, in order.
- `stream.raw_h264()` -- the raw undecoded elementary stream, for piping
  to ffmpeg or recording to disk without decode overhead.

Decoding uses PyAV, not `cv2.VideoCapture("udp://...")` -- the latter is
the classic source of gray frames and multi-second latency in other
Tello libraries. Joining mid-stream is handled by discarding packets
until the first keyframe; corrupt H.264 data is dropped (counted,
logged) rather than crashing the pipeline -- see `stream.stats`.

Only one camera streams at a time; switching to the down camera
(`Camera.DOWN`) requires SDK 3.0 and raises `TelloUnsupportedCapability`
otherwise.

## Security and privacy

- The Tello's Wi-Fi network is open and unauthenticated by design; this
  library does not make that worse. It **drops any inbound datagram
  whose source IP is not the configured drone address** (default
  `192.168.10.1`) on every channel -- command, state, and video -- and
  logs the drop at warning level.
- Zero telemetry, zero phone-home. The only network traffic this library
  ever sends is to the drone address you configured.
- Video and telemetry are never written to disk unless you explicitly
  build that yourself (e.g. via `stream.raw_h264()`).
- Because the drone's AP is open, anyone in Wi-Fi range can, in
  principle, join it or send it UDP packets. Fly in RF-quiet areas when
  it matters. Tello EDU/RoboMaster TT's station mode (joining an
  existing secured network instead of hosting an open AP) changes this
  threat model, but this library does not configure that for you (it
  intentionally never sends `wifi`/`ap`, which would risk locking you
  out of the drone).

## Troubleshooting

- **Nothing connects / every command times out.** Confirm this machine
  is actually joined to the drone's `TELLO-XXXXXX` Wi-Fi network (not
  just in range of it), and that no other app or device is already
  connected -- the Tello only accepts one control link at a time.
- **`TelloConnectionError` right after power-on.** The drone sometimes
  answers the very first post-boot packet with garbage; `connect()`
  already retries a bounded number of times for this, but if it still
  fails, power-cycle the drone and try again.
- **State telemetry or video never arrives, but commands work.** Check
  your firewall isn't blocking inbound UDP on ports 8890 (state) and
  11111 (video) -- commands only need 8889.
- **The drone lands on its own mid-flight.** That's the drone's own
  15-second no-command auto-land watchdog. This library runs a
  background keepalive while `is_flying` is true specifically to avoid
  this (default: send a benign `battery?` if 10s have passed with no
  other command) -- if you're still hitting it, check for exceptions
  logged under the `pytello` logger namespace, which would mean the
  keepalive itself is failing.
- **`TelloUnsupportedCapability` on camera switching or mission pads.**
  Expected on a standard Tello or an EDU on old firmware -- see the
  compatibility table above.
- **Gray/frozen video frames.** Should not happen with this library's
  PyAV-based decoder; if it does, check `stream.stats.frames_dropped`
  for packet loss and file an issue with that number.

## Logging

The library logs under the `pytello` namespace (`pytello.client`,
`pytello.transport`, `pytello.video`, `pytello.sync`) via the standard
`logging` module -- it never `print`s. Configure a handler the normal
way, e.g. `logging.basicConfig(level=logging.INFO)`.

## Development

```bash
pip install -e ".[dev]"
pytest
mypy --strict src/pytello
ruff check src tests examples hardware_tests
```
