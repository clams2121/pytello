# Original prompt

This is the original task prompt this repository was built from, preserved
verbatim for reference.

---

# Claude Code Prompt: `pytello-core` — A Reliable Python Interface Library for the DJI/Ryze Tello Drone

## 1. Role and Objective

You are an expert Python systems programmer specializing in reliable network protocol implementations and asyncio. Build a production-quality, standalone Python library for controlling the Ryze/DJI Tello drone over its Wi-Fi UDP text-command SDK. The existing ecosystem (DJITelloPy, dji-sdk/Tello-Python) is aging, inconsistently maintained, and has well-known reliability problems, especially around video decoding and error handling. This library is a clean-room implementation built directly on the documented protocol.

Working name: `pytello-core` (package importable as `pytello`). Target Python 3.11+ on Linux (Ubuntu) first; keep it cross-platform where free to do so.

**Design philosophy: FAIL LOUD.** Never swallow errors, never silently retry into ambiguity, never guess. If the drone rejects a command, times out, or the firmware lacks a capability, raise a specific typed exception with a clear message. No bare `except:`, no `pass` on errors.

## 2. Protocol Facts (authoritative — build to these)

- Control channel: UDP text commands to drone at `192.168.10.1:8889`. Responses (`ok`, `error`, or an info string) come back on the same socket.
- State telemetry: drone broadcasts a state string (`pitch:%d;roll:%d;...`) to port `8890` at ~10 Hz.
- Video: raw H.264 elementary stream over UDP to port `11111`, enabled with `streamon`, disabled with `streamoff`.
- Enter SDK mode by sending the literal command `command` first. Nothing works before this.
- **Critical timing quirk:** the Tello does not ACK a command until the maneuver *completes*. `forward 500` may take many seconds before any response arrives. There is no command queue on the drone. Timeouts must therefore be per-command-class (short for reads/sets, long for motion commands), and commands must be serialized — never send a second control command while one is in flight.
- **Watchdog:** if the drone receives no command for 15 seconds, it auto-lands. The library must run a keepalive while "flying" state is active (a benign read command like `battery?` or `rc 0 0 0 0` at a safe interval, configurable, default ~10 s), and the keepalive must never interleave with an in-flight motion command (respect the serialization lock).
- SDK versions by model: standard Tello = SDK 1.3; Tello EDU = 2.0 (3.0 with firmware ≥ v02.05.01.17); RoboMaster TT = 3.0.
- Camera switching (`downvision 1` = down camera, `downvision 0` = front) exists only on SDK 3.0 firmware (EDU with v02.05.01.17+, or TT). On a standard Tello it returns `unknown command`. Only one camera can stream at a time. The down camera is 320×240 grayscale; the front camera is 960×720 (720p) color.
- Argument ranges to validate client-side before sending: distances 20–500 cm; rotation 1–360 degrees; speed 10–100 cm/s; `rc` channel values −100…100.

Before implementing, fetch and read the official SDK documents and keep a summarized protocol reference in `docs/protocol.md`:
- SDK 1.3: https://dl-cdn.ryzerobotics.com/downloads/tello/20180910/Tello%20SDK%20Documentation%20EN_1.3.pdf
- SDK 2.0: https://dl-cdn.ryzerobotics.com/downloads/Tello/Tello%20SDK%202.0%20User%20Guide.pdf
- SDK 3.0: https://dl.djicdn.com/downloads/RoboMaster+TT/Tello_SDK_3.0_User_Guide_en.pdf

If you cannot fetch these, implement from the facts in this prompt and mark `docs/protocol.md` sections that need verification against the PDFs.

## 3. Architecture

Async core with a thin synchronous facade:

