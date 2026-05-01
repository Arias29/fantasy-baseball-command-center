"""
Fantasy Baseball Command Center — Streamlit Dashboard
Run: streamlit run app.py
"""

import json
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from analysis import (aggressive_clean, is_eligible, get_impact_string,
                      get_league_stats, clean_projections,
                      build_swap_context, fast_swap_gain)
from config import MY_TEAM_NAME, LEAGUE_ID, YEAR, FOLDER, PROJ_OPTIONS

# ── CONFIG ────────────────────────────────────────────────────────────────────

POSITIONS          = ["C", "1B", "2B", "3B", "SS", "OF", "SP", "RP"]
PITCHING_POSITIONS = {"SP", "RP", "P"}

ANALYSIS_PROJ_KEYS = {
    "TheBatX":      "thebatx_ros",
    "Steamer":      "steamer_ros",
    "Depth Charts": "depth_charts_ros",
}

HITTER_SLOT_ORDER  = ['C', '1B', '2B', '3B', 'SS', '2B/SS', '1B/3B',
                      'LF', 'CF', 'RF', 'OF', 'DH', 'UTIL', 'BE']
PITCHER_SLOT_ORDER = ['SP', 'RP', 'P', 'BE']

DISPLAY_COLS = [
    'Team', 'Total_Points', 'R_total', 'HR_total', 'RBI_total',
    'SB_total', 'OPS_total', 'IP_total', 'QS_total', 'SV_total',
    'ERA_total', 'WHIP_total',
]

INT_COLS = ['R_total', 'HR_total', 'RBI_total', 'SB_total',
            'IP_total', 'QS_total', 'SV_total']


# ── PAGE CONFIG ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Fantasy Baseball Command Center",
    page_icon="⚾",
    layout="wide",
)

st.markdown("""
<style>
  .big-number { font-size: 3rem; font-weight: 700; }
  .green      { color: #22c55e; }
  .red        { color: #ef4444; }
</style>
""", unsafe_allow_html=True)


# ── CACHED DATA LOADERS ───────────────────────────────────────────────────────
@st.cache_data
def load_espn_data():
    """Read the 3 ESPN CSVs, clean names, build the FA lookup table."""
    df_rosters = pd.read_csv(FOLDER / 'espn_current_rosters.csv')
    df_current = pd.read_csv(FOLDER / 'current_team_stats.csv')
    df_fa      = pd.read_csv(FOLDER / 'espn_free_agents.csv')

    df_rosters['Team'] = df_rosters['Team'].astype(str).str.strip()
    df_current['Team'] = df_current['Team'].astype(str).str.strip()
    df_rosters['Clean_Name'] = df_rosters['Player'].apply(aggressive_clean)
    df_fa['Clean_Name']      = df_fa['Player'].apply(aggressive_clean)

    # Dedup FAs by clean name, then rename for unambiguous merges
    df_fa_clean = (df_fa
                   .drop_duplicates('Clean_Name', keep='first')
                   .rename(columns={'Positions': 'ESPN_Positions',
                                    'Player':    'ESPN_Player'}))
    return df_rosters, df_current, df_fa_clean


@st.cache_data
def load_projections(proj_key):
    """Read + clean a FanGraphs projection system from CSV. Cached per system key."""
    h_path = FOLDER / f'Fangraphs_Hitter_{proj_key}.csv'
    p_path = FOLDER / f'Fangraphs_Pitcher_{proj_key}.csv'
    if not h_path.exists() or not p_path.exists():
        raise FileNotFoundError(
            f"No FanGraphs CSV found for '{proj_key}'. "
            "The scheduled refresh hasn't produced this file yet."
        )
    df_h_raw = pd.read_csv(h_path)
    df_p_raw = pd.read_csv(p_path)
    return clean_projections(df_h_raw, df_p_raw)


@st.cache_data
def get_baseline(proj_key):
    """Compute full-league standings for the given projection system."""
    df_rosters, df_current, _ = load_espn_data()
    df_h, df_p = load_projections(proj_key)
    return get_league_stats(df_rosters, df_h, df_p, df_current)


@st.cache_data
def get_roster_projections(proj_key):
    """
    Returns per-player ROS projections for every rostered player (left join,
    so players with no projection match still appear with NaN stats).
    Also returns a slot_template: {slot -> max_count_across_league} used to
    detect which starting slots a team is leaving empty.
    """
    df_rosters, _, _ = load_espn_data()
    df_h, df_p = load_projections(proj_key)

    # Split roster into hitters and pitchers by lineup slot
    hitter_rows  = df_rosters[~df_rosters['Lineup_Slot'].isin(PITCHING_POSITIONS)].copy()
    pitcher_rows = df_rosters[ df_rosters['Lineup_Slot'].isin(PITCHING_POSITIONS)].copy()

    # Left joins — keeps all rostered players even without projection match
    hit_cols = ['Clean_Name', 'R', 'HR', 'RBI', 'SB', 'OPS', 'PA']
    merged_h = (hitter_rows
                .merge(df_h[hit_cols], on='Clean_Name', how='left')
                [['Team', 'Player', 'Lineup_Slot', 'R', 'HR', 'RBI', 'SB', 'OPS', 'PA']]
                .copy())

    pit_cols = ['Clean_Name', 'IP', 'QS', 'SV', 'ER', 'H', 'BB']
    merged_p = pitcher_rows.merge(df_p[pit_cols], on='Clean_Name', how='left').copy()
    merged_p['ERA']  = (merged_p['ER'] / merged_p['IP'].replace(0, 1)) * 9
    merged_p['WHIP'] = (merged_p['H'] + merged_p['BB']) / merged_p['IP'].replace(0, 1)
    merged_p = (merged_p[['Team', 'Player', 'Lineup_Slot', 'IP', 'QS', 'SV', 'ERA', 'WHIP']]
                .copy())

    # Slot template: max players per slot across the whole league (excluding bench)
    non_bench    = df_rosters[df_rosters['Lineup_Slot'] != 'BE']
    slot_counts  = non_bench.groupby(['Team', 'Lineup_Slot']).size().reset_index(name='n')
    slot_template = slot_counts.groupby('Lineup_Slot')['n'].max().to_dict()

    return merged_h, merged_p, slot_template


