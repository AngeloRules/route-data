# Obalende → Falomo route timing study

Logs live, traffic-aware travel times (Mapbox Directions API, `driving-traffic` profile) for two driving routes from the E1 just before the Obalende exit to Falomo roundabout, so the routes can be compared with hypothesis tests.

- **Route A**: Obalende off-ramp, under the E1, Ikoyi Road, round Ikoyi Club, down Alfred Rewane, ending about 150 m before Falomo roundabout (traced 4.09 km)
- **Route B**: stay on the E1 to Onikan, round the roundabout, along Awolowo Road, ending about 150 m before Falomo roundabout (traced 3.91 km)

**Why the routes stop short of the bridge:** Mapbox's road map has no turn from Awolowo Road onto Falomo Bridge, so it sent Route B up Alfred Rewane to U-turn. Both routes now end about 150 m before the roundabout, on their own approach roads. The last stretch through the roundabout onto the bridge isn't measured for either route. Route B's turn through the roundabout probably takes a little longer in reality than Route A's entry from Alfred Rewane, so this slightly favours Route B. Mention that in the write-up.

## How the data supports the tests

- **Paired observations.** Every run requests both routes a few seconds apart and gives both rows the same `run_id`. Each run is one matched pair: the same traffic moment measured on both routes. Pair on `run_id` and test the per-run difference (A − B), with a paired t-test or a Wilcoxon signed-rank test.
- **All-day coverage.** Runs every 20 minutes from 05:07 to 21:47 Lagos time, and hourly overnight. Night is sampled more thinly than day, so for the all-day test either weight runs by hour or compare hour-by-hour.
- **Morning rush.** Filter by `hour_local` / `minute_local` and `is_weekend`. The rush window is defined at analysis time, not at collection, so you can try different windows on the same data.
- **Repeated measures.** Runs on the same day are not independent of each other. For a stricter test, average the A − B difference per day (or per day and hour) before testing.

## Setup (about 10 minutes)

1. **Mapbox token (no card needed)**
   - Sign up at mapbox.com and choose "Start without a credit card".
   - Copy your default public access token from the console.
   - Without a card you're on a trial capped at 10,000 Directions API requests a month. This schedule uses about 3,600. The trial may have an end date, so check your Mapbox console for one: if the trial ends, collection stops and the log fills with `HTTP_401`/`HTTP_403` rows.
2. **GitHub repository**
   - Create a new repository and push this folder to it.
   - Make it **public** if you can. Public repositories get unlimited Actions minutes. A private repository gets 2,000 free minutes a month, and this schedule uses about 1,750 of them, which leaves almost no room.
   - Add the token under Settings → Secrets and variables → Actions → New repository secret. Name it `MAPBOX_TOKEN`.
3. **First run**
   - Go to Actions → "Collect route travel times" → Run workflow.
   - Check that `data/observations.csv` appears with two rows, `status` = `OK` and `drift_flag` = `0`.
   - From then on it runs on its own.

Run it locally with `MAPBOX_TOKEN=... python collect.py`. To print the exact requests without spending a call, run `python collect.py --dry-run`.

### Switching to Google later

If you get a card Google Cloud accepts, add a `GOOGLE_MAPS_API_KEY` secret and change `ROUTE_PROVIDER` in `.github/workflows/collect.yml` to `google`. Every row records its `provider`. Analyse each provider separately, because their traffic models differ.

## Budget

58 runs a day × 2 routes ≈ 3,600 requests in a 31-day month. That fits under Mapbox's 10,000-request trial cap, and under Google's 5,000 free requests if you switch. Manual runs count toward this as well.

GitHub's scheduler sometimes starts runs a few minutes late or skips one at busy times. Every row records its actual time, so this doesn't affect the analysis.

## Data: `data/observations.csv`

One row per route per run.

| Column | Meaning |
|---|---|
| `run_id` | Shared by the A and B rows from the same run; use it to pair them |
| `provider` | `mapbox` or `google`. Analyse one provider at a time |
| `config_version` | Short hash of `routes.json`; changes if the waypoints change. Analyse one version at a time |
| `ts_utc`, `ts_local` | When the run happened (UTC and Lagos time) |
| `date_local`, `weekday`, `is_weekend` | Lagos calendar date, day name, 1 for Sat/Sun |
| `hour_local`, `minute_local` | Lagos clock time, for time-of-day windows |
| `route` | `A` or `B` |
| `status` | `OK`, `HTTP_<code>`, or `ERROR`. Failed requests are logged, not skipped, so gaps are visible |
| `duration_s` | Travel time with live traffic, in seconds. The main outcome variable |
| `baseline_duration_s` | Comparison duration. Mapbox: typical traffic for this time slot. Google: no traffic |
| `delay_s` | `duration_s − baseline_duration_s`. Mapbox: how much worse than usual traffic is right now. Google: time lost to traffic |
| `distance_m` | Length of the route Google returned |
| `traced_length_m` | Length of your Google Earth trace |
| `distance_ratio` | `distance_m / traced_length_m` |
| `drift_flag` | 1 if the returned route is more than 5% longer or shorter than the trace. Exclude these rows, and adjust the waypoints if it keeps happening |
| `polyline` | Encoded shape of the returned route; decode it to check where it went |
| `error` | API error text, when there is one |

## Changing routes or waypoints

Edit `routes.json`. If you change any waypoints, rows logged from then on get a new `config_version`, so keep each version separate in the analysis.

## Limits worth stating in the write-up

These are the provider's traffic estimates, not timed drives. They're consistent and frequent, which suits a comparison between routes, but they model traffic flow rather than record actual journeys. Before relying on the result, check a few timed drives against the logged estimates.
