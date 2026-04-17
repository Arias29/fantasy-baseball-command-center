# Fantasy Baseball Command Center

## Project overview
12-team ESPN Rotisserie league analytical system. Pulls live data from ESPN 
and FanGraphs, calculates projected end-of-season standings, finds slot-legal 
free agent swaps. All league/team constants live in config.py — edit there, 
not here.

## Key files
- config.py — league/team constants (source of truth)
- espn_pull.py — ESPN rosters, YTD stats, free agents
- fangraphs_pull.py — ROS projections (5 systems: steamer_ros, depth_charts_ros, thebatx_ros, thebat_ros, zips_ros)
- analysis.py — core calc engine (get_league_stats, clean_projections, etc.)
- app.py — Streamlit dashboard; run: streamlit run app.py
- run_analysis.py — CLI terminal output
- Fantasy_Baseball_Model_Revised.ipynb — exploration notebook

## Data files (auto-generated — never edit manually)
espn_current_rosters.csv, current_team_stats.csv, espn_free_agents.csv,
Fangraphs_Hitter_{proj_key}.csv, Fangraphs_Pitcher_{proj_key}.csv

## Roto scoring (10 categories)
Hitting: R, HR, RBI, SB, OPS | Pitching: IP, QS, SV, ERA, WHIP

## Critical rules — never break
1. Never average ratio stats (OPS, ERA, WHIP) directly. Weight by PA (hitters) or IP (pitchers).
2. Always run aggressive_clean() before any name merge — strips accents, lowercase, removes Jr./Sr./II.
3. Slot eligibility is strict. 3B-only cannot fill a 1B slot.
4. Free agents come from df_espn_fa only. df_rosters = rostered players only.

## Stack
pandas, numpy, requests, espn_api, streamlit, plotly — install via pip install -r requirements.txt