@st.cache_data
def run_swaps_for_system(proj_key):
    """
    Fast swap simulation using searchsorted (exact for single-team FA moves).
    Screens all candidates via fast_swap_gain, then verifies the top 20
    with full get_league_stats to handle cross-team rank interactions.
    Returns profitable swaps as Add | Drop | Slot | Net Gain.
    """
    df_rosters, df_current, df_fa_clean = load_espn_data()
    df_h, df_p  = load_projections(proj_key)
    baseline    = get_baseline(proj_key)

    ctx             = build_swap_context(baseline, df_current, df_h, df_p, df_rosters, MY_TEAM_NAME)
    hitter_name_set = set(df_h['Clean_Name'])
    top_fa_h        = pd.merge(df_fa_clean, df_h, on='Clean_Name', how='inner').head(200)
    top_fa_p        = pd.merge(df_fa_clean, df_p, on='Clean_Name', how='inner').head(200)

    # Build per-player stat lookup for drop candidates
    hit_stat_cols = ['Clean_Name', 'R', 'HR', 'RBI', 'SB', 'OPS', 'PA']
    pit_stat_cols = ['Clean_Name', 'IP', 'QS', 'SV', 'ER', 'H', 'BB']
    h_lookup = df_h[hit_stat_cols].drop_duplicates('Clean_Name').set_index('Clean_Name').to_dict('index')
    p_lookup = df_p[pit_stat_cols].drop_duplicates('Clean_Name').set_index('Clean_Name').to_dict('index')

    my_players = df_rosters[df_rosters['Team'] == MY_TEAM_NAME].copy()
    base_stats = baseline.set_index('Team').loc[MY_TEAM_NAME]

    # ── PHASE 1: fast screen ──────────────────────────────────────────────────
    fast_results = []
    for _, drop in my_players.iterrows():
        is_hitter  = drop['Clean_Name'] in hitter_name_set
        fa_pool    = top_fa_h if is_hitter else top_fa_p
        drop_stats = (h_lookup if is_hitter else p_lookup).get(drop['Clean_Name'], {})

        eligible_fas = fa_pool[fa_pool['ESPN_Positions'].apply(
            lambda pos: is_eligible(pos, drop['Lineup_Slot'])
        )]
        for _, add in eligible_fas.iterrows():
            add_stats = add.to_dict()
            gain = fast_swap_gain(ctx, drop_stats, add_stats, is_hitter)
            if gain > 0:
                fast_results.append({
                    'Add':        add['ESPN_Player'],
                    'Add_Clean':  add['Clean_Name'],
                    'Drop':       drop['Player'],
                    'Drop_Clean': drop['Clean_Name'],
                    'Slot':       drop['Lineup_Slot'],
                    '_fast_gain': gain,
                })

    if not fast_results:
        return pd.DataFrame(columns=['Add', 'Drop', 'Slot', 'Net Gain'])

    # ── PHASE 2: full verification on top 20 fast candidates ─────────────────
    top_candidates = (pd.DataFrame(fast_results)
                      .sort_values('_fast_gain', ascending=False)
                      .head(20))

    verified = []
    for _, row in top_candidates.iterrows():
        df_temp = df_rosters[df_rosters['Clean_Name'] != row['Drop_Clean']].copy()
        df_temp = pd.concat([df_temp, pd.DataFrame([{
            'Team':        MY_TEAM_NAME,
            'Clean_Name':  row['Add_Clean'],
            'Status':      'Rostered',
            'Lineup_Slot': row['Slot'],
        }])], ignore_index=True)
        sim  = get_league_stats(df_temp, df_h, df_p, df_current).set_index('Team').loc[MY_TEAM_NAME]
        gain = sim['Total_Points'] - base_stats['Total_Points']
        if gain > 0:
            verified.append({
                'Add':       row['Add'],
                'Add_Clean': row['Add_Clean'],
                'Drop':      row['Drop'],
                'Drop_Clean':row['Drop_Clean'],
                'Slot':      row['Slot'],
                'Net Gain':  round(gain, 1),
                'Details':   get_impact_string(base_stats, sim, baseline),
            })

    if not verified:
        return pd.DataFrame(columns=['Add', 'Add_Clean', 'Drop', 'Drop_Clean', 'Slot', 'Net Gain', 'Details'])
    return (pd.DataFrame(verified)
            .sort_values('Net Gain', ascending=False)
            .drop_duplicates(subset=['Add', 'Drop', 'Slot'])
            .reset_index(drop=True))


@st.cache_data
def backfill_gain(add_clean, drop_clean, slot, proj_key):
    """Compute actual gain for one swap in a system that didn't flag it as profitable."""
    df_rosters, df_current, _ = load_espn_data()
    df_h, df_p = load_projections(proj_key)
    baseline   = get_baseline(proj_key)
    base_stats = baseline.set_index('Team').loc[MY_TEAM_NAME]
    df_temp = df_rosters[df_rosters['Clean_Name'] != drop_clean].copy()
    df_temp = pd.concat([df_temp, pd.DataFrame([{
        'Team':        MY_TEAM_NAME,
        'Clean_Name':  add_clean,
        'Status':      'Rostered',
        'Lineup_Slot': slot,
    }])], ignore_index=True)
    sim = get_league_stats(df_temp, df_h, df_p, df_current).set_index('Team').loc[MY_TEAM_NAME]
    return round(sim['Total_Points'] - base_stats['Total_Points'], 1)


