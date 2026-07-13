"""Hardware test 4/7: FIRST FLIGHT TEST. Takes off, hovers, lands.

The minimal possible flight test: takeoff, hold position for a few
seconds, land. Run this before anything that maneuvers -- if the drone
can't cleanly take off and land in place, don't proceed to the scripts
that move it around.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _util import checklist, fail, report  # noqa: E402
from pytello import Tello, TelloError  # noqa: E402

HOVER_S = 5.0


def main() -> int:
    checklist(
        "Connected to the drone's TELLO-XXXXXX Wi-Fi network",
        "Drone on a flat, level surface",
        "At least 3x3 meters of clear space and 2+ meters of headroom",
        "Battery above 30%",
        "Flight area clear of people, pets, and fragile objects",
        "You are ready to press Ctrl-C immediately if anything looks wrong",
    )

    try:
        with Tello() as drone:
            battery = drone.get_battery()
            report("Battery check", battery >= 30, f"{battery}%")
            if battery < 30:
                return fail("Battery too low for this test.")

            print("Taking off...")
            drone.takeoff()
            report("Takeoff", drone.is_flying)

            print(f"Hovering for {HOVER_S:.0f}s...")
            time.sleep(HOVER_S)

            print("Landing...")
            drone.land()
            report("Landing", not drone.is_flying)

        return 0
    except TelloError as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
