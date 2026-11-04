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
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Africa/Lagos")
# Hosted runners stop jobs at 6 hours; leave room for setup and the last push.
MAX_MINUTES = int(os.environ.get("MAX_MINUTES", "340"))


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


def git(*args):
    return subprocess.run(["git", *args], capture_output=True, text=True)


def save(stamp):
    git("add", "data/observations.csv")
    if git("diff", "--cached", "--quiet").returncode == 0:
        return  # nothing new to commit
    git("commit", "-m", f"Log travel times {stamp}")
    for attempt in range(3):
        pull = git("pull", "--rebase", "--autostash")
        push = git("push")
        if push.returncode == 0:
            return
        print(f"push attempt {attempt + 1} failed: {pull.stderr.strip()} {push.stderr.strip()}", flush=True)
        time.sleep(10)
    print("Could not push; the rows stay committed locally and go out with the next successful push.", flush=True)


def main():
    start = datetime.now(TZ)
    deadline = start + timedelta(minutes=MAX_MINUTES)
    print(f"Collector loop started {start:%Y-%m-%d %H:%M} Lagos time, stops by {deadline:%H:%M}.", flush=True)
    while True:
        slot = next_slot(datetime.now(TZ))
        if slot > deadline:
            break
        wait = (slot - datetime.now(TZ)).total_seconds()
        if wait > 0:
            time.sleep(wait)
        # collect.py logs failed API calls as rows itself; a crash here must not end the loop.
        result = subprocess.run([sys.executable, "collect.py"])
        if result.returncode != 0:
            print(f"collect.py exited with {result.returncode} at {slot:%H:%M}", flush=True)
        save(slot.strftime("%Y-%m-%dT%H:%M%z"))
    print(f"Next slot is after this job's time limit; handing over to the queued job.", flush=True)


if __name__ == "__main__":
    main()
