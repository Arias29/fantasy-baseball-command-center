import time
import requests
import pandas as pd

from config import FOLDER

def fetch_fangraphs_projections(proj_type="steamer_ros"):
    """
    Fetches ROS projections directly from the FanGraphs backend API.

    Returns:
        (df_hitters, df_pitchers, status) where status is a dict:
            {
                "hitters_source":  "live" | "cache",
                "pitchers_source": "live" | "cache",
                "warnings":        [list of human-readable warning strings]
            }
    """
    print(f"Fetching FanGraphs Projections ({proj_type})...")

    type_map = {
        "steamer_ros":      "steamerr",
        "depth_charts_ros": "rfangraphsdc",
        "zips_ros":         "rzips",
        "thebat_ros":       "rthebat",
        "thebatx_ros":      "rthebatx"
    }

    fg_type = type_map.get(proj_type.lower(), "steamerr")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.fangraphs.com/projections",
        "Origin": "https://www.fangraphs.com",
    }

    url = "https://www.fangraphs.com/api/projections"

    hitters_params = {
        "pos": "all", "stats": "bat", "type": fg_type,
        "team": "0", "lg": "all", "players": "0"
    }
    pitchers_params = {
        "pos": "all", "stats": "pit", "type": fg_type,
        "team": "0", "lg": "all", "players": "0"
    }

    status = {"hitters_source": "live", "pitchers_source": "live", "warnings": []}

    # ──────────────────────────────────────────
    # Helpers
    # ──────────────────────────────────────────
    def _load_cache(cache_path, label):
        """Read a cache CSV, warn if missing or stale (>24 h)."""
        if not cache_path.exists():
            raise FileNotFoundError(
                f"FanGraphs {label} cache not found at {cache_path}. "
                "Run fangraphs_pull.py directly to create it."
            )
        age_hours = (time.time() - cache_path.stat().st_mtime) / 3600
        if age_hours > 24:
            msg = (f"FanGraphs {label} cache is {age_hours:.0f} h old "
                   f"(last updated: {time.strftime('%Y-%m-%d %H:%M', time.localtime(cache_path.stat().st_mtime))}). "
                   "Consider refreshing.")
            print(f"  [Warning] {msg}")
            status["warnings"].append(msg)
        return pd.read_csv(cache_path)

    def _parse_response(response, label):
        """Parse a successful FanGraphs JSON response into a DataFrame."""
        j = response.json()
        if isinstance(j, dict) and 'Message' in j:
            raise ValueError(f"FanGraphs rejected the {label} request: {j.get('Message')}")
        data = j.get('data', j) if isinstance(j, dict) else j
        return pd.DataFrame(data)

    # ──────────────────────────────────────────
    # Fetch Hitters
    # ──────────────────────────────────────────
    print(" -> Pulling hitters...")
    df_hitters = None
    try:
        h_response = requests.get(url, headers=headers, params=hitters_params, timeout=15)
        if h_response.status_code != 200 or not h_response.text.strip():
            raise requests.exceptions.RequestException(
                f"HTTP {h_response.status_code}"
            )
        df_hitters = _parse_response(h_response, "hitters")
    except requests.exceptions.RequestException as e:
        msg = f"FanGraphs hitters request failed ({e}). Falling back to cached CSV."
        print(f"  [Warning] {msg}")
        status["warnings"].append(msg)
        status["hitters_source"] = "cache"
        df_hitters = _load_cache(FOLDER / f'Fangraphs_Hitter_{proj_type}.csv', "hitters")
        time.sleep(2)

    # ──────────────────────────────────────────
    # Fetch Pitchers
    # ──────────────────────────────────────────
    time.sleep(1)
    print(" -> Pulling pitchers...")
    df_pitchers = None
    try:
        p_response = requests.get(url, headers=headers, params=pitchers_params, timeout=15)
        if p_response.status_code != 200 or not p_response.text.strip():
            raise requests.exceptions.RequestException(
                f"HTTP {p_response.status_code}"
            )
        df_pitchers = _parse_response(p_response, "pitchers")
    except requests.exceptions.RequestException as e:
        msg = f"FanGraphs pitchers request failed ({e}). Falling back to cached CSV."
        print(f"  [Warning] {msg}")
        status["warnings"].append(msg)
        status["pitchers_source"] = "cache"
        df_pitchers = _load_cache(FOLDER / f'Fangraphs_Pitcher_{proj_type}.csv', "pitchers")

    # ──────────────────────────────────────────
    # Cleanup & Formatting
    # ──────────────────────────────────────────
    df_hitters  = df_hitters.rename(columns={'PlayerName': 'Player'}, errors='ignore')
    df_pitchers = df_pitchers.rename(columns={'PlayerName': 'Player'}, errors='ignore')

    if status["hitters_source"] == "cache" or status["pitchers_source"] == "cache":
        h_src = status["hitters_source"]
        p_src = status["pitchers_source"]
        print(f"[OK] FanGraphs data loaded (hitters: {h_src}, pitchers: {p_src}).")
    else:
        print("[OK] FanGraphs data successfully loaded.")

    return df_hitters, df_pitchers, status


# ──────────────────────────────────────────
# Execution Block
# ──────────────────────────────────────────
if __name__ == "__main__":
    ALL_PROJ_KEYS = [
        "steamer_ros",
        "depth_charts_ros",
        "thebatx_ros",
        "thebat_ros",
        "zips_ros",
    ]
    for pk in ALL_PROJ_KEYS:
        df_h, df_p, fetch_status = fetch_fangraphs_projections(pk)
        if fetch_status["warnings"]:
            for w in fetch_status["warnings"]:
                print(f"  [Warning] {w}")
        df_h.to_csv(FOLDER / f"Fangraphs_Hitter_{pk}.csv",  index=False)
        df_p.to_csv(FOLDER / f"Fangraphs_Pitcher_{pk}.csv", index=False)
        print(f"  Saved: Fangraphs_Hitter_{pk}.csv + Fangraphs_Pitcher_{pk}.csv")

    print("\nAll 5 projection systems saved. Your Command Center is ready.")
