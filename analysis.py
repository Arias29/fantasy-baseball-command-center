"""
Shared analysis logic for Fantasy Baseball Model.
Imported by both run_analysis.py and app.py.
"""

import unicodedata
import numpy as np
import pandas as pd


# ── NAME CLEANING ─────────────────────────────────────────────────────────────

def aggressive_clean(name):
    if not isinstance(name, str):
        return ""
    name = "".join(
        c for c in unicodedata.normalize('NFD', name)
        if unicodedata.category(c) != 'Mn'
    )
    name = (name.lower()
            .replace(' jr.', '').replace(' sr.', '')
            .replace(' ii', '').replace(' iii', '')
            .replace('.', '').strip())
    return name


# ── SLOT ELIGIBILITY ──────────────────────────────────────────────────────────

def is_eligible(add_positions, drop_slot):
    """Enforces strict ESPN roster slot rules.
    Blocks out-of-position swaps (e.g. 3B-only player into a 1B slot)."""
    slot = str(drop_slot).strip()
    if pd.isna(slot) or slot in ['BE', 'UTIL', 'DH', 'P']:
        return True
    raw_string = str(add_positions).replace('/', ' ')
    player_pos_set = set(raw_string.split())
    if slot in ['1B/3B', 'CI']:
        return '1B' in player_pos_set or '3B' in player_pos_set
    if slot in ['2B/SS', 'MI']:
        return '2B' in player_pos_set or 'SS' in player_pos_set
    return slot in player_pos_set


# ── IMPACT STRING ─────────────────────────────────────────────────────────────

def get_impact_string(old_row, new_row, baseline_stats):
    """Summarizes per-category point changes between two team stat rows."""
    impacts = []
    pt_cols = [c for c in baseline_stats.columns if '_total_Pts' in c]
    for col in pt_cols:
        diff = new_row[col] - old_row[col]
        if diff != 0:
            label = col.replace('_total_Pts', '')
            impacts.append(f"{'+' if diff > 0 else ''}{round(diff, 1)} {label}")
    return ", ".join(impacts) if impacts else "Lateral move"


# ── CORE CALCULATION ENGINE ───────────────────────────────────────────────────

def get_league_stats(rosters_df, df_hitters, df_pitchers, df_current):
    """
    Merges rosters with ROS projections, adds YTD totals, computes
    volume-weighted ratio stats, and ranks all 10 Roto categories.

    Rules:
    - OPS weighted by PA
    - ERA and WHIP derived from accumulated ER, H, BB, IP (never averaged directly)
    """
    merged_h = pd.merge(
        rosters_df,
        df_hitters[['Clean_Name', 'R', 'HR', 'RBI', 'SB', 'OPS', 'PA']],
        on='Clean_Name', how='left'
    ).fillna(0)
    merged_p = pd.merge(
        rosters_df,
        df_pitchers[['Clean_Name', 'IP', 'QS', 'SV', 'ER', 'H', 'BB']],
        on='Clean_Name', how='left'
    ).fillna(0)

    t_hit = merged_h.groupby('Team')[['R', 'HR', 'RBI', 'SB', 'PA']].sum().reset_index()
    merged_h['OPS_num'] = merged_h['OPS'] * merged_h['PA']
    t_hit = pd.merge(t_hit, merged_h.groupby('Team')['OPS_num'].sum().reset_index(), on='Team')

    t_pit = merged_p.groupby('Team')[['IP', 'QS', 'SV', 'ER', 'H', 'BB']].sum().reset_index()

    proj_stats = pd.merge(t_hit, t_pit, on='Team')
    proj_stats = proj_stats.rename(
        columns={col: f"{col}_proj" for col in proj_stats.columns if col != 'Team'}
    )

    stats = pd.merge(df_current, proj_stats, on='Team')

    # Counting stats: YTD + ROS
    stats['R_total']   = stats['R_curr']   + stats['R_proj']
    stats['HR_total']  = stats['HR_curr']  + stats['HR_proj']
    stats['RBI_total'] = stats['RBI_curr'] + stats['RBI_proj']
    stats['SB_total']  = stats['SB_curr']  + stats['SB_proj']

    # OPS: volume-weighted by PA
    stats['PA_total']  = stats['PA_curr']  + stats['PA_proj']
    stats['OPS_total'] = (
        (stats['OPS_curr'] * stats['PA_curr']) + stats['OPS_num_proj']
    ) / stats['PA_total'].replace(0, 1)

    # Pitching counting stats
    stats['IP_total'] = stats['IP_curr'] + stats['IP_proj']
    stats['QS_total'] = stats['QS_curr'] + stats['QS_proj']
    stats['SV_total'] = stats['SV_curr'] + stats['SV_proj']

    # ERA/WHIP: accumulate components, then divide
    stats['ER_total']   = stats['ER_curr'] + stats['ER_proj']
    stats['H_total']    = stats['H_curr']  + stats['H_proj']
    stats['BB_total']   = stats['BB_curr'] + stats['BB_proj']
    stats['ERA_total']  = (stats['ER_total'] / stats['IP_total'].replace(0, 1)) * 9
    stats['WHIP_total'] = (stats['H_total'] + stats['BB_total']) / stats['IP_total'].replace(0, 1)

    # Rank all 10 categories
    high_is_better = ['R_total', 'HR_total', 'RBI_total', 'SB_total',
                      'OPS_total', 'IP_total', 'QS_total', 'SV_total']
    for s in high_is_better:
        stats[f'{s}_Pts'] = stats[s].rank(method='average')

    stats['ERA_total_Pts']  = stats['ERA_total'].rank(method='average', ascending=False)
    stats['WHIP_total_Pts'] = stats['WHIP_total'].rank(method='average', ascending=False)

    stats['Total_Points'] = stats[[c for c in stats.columns if '_Pts' in c]].sum(axis=1)
    return stats