@st.cache_data
def backfill_add_gain(clean_name, proj_key):
    """Compute actual gain for adding one FA (no drop) in a system that didn't flag it."""
    df_rosters, df_current, _ = load_espn_data()
    df_h, df_p = load_projections(proj_key)
    baseline   = get_baseline(proj_key)
    base_stats = baseline.set_index('Team').loc[MY_TEAM_NAME]
    df_temp = pd.concat([df_rosters, pd.DataFrame([{
        'Team':        MY_TEAM_NAME,
        'Clean_Name':  clean_name,
        'Status':      'Rostered',
        'Lineup_Slot': 'BE',
    }])], ignore_index=True)
    sim = get_league_stats(df_temp, df_h, df_p, df_current).set_index('Team').loc[MY_TEAM_NAME]
    return round(sim['Total_Points'] - base_stats['Total_Points'], 1)


@st.cache_data
def run_best_adds_for_system(proj_key, position):
    """
    Finds the best FA adds at a given position (stacked on roster, no drop).
    Fast-screens all candidates, then fully verifies the top 15.
    Returns Player | Net Gain sorted descending.
    """
    df_rosters, df_current, df_fa_clean = load_espn_data()
    df_h, df_p   = load_projections(proj_key)
    baseline     = get_baseline(proj_key)
    base_my_team = baseline.set_index('Team').loc[MY_TEAM_NAME]

    ctx        = build_swap_context(baseline, df_current, df_h, df_p, df_rosters, MY_TEAM_NAME)
    is_pitcher = position in PITCHING_POSITIONS
    proj_pool  = df_p if is_pitcher else df_h
    sort_col   = 'IP' if is_pitcher else 'PA'

    fa_with_proj = (
        pd.merge(df_fa_clean, proj_pool, on='Clean_Name', how='inner')
        .loc[lambda d: d['ESPN_Positions'].astype(str)
             .str.contains(rf'\b{position}\b', regex=True, na=False)]
        .sort_values(sort_col, ascending=False)
        .head(50)
    )

    # ── PHASE 1: fast screen (add with no drop = drop a zero-stat phantom) ────
    fast_results = []
    for _, add_player in fa_with_proj.iterrows():
        gain = fast_swap_gain(ctx, {}, add_player.to_dict(), is_pitcher is False)
        fast_results.append({
            'ESPN_Player': add_player.get('ESPN_Player', add_player['Clean_Name']),
            'Clean_Name':  add_player['Clean_Name'],
            '_fast_gain':  gain,
        })

    if not fast_results:
        return pd.DataFrame(columns=['Player', 'Clean_Name', 'Net Gain', 'Details'])

    top_candidates = (pd.DataFrame(fast_results)
                      .sort_values('_fast_gain', ascending=False)
                      .head(15))

    # ── PHASE 2: full verification on top 15 ─────────────────────────────────
    verified = []
    for _, row in top_candidates.iterrows():
        df_temp = pd.concat([df_rosters, pd.DataFrame([{
            'Team':        MY_TEAM_NAME,
            'Clean_Name':  row['Clean_Name'],
            'Status':      'Rostered',
            'Lineup_Slot': 'BE',
        }])], ignore_index=True)
        new_stats = (get_league_stats(df_temp, df_h, df_p, df_current)
                     .set_index('Team').loc[MY_TEAM_NAME])
        gain = new_stats['Total_Points'] - base_my_team['Total_Points']
        verified.append({
            'Player':     row['ESPN_Player'],
            'Clean_Name': row['Clean_Name'],
            'Net Gain':   round(gain, 1),
            'Details':    get_impact_string(base_my_team, new_stats, baseline),
        })

    return pd.DataFrame(verified).sort_values('Net Gain', ascending=False).reset_index(drop=True)


# ── DISPLAY HELPERS ───────────────────────────────────────────────────────────
def style_standings(df):
    """Apply blue highlight to my team's row, format numeric columns."""
    out = df.copy()
    for col in [c for c in INT_COLS if c in out.columns]:
        out[col] = out[col].round(0).astype(int)
    out = out.rename(columns={c: c.replace('_total', '') for c in out.columns})

    def highlight_row(row):
        color = 'background-color: #0e3d6e; color: #ffffff'
        return [color if row['Team'] == MY_TEAM_NAME else '' for _ in row]

    return out.style.apply(highlight_row, axis=1).format({
        'Total_Points': '{:.1f}',
        'OPS':          '{:.3f}',
        'ERA':          '{:.2f}',
        'WHIP':         '{:.2f}',
    })


def _build_my_team_tooltips(my_h, my_p):
    """Build a dict of stat_col → tooltip string for my team's standings row."""
    tips = {}

    # Hitter counting stats — descending
    for stat, fmt in [('R', '{:.0f}'), ('HR', '{:.0f}'), ('RBI', '{:.0f}'), ('SB', '{:.0f}')]:
        rows = (my_h[['Player', stat]].dropna()
                .loc[lambda d: d[stat] > 0]
                .sort_values(stat, ascending=False))
        tips[f'{stat}_total'] = '\n'.join(
            f"{r['Player']}: {fmt.format(r[stat])}" for _, r in rows.iterrows()
        )

    # OPS — descending, only players with PA > 0
    rows = (my_h[['Player', 'OPS', 'PA']].dropna()
            .loc[lambda d: d['PA'] > 0]
            .sort_values('OPS', ascending=False))
    tips['OPS_total'] = '\n'.join(
        f"{r['Player']}: {r['OPS']:.3f}" for _, r in rows.iterrows()
    )

    # Pitcher counting stats — descending
    for stat, fmt in [('IP', '{:.1f}'), ('QS', '{:.0f}'), ('SV', '{:.0f}')]:
        rows = (my_p[['Player', stat]].dropna()
                .loc[lambda d: d[stat] > 0]
                .sort_values(stat, ascending=False))
        tips[f'{stat}_total'] = '\n'.join(
            f"{r['Player']}: {fmt.format(r[stat])}" for _, r in rows.iterrows()
        )

    # ERA and WHIP — ascending (lower is better), only pitchers with IP > 0
    for stat, fmt in [('ERA', '{:.2f}'), ('WHIP', '{:.2f}')]:
        rows = (my_p[['Player', stat, 'IP']].dropna()
                .loc[lambda d: d['IP'] > 0]
                .sort_values(stat, ascending=True))
        tips[f'{stat}_total'] = '\n'.join(
            f"{r['Player']}: {fmt.format(r[stat])}" for _, r in rows.iterrows()
        )

    return tips


