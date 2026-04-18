from pathlib import Path

MY_TEAM_NAME       = "Budget Ballers"
LEAGUE_ID          = 283187668
YEAR               = 2026
FOLDER             = Path(__file__).resolve().parent
PROJECTION_SYSTEMS = ["thebatx_ros", "steamer_ros", "depth_charts_ros"]

PROJ_OPTIONS = {
    "Steamer ROS":      "steamer_ros",
    "Depth Charts ROS": "depth_charts_ros",
    "ZiPS ROS":         "zips_ros",
    "THE BAT ROS":      "thebat_ros",
    "THE BATx ROS":     "thebatx_ros",
}
