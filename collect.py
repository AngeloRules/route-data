"""Measure both routes back-to-back and append one row per route to data/observations.csv.

Both routes in a run share a run_id and are requested seconds apart, so each run is a matched pair:
the same traffic moment measured on both routes. That pairing is what the analysis tests on.

Providers (set ROUTE_PROVIDER):
  mapbox (default) - needs MAPBOX_TOKEN. Mapbox Directions API, driving-traffic profile.
  google           - needs GOOGLE_MAPS_API_KEY. Google Routes API, TRAFFIC_AWARE_OPTIMAL.

Standard library only. Run with --dry-run to print the requests without calling any API.
"""
import csv
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
CONFIG_TEXT = (HERE / "routes.json").read_text()
CONFIG = json.loads(CONFIG_TEXT)
# Changes whenever routes.json changes, so rows from different waypoint sets can be told apart.
CONFIG_VERSION = hashlib.sha1(CONFIG_TEXT.encode()).hexdigest()[:8]
OUT = HERE / "data" / "observations.csv"
PROVIDER = os.environ.get("ROUTE_PROVIDER", "mapbox").lower()

COLUMNS = [
    "run_id", "provider", "config_version", "ts_utc", "ts_local", "date_local", "weekday", "is_weekend",
    "hour_local", "minute_local", "route", "status", "duration_s", "baseline_duration_s", "delay_s",
    "distance_m", "traced_length_m", "distance_ratio", "drift_flag", "polyline", "error",
]


def points(route):
    return [CONFIG["origin"], *route["waypoints"], CONFIG["destination"]]


# ---------- Mapbox ----------
MAPBOX_URL = "https://api.mapbox.com/directions/v5/mapbox/driving-traffic/"


def mapbox_request(route, token):
    pts = points(route)
    coords = ";".join(f"{p['lng']:.6f},{p['lat']:.6f}" for p in pts)
    params = {
        # Only the first and last points are stops; the rest are silent pass-through points.
        "waypoints": f"0;{len(pts) - 1}",
        "steps": "true",  # required by Mapbox when 'waypoints' is set
        "overview": "full",
        "geometries": "polyline6",
        "alternatives": "false",
        "access_token": token,
    }
    # Direction of travel at each point, so it snaps to the correct side of a dual carriageway.
    params["bearings"] = ";".join(f"{q['heading']},45" if q.get("heading") is not None else "" for q in pts)
    return MAPBOX_URL + coords + "?" + urllib.parse.urlencode(params, safe=";,")


def mapbox_measure(route, token):
    with urllib.request.urlopen(mapbox_request(route, token), timeout=30) as resp:
        data = json.loads(resp.read())
    if data.get("code") != "Ok" or not data.get("routes"):
        raise RuntimeError(f"Mapbox code {data.get('code')}: {data.get('message', '')}")
    r = data["routes"][0]
    typical = r.get("duration_typical")
    return {
        "duration_s": round(r["duration"]),
        # Typical-traffic duration for this time slot (Mapbox has no free-flow figure).
        "baseline_duration_s": round(typical) if typical is not None else None,
        "distance_m": round(r["distance"]),
        "polyline": r.get("geometry", ""),
    }


# ---------- Google ----------
GOOGLE_URL = "https://routes.googleapis.com/directions/v2:computeRoutes"
GOOGLE_FIELDS = "routes.duration,routes.staticDuration,routes.distanceMeters,routes.polyline.encodedPolyline"


def google_body(route):
    def loc(p, heading=None):
        l = {"latLng": {"latitude": p["lat"], "longitude": p["lng"]}}
        if heading is not None:
            l["heading"] = heading
        return {"location": l}

    return {
        "origin": loc(CONFIG["origin"], CONFIG["origin"].get("heading")),
        "destination": loc(CONFIG["destination"], CONFIG["destination"].get("heading")),
        "intermediates": [{"via": True, **loc(w, w.get("heading"))} for w in route["waypoints"]],
        "travelMode": "DRIVE",
        "routingPreference": "TRAFFIC_AWARE_OPTIMAL",
        "computeAlternativeRoutes": False,
        "units": "METRIC",
    }