def _standings_html_table(standings_df, tooltips, ranks):
    """
    Render standings as an HTML table. My team's row is highlighted blue,
    each stat cell has a tooltip, and stat values are colored by rank position.
    ranks: dict of stat_col → _total_Pts value (1=worst, 12=best).
    """
    stat_cols = ['R_total', 'HR_total', 'RBI_total', 'SB_total', 'OPS_total',
                 'IP_total', 'QS_total', 'SV_total', 'ERA_total', 'WHIP_total']
    display_cols = ['Team', 'Total_Points'] + stat_cols
    headers = ['#', 'Team', 'Pts'] + [c.replace('_total', '') for c in stat_cols]

    def _fmt(col, val):
        if col in INT_COLS:        return f'{int(round(val))}'
        if col == 'OPS_total':     return f'{val:.3f}'
        if col in ('ERA_total', 'WHIP_total'): return f'{val:.2f}'
        return f'{val:g}'

    def _rank_color(pts):
        if pts >= 11: return '#3b82f6'   # 1st–2nd
        if pts >= 8:  return '#22c55e'   # 3rd–5th
        if pts >= 5:  return '#eab308'   # 6th–8th
        return '#ef4444'                 # 9th–12th

    th = ('padding:8px 12px;text-align:left;border-bottom:1px solid #444;'
          'font-size:0.82rem;color:#aaa;font-weight:600;white-space:nowrap;')
    th_r = th + 'text-align:right;'
    td_base = 'padding:6px 12px;border-bottom:1px solid #2a2a2a;font-size:0.88rem;white-space:nowrap;'
    td_num  = td_base + 'text-align:right;font-variant-numeric:tabular-nums;'

    header_html = (f'<th style="{th}">{headers[0]}</th>'
                   f'<th style="{th}">{headers[1]}</th>'
                   + ''.join(f'<th style="{th_r}">{h}</th>' for h in headers[2:]))

    rows_html = []
    for rank, (_, row) in enumerate(standings_df.iterrows(), start=1):
        is_my_team = row['Team'] == MY_TEAM_NAME
        bg       = 'background-color:#0e3d6e;' if is_my_team else ''
        def_color = 'color:#ffffff;' if is_my_team else ''
        style_td     = td_base + bg + def_color
        style_td_num = td_num  + bg + def_color

        cells = [f'<td style="{style_td}">{rank}</td>',
                 f'<td style="{style_td}">{row["Team"]}</td>',
                 f'<td style="{style_td_num}">{row["Total_Points"]:.1f}</td>']

        team_ranks = ranks.get(row['Team'], {})
        for col in stat_cols:
            val      = row[col]
            text     = _fmt(col, val)
            color    = _rank_color(team_ranks.get(col, 0))
            cell_sty = td_num + bg + f'color:{color};'
            if is_my_team and col in tooltips and tooltips[col]:
                tip = tooltips[col].replace('"', '&quot;')
                cells.append(f'<td style="{cell_sty}cursor:default;" title="{tip}">{text}</td>')
            else:
                cells.append(f'<td style="{cell_sty}">{text}</td>')

        rows_html.append(f'<tr>{"".join(cells)}</tr>')

    return (
        '<div style="overflow-x:auto;max-height:500px;overflow-y:auto;">'
        '<table style="border-collapse:collapse;width:100%;background:#0e1117;">'
        f'<thead><tr>{header_html}</tr></thead>'
        f'<tbody>{"".join(rows_html)}</tbody>'
        '</table></div>'
    )


def build_category_chart(baseline, my_team):
    """Horizontal bar chart of my team's rank in each of the 10 Roto categories."""
    cats = ['R', 'HR', 'RBI', 'SB', 'OPS', 'IP', 'QS', 'SV', 'ERA', 'WHIP']
    my_row = baseline[baseline['Team'] == my_team].iloc[0]

    bars, colors, labels = [], [], []
    for cat in cats:
        pts   = my_row.get(f'{cat}_total_Pts', 6.5)
        rank  = int(round(pts))      # 12 = best, 1 = worst
        place = 13 - rank            # 1 = best, 12 = worst

        color = '#22c55e' if rank >= 10 else ('#ef4444' if rank <= 3 else '#f59e0b')
        bars.append(pts)
        colors.append(color)
        labels.append(f'#{place} of 12')

    fig = go.Figure(go.Bar(
        x=bars,
        y=cats,
        orientation='h',
        marker_color=colors,
        text=labels,
        textposition='inside',
        insidetextanchor='middle',
        textfont=dict(color='white', size=13),
    ))
    fig.update_layout(
        title=dict(text=f'{my_team} — Category Rankings', font=dict(size=15)),
        xaxis=dict(range=[0, 12.5], title='Roto Points (higher = better)',
                   showgrid=False, zeroline=False),
        yaxis=dict(autorange='reversed', tickfont=dict(size=13)),
        height=360,
        margin=dict(l=60, r=20, t=40, b=40),
        plot_bgcolor='rgba(0,0,0,0)',
        paper_bgcolor='rgba(0,0,0,0)',
    )
    fig.add_vline(x=6.5, line_dash='dot', line_color='grey', opacity=0.4)
    return fig


