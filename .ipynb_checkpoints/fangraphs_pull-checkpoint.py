import requests
import pandas as pd

def fetch_fangraphs_projections(proj_type="steamer_ros"):
    """
    Fetches ROS projections directly from the FanGraphs backend API.
    """
    print(f"Fetching FanGraphs Projections ({proj_type})...")
    
    # Map clean names to FanGraphs' internal API acronyms
    type_map = {
        "steamer_ros": "steamerr",
        "depth_charts_ros": "rfangraphsdc",
        "zips_ros": "rzips",
        "thebat_ros": "rthebat",
        "thebatx_ros": "rthebatx"
    }
    
    fg_type = type_map.get(proj_type.lower(), "steamerr") # Defaults to Steamer ROS
    
    # 1. Security Headers: Spoof a legitimate browser request
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "Referer": "https://www.fangraphs.com/projections",
        "Origin": "https://www.fangraphs.com",
    }
    
    url = "https://www.fangraphs.com/api/projections"
    
    # 2. Required Parameters
    hitters_params = {
        "pos": "all",
        "stats": "bat",
        "type": fg_type,
        "team": "0",
        "lg": "all",
        "players": "0"
    }
    
    pitchers_params = {
        "pos": "all",
        "stats": "pit",
        "type": fg_type,
        "team": "0",
        "lg": "all",
        "players": "0"
    }
    
    # ==========================================
    # Fetch Hitters
    # ==========================================
    print(" -> Pulling hitters...")
    h_response = requests.get(url, headers=headers, params=hitters_params)
    h_json = h_response.json()
    
    if isinstance(h_json, dict) and 'Message' in h_json:
        raise ValueError(f"FanGraphs rejected the Hitters request: {h_json.get('Message')}")
    
    h_data = h_json.get('data', h_json) if isinstance(h_json, dict) else h_json
    df_hitters = pd.DataFrame(h_data)
    
    # ==========================================
    # Fetch Pitchers
    # ==========================================
    print(" -> Pulling pitchers...")
    p_response = requests.get(url, headers=headers, params=pitchers_params)
    p_json = p_response.json()
    
    if isinstance(p_json, dict) and 'Message' in p_json:
        raise ValueError(f"FanGraphs rejected the Pitchers request: {p_json.get('Message')}")
        
    p_data = p_json.get('data', p_json) if isinstance(p_json, dict) else p_json
    df_pitchers = pd.DataFrame(p_data)
    
    # ==========================================
    # Cleanup & Formatting
    # ==========================================
    # Rename 'PlayerName' to 'Player' to match your app logic
    df_hitters = df_hitters.rename(columns={'PlayerName': 'Player'}, errors='ignore')
    df_pitchers = df_pitchers.rename(columns={'PlayerName': 'Player'}, errors='ignore')
    
    print("✓ FanGraphs data successfully loaded.")
    return df_hitters, df_pitchers


# ==========================================
# Execution Block
# ==========================================
if __name__ == "__main__":
    # You can change this to "depth_charts_ros", "thebatx_ros", etc.
    df_hitters_proj, df_pitchers_proj = fetch_fangraphs_projections("steamer_ros")
    
    # Save directly to the CSVs your main app is looking for
    df_hitters_proj.to_csv("Fangraphs_Hitter_Projections_ROS.csv", index=False)
    df_pitchers_proj.to_csv("Fangraphs_Pitcher_Projections_ROS.csv", index=False)
    
    print("Data saved to CSVs! Your Command Center is ready to read them.")