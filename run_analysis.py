"""
Fantasy Baseball Analysis Script
Run: python run_analysis.py

Fetches ESPN data once, then runs full analysis under 3 projection systems:
  - TheBatX ROS
  - Steamer ROS
  - ZiPS ROS

For each system:
  1. Projected end-of-season standings
  2. Profitable legal FA swap recommendations
  3. Manual trade / waiver pickup evaluator
  4. Best available player by position
"""

import pandas as pd
import numpy as np
from pathlib import Path

from analysis import (aggressive_clean, is_eligible, get_impact_string,
                      get_league_stats, clean_projections)
from config import MY_TEAM_NAME, LEAGUE_ID, YEAR, FOLDER, PROJECTION_SYSTEMS
from espn_pull import fetch_espn_data, fetch_espn_free_agents

# Manual swap evaluator
PLAYERS_TO_ACQUIRE       = ["Brendan Donovan"]
PLAYERS_TO_DROP_OR_TRADE = ["Jorge Polanco"]
TRADE_PARTNER            = None  # Team name string for a trade, None for waiver pickup

# Empty slot filler
TARGET_POSITION        = "RP"   # e.g. 'OF', 'SP', '1B', 'C', 'RP'
MAX_CANDIDATES_TO_TEST = 50
TOP_N_RESULTS          = 10


def fmt_standings(df):
    """Format a standings DataFrame for terminal output."""
    out = df.copy()
    int_cols = ['R_total', 'HR_total', 'RBI_total', 'SB_total', 'IP_total', 'QS_total', 'SV_total']
    for col in [c for c in int_cols if c in out.columns]:
        out[col] = out[col].round(0).astype(int)
    out = out.rename(columns={c: c.replace('_total', '') for c in out.columns})
    pd.set_option('display.float_format', lambda x: f'{x:.2f}')
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', 160)
    return out.to_string(
        index=True,
        formatters={
            'Total_Points': '{:.1f}'.format,
            'OPS':  '{:.3f}'.format,
            'ERA':  '{:.2f}'.format,
            'WHIP': '{:.2f}'.format,
        }
    )