# ── HEADER ────────────────────────────────────────────────────────────────────
def _last_updated_caption() -> str:
    """Return 'Data updated: Apr 18, 2026 at 6:00 AM CT' from metadata or mtime fallback."""
    meta_path = FOLDER / 'data_metadata.json'
    if meta_path.exists():
        raw = json.loads(meta_path.read_text())['last_updated_utc']
        ts_utc = datetime.fromisoformat(raw.replace('Z', '+00:00'))
    else:
        fallback = FOLDER / 'espn_current_rosters.csv'
        if not fallback.exists():
            return "Data updated: unknown"
        ts_utc = datetime.fromtimestamp(fallback.stat().st_mtime, tz=timezone.utc)
    ct = ts_utc.astimezone(ZoneInfo('America/Chicago'))
    hour = ct.strftime('%I').lstrip('0') or '12'
    return f"Data updated: {ct.strftime('%b %d, %Y')} at {hour}:{ct.strftime('%M %p')} CT"


st.title("⚾ Fantasy Baseball Command Center")
st.caption(f"Team: **{MY_TEAM_NAME}** | League {LEAGUE_ID} | {YEAR} Season")
st.caption(_last_updated_caption())

st.divider()

# Guard: ensure all required FanGraphs CSVs exist before rendering tabs
_missing_csvs = [pk for pk in set(list(PROJ_OPTIONS.values()) + list(ANALYSIS_PROJ_KEYS.values()))
                 if not (FOLDER / f'Fangraphs_Hitter_{pk}.csv').exists()]
if _missing_csvs:
    st.warning(
        f"FanGraphs CSVs are missing for: **{', '.join(_missing_csvs)}**\n\n"
        "The scheduled GitHub Actions refresh hasn't produced these yet — "
        "check the Actions tab for a failed run."
    )
    st.stop()

# ── TABS ──────────────────────────────────────────────────────────────────────
tab1, tab2, tab3, tab4 = st.tabs(["📊 Standings", "🔄 Best Swaps", "🔍 Best Adds by Position", "⚖️ Trade Evaluator"])


# ════════════════════════════════════════════════════════════════════════════════
# TAB 1 — STANDINGS
# ════════════════════════════════════════════════════════════════════════════════
with tab1:
    proj_name = st.selectbox(
        "Projection System",
        list(PROJ_OPTIONS.keys()),
        key='proj_selector',
        help="Select which projection system to use for standings.",
    )
    proj_key = PROJ_OPTIONS[proj_name]

    with st.spinner(f"Loading {proj_name} projections..."):
        baseline = get_baseline(proj_key)

    # Warn if the FanGraphs CSV for this system is more than 24 hours old
    _fg_csv = FOLDER / f'Fangraphs_Hitter_{proj_key}.csv'
    if _fg_csv.exists():
        _age_h = (time.time() - _fg_csv.stat().st_mtime) / 3600
        if _age_h > 24:
            _updated = time.strftime('%b %d %H:%M', time.localtime(_fg_csv.stat().st_mtime))
            st.warning(f"FanGraphs projections ({proj_name}) are **{_age_h:.0f} h old** "
                       f"(last refreshed {_updated}). The scheduled refresh may have failed — "
                       "check the GitHub Actions tab.")

    standings_df = (baseline[DISPLAY_COLS]
                    .sort_values('Total_Points', ascending=False)
                    .reset_index(drop=True))
    standings_df.index += 1

    my_rank = int(standings_df[standings_df['Team'] == MY_TEAM_NAME].index[0])

    # +/- badge: compare vs previously-viewed projection system
    rank_store = st.session_state.setdefault('rank_by_proj', {})
    prev_proj  = st.session_state.get('last_proj_key')
    prev_rank  = rank_store.get(prev_proj) if (prev_proj and prev_proj != proj_key) else None
    rank_delta = (prev_rank - my_rank) if prev_rank is not None else None

    rank_store[proj_key]              = my_rank
    st.session_state['last_proj_key'] = proj_key

    # Summary metrics row
    my_row = baseline[baseline['Team'] == MY_TEAM_NAME].iloc[0]
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric(
        "Standings",
        f"#{my_rank} of 12",
        delta=f"{rank_delta:+d} spots" if rank_delta is not None else None,
    )
    m2.metric("Roto Points", f"{my_row['Total_Points']:.1f}")
    m3.metric("OPS",         f"{my_row['OPS_total']:.3f}")
    m4.metric("ERA",         f"{my_row['ERA_total']:.2f}")
    m5.metric("WHIP",        f"{my_row['WHIP_total']:.2f}")

    st.divider()

    st.subheader("Projected End-of-Season Standings (YTD + ROS)")
    _merged_h, _merged_p, _ = get_roster_projections(proj_key)
    _tooltips = _build_my_team_tooltips(
        _merged_h[_merged_h['Team'] == MY_TEAM_NAME],
        _merged_p[_merged_p['Team'] == MY_TEAM_NAME],
    )
    _stat_cols = ['R_total', 'HR_total', 'RBI_total', 'SB_total', 'OPS_total',
                  'IP_total', 'QS_total', 'SV_total', 'ERA_total', 'WHIP_total']
    _all_ranks = {
        r['Team']: {col: r.get(f'{col}_Pts', 0) for col in _stat_cols}
        for _, r in baseline.iterrows()
    }
    st.markdown(_standings_html_table(standings_df, _tooltips, _all_ranks), unsafe_allow_html=True)

    st.divider()

    st.subheader(f"Category Rankings — {MY_TEAM_NAME}")
    st.caption("Green = top 3  |  Yellow = middle  |  Red = bottom 3")
    st.plotly_chart(build_category_chart(baseline, MY_TEAM_NAME), width='stretch')

    st.divider()
    st.subheader("Team Rosters — Projected ROS Stats")
    st.caption("Expand any team to see their players and remaining-season projections. "
               "⚠️ marks starting slots with no player assigned.")

    roster_h, roster_p, slot_template = get_roster_projections(proj_key)

    def build_slot_table(team_df, slot_order, slot_template):
        """
        Returns a DataFrame ordered by slot_order, with placeholder rows
        for any expected starting slot that has no player assigned.
        """
        rows = []
        for slot in slot_order:
            expected = slot_template.get(slot, 0)
            if expected == 0 and slot != 'BE':
                continue  # this slot doesn't exist in this league
            slot_players = team_df[team_df['Lineup_Slot'] == slot]
            if slot == 'BE':
                for _, p in slot_players.iterrows():
                    rows.append(p.to_dict())
            else:
                for i in range(expected):
                    if i < len(slot_players):
                        rows.append(slot_players.iloc[i].to_dict())
                    else:
                        empty = {col: None for col in team_df.columns}
                        empty['Lineup_Slot'] = slot
                        empty['Player'] = '⚠️ Empty'
                        rows.append(empty)
        return pd.DataFrame(rows) if rows else pd.DataFrame(columns=team_df.columns)

    for rank, row in standings_df.iterrows():
        team = row['Team']
        pts  = row['Total_Points']
        label = f"#{rank}  {team}  —  {pts:.1f} pts"

        with st.expander(label, expanded=(team == MY_TEAM_NAME)):
            th = roster_h[roster_h['Team'] == team].drop(columns='Team')
            tp = roster_p[roster_p['Team'] == team].drop(columns='Team')

            th_ordered = build_slot_table(th, HITTER_SLOT_ORDER, slot_template)
            tp_ordered = build_slot_table(tp, PITCHER_SLOT_ORDER, slot_template)

            def highlight_empty(row):
                if row.get('Player') == '⚠️ Empty':
                    return ['background-color: #3d1f1f; color: #ef4444'] * len(row)
                return [''] * len(row)

            if not th_ordered.empty:
                st.markdown("**Hitters**")
                st.dataframe(
                    th_ordered.style
                        .apply(highlight_empty, axis=1)
                        .format({
                            'R':   '{:.0f}', 'HR':  '{:.0f}', 'RBI': '{:.0f}',
                            'SB':  '{:.0f}', 'PA':  '{:.0f}', 'OPS': '{:.3f}',
                        }, na_rep='—'),
                    width='stretch',
                    hide_index=True,
                )

            if not tp_ordered.empty:
                st.markdown("**Pitchers**")
                st.dataframe(
                    tp_ordered.style
                        .apply(highlight_empty, axis=1)
                        .format({
                            'IP':   '{:.1f}', 'QS':  '{:.0f}', 'SV':  '{:.0f}',
                            'ERA':  '{:.2f}', 'WHIP': '{:.2f}',
                        }, na_rep='—'),
                    width='stretch',
                    hide_index=True,
                )