# ── FAST SWAP ENGINE ─────────────────────────────────────────────────────────

_HIGH_CATS = ['R_total', 'HR_total', 'RBI_total', 'SB_total', 'OPS_total',
              'IP_total', 'QS_total', 'SV_total']
_LOW_CATS  = ['ERA_total', 'WHIP_total']


def build_swap_context(baseline_df, df_current, df_hitters, df_pitchers,
                       df_rosters, my_team_name):
    """
    Pre-compute per-projection-system structures used by fast_swap_gain().
    Call once after get_league_stats(); pass ctx to every fast_swap_gain() call.
    """
    other = baseline_df[baseline_df['Team'] != my_team_name]
    n     = len(other)

    other_sorted = {cat: np.sort(other[cat].values)
                    for cat in _HIGH_CATS + _LOW_CATS}

    my_roster = df_rosters[df_rosters['Team'] == my_team_name]
    merged_h  = pd.merge(
        my_roster,
        df_hitters[['Clean_Name', 'R', 'HR', 'RBI', 'SB', 'OPS', 'PA']],
        on='Clean_Name', how='left'
    ).fillna(0)
    merged_h['OPS_num'] = merged_h['OPS'] * merged_h['PA']
    merged_p = pd.merge(
        my_roster,
        df_pitchers[['Clean_Name', 'IP', 'QS', 'SV', 'ER', 'H', 'BB']],
        on='Clean_Name', how='left'
    ).fillna(0)

    my_proj = {
        'R':       float(merged_h['R'].sum()),
        'HR':      float(merged_h['HR'].sum()),
        'RBI':     float(merged_h['RBI'].sum()),
        'SB':      float(merged_h['SB'].sum()),
        'OPS_num': float(merged_h['OPS_num'].sum()),
        'PA':      float(merged_h['PA'].sum()),
        'IP':      float(merged_p['IP'].sum()),
        'QS':      float(merged_p['QS'].sum()),
        'SV':      float(merged_p['SV'].sum()),
        'ER':      float(merged_p['ER'].sum()),
        'H':       float(merged_p['H'].sum()),
        'BB':      float(merged_p['BB'].sum()),
    }

    my_curr = df_current[df_current['Team'] == my_team_name].iloc[0]

    # Base points via same searchsorted logic so gain deltas are consistent
    my_base  = baseline_df[baseline_df['Team'] == my_team_name].iloc[0]
    base_pts = 0.0
    for cat in _HIGH_CATS:
        v = my_base[cat]
        base_pts += (np.searchsorted(other_sorted[cat], v, 'left') +
                     np.searchsorted(other_sorted[cat], v, 'right') + 2) / 2
    for cat in _LOW_CATS:
        v = my_base[cat]
        base_pts += n + 1 - (np.searchsorted(other_sorted[cat], v, 'left') +
                              np.searchsorted(other_sorted[cat], v, 'right')) / 2

    return {
        'other_sorted': other_sorted,
        'n':            n,
        'my_proj':      my_proj,
        'my_curr':      my_curr,
        'base_pts':     base_pts,
    }


