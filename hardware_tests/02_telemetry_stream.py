"""Hardware test 2/7: state telemetry stream. Does not fly.

Subscribes to state telemetry via ``on_state`` and verifies packets
arrive at roughly the documented ~10 Hz rate for 15 seconds, printing a
live line of battery/height/attitude/TOF as they come in.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _util import checklist, fail, report  # noqa: E402
from pytello import Tello, TelloError, TelloState  # noqa: E402

DURATION_S = 15.0


def main() -> int:
    checklist(
        "Connected to the drone's TELLO-XXXXXX Wi-Fi network",
        "Drone is powered on (stays on the ground the whole time)",
    )

    count = 0
    timestamps: list[float] = []

    def on_state(state: TelloState) -> None:
        nonlocal count
        count += 1
        timestamps.append(time.monotonic())
        print(
            f"  #{count:4d}  bat={state.bat:3d}%  h={state.h:4d}cm  tof={state.tof:4d}cm  "
            f"pitch={state.pitch:4d} roll={state.roll:4d} yaw={state.yaw:4d}",
            end="\r",
        )

    try:
        with Tello(on_state=on_state) as drone:
            print(f"Connected. SDK {drone.capabilities.sdk_version.value}.")
            print(f"Collecting state packets for {DURATION_S:.0f}s...")
            time.sleep(DURATION_S)
        print()

        report("State packets received", count > 0, f"{count} packets")
        if len(timestamps) >= 2:
            rate_hz = (len(timestamps) - 1) / (timestamps[-1] - timestamps[0])
            report("Approximate rate", 3.0 <= rate_hz <= 20.0, f"{rate_hz:.1f} Hz (expect ~10 Hz)")
        return 0
    except TelloError as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