# ════════════════════════════════════════════════════════════════════════════════
# TAB 2 — BEST SWAPS
# ════════════════════════════════════════════════════════════════════════════════
with tab2:
    st.subheader("Best Swaps")
    st.caption(
        "Slot-legal swaps vs top 200 free agents across TheBatX, Steamer, and "
        "Depth Charts. A swap appears if it's profitable under **any** system. "
        "First load takes ~30–60 s; cached after that."
    )

    sys_labels = list(ANALYSIS_PROJ_KEYS.keys())

    with st.spinner("Simulating swaps across all 3 projection systems..."):
        swap_frames = {}
        for label, pk in ANALYSIS_PROJ_KEYS.items():
            df = run_swaps_for_system(pk)
            if not df.empty:
                swap_frames[label] = df.rename(columns={
                    'Net Gain': label,
                    'Details':  f'_Details_{label}',
                })

    if swap_frames:
        merge_keys = ['Add', 'Add_Clean', 'Drop', 'Drop_Clean', 'Slot']
        merged_swaps = list(swap_frames.values())[0]
        for df in list(swap_frames.values())[1:]:
            merged_swaps = merged_swaps.merge(df, on=merge_keys, how='outer')

        # Backfill NaN gain cells with the actual computed value (may be negative).
        # These arise when a swap was profitable in one system but not another.
        for label, pk in ANALYSIS_PROJ_KEYS.items():
            nan_mask = merged_swaps[label].isna()
            for idx, row in merged_swaps[nan_mask].iterrows():
                merged_swaps.at[idx, label] = backfill_gain(
                    row['Add_Clean'], row['Drop_Clean'], row['Slot'], pk
                )

        merged_swaps['Avg Gain']  = merged_swaps[sys_labels].mean(axis=1).round(1)
        merged_swaps['Best Gain'] = merged_swaps[sys_labels].max(axis=1).round(1)
        merged_swaps = merged_swaps.sort_values('Avg Gain', ascending=False).reset_index(drop=True)

        detail_col_names = [f'_Details_{l}' for l in sys_labels]

        def _swaps_html_table(df):
            cols_display = ['Add', 'Drop', 'Slot'] + sys_labels + ['Avg Gain', 'Best Gain']
            gain_cols    = set(sys_labels) | {'Avg Gain', 'Best Gain'}

            def _gain_color(v):
                if v >= 4.0: return '#3b82f6'
                if v >= 1.5: return '#22c55e'
                if v >= 0.0: return '#eab308'
                return '#ef4444'

            th_style = (
                'padding:8px 12px;text-align:left;border-bottom:1px solid #444;'
                'font-size:0.82rem;color:#aaa;font-weight:600;white-space:nowrap;'
            )
            td_base = (
                'padding:6px 12px;border-bottom:1px solid #2a2a2a;'
                'font-size:0.88rem;white-space:nowrap;'
            )
            td_num = td_base + 'text-align:right;font-variant-numeric:tabular-nums;'

            header = ''.join(f'<th style="{th_style}">{c}</th>' for c in cols_display)
            rows_html = []
            for _, row in df.iterrows():
                cells = []
                for c in cols_display:
                    val = row.get(c)
                    if c in ('Add', 'Drop', 'Slot'):
                        cells.append(f'<td style="{td_base}">{val if pd.notna(val) else "—"}</td>')
                    elif c in sys_labels:
                        detail_key = f'_Details_{c}'
                        _dv = row.get(detail_key) if detail_key in df.columns else None
                        detail = '' if pd.isna(_dv) else (_dv or '')
                        if pd.notna(val):
                            color = _gain_color(val)
                            tip   = f' title="{detail}"' if detail else ''
                            cells.append(
                                f'<td style="{td_num}color:{color};cursor:default;"{tip}>{val:g}</td>'
                            )
                        else:
                            cells.append(f'<td style="{td_num}color:#555;">—</td>')
                    elif c in gain_cols:
                        if pd.notna(val):
                            color = _gain_color(val)
                            cells.append(f'<td style="{td_num}color:{color};">{val:g}</td>')
                        else:
                            cells.append(f'<td style="{td_num}color:#555;">—</td>')
                    else:
                        cells.append(
                            f'<td style="{td_num}">{val:g}</td>'
                            if pd.notna(val) else f'<td style="{td_num}color:#555;">—</td>'
                        )
                rows_html.append(f'<tr>{"".join(cells)}</tr>')

            return (
                '<div style="overflow-x:auto;max-height:480px;overflow-y:auto;">'
                '<table style="border-collapse:collapse;width:100%;background:#0e1117;">'
                f'<thead><tr>{header}</tr></thead>'
                f'<tbody>{"".join(rows_html)}</tbody>'
                '</table></div>'
            )

        st.success(
            f"Found **{len(merged_swaps)}** profitable swap(s). "
            "Hover over a gain value to see the category breakdown."
        )
        st.markdown(_swaps_html_table(merged_swaps), unsafe_allow_html=True)
    else:
        st.info("No profitable swaps found across any projection system.")


