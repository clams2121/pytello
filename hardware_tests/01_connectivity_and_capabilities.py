"""Hardware test 1/7: connectivity and capability detection. Does not fly.

Connects to the drone, verifies SDK-mode entry succeeded, and prints the
detected capabilities (SDK version, camera switching, mission pads),
battery, and serial number. This is the first thing to run against any
new (or newly power-cycled) drone.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _util import checklist, fail, report  # noqa: E402

from pytello import Tello, TelloError  # noqa: E402


def main() -> int:
    checklist(
        "Connected to the drone's TELLO-XXXXXX Wi-Fi network",
        "Drone is powered on and stationary",
    )

    try:
        with Tello() as drone:
            report("SDK-mode entry", True)
            caps = drone.capabilities
            print(f"  SDK version:      {caps.sdk_version.value}")
            print(f"  Camera switching:  {caps.camera_switching}")
            print(f"  Mission pads:      {caps.mission_pads}")
            print(f"  Serial number:     {caps.serial_number or '(not reported)'}")

            battery = drone.get_battery()
            report("battery? query", 0 <= battery <= 100, f"{battery}%")

            flight_time = drone.get_flight_time()
            report("time? query", flight_time >= 0, f"{flight_time}s")

            wifi_snr = drone.get_wifi_snr()
            report("wifi? query", bool(wifi_snr), wifi_snr)

        report("Clean shutdown", True)
        return 0
    except TelloError as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
