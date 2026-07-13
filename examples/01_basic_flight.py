"""Canonical basic-flight demo.

Connects to the drone, prints the battery level, takes off, rotates 180
degrees, flies forward 50 cm and back 50 cm, then lands. Landing is
guaranteed by a ``try``/``finally`` block (and by the ``with`` context
manager itself, which lands automatically on exit if still airborne) --
if anything goes wrong mid-flight, the drone lands rather than being left
hovering or falling back to its 15-second auto-land watchdog.

Run:
    python examples/01_basic_flight.py

Pre-flight checklist (printed again at startup):
    [ ] This computer is connected to the drone's TELLO-XXXXXX Wi-Fi network.
    [ ] The drone is on a flat, level surface with at least 3x3 meters of
        clear space around it and 2+ meters of headroom.
    [ ] Battery is above 20%.
    [ ] No people, pets, or fragile objects in the flight area.
"""

from __future__ import annotations

import logging
import sys

from pytello import Tello, TelloError

CHECKLIST = """\
Pre-flight checklist:
  [ ] Connected to the drone's TELLO-XXXXXX Wi-Fi network
  [ ] Drone on a flat, level surface
  [ ] At least 3x3 meters of clear space and 2+ meters of headroom
  [ ] Battery above 20%
  [ ] Flight area clear of people, pets, and fragile objects
"""


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    print(CHECKLIST)
    input("Press Enter once the checklist above is satisfied...")

    try:
        with Tello() as drone:
            battery = drone.get_battery()
            print(f"Battery: {battery}%")
            if battery < 20:
                print("Battery too low for a safe flight; aborting.")
                return 1

            print("Taking off...")
            drone.takeoff()

            print("Rotating 180 degrees clockwise...")
            drone.cw(180)

            print("Flying forward 50 cm...")
            drone.forward(50)

            print("Flying back 50 cm...")
            drone.back(50)

            print("Landing...")
            drone.land()

        print("Done.")
        return 0
    except TelloError as exc:
        print(f"Flight aborted: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
