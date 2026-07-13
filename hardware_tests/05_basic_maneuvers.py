"""Hardware test 5/7: basic maneuvers.

Takeoff, then a scripted sequence of up/down/left/right/forward/back/
cw/ccw and a flip, each with a short pause, then land. Exercises the
full distance/rotation command surface and its client-side validation.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _util import checklist, fail, report  # noqa: E402
from pytello import FlipDirection, Tello, TelloError  # noqa: E402

PAUSE_S = 2.0


def main() -> int:
    checklist(
        "Connected to the drone's TELLO-XXXXXX Wi-Fi network",
        "Drone on a flat, level surface",
        "At least 4x4 meters of clear space and 2+ meters of headroom",
        "Battery above 40%",
        "Flight area clear of people, pets, and fragile objects",
        "You are ready to press Ctrl-C immediately if anything looks wrong",
    )

    try:
        with Tello() as drone:
            battery = drone.get_battery()
            report("Battery check", battery >= 40, f"{battery}%")
            if battery < 40:
                return fail("Battery too low for this test.")

            print("Taking off...")
            drone.takeoff()
            time.sleep(PAUSE_S)

            steps = [
                ("up 50cm", lambda: drone.up(50)),
                ("down 50cm", lambda: drone.down(50)),
                ("left 50cm", lambda: drone.left(50)),
                ("right 50cm", lambda: drone.right(50)),
                ("forward 50cm", lambda: drone.forward(50)),
                ("back 50cm", lambda: drone.back(50)),
                ("cw 90deg", lambda: drone.cw(90)),
                ("ccw 90deg", lambda: drone.ccw(90)),
                ("flip forward", lambda: drone.flip(FlipDirection.FORWARD)),
            ]
            for name, action in steps:
                print(f"{name}...")
                action()
                report(name, True)
                time.sleep(PAUSE_S)

            print("Landing...")
            drone.land()
            report("Landing", not drone.is_flying)

        return 0
    except TelloError as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