# ════════════════════════════════════════════════════════════════════════════════
# TAB 3 — BEST ADDS BY POSITION
# ════════════════════════════════════════════════════════════════════════════════
with tab3:
    st.subheader("Best Adds by Position")
    st.caption(
        "Simulates adding a free agent to your roster with no drop. "
        "Net Gain = increase in projected Roto points across all 3 systems."
    )

    sys_labels = list(ANALYSIS_PROJ_KEYS.keys())

    selected_pos = st.radio(
        "Select position",
        POSITIONS,
        horizontal=True,
        label_visibility="collapsed",
        key='pos_radio',
    )

    with st.spinner(f"Simulating {selected_pos} adds across all 3 projection systems..."):
        add_frames = {}
        for label, pk in ANALYSIS_PROJ_KEYS.items():
            df = run_best_adds_for_system(pk, selected_pos)
            if not df.empty:
                add_frames[label] = df.rename(columns={
                    'Net Gain': label,
                    'Details':  f'_Details_{label}',
                })

    if add_frames:
        merge_keys  = ['Player', 'Clean_Name']
        merged_adds = list(add_frames.values())[0]
        for df in list(add_frames.values())[1:]:
            merged_adds = merged_adds.merge(df, on=merge_keys, how='outer')

        # Backfill NaN gain cells with actual (possibly negative) values.
        for label, pk in ANALYSIS_PROJ_KEYS.items():
            nan_mask = merged_adds[label].isna()
            for idx, row in merged_adds[nan_mask].iterrows():
                merged_adds.at[idx, label] = backfill_add_gain(row['Clean_Name'], pk)

        merged_adds['Avg Gain']  = merged_adds[sys_labels].mean(axis=1).round(1)
        merged_adds['Best Gain'] = merged_adds[sys_labels].max(axis=1).round(1)
        merged_adds = merged_adds.sort_values('Avg Gain', ascending=False).reset_index(drop=True)

        def _adds_html_table(df):
            cols_display = ['Player'] + sys_labels + ['Avg Gain', 'Best Gain']
            gain_cols    = set(sys_labels) | {'Avg Gain', 'Best Gain'}

            def _gain_color(v):
                if v >= 4.0: return '#3b82f6'
                if v >= 1.5: return '#22c55e'
                if v >= 0.0: return '#eab308'
                return '#ef4444'

            th_style = (
                'padding:8px 12px;text-align:left;border-bottom:1px solid #444;'
                'font-size:0.82rem;color:#aaa;font-weight:600;white-space:nowrap;'
            )
            td_base = (
                'padding:6px 12px;border-bottom:1px solid #2a2a2a;'
                'font-size:0.88rem;white-space:nowrap;'
            )
            td_num = td_base + 'text-align:right;font-variant-numeric:tabular-nums;'

            header = ''.join(f'<th style="{th_style}">{c}</th>' for c in cols_display)
            rows_html = []
            for _, row in df.iterrows():
                cells = []
                for c in cols_display:
                    val = row.get(c)
                    if c == 'Player':
                        cells.append(f'<td style="{td_base}">{val if pd.notna(val) else "—"}</td>')
                    elif c in sys_labels:
                        detail_key = f'_Details_{c}'
                        _dv = row.get(detail_key) if detail_key in df.columns else None
                        detail = '' if pd.isna(_dv) else (_dv or '')
                        if pd.notna(val):
                            color = _gain_color(val)
                            tip   = f' title="{detail}"' if detail else ''
                            cells.append(
                                f'<td style="{td_num}color:{color};cursor:default;"{tip}>{val:g}</td>'
                            )
                        else:
                            cells.append(f'<td style="{td_num}color:#555;">—</td>')
                    elif c in gain_cols:
                        if pd.notna(val):
                            color = _gain_color(val)
                            cells.append(f'<td style="{td_num}color:{color};">{val:g}</td>')
                        else:
                            cells.append(f'<td style="{td_num}color:#555;">—</td>')
                    else:
                        cells.append(
                            f'<td style="{td_num}">{val:g}</td>'
                            if pd.notna(val) else f'<td style="{td_num}color:#555;">—</td>'
                        )
                rows_html.append(f'<tr>{"".join(cells)}</tr>')

            return (
                '<div style="overflow-x:auto;max-height:480px;overflow-y:auto;">'
                '<table style="border-collapse:collapse;width:100%;background:#0e1117;">'
                f'<thead><tr>{header}</tr></thead>'
                f'<tbody>{"".join(rows_html)}</tbody>'
                '</table></div>'
            )

        st.success(
            f"Found **{len(merged_adds)}** candidate(s). "
            "Hover over a gain value to see the category breakdown."
        )
        st.markdown(_adds_html_table(merged_adds), unsafe_allow_html=True)
    else:
        st.info(f"No {selected_pos} free agents found with matching projections.")


