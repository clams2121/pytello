"""Live front-camera video display.

Connects to the drone (without taking off), streams the front camera
(960x720 color), and displays it in an OpenCV window. Press 'q' to quit
normally, or Esc to trigger an emergency motor cut (useful if you've also
taken off manually and something is going wrong).

Requires the ``examples`` extra: ``pip install pytello-core[examples]``.

Run:
    python examples/02_front_camera.py

Pre-flight checklist:
    [ ] This computer is connected to the drone's TELLO-XXXXXX Wi-Fi network.
    [ ] cv2 (opencv-python) is installed: pip install pytello-core[examples]
"""

from __future__ import annotations

import logging
import sys

try:
    import cv2
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
  [ ] This example does not take off -- video only
"""

WINDOW_NAME = "pytello - front camera (q: quit, Esc: emergency)"


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    print(CHECKLIST)

    try:
        with Tello() as drone:
            print(f"Battery: {drone.get_battery()}%")
            stream = drone.start_video(camera=Camera.FRONT)
            try:
                print("Streaming front camera. Press 'q' to quit, Esc for emergency stop.")
                while True:
                    frame = stream.latest_frame()
                    if frame is None:
                        if cv2.waitKey(1) & 0xFF == ord("q"):
                            break
                        continue

                    cv2.imshow(WINDOW_NAME, frame)
                    key = cv2.waitKey(1) & 0xFF
                    if key == ord("q"):
                        break
                    if key == 27:  # Esc
                        print("Esc pressed: emergency motor cut.")
                        drone.emergency()
                        break
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
