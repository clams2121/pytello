"""Hardware test 3/7: video streaming. Does not fly.

Starts the front camera, waits for frames to decode, and reports
throughput/drop stats. Then attempts the down camera: on a capable drone
(SDK 3.0) it repeats the same check; on a standard Tello it should raise
``TelloUnsupportedCapability``, which this script treats as an expected,
informative result rather than a failure.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _util import checklist, fail, report  # noqa: E402
from pytello import Camera, Tello, TelloError, TelloUnsupportedCapability  # noqa: E402

STREAM_DURATION_S = 8.0


def run_camera_check(drone: Tello, camera: Camera) -> None:
    stream = drone.start_video(camera=camera)
    try:
        deadline = time.monotonic() + STREAM_DURATION_S
        while time.monotonic() < deadline:
            time.sleep(0.2)
        stats = stream.stats
        frame = stream.latest_frame()
        report(
            f"{camera.value} camera decoded frames",
            stats.frames_decoded > 0,
            f"{stats.frames_decoded} decoded, {stats.frames_dropped} dropped, "
            f"{stats.packets_received} packets",
        )
        if frame is not None:
            report(f"{camera.value} camera frame shape", True, str(frame.shape))
    finally:
        stream.close()


def main() -> int:
    checklist(
        "Connected to the drone's TELLO-XXXXXX Wi-Fi network",
        "Drone is powered on (stays on the ground the whole time)",
    )

    try:
        with Tello() as drone:
            print("Starting front camera...")
            run_camera_check(drone, Camera.FRONT)
            drone.stop_video()

            print("Attempting down camera...")
            try:
                run_camera_check(drone, Camera.DOWN)
                drone.stop_video()
            except TelloUnsupportedCapability as exc:
                report(
                    "down camera",
                    True,
                    f"correctly rejected -- {exc}",
                )
        return 0
    except TelloError as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