- `pytello.aio.TelloClient` — the real implementation. asyncio `DatagramProtocol` transports for command, state, and video ports. All public methods are coroutines.
- `pytello.Tello` — synchronous wrapper that owns a background thread running an event loop and delegates every call via `asyncio.run_coroutine_threadsafe`. Identical method names and semantics. This is what the examples use; most users start here.
- Internal layers, cleanly separated:
  - `transport.py` — sockets only. Binds, sends, receives datagrams.
  - `protocol.py` — command formatting, response parsing, state-string parsing, argument validation, per-command timeout table.
  - `client.py` — the async client: serialization lock, keepalive task, capability detection, connection lifecycle.
  - `video.py` — stream handling (see §5).
  - `capabilities.py` — model/SDK detection and feature gating.
  - `exceptions.py` — typed exception hierarchy: `TelloError` → `TelloConnectionError`, `TelloTimeoutError`, `TelloCommandError` (carries the raw drone response), `TelloUnsupportedCapability`, `TelloValidationError`, `TelloNotFlyingError`.

## 4. Capability Detection (the owner does not know which model they have)

On `connect()`:
1. Send `command`; retry a small bounded number of times (drone occasionally returns garbage to the first packet after boot — a known firmware quirk). If it never ACKs, raise `TelloConnectionError` with troubleshooting hints (are you on the TELLO-xxxx Wi-Fi? is another app connected?).
2. Query `sdk?`. If it answers, record the SDK version; if `unknown command`, this is a standard Tello on SDK 1.3.
3. Query `battery?`, and `sn?` where supported. Log a one-line summary: model class inferred, SDK version, firmware, battery.
4. Populate a `client.capabilities` object (e.g., `.camera_switching: bool`, `.mission_pads: bool`, `.sdk_version`). Any method that needs an absent capability raises `TelloUnsupportedCapability` immediately with a message explaining exactly why ("Camera switching requires Tello EDU/TT with firmware ≥ v02.05.01.17; this drone reports SDK 1.3").

## 5. Video

- `streamon`/`streamoff` management tied to a `VideoStream` object: `client.start_video(camera=Camera.FRONT | Camera.DOWN)`.
- Camera selection: if `Camera.DOWN` requested, check capability first; send `downvision 1`; verify the response; fail loud if rejected. Document that streams are exclusive (front OR down, never both).
- Decode with **PyAV** (`av` package) as the primary decoder — not `cv2.VideoCapture("udp://...")`, which is the main source of gray frames and latency in existing libraries. Feed the raw UDP payloads into a PyAV H.264 parser/decoder. Handle mid-GOP join (discard until first keyframe) and packet loss (drop corrupt frames, count them, expose a stats object — do not crash on a bad NAL, but do log it).
- Expose three access patterns:
  1. `stream.latest_frame()` → most recent decoded frame as a numpy array (BGR), non-blocking; ideal for control loops.
  2. `async for frame in stream:` — async iterator of decoded frames.
  3. `stream.raw_h264()` → access to the raw elementary stream for users who want to pipe it elsewhere (e.g., ffmpeg, recording) without decode overhead.
- `opencv-python` is an optional extra used only for the display examples; the core library must not require it. Dependencies: `av`, `numpy`; extras: `[examples]` adds `opencv-python`.

## 6. Safety Behavior (drone-side, non-negotiable)

- `emergency()` (immediate motor cut) must bypass the serialization lock and any queue — it sends immediately.
- Track flying state. If the process is exiting (context manager `__exit__`/`__aexit__`, `atexit`, SIGINT handler) while the drone is airborne, attempt `land` with a bounded timeout before closing sockets. Both clients are context managers.
- If the connection is lost while flying (state packets stop arriving for a configurable window), raise loudly in the background and surface it to the caller on their next call and via an optional `on_connection_lost` callback.
- Validate all arguments client-side (§2 ranges) before anything hits the network; raise `TelloValidationError`.

## 7. Security and Privacy

- The Tello's network is open and unauthenticated; the library must not make that worse. Bind receive sockets to the interface facing the drone where practical, and **drop any inbound datagram whose source IP is not the configured drone address** (default `192.168.10.1`) — on command, state, and video ports. Log dropped spoofed packets at warning level.
- Zero telemetry, zero phone-home, zero network traffic to anything except the drone. State this explicitly in the README.
- Never write video or telemetry to disk unless the user explicitly calls a recording API.
- Document the inherent risk in the README: anyone can join a Tello's open AP; recommend flying in RF-quiet areas and note that the EDU/TT station mode changes the threat model.
- No `eval`, no `exec`, no shelling out. Pin minimum dependency versions in `pyproject.toml`.

