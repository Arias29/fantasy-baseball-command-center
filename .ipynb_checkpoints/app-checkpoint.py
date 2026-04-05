import streamlit as st
import pandas as pd
import numpy as np
import os

# ==========================================
# PAGE CONFIGURATION
# ==========================================
st.set_page_config(page_title="Fantasy Baseball Command Center", layout="wide")
st.title("⚾ Budget Ballers Command Center")

# ==========================================
# DATA LOADING (Cached for speed)
# ==========================================
# The @st.cache_data decorator tells Streamlit to only load these files once.
# This prevents the app from reloading CSVs every time you click a button!
@st.cache_data
def load_and_clean_data():
    FOLDER = './' # Looks in the exact same folder as app.py
    
    df_rosters = pd.read_csv(FOLDER + 'espn_current_rosters.csv', encoding='ISO-8859-1')
    df_hitters = pd.read_csv(FOLDER + 'Fangraphs_Hitter_Projections_ROS.csv', encoding='ISO-8859-1')
    df_pitchers = pd.read_csv(FOLDER + 'Fangraphs_Pitcher_Projections_ROS.csv', encoding='ISO-8859-1')
    df_current = pd.read_csv(FOLDER + 'current_team_stats.csv', encoding='ISO-8859-1')
    
    # Clean formatting
    df_rosters['Team'] = df_rosters['Team'].astype(str).str.strip()
    df_current['Team'] = df_current['Team'].astype(str).str.strip()

    def clean_stat(value):
        if isinstance(value, str) and '%' in value:
            return float(value.replace('%', '')) / 100
        try: return float(value)
        except: return 0.0

    for df in [df_rosters, df_hitters, df_pitchers]:
        name_col = 'Player' if 'Player' in df.columns else 'Name'
        df['Clean_Name'] = df[name_col].str.replace(' Jr.', '', regex=False).str.replace(' II', '', regex=False)

    hit_stats = ['R', 'HR', 'RBI', 'SB', 'OPS', 'PA']
    pit_stats = ['IP', 'QS', 'SV', 'ER', 'H', 'BB']

    for col in hit_stats: df_hitters[col] = df_hitters[col].apply(clean_stat)
    for col in pit_stats: df_pitchers[col] = df_pitchers[col].apply(clean_stat)
    
    # Filter IL Players
    il_slots = ['IL', 'IR', 'Injured', 'Injured Reserve']
    active_roster = df_rosters[(df_rosters['Status'] == 'Rostered') & 
                               (~df_rosters['Lineup_Slot'].str.contains('|'.join(il_slots), na=False))].copy()
                               
    return active_roster, df_hitters, df_pitchers, df_current, df_rosters

try:
    active_roster_full, df_hitters, df_pitchers, df_current, df_rosters_full = load_and_clean_data()
except FileNotFoundError:
    st.error("⚠️ Could not find your CSV files. Please make sure they are in the same folder as app.py")
    st.stop()

