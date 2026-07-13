"""Hardware test 7/7: maneuvers and video streaming simultaneously.

The most demanding test in this directory: streams the front camera
while flying a short maneuver sequence, to catch any interaction between
the video decode pipeline and command serialization/timing (they run on
independent UDP channels and background tasks, but this is the final
check that they don't interfere in practice).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _util import checklist, fail, report  # noqa: E402

from pytello import Camera, Tello, TelloError  # noqa: E402

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

            print("Starting front camera stream...")
            stream = drone.start_video(camera=Camera.FRONT)
            time.sleep(1.0)  # let the first keyframe arrive

            try:
                print("Taking off...")
                drone.takeoff()
                time.sleep(PAUSE_S)

                for name, action in [
                    ("cw 90deg", lambda: drone.cw(90)),
                    ("forward 50cm", lambda: drone.forward(50)),
                    ("back 50cm", lambda: drone.back(50)),
                    ("ccw 90deg", lambda: drone.ccw(90)),
                ]:
                    print(f"{name}...")
                    action()
                    time.sleep(PAUSE_S)
                    frame = stream.latest_frame()
                    report(f"video still decoding during/after {name}", frame is not None)

                print("Landing...")
                drone.land()
            finally:
                stats = stream.stats
                report(
                    "Video stats over the flight",
                    stats.frames_decoded > 0,
                    f"{stats.frames_decoded} decoded, {stats.frames_dropped} dropped",
                )
                stream.close()

        return 0
    except TelloError as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
