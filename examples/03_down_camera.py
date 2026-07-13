"""Down-camera capability probe / model-detection tool.

Connects to the drone and attempts to switch to the down-facing camera
(320x240 grayscale). This only works on a Tello EDU with firmware
>= v02.05.01.17, or a RoboMaster TT (SDK 3.0) -- a standard Tello (SDK
1.3) will cause this to raise ``TelloUnsupportedCapability``. Since most
owners don't actually know which model/firmware they have, running this
script is a quick way to find out: if it prints the explanation and
exits cleanly, you have a standard Tello (or an EDU on old firmware); if
it starts streaming, you have a capable model.

Requires the ``examples`` extra: ``pip install pytello-core[examples]``.

Run:
    python examples/03_down_camera.py

Pre-flight checklist:
    [ ] This computer is connected to the drone's TELLO-XXXXXX Wi-Fi network.
    [ ] cv2 (opencv-python) is installed: pip install pytello-core[examples]
    [ ] This example does not take off -- video only
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

from pytello import Camera, Tello, TelloError, TelloUnsupportedCapability

CHECKLIST = """\
Pre-flight checklist:
  [ ] Connected to the drone's TELLO-XXXXXX Wi-Fi network
  [ ] opencv-python installed (pip install pytello-core[examples])
  [ ] This example does not take off -- video only
"""

WINDOW_NAME = "pytello - down camera (q: quit)"


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    print(CHECKLIST)

    try:
        with Tello() as drone:
            print(f"Detected SDK version: {drone.capabilities.sdk_version.value}")
            print(f"Battery: {drone.get_battery()}%")

            try:
                stream = drone.start_video(camera=Camera.DOWN)
            except TelloUnsupportedCapability as exc:
                print()
                print("This drone does not support the down-facing camera:")
                print(f"  {exc}")
                print()
                print(
                    "That means you have a standard Tello, or a Tello EDU on firmware "
                    "older than v02.05.01.17. Camera switching requires a Tello EDU with "
                    "firmware >= v02.05.01.17, or a RoboMaster TT."
                )
                return 0

            try:
                print("Down camera active (320x240 grayscale). Press 'q' to quit.")
                while True:
                    frame = stream.latest_frame()
                    if frame is not None:
                        cv2.imshow(WINDOW_NAME, frame)
                    if cv2.waitKey(1) & 0xFF == ord("q"):
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
