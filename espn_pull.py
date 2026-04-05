import requests
import pandas as pd
import json
from espn_api.baseball import League

# Map ESPN Slot IDs to human-readable labels for our Slot-Aware Gatekeeper
SLOT_MAP = {
    0: 'C', 1: '1B', 2: '2B', 3: '3B', 4: 'SS', 5: 'OF', 
    6: '2B/SS', 7: '1B/3B', 12: 'UTIL', 13: 'P', 14: 'SP', 15: 'RP', 16: 'BE'
}

def fetch_espn_data(league_id, year):
    """
    Extracts live ACTIVE rosters (skips IL) and current YTD stats.
    """
    print(f"Connecting to ESPN Fantasy API for League {league_id}...")
    league = League(league_id=league_id, year=year)
    print(f"✓ Successfully connected to: {league.settings.name}")

    # 1. ROSTERS (Active Only)
    roster_data = []
    il_slots = ['IL', 'IR', 'Injured', 'Injured Reserve']
    
    for team in league.teams:
        for player in team.roster:
            if player.lineupSlot in il_slots:
                continue
            roster_data.append({
                'Team': team.team_name,
                'Player': player.name,
                'Positions': '/'.join(player.eligibleSlots),
                'Lineup_Slot': player.lineupSlot,
                'Status': 'Rostered'
            })
    df_rosters = pd.DataFrame(roster_data)

    # 2. YTD STATS 
    headers = {"User-Agent": "Mozilla/5.0"}
    url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/{year}/segments/0/leagues/{league_id}?view=mTeam"
    response = requests.get(url, headers=headers)
    
    if response.status_code != 200:
        raise ConnectionError(f"ESPN returned status code {response.status_code}")
        
    raw_data = response.json()
    team_id_to_name = {team.team_id: team.team_name for team in league.teams}
    
    stats_data = []
    for team_json in raw_data.get('teams', []):
        team_id = team_json.get('id')
        team_name = team_id_to_name.get(team_id, "Unknown Team")
        team_stats = {'Team': team_name}
        
        stats_source = team_json.get('valuesByStat') or team_json.get('record', {}).get('overall', {}).get('stats', {})
        if not isinstance(stats_source, dict): stats_source = {}
        
        # Stat IDs mapping
        team_stats['R_curr'] = stats_source.get('20', 0.0)
        team_stats['HR_curr'] = stats_source.get('5', 0.0)
        team_stats['RBI_curr'] = stats_source.get('21', 0.0)
        team_stats['SB_curr'] = stats_source.get('23', 0.0)
        
        ab, h, d, t, hr, bb, hbp, sf = [stats_source.get(i, 0.0) for i in ['0', '1', '3', '4', '5', '10', '12', '14']]
        tb = (h - d - t - hr) + (2 * d) + (3 * t) + (4 * hr)
        slg = tb / ab if ab > 0 else 0.0
        obp_den = ab + bb + hbp + sf
        obp = (h + bb + hbp) / obp_den if obp_den > 0 else 0.0
        team_stats['OPS_curr'] = round(obp + slg, 4)
        team_stats['PA_curr'] = stats_source.get('33', 0.0) if stats_source.get('33', 0.0) > 0 else obp_den
        
        outs = stats_source.get('34', 0.0)
        team_stats['IP_curr'] = round(outs / 3.0, 2)
        team_stats['QS_curr'] = stats_source.get('63', 0.0)
        team_stats['SV_curr'] = stats_source.get('57', 0.0)
        team_stats['ER_curr'] = stats_source.get('45', 0.0)
        team_stats['H_curr'] = stats_source.get('37', 0.0)
        team_stats['BB_curr'] = stats_source.get('39', 0.0) 
        team_stats['ERA_curr'] = round(stats_source.get('47', 0.0), 3)
        team_stats['WHIP_curr'] = round(stats_source.get('41', 0.0), 3)
            
        stats_data.append(team_stats)
        
    return df_rosters, pd.DataFrame(stats_data)

def fetch_espn_free_agents(league_id, year):
    """
    Hits the ESPN Kona API for the real Free Agent list and their eligible slots.
    """
    print(f"Connecting to ESPN for Real-Time Free Agents...")
    filter_data = {
        "players": {
            "filterStatus": {"value": ["FREEAGENT", "WAIVERS"]},
            "filterSlotIds": {"value": [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 19]},
            "limit": 200,
            "sortPercOwned": {"sortAsc": False, "sortPriority": 1}
        }
    }
    headers = {"User-Agent": "Mozilla/5.0", "x-fantasy-filter": json.dumps(filter_data)}
    url = f"https://lm-api-reads.fantasy.espn.com/apis/v3/games/flb/seasons/{year}/segments/0/leagues/{league_id}?view=kona_player_info"
    
    response = requests.get(url, headers=headers)
    data = response.json()
    
    fa_list = []
    for p_wrapper in data.get('players', []):
        p = p_wrapper.get('player', {})
        # Use the SLOT_MAP to get text-based positions (e.g., '1B/3B/UTIL')
        slots = [SLOT_MAP.get(s) for s in p.get('eligibleSlots', []) if s in SLOT_MAP]
        
        fa_list.append({
            'Player': p.get('fullName'),
            'Positions': '/'.join(filter(None, slots)),
            'Status': 'Available'
        })
    
    print(f"✓ Found {len(fa_list)} real ESPN Free Agents with position data.")
    return pd.DataFrame(fa_list)