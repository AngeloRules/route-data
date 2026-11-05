"""Keep one GitHub Actions job alive for several hours and run collect.py at every sampling slot.

GitHub's own scheduler often starts runs late or skips them. Instead, each job runs for up to
MAX_MINUTES, sleeps until the next slot, collects, commits and pushes, and repeats. An hourly
"watchdog" schedule in the workflow keeps the next job queued, so a fresh one takes over as soon as
the current one finishes.

Sampling slots, in Lagos time:
  weekdays 06:00-09:59   every 10 minutes (morning rush)
  05:00-21:59 otherwise  every 20 minutes
  22:00-04:59            every hour, on the hour
"""
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Africa/Lagos")
# Hosted runners stop jobs at 6 hours; leave room for setup and the last push.
MAX_MINUTES = int(os.environ.get("MAX_MINUTES", "340"))
BRANCH = os.environ.get("BRANCH", "main")


def is_slot(t):
    """True if local time t (whole minute) is a sampling slot."""
    if t.weekday() < 5 and 6 <= t.hour < 10:
        return t.minute % 10 == 0
    if 5 <= t.hour < 22:
        return t.minute % 20 == 0
    return t.minute == 0


def next_slot(now):
    t = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
    while not is_slot(t):
        t += timedelta(minutes=1)
    return t


CSV = Path(__file__).resolve().parent / "data" / "observations.csv"


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


def read_lines():
    return CSV.read_text().splitlines(keepends=True) if CSV.exists() else []


def save(pending, stamp):
    """Push the pending rows. Returns True on success.

    Never rebases or merges the CSV, so a change to the file on GitHub can't cause a conflict:
    start from GitHub's latest copy, append our rows to it, commit, push. Rows that fail to push stay
    in `pending` and are retried at the next slot.
    """
    header = None
    for attempt in range(3):
        git("rebase", "--abort")  # clear any half-finished rebase from an older version of this script
        fetch = git("fetch", "origin", BRANCH)
        if fetch.returncode != 0:
            print(f"fetch attempt {attempt + 1} failed: {fetch.stderr.strip()}", flush=True)
            time.sleep(10)
            continue
        git("reset", "--hard")
        git("checkout", "-B", BRANCH, f"origin/{BRANCH}")
        lines = read_lines()
        if not lines:
            header = header or pending_header()
            lines = [header]
        if not lines[-1].endswith("\n"):
            lines[-1] += "\n"
        CSV.parent.mkdir(parents=True, exist_ok=True)
        CSV.write_text("".join(lines + pending))
        git("add", str(CSV))
        git("commit", "-m", f"Log travel times {stamp}")
        push = git("push", "origin", f"HEAD:{BRANCH}")
        if push.returncode == 0:
            return True
        print(f"push attempt {attempt + 1} failed: {push.stderr.strip()}", flush=True)
        time.sleep(10)
    return False


def pending_header():
    import collect  # only needed when the CSV doesn't exist on GitHub yet
    return ",".join(collect.COLUMNS) + "\n"


def main():
    start = datetime.now(TZ)
    deadline = start + timedelta(minutes=MAX_MINUTES)
    print(f"Collector loop started {start:%Y-%m-%d %H:%M} Lagos time, stops by {deadline:%H:%M}.", flush=True)
    pending = []  # rows collected but not yet on GitHub
    while True:
        slot = next_slot(datetime.now(TZ))
        if slot > deadline:
            break
        wait = (slot - datetime.now(TZ)).total_seconds()
        if wait > 0:
            time.sleep(wait)
        before = read_lines()
        # collect.py logs failed API calls as rows itself; a crash here must not end the loop.
        result = subprocess.run([sys.executable, "collect.py"])
        if result.returncode != 0:
            print(f"collect.py exited with {result.returncode} at {slot:%H:%M}", flush=True)
        after = read_lines()
        pending += after[max(len(before), 1):]  # new rows only, never the header
        if pending:
            if save(pending, slot.strftime("%Y-%m-%dT%H:%M%z")):
                pending = []
            else:
                print(f"{len(pending)} rows not pushed yet; retrying at the next slot.", flush=True)
    if pending and not save(pending, "final"):
        print(f"::error::{len(pending)} rows could not be pushed and are lost:", flush=True)
        print("".join(pending), flush=True)
        sys.exit(1)
    print("Next slot is after this job's time limit; handing over to the queued job.", flush=True)

if __name__ == "__main__":
    main()