# ════════════════════════════════════════════════════════════════════════════════
# TAB 4 — TRADE EVALUATOR
# ════════════════════════════════════════════════════════════════════════════════
with tab4:
    st.subheader("Trade / Waiver Pickup Evaluator")
    st.caption("Results shown across TheBatX, Steamer, and Depth Charts projections.")

    col_a, col_b = st.columns(2)
    with col_a:
        acquire_raw = st.text_input(
            "Players I'm acquiring",
            placeholder="e.g. Grayson Rodriguez, Chris Martin",
            key='acquire_input',
        )
    with col_b:
        drop_raw = st.text_input(
            "Players I'm giving up",
            placeholder="e.g. Seth Lugo, Adrian Morejon",
            key='drop_input',
        )

    partner_raw = st.text_input(
        "Trade partner team name  (leave blank for waiver pickup)",
        placeholder="e.g. Exit Velo Vets",
        key='partner_input',
    )

    evaluate = st.button("Evaluate Trade", type="primary")

    if evaluate:
        if not acquire_raw.strip() or not drop_raw.strip():
            st.warning("Enter at least one player to acquire and one to give up.")
        else:
            df_rosters, df_current, _ = load_espn_data()

            players_to_acquire = [p.strip() for p in acquire_raw.split(',') if p.strip()]
            players_to_drop    = [p.strip() for p in drop_raw.split(',')    if p.strip()]
            trade_partner      = partner_raw.strip() or None

            acquire_cleaned = [aggressive_clean(p) for p in players_to_acquire]
            drop_cleaned    = [aggressive_clean(p) for p in players_to_drop]

            # Build the simulated roster once (same for all projection systems)
            df_sim = df_rosters.copy()
            warnings_shown = set()

            for raw, cleaned in zip(players_to_drop, drop_cleaned):
                mask = df_sim['Clean_Name'] == cleaned
                if mask.any():
                    if trade_partner:
                        df_sim.loc[mask, 'Team'] = trade_partner
                    else:
                        df_sim = df_sim[~mask]
                elif raw not in warnings_shown:
                    st.warning(f"**{raw}** not found on your active roster.")
                    warnings_shown.add(raw)

            for raw, cleaned in zip(players_to_acquire, acquire_cleaned):
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

            with st.spinner("Running simulation across all 3 projection systems..."):
                trade_results = {}
                for label, pk in ANALYSIS_PROJ_KEYS.items():
                    df_h, df_p   = load_projections(pk)
                    baseline     = get_baseline(pk)
                    base_my_team = baseline.set_index('Team').loc[MY_TEAM_NAME]
                    sim_stats    = get_league_stats(df_sim, df_h, df_p, df_current)
                    sim_my_team  = sim_stats.set_index('Team').loc[MY_TEAM_NAME]
                    gain         = sim_my_team['Total_Points'] - base_my_team['Total_Points']
                    impact_str   = get_impact_string(base_my_team, sim_my_team, baseline)
                    trade_results[label] = {
                        'gain':       gain,
                        'impact_str': impact_str,
                        'sim_stats':  sim_stats,
                        'baseline':   baseline,
                    }

            st.divider()

            # ── Summary metrics row ───────────────────────────────────────────
            gains     = [r['gain'] for r in trade_results.values()]
            avg_gain  = sum(gains) / len(gains)
            sign      = '+' if avg_gain > 0 else ''
            clr_class = 'green' if avg_gain > 0 else ('red' if avg_gain < 0 else '')
            st.markdown(
                f'<p class="big-number {clr_class}">{sign}{avg_gain:.1f} pts avg</p>',
                unsafe_allow_html=True,
            )

            cols = st.columns(len(ANALYSIS_PROJ_KEYS))
            for col, (label, res) in zip(cols, trade_results.items()):
                g    = res['gain']
                s    = '+' if g > 0 else ''
                col.metric(label, f"{s}{g:.1f} pts", delta=f"{s}{g:.1f}",
                           delta_color="normal" if g >= 0 else "inverse")

            st.divider()

            # ── Per-system category shifts + standings preview ────────────────
            for label, res in trade_results.items():
                with st.expander(f"{label} — Category Shifts & Standings", expanded=False):
                    st.write(f"**Category shifts:** {res['impact_str']}")
                    sim_standings = (res['sim_stats'][DISPLAY_COLS]
                                     .sort_values('Total_Points', ascending=False)
                                     .reset_index(drop=True))
                    sim_standings.index += 1
                    st.dataframe(
                        style_standings(sim_standings.head(5)),
                        width='stretch',
                    )
