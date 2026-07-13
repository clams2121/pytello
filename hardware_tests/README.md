# Hardware tests

These are **manual scripts to run against a real drone**, not part of the
`pytest` suite (nothing in `tests/` touches real hardware, per the design
brief -- these exist because there's no way to simulate a real drone's
timing quirks, RF behavior, or camera output).

Run them **in order**. Each one only exercises what the previous ones
already proved works, so if something fails you'll know roughly where.

| # | Script | Flies? | What it checks |
|---|--------|--------|-----------------|
| 01 | `01_connectivity_and_capabilities.py` | No | SDK-mode entry, capability detection, battery/SN reads |
| 02 | `02_telemetry_stream.py` | No | State telemetry arrives at ~10 Hz and parses cleanly |
| 03 | `03_video_stream_no_fly.py` | No | Front-camera video decodes; down-camera capability gating |
| 04 | `04_hover_and_land.py` | **Yes** (minimal) | Takeoff, brief hover, land |
| 05 | `05_basic_maneuvers.py` | **Yes** | up/down/left/right/forward/back/cw/ccw, flip |
| 06 | `06_watchdog_and_reconnect.py` | **Yes** (~25s) | Keepalive avoids the drone's 15s auto-land watchdog |
| 07 | `07_full_flight_and_video.py` | **Yes** | Maneuvers + live video simultaneously |

Run each with:

```bash
python hardware_tests/01_connectivity_and_capabilities.py
```

Every script prints its own pre-flight checklist and waits for you to
press Enter before doing anything on the network. Flight scripts
additionally require a clear 3x3 meter area with 2+ meters of headroom.
Keep your hand near the power button / be ready to hit Esc or Ctrl-C
(both trigger a safety-net landing or emergency stop) for every flight
script.
