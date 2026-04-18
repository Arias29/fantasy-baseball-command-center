"""
Refresh all ESPN + Fangraphs CSVs and write data_metadata.json.
Invoked by the scheduled GitHub Actions workflow (.github/workflows/refresh-data.yml)
and runnable manually via `python refresh_data.py`.

Exits 0 only when every source fetched live data. Exits 1 if ESPN raises or if
Fangraphs silently fell back to a cached CSV for any projection system.
"""

import json
import sys
import traceback
from datetime import datetime, timezone

from config import FOLDER, LEAGUE_ID, PROJ_OPTIONS, YEAR
from espn_pull import fetch_espn_data, fetch_espn_free_agents
from fangraphs_pull import fetch_fangraphs_projections


def refresh_espn() -> list[str]:
    failures: list[str] = []
    try:
        df_rosters, df_current = fetch_espn_data(LEAGUE_ID, YEAR)
        df_fa = fetch_espn_free_agents(LEAGUE_ID, YEAR)
        df_rosters.to_csv(FOLDER / 'espn_current_rosters.csv', index=False)
        df_current.to_csv(FOLDER / 'current_team_stats.csv',   index=False)
        df_fa.to_csv(FOLDER / 'espn_free_agents.csv',          index=False)
        print("ESPN: ok (rosters, team stats, free agents)")
    except Exception as exc:
        traceback.print_exc()
        failures.append(f"ESPN: {exc}")
    return failures


def refresh_fangraphs() -> list[str]:
    failures: list[str] = []
    for proj_key in PROJ_OPTIONS.values():
        try:
            df_h, df_p, status = fetch_fangraphs_projections(proj_key)
            df_h.to_csv(FOLDER / f'Fangraphs_Hitter_{proj_key}.csv',  index=False)
            df_p.to_csv(FOLDER / f'Fangraphs_Pitcher_{proj_key}.csv', index=False)
            h_src, p_src = status["hitters_source"], status["pitchers_source"]
            print(f"Fangraphs {proj_key}: hitters={h_src} pitchers={p_src}")
            if h_src == "cache" or p_src == "cache":
                failures.append(
                    f"Fangraphs {proj_key}: fell back to cache "
                    f"(hitters={h_src}, pitchers={p_src})"
                )
        except Exception as exc:
            traceback.print_exc()
            failures.append(f"Fangraphs {proj_key}: {exc}")
    return failures


def write_metadata() -> None:
    now_utc = datetime.now(timezone.utc).replace(microsecond=0)
    payload = {"last_updated_utc": now_utc.isoformat().replace("+00:00", "Z")}
    (FOLDER / 'data_metadata.json').write_text(json.dumps(payload, indent=2) + "\n")
    print(f"Metadata written: {payload['last_updated_utc']}")


def main() -> int:
    failures = refresh_espn() + refresh_fangraphs()
    write_metadata()
    if failures:
        print("\nRefresh finished with failures:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nRefresh finished cleanly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
