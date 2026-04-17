# Fantasy Baseball Command Center

## What this project does
Automated analytical system for a 12-team ESPN Rotisserie (Roto) baseball 
league. Pulls live data from ESPN and FanGraphs, calculates projected 
end-of-season standings, and finds slot-legal free agent swaps.

## My team & league
- Team name: "Budget Ballers"
- League ID: 283187668
- Year: 2026
- League size: 12 teams

## Key files
- config.py — single source of truth for league/team constants (edit here each season)
- espn_pull.py — fetches rosters, YTD stats, and free agents from ESPN API
- fangraphs_pull.py — fetches ROS projections from FanGraphs (5 systems)
- analysis.py — core calculation engine (get_league_stats, clean_projections, etc.)
- app.py — Streamlit dashboard (main UI); run with: streamlit run app.py
- run_analysis.py — CLI script for terminal output
- Fantasy_Baseball_Model_Revised.ipynb — interactive notebook for exploration

## Data files (auto-generated, do not edit manually)
- espn_current_rosters.csv
- current_team_stats.csv
- espn_free_agents.csv
- Fangraphs_Hitter_{proj_key}.csv  (one per projection system)
- Fangraphs_Pitcher_{proj_key}.csv (one per projection system)
  proj_key options: steamer_ros, depth_charts_ros, thebatx_ros, thebat_ros, zips_ros

## Scoring categories (Roto, 10 categories)
Hitting: R, HR, RBI, SB, OPS
Pitching: IP, QS, SV, ERA, WHIP

## Critical rules — never break these
1. Never average ratio stats (OPS, ERA, WHIP) directly. Always weight by 
   PA for hitters and IP for pitchers.
2. Player name matching uses aggressive_clean() — strips accents, lowercases, 
   removes suffixes (Jr., Sr., II). Always clean names before any merge.
3. Slot eligibility is strict. A 3B-only player cannot fill a 1B slot.
4. Free agents come from df_espn_fa, NOT df_rosters. df_rosters only 
   contains actively rostered players.

## How to refresh data
Click "Refresh All Data" in the app — this fetches ESPN + all 5 FanGraphs 
projection systems and saves them to CSV. All analysis runs off those CSVs.
Alternatively: python fangraphs_pull.py (saves all 5 systems) or use the notebook Cell 1.

## Python environment
Uses standard Python data stack: pandas, numpy, requests, espn_api, streamlit, plotly.
Install dependencies: pip install -r requirements.txt
