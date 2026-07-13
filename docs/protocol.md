# Tello UDP text-command protocol reference

Summarized from the three official Ryze/DJI SDK PDFs, fetched and
text-extracted directly for this document:

- SDK 1.3: `Tello SDK Documentation EN_1.3.pdf` (2018)
- SDK 2.0: `Tello SDK 2.0 User Guide.pdf` (2018)
- SDK 3.0: `Tello_SDK_3.0_User_Guide_en.pdf` / "RoboMaster TT SDK 3.0 User Guide" (2021)

Where the PDFs disagree with each other, or with the working assumptions
this library started from, it's called out explicitly below rather than
silently picked. Sections marked **UNVERIFIED** could not be confirmed
against the official text and should be checked against a real drone if
they matter to you.

## Architecture

Three independent UDP channels, all to/from the drone at `192.168.10.1`:

| Channel | Direction | Port | Purpose |
|---|---|---|---|
| Command | bidirectional | 8889 | Text commands out, `ok`/`error`/info responses back |
| State | drone -> client | 8890 | `key:value;...` telemetry string, ~10 Hz |
| Video | drone -> client | 11111 | Raw H.264 elementary stream |

Send the literal command `command` on the command channel before
anything else, to enter SDK mode. All three PDFs describe this
identically.

## Command types and responses

- **Control commands** (`takeoff`, `land`, ...): respond `ok` or `error`
  (optionally with a reason/result code appended, e.g. `error Not
  joystick`).
- **Set commands** (`speed x`, `rc a b c d`, ...): same response shape as
  control commands. **Exception:** the SDK 2.0/3.0 PDFs both mark `rc`'s
  response as "No response" -- unlike every other command, the drone
  does not acknowledge it. This library never waits for a response to
  `rc` (`pytello.protocol.NO_RESPONSE_COMMANDS`).
- **Read commands** (`battery?`, `sdk?`, ...): respond with the current
  value as plain text, or `error`/`unknown command` if unsupported.

A standard Tello (SDK 1.3) answers `unknown command` to any command it
doesn't recognize, including `sdk?` itself -- which is how this library
infers "SDK 1.3" when `sdk?` comes back unrecognized
(`pytello.capabilities.detect_sdk_version`).

## Timing and the auto-land watchdog

All three PDFs state the same safety rule: **if the drone receives no
command for 15 seconds, it lands automatically.** The SDK 3.0 PDF adds
one nuance: a `battery?` sent by an attached open-source ESP32
controller doesn't count as resetting this timer -- not relevant to this
library, which only ever runs one control link.

None of the PDFs give explicit per-command response-time bounds. The
working assumption from the design brief -- that the drone does not ack
a motion command (`up`, `forward`, `flip`, `go`, `curve`, ...) until the
maneuver *completes*, so those need much longer timeouts than reads --
is **not directly stated** in any PDF and is **UNVERIFIED** against real
hardware. `pytello.protocol.command_timeout()` encodes it as a
three-tier table (10s reads/sets, 15s takeoff/land/emergency/stream
on/off, 25s motion commands); tune `TelloClient`'s constructor if your
drone's actual behavior differs.

## Control commands

| Command | Description | Range | SDK |
|---|---|---|---|
| `command` | Enter SDK mode | -- | 1.3+ |
| `takeoff` | Auto takeoff | -- | 1.3+ |
| `land` | Auto land | -- | 1.3+ |
| `streamon` | Start video stream | -- | 1.3+ |
| `streamoff` | Stop video stream | -- | 1.3+ |
| `emergency` | Stop all motors immediately | -- | 1.3+ |
| `up x` | Ascend x cm | 20-500 | 1.3+ |
| `down x` | Descend x cm | 20-500 | 1.3+ |
| `left x` | Fly left x cm | 20-500 | 1.3+ |
| `right x` | Fly right x cm | 20-500 | 1.3+ |
| `forward x` | Fly forward x cm | 20-500 | 1.3+ |
| `back x` | Fly back x cm | 20-500 | 1.3+ |
| `cw x` | Rotate clockwise x degrees | 1-360 | 1.3+ |
| `ccw x` | Rotate counter-clockwise x degrees | 1-360 | 1.3+ |
| `flip x` | Flip: `l`/`r`/`f`/`b` | -- | 1.3+ |
| `go x y z speed [mid]` | Fly to relative (x,y,z) at speed cm/s | see below | 1.3+ (mid: 2.0+) |
| `curve x1 y1 z1 x2 y2 z2 speed [mid]` | Fly a curve through two waypoints | see below | 1.3+ (mid: 2.0+) |
| `stop` | Hover in place immediately | -- | 2.0+ |
| `jump x y z speed yaw mid1 mid2` | Fly to a point relative to one mission pad, then reorient relative to a second | -500-500 / 10-100 | 2.0+, requires mission pads |

**Not implemented by this library** (out of scope for the brief this was
built from): `stop`, `jump`, and the RoboMaster-TT-only `motoron`,
`motoroff`, `throwfly`, `reboot`, `wifisetchannel`, `port`, `setfps`,
`setbitrate`, `setresolution`, and the `EXT ...` LED/matrix-display
commands (TT expansion board only).