## 8. Documentation

- Every public method: docstring with args, units (cm, degrees, cm/s), valid ranges, which SDK version it requires, exceptions raised.
- Full type hints; `py.typed` marker; pass `mypy --strict` on the package.
- `README.md`: quickstart (connect → takeoff → move → land in <15 lines), model/SDK compatibility matrix, how to tell which model you own, video usage, troubleshooting section covering the classic failure modes (not on drone Wi-Fi, firewall blocking inbound UDP 8890/11111, drone returns gibberish after boot → power cycle, auto-land watchdog).
- `docs/protocol.md`: the summarized command reference per §2.

## 9. Examples (`examples/`, each runnable, each with a big docstring and a pre-flight checklist printout)

1. `01_basic_flight.py` — connect, print battery, take off, rotate 180° (`cw 180`), move forward 50 cm, move backward 50 cm, land. Exactly the canonical demo, with proper try/finally ensuring landing.
2. `02_front_camera.py` — stream front camera, display with OpenCV, 'q' to quit, ESC to emergency-land.
3. `03_down_camera.py` — attempt to switch to the down camera; on `TelloUnsupportedCapability`, print the explanation of the model/firmware requirement and exit cleanly (this doubles as the user's model-detection tool).
4. `04_telemetry.py` — print live state (battery, height, attitude, TOF) without flying.
5. `05_keyboard_rc.py` — manual `rc` control via keyboard, demonstrating the low-latency RC channel.

## 10. Testing

The user will validate against a real drone; you cannot. Therefore:
- Unit-test the protocol layer exhaustively: command formatting, response parsing (including `error`, info codes, garbage bytes, `unknown command`), state-string parsing, validation ranges, timeout table.
- Test the async client against a **mocked UDP transport** (a fake in-process endpoint that scripts drone responses, including delayed ACKs to prove the serialization lock and per-command timeouts work, dropped responses to prove timeout behavior, and spoofed-source packets to prove they're rejected). This is a test double, not a user-facing simulator — do not expose it as a public API.
- Test video decode by synthesizing a short H.264 stream with PyAV and pushing it through the pipeline chunked like UDP datagrams, including starting mid-stream.
- pytest + pytest-asyncio; aim for the protocol and client layers to be fully covered.
- Add a separate `hardware_tests/` directory of clearly-marked manual test scripts for the user to run against the real drone, ordered from safest (no-fly telemetry) to full flight.

## 11. Milestones (commit at each; do not proceed past a milestone with failing tests)

1. Project scaffolding: `pyproject.toml`, package layout, exceptions, lint (ruff) + mypy strict config, CI-ready pytest setup.
2. Protocol layer + full unit tests (no sockets yet).
3. Async transport + client: connect, SDK-mode entry with bounded retry, capability detection, command serialization, timeouts, keepalive. Mock-transport tests proving the timing quirks are handled.
4. Flight commands: takeoff, land, emergency, up/down/left/right/forward/back, cw/ccw, flip, go, rc, speed set/query, all reads. Safety exit behavior.
5. State telemetry parsing + accessors + `on_state` callback.
6. Video: raw stream capture, PyAV decode pipeline, three access patterns, camera switching with capability gating. Synthetic-stream tests.
7. Sync facade over the async core, with tests.
8. Examples 01–05 and `hardware_tests/`.
9. Documentation pass: README, protocol.md, docstring audit, mypy --strict clean, final review for any silent error handling (grep for `except` and justify every handler).

## 12. Ground Rules

- Ask before adding any dependency beyond `av` and `numpy`.
- Prefer clarity over cleverness; this library will be read and extended by its owner.
- Every background task must have an owner and a shutdown path — no orphaned tasks, no daemon threads left spinning.
- Log via the `logging` module under the `pytello` namespace; never `print` from library code.
- If any protocol detail in this prompt conflicts with the official SDK PDFs, the PDFs win — note the discrepancy in `docs/protocol.md`.