# ==========================================
# CORE CALCULATION ENGINE
# ==========================================
def get_league_stats(rosters_df):
    hit_stats = ['R', 'HR', 'RBI', 'SB', 'OPS', 'PA']
    pit_stats = ['IP', 'QS', 'SV', 'ER', 'H', 'BB']
    
    merged_h = pd.merge(rosters_df, df_hitters[['Clean_Name'] + hit_stats], on='Clean_Name', how='left').fillna(0)
    merged_p = pd.merge(rosters_df, df_pitchers[['Clean_Name'] + pit_stats], on='Clean_Name', how='left').fillna(0)
    
    t_hit = merged_h.groupby('Team').agg({'R':'sum','HR':'sum','RBI':'sum','SB':'sum','PA':'sum'}).reset_index()
    merged_h['OPS_num'] = merged_h['OPS'] * merged_h['PA']
    t_hit = pd.merge(t_hit, merged_h.groupby('Team')['OPS_num'].sum().reset_index(), on='Team')
    t_pit = merged_p.groupby('Team').agg({'IP':'sum','QS':'sum','SV':'sum','ER':'sum','H':'sum','BB':'sum'}).reset_index()
    
    proj_stats = pd.merge(t_hit, t_pit, on='Team')
    stats = pd.merge(df_current, proj_stats, on='Team', suffixes=('_curr', '_proj'))
    
    stats['R'] = stats['R_curr'] + stats['R']
    stats['HR'] = stats['HR_curr'] + stats['HR']
    stats['RBI'] = stats['RBI_curr'] + stats['RBI']
    stats['SB'] = stats['SB_curr'] + stats['SB']
    stats['QS'] = stats['QS_curr'] + stats['QS']
    stats['SV'] = stats['SV_curr'] + stats['SV']
    
    stats['PA'] = stats['PA_curr'] + stats['PA']
    stats['OPS'] = ((stats['OPS_curr'] * stats['PA_curr']) + stats['OPS_num']) / stats['PA'].replace(0, 1)
    
    stats['IP'] = stats['IP_curr'] + stats['IP']
    stats['ER'] = stats['ER_curr'] + stats['ER']
    stats['H'] = stats['H_curr'] + stats['H']
    stats['BB'] = stats['BB_curr'] + stats['BB']
    
    stats['ERA'] = (stats['ER'] / stats['IP'].replace(0, 1)) * 9
    stats['WHIP'] = (stats['H'] + stats['BB'] ) / stats['IP'].replace(0, 1)
    
    for s in ['R', 'HR', 'RBI', 'SB', 'OPS', 'IP', 'QS', 'SV']:
        stats[f'{s}_Pts'] = stats[s].rank(method='min')
    stats['ERA_Pts'] = stats['ERA'].rank(method='min', ascending=False)
    stats['WHIP_Pts'] = stats['WHIP'].rank(method='min', ascending=False)
    
    stats['Total_Points'] = stats[[c for c in stats.columns if '_Pts' in c]].sum(axis=1)
    return stats

def get_impact_string(old_row, new_row, pt_cols):
    impacts = []
    for col in pt_cols:
        diff = new_row[col] - old_row[col]
        if diff != 0:
            label = col.replace('_Pts', '')
            impacts.append(f"{'+' if diff > 0 else ''}{int(diff)} {label}")
    return ", ".join(impacts) if impacts else "Lateral move"

# Calculate baselines
baseline_stats = get_league_stats(active_roster_full)
display_cols = ['Team', 'Total_Points', 'R', 'HR', 'RBI', 'SB', 'OPS', 'IP', 'QS', 'SV', 'ERA', 'WHIP']
pt_cols = [c for c in baseline_stats.columns if '_Pts' in c]

# ==========================================
# USER INTERFACE
# ==========================================
# Sidebar for global settings
st.sidebar.header("Settings")
team_list = df_rosters_full['Team'].dropna().unique().tolist()
# Default to Budget Ballers if it exists, otherwise pick the first team
default_index = team_list.index("Budget Ballers") if "Budget Ballers" in team_list else 0
MY_TEAM_NAME = st.sidebar.selectbox("Select Your Team", team_list, index=default_index)

# Create tabs for different features
tab1, tab2, tab3 = st.tabs(["📊 Current Standings", "🔄 Trade Simulator", "🔍 FA Position Finder"])

with tab1:
    st.subheader("Projected End of Season Standings (YTD + ROS)")
    standings = baseline_stats[display_cols].sort_values(by='Total_Points', ascending=False).reset_index(drop=True)
    standings.index += 1
    # Format the dataframe cleanly for the web
    st.dataframe(standings.style.format({'Total_Points': '{:.1f}', 'OPS': '{:.3f}', 'ERA': '{:.2f}', 'WHIP': '{:.2f}'}), use_container_width=True)

