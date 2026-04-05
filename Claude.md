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
- espn_pull.py — fetches rosters, YTD stats, and free agents from ESPN API
- fangraphs_pull.py — fetches Steamer ROS projections from FanGraphs
- Fantasy_Baseball_Model.ipynb — main analysis engine (notebook)
- CLAUDE.md — this file

## Data files (auto-generated, do not edit manually)
- espn_current_rosters.csv
- current_team_stats.csv
- espn_free_agents.csv
- Fangraphs_Hitter_Projections_ROS.csv
- Fangraphs_Pitcher_Projections_ROS.csv

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

## How to run a data refresh
Run espn_pull.py first, then fangraphs_pull.py. Both save CSVs to the 
project folder. Then run the notebook analysis cells in order.

## Python environment
Uses standard Python data stack: pandas, numpy, requests, espn_api.