"""Manual keyboard RC control, demonstrating the low-latency RC channel.

Unlike distance/rotation commands (``forward``, ``cw``, ...), which block
until the maneuver completes, ``rc`` is fire-and-forget: it sets the
current stick position and returns immediately, so it's suitable for a
tight control loop. This example polls the keyboard at ~20 Hz and sends
the corresponding ``rc`` frame each tick; each keypress sets a fixed
speed on that axis until a new key (or the neutral key) is pressed.

Controls (keys are discrete "set this axis" commands, not proportional):
    w / s       forward / back
    a / d       left / right
    i / k       up / down
    j / l       yaw left / yaw right
    space       neutral (zero all axes, keep hovering)
    t           takeoff
    q           land and quit
    Esc         emergency motor cut and quit

Requires the ``examples`` extra: ``pip install pytello-core[examples]``.

Run:
    python examples/05_keyboard_rc.py

Pre-flight checklist:
    [ ] This computer is connected to the drone's TELLO-XXXXXX Wi-Fi network.
    [ ] cv2 (opencv-python) is installed: pip install pytello-core[examples]
    [ ] At least 3x3 meters of clear space and 2+ meters of headroom.
    [ ] You are ready to press 'q' or Esc immediately if anything looks wrong.
"""

from __future__ import annotations

import logging
import sys

try:
    import cv2
    import numpy as np
except ImportError:
    print(
        "This example needs opencv-python. Install it with:\n"
        "  pip install pytello-core[examples]",
        file=sys.stderr,
    )
    raise SystemExit(1) from None

from pytello import Camera, Tello, TelloError

CHECKLIST = """\
Pre-flight checklist:
  [ ] Connected to the drone's TELLO-XXXXXX Wi-Fi network
  [ ] opencv-python installed (pip install pytello-core[examples])
  [ ] At least 3x3 meters of clear space and 2+ meters of headroom
  [ ] Ready to press 'q' or Esc immediately if anything looks wrong
"""

WINDOW_NAME = "pytello - keyboard rc (see terminal for controls)"
SPEED = 50  # cm/s on any single active axis

# key -> (left_right, forward_back, up_down, yaw)
KEY_TO_RC = {
    ord("w"): (0, SPEED, 0, 0),
    ord("s"): (0, -SPEED, 0, 0),
    ord("a"): (-SPEED, 0, 0, 0),
    ord("d"): (SPEED, 0, 0, 0),
    ord("i"): (0, 0, SPEED, 0),
    ord("k"): (0, 0, -SPEED, 0),
    ord("j"): (0, 0, 0, -SPEED),
    ord("l"): (0, 0, 0, SPEED),
    ord(" "): (0, 0, 0, 0),
}


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    print(CHECKLIST)
    print(__doc__)
    input("Press Enter once the checklist above is satisfied...")

    rc_state = (0, 0, 0, 0)
    blank_frame = np.zeros((480, 640, 3), dtype=np.uint8)

    try:
        with Tello() as drone:
            print(f"Battery: {drone.get_battery()}%")
            stream = drone.start_video(camera=Camera.FRONT)
            try:
                while True:
                    frame = stream.latest_frame()
                    cv2.imshow(WINDOW_NAME, frame if frame is not None else blank_frame)
                    key = cv2.waitKey(50) & 0xFF

                    if key == 27:  # Esc
                        print("Esc pressed: emergency motor cut.")
                        drone.emergency()
                        break
                    if key == ord("q"):
                        print("Landing and quitting.")
                        if drone.is_flying:
                            drone.rc(0, 0, 0, 0)
                            drone.land()
                        break
                    if key == ord("t") and not drone.is_flying:
                        print("Taking off.")
                        drone.takeoff()
                        continue
                    if key in KEY_TO_RC:
                        rc_state = KEY_TO_RC[key]

                    if drone.is_flying:
                        drone.rc(*rc_state)
            finally:
                stream.close()
                cv2.destroyAllWindows()

        print("Done.")
        return 0
    except TelloError as exc:
        print(f"Aborted: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
