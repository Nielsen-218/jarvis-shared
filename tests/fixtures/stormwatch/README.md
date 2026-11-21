# Storm Watch recorded samples (tests/test_stormwatch.py)

Recorded from the real public feeds on 2026-11-16; nothing here is invented.

- `nhc_current_20251026.json`: the NHC active-storms feed (https://www.nhc.noaa.gov/CurrentStorms.json) as archived by the
  Wayback Machine at 2025-10-26 01:33 UTC (Hurricane Melissa AL132025, Tropical Storm Sonia EP182025). The live feed had no
  active storms on 2026-11-16.
- `nhc_melissa_tcm_018.html`, `nhc_melissa_tcp_018.html`: Melissa's forecast advisory 18 and public advisory 18 (corrected)
  from the NHC archive (https://www.nhc.noaa.gov/archive/2025/al13/al132025.fstadv.018.shtml, .public.018.shtml), the
  bulletin text only, wrapped in <pre> the way the live text pages serve it.
- `usgs_haiti_20210814.geojson`: USGS event query, 2021-08-14 12:00-13:00 UTC, M4.5+ (the M7.2 Nippes, Haiti earthquake and
  its first aftershock among unrelated quakes).
- `usgs_4.5_day_20261116.geojson`: the USGS M4.5+ past-day feed on 2026-11-16.
- `nws_va_20261116.json`: NWS active alerts for area=VA on 2026-11-16 (none).
- `nws_us_severe_sample_20261116.json`: three real NWS alerts active on 2026-11-16 (Severe/Extreme, US-wide). Tests that
  need an alert for Springfield EDIT one field (the county code) and say so.