def run_analysis(label, df_hitters, df_pitchers, active_roster_full, df_current, df_espn_fa_clean):
    """Run the full 4-phase analysis for one projection system."""
    divider = "=" * 70
    print(f"\n{divider}")
    print(f"  PROJECTION SYSTEM: {label.upper()}")
    print(divider)

    display_cols = [
        'Team', 'Total_Points', 'R_total', 'HR_total', 'RBI_total',
        'SB_total', 'OPS_total', 'IP_total', 'QS_total', 'SV_total',
        'ERA_total', 'WHIP_total'
    ]

    # ------------------------------------------
    # 1. PROJECTED STANDINGS
    # ------------------------------------------
    baseline_stats = get_league_stats(active_roster_full, df_hitters, df_pitchers, df_current)

    standings = (baseline_stats[display_cols]
                 .sort_values('Total_Points', ascending=False)
                 .reset_index(drop=True))
    standings.index += 1

    print(f"\n--- [{label}] 1. PROJECTED END-OF-SEASON STANDINGS (YTD + ROS) ---")
    print(fmt_standings(standings))

    # ------------------------------------------
    # 2. PROFITABLE LEGAL FA SWAPS
    # ------------------------------------------
    my_players      = active_roster_full[active_roster_full['Team'] == MY_TEAM_NAME].copy()
    hitter_name_set = set(df_hitters['Clean_Name'])

    top_fa_h = pd.merge(df_espn_fa_clean, df_hitters, on='Clean_Name', how='inner').head(100)
    top_fa_p = pd.merge(df_espn_fa_clean, df_pitchers, on='Clean_Name', how='inner').head(100)

    base_stats   = baseline_stats.set_index('Team').loc[MY_TEAM_NAME]
    h_results, p_results = [], []

    print(f"\n[Analyzing {len(my_players)} roster spots for legal swaps...]")

    for _, drop in my_players.iterrows():
        is_hitter    = drop['Clean_Name'] in hitter_name_set
        fa_pool      = top_fa_h if is_hitter else top_fa_p
        eligible_fas = fa_pool[fa_pool['ESPN_Positions'].apply(
            lambda pos: is_eligible(pos, drop['Lineup_Slot'])
        )]

        for _, add in eligible_fas.iterrows():
            df_temp = active_roster_full[
                active_roster_full['Clean_Name'] != drop['Clean_Name']
            ].copy()
            new_row = pd.DataFrame([{
                'Team':        MY_TEAM_NAME,
                'Clean_Name':  add['Clean_Name'],
                'Status':      'Rostered',
                'Lineup_Slot': drop['Lineup_Slot'],
            }])
            df_temp    = pd.concat([df_temp, new_row], ignore_index=True)
            sim_stats  = get_league_stats(df_temp, df_hitters, df_pitchers, df_current).set_index('Team').loc[MY_TEAM_NAME]
            point_gain = sim_stats['Total_Points'] - base_stats['Total_Points']

            if point_gain > 0:
                res = {
                    'Add':      add['ESPN_Player'],
                    'Drop':     drop['Player'],
                    'Slot':     drop['Lineup_Slot'],
                    'Net Gain': round(point_gain, 1),
                    'Details':  get_impact_string(base_stats, sim_stats, baseline_stats),
                }
                (h_results if is_hitter else p_results).append(res)

    print(f"\n--- [{label}] 2. PROFITABLE LEGAL HITTER SWAPS ---")
    if h_results:
        print(pd.DataFrame(h_results).sort_values('Net Gain', ascending=False).head(10).to_string(index=False))
    else:
        print("No legal hitter swaps found that improve your Roto points.")

    print(f"\n--- [{label}] 3. PROFITABLE LEGAL PITCHER SWAPS ---")
    if p_results:
        print(pd.DataFrame(p_results).sort_values('Net Gain', ascending=False).head(10).to_string(index=False))
    else:
        print("No legal pitcher swaps found that improve your Roto points.")

    # ------------------------------------------
    # 3. MANUAL TRADE / WAIVER EVALUATOR
    # ------------------------------------------
    print(f"\n--- [{label}] 4. MANUAL SWAP EVALUATOR ---")
    print(f"Acquiring:    {', '.join(PLAYERS_TO_ACQUIRE)}")
    print(f"Shipping out: {', '.join(PLAYERS_TO_DROP_OR_TRADE)}")
    if TRADE_PARTNER:
        print(f"Trade partner: {TRADE_PARTNER}")

    acquire_cleaned = [aggressive_clean(p) for p in PLAYERS_TO_ACQUIRE]
    drop_cleaned    = [aggressive_clean(p) for p in PLAYERS_TO_DROP_OR_TRADE]
    df_sim          = active_roster_full.copy()

    for raw, cleaned in zip(PLAYERS_TO_DROP_OR_TRADE, drop_cleaned):
        mask = df_sim['Clean_Name'] == cleaned
        if mask.any():
            if TRADE_PARTNER:
                df_sim.loc[mask, 'Team'] = TRADE_PARTNER
            else:
                df_sim = df_sim[~mask]
        else:
            print(f"  Warning: '{raw}' not found on active roster (checked as '{cleaned}').")

    for raw, cleaned in zip(PLAYERS_TO_ACQUIRE, acquire_cleaned):
        mask = df_sim['Clean_Name'] == cleaned
        if mask.any():
            df_sim.loc[mask, 'Team'] = MY_TEAM_NAME
        else:
            new_row = pd.DataFrame([{
                'Team':        MY_TEAM_NAME,
                'Clean_Name':  cleaned,
                'Status':      'Rostered',
                'Lineup_Slot': 'BE',
            }])
            df_sim = pd.concat([df_sim, new_row], ignore_index=True)

    sim_stats    = get_league_stats(df_sim, df_hitters, df_pitchers, df_current)
    base_my_team = baseline_stats.set_index('Team').loc[MY_TEAM_NAME]
    sim_my_team  = sim_stats.set_index('Team').loc[MY_TEAM_NAME]
    gain         = sim_my_team['Total_Points'] - base_my_team['Total_Points']
    impact_str   = get_impact_string(base_my_team, sim_my_team, baseline_stats)

    print(f"\nNet Point Change: {'+' if gain > 0 else ''}{gain:.1f}")
    print(f"Category Shifts:  {impact_str}")

    sim_standings = (sim_stats[display_cols]
                     .sort_values('Total_Points', ascending=False)
                     .reset_index(drop=True))
    sim_standings.index += 1
    print("\nNew standings preview (top 5):")
    print(fmt_standings(sim_standings.head(5)))

    # ------------------------------------------
    # 4. EMPTY SLOT FILLER
    # ------------------------------------------
    print(f"\n--- [{label}] 5. BEST AVAILABLE '{TARGET_POSITION}' ---")

    pitching_positions = {'SP', 'RP', 'P'}
    is_pitcher         = TARGET_POSITION in pitching_positions

    # df_espn_fa_clean still has original 'Positions' column for this filter
    available_fa_names = df_espn_fa_clean[
        df_espn_fa_clean['ESPN_Positions'].astype(str).str.contains(
            rf'\b{TARGET_POSITION}\b', regex=True, na=False
        )
    ]['Clean_Name'].tolist()

    if not available_fa_names:
        print(f"No free agents with '{TARGET_POSITION}' eligibility found.")
    else:
        proj_pool  = df_pitchers if is_pitcher else df_hitters
        sort_col   = 'IP' if is_pitcher else 'PA'
        candidates = (proj_pool[proj_pool['Clean_Name'].isin(available_fa_names)]
                      .sort_values(sort_col, ascending=False)
                      .head(MAX_CANDIDATES_TO_TEST))

        print(f"Simulating top {len(candidates)} projected {TARGET_POSITION}s...")
        fill_results = []
        base_my_team = baseline_stats.set_index('Team').loc[MY_TEAM_NAME]

        for _, add_player in candidates.iterrows():
            df_temp = active_roster_full.copy()
            new_row = pd.DataFrame([{
                'Team':        MY_TEAM_NAME,
                'Clean_Name':  add_player['Clean_Name'],
                'Status':      'Rostered',
                'Lineup_Slot': TARGET_POSITION,
            }])
            df_temp   = pd.concat([df_temp, new_row], ignore_index=True)
            new_stats = get_league_stats(df_temp, df_hitters, df_pitchers, df_current).set_index('Team').loc[MY_TEAM_NAME]
            gain      = new_stats['Total_Points'] - base_my_team['Total_Points']
            fill_results.append({
                'Add':      add_player.get('Player', add_player['Clean_Name']),
                'Net Gain': round(gain, 1),
                'Details':  get_impact_string(base_my_team, new_stats, baseline_stats),
            })

        if fill_results:
            df_out = (pd.DataFrame(fill_results)
                      .sort_values('Net Gain', ascending=False)
                      .head(TOP_N_RESULTS))
            print(df_out.to_string(index=False))
        else:
            print(f"No {TARGET_POSITION}s with matching projections found.")