with tab2:
    st.subheader("Simulate a Trade or Add/Drop")
    col1, col2 = st.columns(2)
    
    all_players = df_rosters_full['Clean_Name'].dropna().unique().tolist()
    my_players = active_roster_full[active_roster_full['Team'] == MY_TEAM_NAME]['Clean_Name'].tolist()
    
    with col1:
        acquire_list = st.multiselect("Players to Acquire", options=all_players)
    with col2:
        drop_list = st.multiselect("Players to Drop / Trade Away", options=my_players)
        
    trade_partner = st.selectbox("Trade Partner (Leave blank if Free Agent move)", [""] + team_list)
    
    if st.button("Run Simulation", type="primary"):
        if not acquire_list and not drop_list:
            st.warning("Please select players to simulate.")
        else:
            df_sim = active_roster_full.copy()
            
            for player in drop_list:
                if trade_partner:
                    df_sim.loc[df_sim['Clean_Name'] == player, 'Team'] = trade_partner
                else:
                    df_sim = df_sim[df_sim['Clean_Name'] != player]
                    
            for player in acquire_list:
                if player in df_sim['Clean_Name'].values:
                    df_sim.loc[df_sim['Clean_Name'] == player, 'Team'] = MY_TEAM_NAME
                else:
                    new_row = pd.DataFrame([{'Team': MY_TEAM_NAME, 'Clean_Name': player, 'Status': 'Rostered', 'Lineup_Slot': 'BE'}])
                    df_sim = pd.concat([df_sim, new_row], ignore_index=True)
            
            sim_stats = get_league_stats(df_sim)
            base_my_team = baseline_stats.set_index('Team').loc[MY_TEAM_NAME]
            sim_my_team = sim_stats.set_index('Team').loc[MY_TEAM_NAME]
            
            gain = sim_my_team['Total_Points'] - base_my_team['Total_Points']
            
            st.success(f"**Net Point Change:** {'+' if gain > 0 else ''}{gain:.1f} Points")
            st.info(f"**Category Shifts:** {get_impact_string(base_my_team, sim_my_team, pt_cols)}")
            
            st.write("---")
            st.write("### New Standings Preview")
            sim_standings = sim_stats[display_cols].sort_values(by='Total_Points', ascending=False).reset_index(drop=True)
            sim_standings.index += 1
            st.dataframe(sim_standings.style.format({'Total_Points': '{:.1f}', 'OPS': '{:.3f}', 'ERA': '{:.2f}', 'WHIP': '{:.2f}'}), use_container_width=True)

with tab3:
    st.subheader("Find Best Available Free Agent by Position")
    
    pos_col1, pos_col2 = st.columns([1, 3])
    with pos_col1:
        target_pos = st.selectbox("Position", ["C", "1B", "2B", "3B", "SS", "OF", "SP", "RP"])
    with pos_col2:
        st.write("") # Spacing
        st.write(f"Searching pool for the best available **{target_pos}** that maximizes your End of Season roto points.")
    
    if st.button(f"Search for {target_pos}"):
        with st.spinner('Running simulations...'):
            is_pitcher = target_pos in ['SP', 'RP', 'P']
            available_fas = df_rosters_full[df_rosters_full['Status'] == 'Available'].copy()
            available_fas = available_fas[available_fas['Positions'].astype(str).str.contains(rf'\b{target_pos}\b', regex=True, na=False)]
            fa_names = available_fas['Clean_Name'].tolist()
            
            if is_pitcher:
                candidates = df_pitchers[df_pitchers['Clean_Name'].isin(fa_names)].sort_values(by='IP', ascending=False).head(40)
            else:
                candidates = df_hitters[df_hitters['Clean_Name'].isin(fa_names)].sort_values(by='PA', ascending=False).head(40)
            
            fill_results = []
            base_my_team = baseline_stats.set_index('Team').loc[MY_TEAM_NAME]
            
            for _, add_player in candidates.iterrows():
                df_temp = active_roster_full.copy()
                new_row = pd.DataFrame([{'Team': MY_TEAM_NAME, 'Clean_Name': add_player['Clean_Name'], 'Status': 'Rostered', 'Lineup_Slot': target_pos}])
                df_temp = pd.concat([df_temp, new_row], ignore_index=True)
                
                new_stats = get_league_stats(df_temp).set_index('Team').loc[MY_TEAM_NAME]
                gain = new_stats['Total_Points'] - base_my_team['Total_Points']
                
                fill_results.append({
                    'Player': add_player['Clean_Name'], 
                    'Net Roto Gain': gain, 
                    'Details': get_impact_string(base_my_team, new_stats, pt_cols)
                })
            
            if fill_results:
                df_results = pd.DataFrame(fill_results).sort_values(by='Net Roto Gain', ascending=False).head(10)
                st.dataframe(df_results, use_container_width=True)
            else:
                st.warning(f"No available {target_pos}s found in the free agent pool with projections.")