def fast_swap_gain(ctx, drop_dict, add_dict, is_hitter):
    """
    Exact point gain for a single-team FA pickup (drop → add).
    Holds all other teams fixed; uses searchsorted for rank computation.

    drop_dict / add_dict keys:
      hitter:  R, HR, RBI, SB, OPS, PA
      pitcher: IP, QS, SV, ER, H, BB
    """
    proj = ctx['my_proj']
    curr = ctx['my_curr']
    srt  = ctx['other_sorted']
    n    = ctx['n']

    def _g(d, k):
        v = float(d.get(k, 0) or 0)
        return 0.0 if np.isnan(v) else v

    if is_hitter:
        new_R       = proj['R']       - _g(drop_dict, 'R')   + _g(add_dict, 'R')
        new_HR      = proj['HR']      - _g(drop_dict, 'HR')  + _g(add_dict, 'HR')
        new_RBI     = proj['RBI']     - _g(drop_dict, 'RBI') + _g(add_dict, 'RBI')
        new_SB      = proj['SB']      - _g(drop_dict, 'SB')  + _g(add_dict, 'SB')
        new_OPS_num = (proj['OPS_num']
                       - _g(drop_dict, 'OPS') * _g(drop_dict, 'PA')
                       + _g(add_dict, 'OPS')  * _g(add_dict, 'PA'))
        new_PA      = proj['PA']      - _g(drop_dict, 'PA')  + _g(add_dict, 'PA')

        R_t   = curr['R_curr']   + new_R
        HR_t  = curr['HR_curr']  + new_HR
        RBI_t = curr['RBI_curr'] + new_RBI
        SB_t  = curr['SB_curr']  + new_SB
        PA_t  = curr['PA_curr']  + new_PA
        OPS_t = (curr['OPS_curr'] * curr['PA_curr'] + new_OPS_num) / max(PA_t, 1)

        IP_t  = curr['IP_curr'] + proj['IP']
        QS_t  = curr['QS_curr'] + proj['QS']
        SV_t  = curr['SV_curr'] + proj['SV']
        ER_t  = curr['ER_curr'] + proj['ER']
        H_t   = curr['H_curr']  + proj['H']
        BB_t  = curr['BB_curr'] + proj['BB']
    else:
        R_t   = curr['R_curr']   + proj['R']
        HR_t  = curr['HR_curr']  + proj['HR']
        RBI_t = curr['RBI_curr'] + proj['RBI']
        SB_t  = curr['SB_curr']  + proj['SB']
        PA_t  = curr['PA_curr']  + proj['PA']
        OPS_t = (curr['OPS_curr'] * curr['PA_curr'] + proj['OPS_num']) / max(PA_t, 1)

        IP_t  = curr['IP_curr'] + proj['IP'] - _g(drop_dict,'IP') + _g(add_dict,'IP')
        QS_t  = curr['QS_curr'] + proj['QS'] - _g(drop_dict,'QS') + _g(add_dict,'QS')
        SV_t  = curr['SV_curr'] + proj['SV'] - _g(drop_dict,'SV') + _g(add_dict,'SV')
        ER_t  = curr['ER_curr'] + proj['ER'] - _g(drop_dict,'ER') + _g(add_dict,'ER')
        H_t   = curr['H_curr']  + proj['H']  - _g(drop_dict,'H')  + _g(add_dict,'H')
        BB_t  = curr['BB_curr'] + proj['BB'] - _g(drop_dict,'BB') + _g(add_dict,'BB')

    ERA_t  = ER_t / max(IP_t, 1) * 9
    WHIP_t = (H_t + BB_t) / max(IP_t, 1)

    vals = {
        'R_total': R_t, 'HR_total': HR_t, 'RBI_total': RBI_t, 'SB_total': SB_t,
        'OPS_total': OPS_t, 'IP_total': IP_t, 'QS_total': QS_t, 'SV_total': SV_t,
        'ERA_total': ERA_t, 'WHIP_total': WHIP_t,
    }

    pts = 0.0
    for cat in _HIGH_CATS:
        v = vals[cat]
        pts += (np.searchsorted(srt[cat], v, 'left') +
                np.searchsorted(srt[cat], v, 'right') + 2) / 2
    for cat in _LOW_CATS:
        v = vals[cat]
        pts += n + 1 - (np.searchsorted(srt[cat], v, 'left') +
                        np.searchsorted(srt[cat], v, 'right')) / 2

    return pts - ctx['base_pts']


# ── PROJECTION CLEANING ───────────────────────────────────────────────────────

def clean_projections(df_hitters_raw, df_pitchers_raw):
    """Apply name cleaning and numeric coercion to a raw FanGraphs projection pair.
    Missing columns (e.g. QS absent in ZiPS) are filled with 0 so all systems work."""
    df_h = df_hitters_raw.copy()
    df_p = df_pitchers_raw.copy()

    df_h['Clean_Name'] = df_h['Player'].apply(aggressive_clean)
    df_p['Clean_Name'] = df_p['Player'].apply(aggressive_clean)

    hit_stats = ['R', 'HR', 'RBI', 'SB', 'OPS', 'PA']
    pit_stats = ['IP', 'QS', 'SV', 'ER', 'H', 'BB']

    for col in hit_stats:
        if col in df_h.columns:
            df_h[col] = pd.to_numeric(
                df_h[col].astype(str).str.replace('%', ''), errors='coerce'
            ).fillna(0.0)
        else:
            df_h[col] = 0.0

    for col in pit_stats:
        if col in df_p.columns:
            df_p[col] = pd.to_numeric(
                df_p[col].astype(str).str.replace('%', ''), errors='coerce'
            ).fillna(0.0)
        else:
            df_p[col] = 0.0

    # Deduplicate by Clean_Name, keeping the row with the most playing time.
    # FanGraphs occasionally includes ghost minor-league rows for the same player
    # name that share a Clean_Name with the real MLB entry.
    df_h = df_h.sort_values('PA', ascending=False).drop_duplicates('Clean_Name', keep='first')
    df_p = df_p.sort_values('IP', ascending=False).drop_duplicates('Clean_Name', keep='first')

    return df_h, df_p