# ==========================================
# MAIN: ESPN DATA REFRESH (once)
# ==========================================
print(f"--- STARTING ROS ANALYSIS FOR {MY_TEAM_NAME} ---\n")
print("[Refreshing ESPN data...]")

df_rosters, df_current = fetch_espn_data(LEAGUE_ID, YEAR)
df_fa                  = fetch_espn_free_agents(LEAGUE_ID, YEAR)

df_rosters.to_csv(FOLDER / 'espn_current_rosters.csv', index=False)
df_current.to_csv(FOLDER / 'current_team_stats.csv', index=False)
df_fa.to_csv(FOLDER / 'espn_free_agents.csv', index=False)

# Clean shared ESPN data
df_rosters['Team'] = df_rosters['Team'].astype(str).str.strip()
df_current['Team'] = df_current['Team'].astype(str).str.strip()
df_rosters['Clean_Name'] = df_rosters['Player'].apply(aggressive_clean)
df_fa['Clean_Name']      = df_fa['Player'].apply(aggressive_clean)

active_roster_full = df_rosters.copy()

# Build the FA lookup table once — shared across all projection systems
df_espn_fa_clean = (df_fa
                    .drop_duplicates(subset=['Clean_Name'], keep='first')
                    .rename(columns={'Positions': 'ESPN_Positions', 'Player': 'ESPN_Player'}))

print(f"[OK] ESPN data loaded: {len(active_roster_full)} active players, {len(df_espn_fa_clean)} free agents.\n")

# ==========================================
# MAIN: FETCH ALL PROJECTION SYSTEMS
# ==========================================
projections = {}
for proj_key in PROJECTION_SYSTEMS:
    h_path = FOLDER / f'Fangraphs_Hitter_{proj_key}.csv'
    p_path = FOLDER / f'Fangraphs_Pitcher_{proj_key}.csv'
    if not h_path.exists() or not p_path.exists():
        print(f"  [Error] FanGraphs CSV missing for '{proj_key}'. "
              "Use the app's Refresh All Data button first.")
        continue
    h_raw = pd.read_csv(h_path)
    p_raw = pd.read_csv(p_path)
    projections[proj_key] = clean_projections(h_raw, p_raw)
    print(f"[OK] Loaded {proj_key} from CSV.")

print(f"\n[OK] Projection systems ready: {', '.join(projections.keys())}")

# ==========================================
# MAIN: RUN ANALYSIS FOR EACH SYSTEM
# ==========================================
for proj_key, (df_h, df_p) in projections.items():
    run_analysis(proj_key, df_h, df_p, active_roster_full, df_current, df_espn_fa_clean)

print(f"\n{'=' * 70}")
print("  ANALYSIS COMPLETE")
print(f"{'=' * 70}\n")
