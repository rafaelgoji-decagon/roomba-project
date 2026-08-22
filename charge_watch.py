"""Poll Roomba battery/charging sensors until Ctrl-C."""

import sys
import time
from datetime import datetime

from roomba import Roomba, find_port

POLL = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0


def verdict(amps: float, state: str) -> str:
    if state in ("full charging", "trickle charging", "reconditioning"):
        return f"CHARGING ({state})"
    if state == "charging fault":
        return "FAULT — battery or contacts, not a software problem"
    if state == "waiting":
        return "on dock, waiting to start (battery too hot/cold, or settling)"
    if amps > 0.05:
        return "CHARGING (current flowing in)"
    if amps < -0.05:
        return "NOT charging — running on battery"
    return "NOT charging — no current either way, check dock contacts"


def main() -> None:
    port = find_port()
    print(f"Connecting on {port} ... (press CLEAN first if this times out)\n")
    first_mah = None
    started = time.time()
    with Roomba(port) as bot:
        print(f"{'time':>8}  {'volts':>6}  {'amps':>7}  {'mAh':>6}  {'%':>5}  {'C':>3}  state")
        print("-" * 78)
        while True:
            try:
                b = bot.battery()
            except TimeoutError as error:
                print(f"  timeout: {error}")
                time.sleep(POLL)
                continue
            if first_mah is None:
                first_mah = b["charge_mah"]
            print(
                f"{datetime.now():%H:%M:%S}  {b['volts']:6.2f}  {b['amps']:7.3f}  "
                f"{b['charge_mah']:6}  {b['percent']:5.1f}  {b['temp_c']:3}  {b['charging']}"
            )
            mins = (time.time() - started) / 60
            if mins > 3:
                gained = b["charge_mah"] - first_mah
                print(f"          -> {verdict(b['amps'], b['charging'])}  |  {gained:+d} mAh in {mins:.0f} min")
            time.sleep(POLL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nstopped.")
