"""Live telemetry printout, without flying.

Connects to the drone and prints battery, height, attitude (pitch/roll/
yaw), and time-of-flight distance as state packets arrive (~10 Hz), via
the ``on_state`` callback. Never takes off -- safe to run anywhere the
drone has power and Wi-Fi.

Run:
    python examples/04_telemetry.py

Pre-flight checklist:
    [ ] This computer is connected to the drone's TELLO-XXXXXX Wi-Fi network.
    [ ] The drone is powered on (it does not need to be airborne).
"""

from __future__ import annotations

import logging
import sys
import time

from pytello import Tello, TelloError, TelloState

CHECKLIST = """\
Pre-flight checklist:
  [ ] Connected to the drone's TELLO-XXXXXX Wi-Fi network
  [ ] Drone is powered on (stays on the ground the whole time)
"""


def print_state(state: TelloState) -> None:
    print(
        f"battery={state.bat:3d}%  height={state.h:4d}cm  tof={state.tof:4d}cm  "
        f"pitch={state.pitch:4d}  roll={state.roll:4d}  yaw={state.yaw:4d}  "
        f"temp={state.templ}-{state.temph}C",
        end="\r",
    )


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    print(CHECKLIST)

    try:
        with Tello(on_state=print_state) as drone:
            print(f"Connected. SDK {drone.capabilities.sdk_version.value}. Printing state for 30s...")
            print("(This example never takes off.)")
            time.sleep(30)
        print()
        print("Done.")
        return 0
    except TelloError as exc:
        print(f"Aborted: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print()
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