### `cw`/`ccw` range discrepancy

The SDK 2.0 and SDK 3.0 PDFs both give `x = 1-360`. The **SDK 1.3 PDF
says `x: 1-3600`** (an extra trailing zero) for both `cw` and `ccw`.
This library validates 1-360, matching the two newer PDFs and the
well-established real-world behavior of a single rotation command (a
drone can't physically execute a >360-degree turn as one command); the
`1-3600` in the 1.3 PDF is treated as a documentation typo, not honored.

### `go`/`curve` coordinate range discrepancy by SDK version

- **SDK 1.3 PDF**: `go x y z speed` -- `x`, `y`, `z` each **20-500**
  (positive only, matching the basic movement commands).
- **SDK 2.0 / 3.0 PDFs**: `go x y z speed [mid]` -- `x`, `y`, `z` each
  **-500 to 500** (bidirectional). All three PDFs agree `x`/`y`/`z` may
  not all lie within -20..20 simultaneously.

This library validates against the more permissive **-500 to 500**
range uniformly (`pytello.protocol.validate_go_coordinates`), regardless
of detected SDK version. A standard SDK 1.3 drone sent a negative
coordinate will reject it with the drone's own `error`/`out of range`
response, which surfaces as `TelloCommandError` -- fail loud, just one
step later than client-side validation would catch it on that specific
firmware. Implementing per-SDK-version range gating was judged not
worth the added complexity for this edge case.

### `curve` speed range

All three PDFs agree: `curve`'s `speed` is **10-60 cm/s** -- narrower
than every other speed-taking command (`go`, `speed`, both 10-100).
This is validated separately (`validate_curve_speed`) from `go`'s speed
validation. The SDK 2.0/3.0 PDFs also note: if the resulting arc's
radius is not within 0.5-10 meters, the drone rejects the command (no
client-side check for this -- it depends on both waypoints together in a
way that's easiest to just let the drone judge).

### Mission-pad `go`/`curve`/`z` range (SDK 3.0 PDF only) -- **UNVERIFIED, not implemented**

The SDK 3.0 PDF's `go x y z speed mid` and `curve ... mid` table rows
give `z: 0-500` (not -500 to 500) when a mission pad ID is supplied,
differing from the SDK 2.0 PDF's `z: -500-500` for the same mid-relative
commands. This library does not special-case validation when `mid` is
passed; it uses the same -500..500 range as the non-mid form. Flag this
if you rely on mission-pad-relative negative-z moves on SDK 3.0 firmware.

## Set commands

| Command | Description | Range | SDK |
|---|---|---|---|
| `speed x` | Set default movement speed | 10-100 cm/s | 1.3+ |
| `rc a b c d` | RC stick frame: left/right, forward/back, up/down, yaw | -100 to 100 each | 1.3+ |
| `wifi ssid pass` | Set Wi-Fi SSID/password | -- | 1.3+ |
| `mon` | Enable mission pad detection | -- | 2.0+ |
| `moff` | Disable mission pad detection | -- | 2.0+ |
| `mdirection x` | Select detection camera(s): 0=down, 1=forward, 2=both | -- | 2.0+ |
| `ap ssid pass` | Station mode: connect to an access point | -- | 2.0+ |

`rc`'s four channels are, per the SDK 1.3/2.0 PDFs, `a`=left/right,
`b`=forward/backward, `c`=up/down, `d`=yaw -- matching this library's
`rc(left_right, forward_back, up_down, yaw)` parameter order. The SDK
3.0 PDF renames the same four channels to roll/pitch/throttle/yaw
(RC-stick terminology); the axis order and semantics are identical, only
the naming differs.

`mon` default detection direction differs by SDK version per the PDFs:
the **SDK 2.0 PDF** says `mon` enables both forward and downward
detection by default; the **SDK 3.0 PDF** says `mon` enables **downward
only** by default (use `mdirection` afterward to change it). This is
drone-side behavior, not something this library validates or works
around -- noted here so it isn't a surprise.

### Camera switching (`downvision`) -- **not found in any of the three official PDFs**

The design brief this library was built from specifies a `downvision 1`
/ `downvision 0` command to switch between the down-facing (320x240
grayscale) and front-facing (960x720 color) cameras, gated to SDK 3.0
firmware. **This command does not appear anywhere in the text of any of
the three official SDK PDFs fetched for this document** -- confirmed by
grepping the extracted text of all three for "vision" and finding no
match outside one unrelated sentence ("downward camera recognition" in
the SDK 3.0 PDF's mission-pad section).

The SDK 3.0 PDF does mention a separate, richer "RoboMaster SDK"
(`https://robomaster-dev.readthedocs.io/latest/`) layered on top of the
raw UDP text protocol for the TT specifically; `downvision` may live
there, or in a firmware/doc revision not captured by the PDF snapshot
this document was built from, or it may not exist as documented in the
original brief. `downvision` is nonetheless a command referenced by
other community Tello libraries for EDU/TT hardware.

**This library keeps the `downvision` implementation as specified** in
the design brief (`pytello.aio.client.TelloClient.select_camera_source`,
gated on `capabilities.camera_switching`, itself gated on `sdk?`
reporting SDK 3.0). If you have a capable drone, please verify this
command actually works against your hardware -- if it doesn't, the
drone will answer `unknown command` or `error`, which surfaces as
`TelloUnsupportedCapability` or `TelloCommandError` rather than silently
doing nothing.

## Read commands

| Command | Description | Response | SDK |
|---|---|---|---|
| `speed?` | Current set speed | x (cm/s) | 1.3+ |
| `battery?` | Battery percentage | x (0-100) | 1.3+ |
| `time?` | Motor running time | seconds | 1.3+ |
| `height?` | Height | x cm (0-3000) | 1.3+ |
| `temp?` | Internal temperature | x degC (0-90) | 1.3+ |
| `attitude?` | IMU attitude | "pitch roll yaw" | 1.3+ |
| `baro?` | Barometer reading | x -- unit ambiguous, see below | 1.3+ |
| `acceleration?` | IMU angular acceleration | "x y z" (0.001g per SDK 1.3 PDF) | 1.3+ |
| `tof?` | Time-of-flight distance | x cm (30-1000) | 1.3+ |
| `wifi?` | Wi-Fi SNR | snr | 1.3+ |
| `sdk?` | SDK version | version string | 2.0+ (unrecognized on 1.3 -- see below) |
| `sn?` | Serial number | serial | 1.3+ |

### `sdk?` on a standard Tello

The SDK 1.3 PDF does not list `sdk?` at all. In practice (and per the
design brief), a standard Tello answers `unknown command` to `sdk?`,
which this library treats as the signal that it's talking to SDK 1.3
(`detect_sdk_version(None) -> SdkVersion.V1_3`). **UNVERIFIED** against
real 1.3 hardware by this project, since none was available.

### `baro?` / state `baro` unit -- ambiguous even within the official PDFs

- SDK 1.3 PDF, read-command table: "get barometer value **(m)**".
- SDK 1.3 PDF, state-field description: "baro: Barometer measurement,
  **cm**" -- for the identically-named field.
- SDK 3.0 PDF, state-field description: "baro: Height detected by
  barometer **(m)**".

These three descriptions do not agree with each other. This library
does not convert or annotate the unit beyond flagging it (see
`TelloState.baro`, `TelloClient.get_barometer`) -- treat the raw value
as unverified until you've checked it against a known altitude on your
own drone.

## State telemetry

Drone broadcasts a `key:value;key:value;...\r\n` string to port 8890 at
~10 Hz. Per the SDK 1.3/2.0 PDFs (no mission pad detection enabled):

```
pitch:%d;roll:%d;yaw:%d;vgx:%d;vgy:%d;vgz:%d;templ:%d;temph:%d;tof:%d;h:%d;bat:%d;baro:%.2f;time:%d;agx:%.2f;agy:%.2f;agz:%.2f;
```

With mission pad detection enabled (SDK 2.0+, after `mon`), the SDK 3.0
PDF's example prepends mission-pad fields:

```
mid:%d;x:%d;y:%d;z:%d;mpry:%d,%d,%d;pitch:%d;...
```

| Field | Meaning | Unit |
|---|---|---|
| `pitch`, `roll`, `yaw` | Attitude | degrees |
| `vgx`, `vgy`, `vgz` | Speed per axis | dm/s (SDK 3.0 PDF; SDK 1.3 PDF gives no unit) |
| `templ`, `temph` | Min/max internal temperature | Celsius |
| `tof` | Time-of-flight distance | cm |
| `h` | Height above takeoff point | cm |
| `bat` | Battery | % |
| `baro` | Barometer reading | ambiguous -- see above |
| `time` | Motor running time | seconds |
| `agx`, `agy`, `agz` | Acceleration per axis | cm/s^2 (SDK 3.0 PDF; SDK 1.3 PDF gives no unit) |
| `mid` | Detected mission pad ID | -1 = none detected, -2 = detection disabled (SDK 3.0 PDF) |
| `x`, `y`, `z` | Position relative to detected pad | cm |
| `mpry` | Pitch, roll, yaw relative to the pad | degrees |

`pytello.protocol.parse_state` treats every field except the
mission-pad ones as required, and raises `ValueError` (which the client
layer logs and discards the sample for, rather than crashing the state
loop) if one is missing or unparseable.

## Safety / misc

- **Auto-land watchdog**: 15 seconds without a command. See "Timing"
  above.
- **Wi-Fi reset**: hold the power button 5 seconds while powered on;
  indicator flashes yellow when reset to factory SSID/password (no
  password by default). Not something this library automates -- it's a
  physical-button action.
- This library never sends `wifi`, `ap`, or any command that would
  change the drone's own network configuration -- out of scope, and
  arguably dangerous to automate (could lock you out of the drone).
