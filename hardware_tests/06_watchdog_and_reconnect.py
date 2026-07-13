"""Hardware test 6/7: keepalive vs. the drone's auto-land watchdog.

The Tello auto-lands if it receives no command for 15 seconds. This test
takes off and then does *nothing itself* for 25 seconds -- if the
library's keepalive is working, the client's own background task sends
a benign command periodically and the drone stays airborne the whole
time; if it isn't, the drone will auto-land partway through and the
final ``land()`` call will simply be a no-op on an already-landed drone.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _util import checklist, fail, report  # noqa: E402
from pytello import Tello, TelloError  # noqa: E402

WAIT_S = 25.0


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

            print(f"Waiting {WAIT_S:.0f}s (longer than the drone's 15s auto-land watchdog)...")
            print("Doing nothing but watching -- the library's keepalive should carry this.")
            for remaining in range(int(WAIT_S), 0, -1):
                print(f"  {remaining:3d}s remaining, still flying: {drone.is_flying}", end="\r")
                time.sleep(1.0)
            print()

            report(
                "Still flying after 25s idle",
                drone.is_flying,
                "keepalive kept the drone airborne past the 15s watchdog"
                if drone.is_flying
                else "drone auto-landed -- keepalive did not fire in time",
            )

            if drone.is_flying:
                print("Landing...")
                drone.land()

        return 0
    except TelloError as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
