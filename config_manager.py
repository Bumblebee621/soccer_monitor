import json
import os
import logging
from typing import Dict, List, Set

logger = logging.getLogger("ConfigManager")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

SUPPORTED_LEAGUES = {
    # Top 5 European Leagues
    "Premier League": {"api_id": 39, "polymarket_tags": ["premier league", "epl"]},
    "La Liga": {"api_id": 140, "polymarket_tags": ["la liga", "laliga"]},
    "Serie A": {"api_id": 135, "polymarket_tags": ["serie a"]},
    "Bundesliga": {"api_id": 78, "polymarket_tags": ["bundesliga"]},
    "Ligue 1": {"api_id": 61, "polymarket_tags": ["ligue 1"]},
    # Other European Leagues
    "Eredivisie": {"api_id": 88, "polymarket_tags": ["eredivisie"]},
    "Liga Portugal": {"api_id": 94, "polymarket_tags": ["liga portugal", "primeira liga"]},
    "Scottish Premiership": {"api_id": 179, "polymarket_tags": ["scottish premiership", "spfl"]},
    "Turkish Süper Lig": {"api_id": 203, "polymarket_tags": ["super lig", "süper lig", "turkish super lig"]},
    # International Tournaments
    "World Cup": {"api_id": 1, "polymarket_tags": ["fifa world cup", "world cup"]},
    "Euro Championship": {"api_id": 4, "polymarket_tags": ["euro", "european championship", "uefa euro"]},
    "Copa America": {"api_id": 9, "polymarket_tags": ["copa america", "copa américa"]},
    # UEFA Club Competitions
    "Champions League": {"api_id": 2, "polymarket_tags": ["champions league", "ucl"]},
    "Europa League": {"api_id": 3, "polymarket_tags": ["europa league"]},
    "Conference League": {"api_id": 848, "polymarket_tags": ["conference league", "europa conference league"]},
    # Domestic Cups
    "FA Cup": {"api_id": 45, "polymarket_tags": ["fa cup"]},
    "Copa del Rey": {"api_id": 143, "polymarket_tags": ["copa del rey"]},
    # Americas
    "MLS": {"api_id": 253, "polymarket_tags": ["mls", "major league soccer"]},
    "Copa Libertadores": {"api_id": 13, "polymarket_tags": ["copa libertadores", "libertadores"]},
    "Brazilian Série A": {"api_id": 71, "polymarket_tags": ["brasileirão", "serie a brazil", "brazilian serie a"]},
    # Other
    "Saudi Pro League": {"api_id": 307, "polymarket_tags": ["saudi pro league", "saudi league", "spl"]},
}

DEFAULT_ACTIVE_LEAGUES = [
    "Premier League",
    "La Liga",
    "Serie A",
    "Bundesliga",
    "Ligue 1",
    "World Cup"
]

class RuntimeConfig:
    def __init__(self, filepath=None):
        if filepath is None:
            filepath = os.path.join(SCRIPT_DIR, "config_state.json")
        self.filepath = filepath
        
        # Default settings
        self.global_threshold = float(os.getenv("ASK_PRICE_THRESHOLD", "0.10"))
        self.active_leagues: List[str] = list(DEFAULT_ACTIVE_LEAGUES)
        self.league_thresholds: Dict[str, float] = {} # e.g. {"La Liga": 0.08}
        
        self.load()

    def load(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, 'r') as f:
                    data = json.load(f)
                self.global_threshold = data.get("global_threshold", self.global_threshold)
                self.active_leagues = data.get("active_leagues", self.active_leagues)
                self.league_thresholds = data.get("league_thresholds", self.league_thresholds)
                logger.info(f"Loaded config from {self.filepath}")
            except Exception as e:
                logger.error(f"Failed to load config: {e}")
        else:
            self.save() # Create initial file

    def save(self):
        data = {
            "global_threshold": self.global_threshold,
            "active_leagues": self.active_leagues,
            "league_thresholds": self.league_thresholds,
        }
        try:
            with open(self.filepath, 'w') as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save config: {e}")

    def get_threshold(self, league_name: str) -> float:
        # Match polymorphic league name string to our standardized supported leagues
        matched_league = self.match_league_name(league_name)
        if matched_league and matched_league in self.league_thresholds:
            return self.league_thresholds[matched_league]
        return self.global_threshold
        
    def set_global_threshold(self, threshold: float):
        self.global_threshold = threshold
        self.save()
        
    def set_league_threshold(self, league_name: str, threshold: float):
        if league_name in SUPPORTED_LEAGUES:
            self.league_thresholds[league_name] = threshold
            self.save()
            
    def remove_league_threshold(self, league_name: str):
        if league_name in self.league_thresholds:
            del self.league_thresholds[league_name]
            self.save()
            
    def toggle_league(self, league_name: str) -> bool:
        """Toggles a league's active state. Returns the new state (True if active)."""
        if league_name not in SUPPORTED_LEAGUES:
            return False
            
        if league_name in self.active_leagues:
            self.active_leagues.remove(league_name)
            self.save()
            return False
        else:
            self.active_leagues.append(league_name)
            self.save()
            return True

    def get_active_api_ids(self) -> Set[int]:
        ids = set()
        for l in self.active_leagues:
            if l in SUPPORTED_LEAGUES:
                ids.add(SUPPORTED_LEAGUES[l]["api_id"])
        return ids

    def get_active_polymarket_tags(self) -> Set[str]:
        tags = set()
        for l in self.active_leagues:
            if l in SUPPORTED_LEAGUES:
                tags.update(SUPPORTED_LEAGUES[l]["polymarket_tags"])
        return tags
        
    def match_league_name(self, polymarket_league_name: str) -> str:
        """Attempts to match a tag or name from polymarket to a supported league name."""
        lower_name = polymarket_league_name.lower()
        for name, data in SUPPORTED_LEAGUES.items():
            if lower_name == name.lower() or lower_name in data["polymarket_tags"]:
                return name
        return ""