def google_measure(route, key):
    req = urllib.request.Request(
        GOOGLE_URL, data=json.dumps(google_body(route)).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-Goog-Api-Key": key, "X-Goog-FieldMask": GOOGLE_FIELDS},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        r = json.loads(resp.read())["routes"][0]
    secs = lambda v: round(float(str(v).rstrip("s"))) if v else None
    return {
        "duration_s": secs(r.get("duration")),
        # Free-flow duration with no traffic.
        "baseline_duration_s": secs(r.get("staticDuration")),
        "distance_m": int(r.get("distanceMeters", 0)),
        "polyline": r.get("polyline", {}).get("encodedPolyline", ""),
    }


PROVIDERS = {"mapbox": (mapbox_measure, "MAPBOX_TOKEN"), "google": (google_measure, "GOOGLE_MAPS_API_KEY")}


# ---------- shared ----------
def with_retry(fn, *args):
    try:
        return fn(*args)
    except urllib.error.HTTPError as e:
        if e.code not in (429, 500, 502, 503, 504):
            raise
    except urllib.error.URLError:
        pass
    time.sleep(5)
    return fn(*args)


def measure(route_id, route, fn, key):
    row = {"route": route_id, "traced_length_m": route["traced_length_m"]}
    try:
        m = with_retry(fn, route, key)
        dur, base, dist = m["duration_s"], m["baseline_duration_s"], m["distance_m"]
        ratio = dist / route["traced_length_m"]
        row.update(
            status="OK", duration_s=dur, baseline_duration_s=base if base is not None else "",
            delay_s=(dur - base) if base is not None else "",
            distance_m=dist, distance_ratio=round(ratio, 4),
            drift_flag=int(abs(ratio - 1) > CONFIG["drift_tolerance"]), polyline=m["polyline"],
        )
    except urllib.error.HTTPError as e:
        row.update(status=f"HTTP_{e.code}", error=e.read().decode(errors="replace")[:300])
    except Exception as e:  # network errors, empty or unexpected responses
        row.update(status="ERROR", error=repr(e)[:300])
    return row


def main():
    if PROVIDER not in PROVIDERS:
        sys.exit(f"ROUTE_PROVIDER must be one of {', '.join(PROVIDERS)}.")
    fn, key_var = PROVIDERS[PROVIDER]

    if "--dry-run" in sys.argv:
        for rid, r in CONFIG["routes"].items():
            print(f"Route {rid}:")
            print(mapbox_request(r, "<MAPBOX_TOKEN>") if PROVIDER == "mapbox" else json.dumps(google_body(r), indent=2))
            print()
        print(f"provider {PROVIDER}, config_version {CONFIG_VERSION}")
        return

    key = os.environ.get(key_var)
    if not key:
        sys.exit(f"Set {key_var} first.")

    now = datetime.now(timezone.utc)
    local = now.astimezone(ZoneInfo(CONFIG["timezone"]))
    shared = {
        "run_id": uuid.uuid4().hex[:12],
        "provider": PROVIDER,
        "config_version": CONFIG_VERSION,
        "ts_utc": now.isoformat(timespec="seconds"),
        "ts_local": local.isoformat(timespec="seconds"),
        "date_local": local.date().isoformat(),
        "weekday": local.strftime("%a"),
        "is_weekend": int(local.weekday() >= 5),
        "hour_local": local.hour,
        "minute_local": local.minute,
    }
    rows = [{**shared, **measure(rid, r, fn, key)} for rid, r in CONFIG["routes"].items()]

    OUT.parent.mkdir(parents=True, exist_ok=True)
    new_file = not OUT.exists()
    with OUT.open("a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        if new_file:
            w.writeheader()
        w.writerows(rows)

    for r in rows:
        if r["status"] == "OK":
            flag = "  (DRIFT: check waypoints)" if r["drift_flag"] else ""
            print(f"{shared['ts_local']} route {r['route']}: {r['duration_s']}s, {r['distance_m']} m{flag}")
        else:
            print(f"{shared['ts_local']} route {r['route']}: {r['status']} {r.get('error', '')}")
    if all(r["status"] != "OK" for r in rows):
        sys.exit(1)


if __name__ == "__main__":
    main()
