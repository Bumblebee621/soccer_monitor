"""
Soccer Monitor Daemon
Monitors Polymarket soccer Over/Under markets for price dips.
"""

import asyncio
import json
import logging
import os
import re
import time
import difflib
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Dict, List, Optional

# pyrefly: ignore [missing-import]
import aiohttp
# pyrefly: ignore [missing-import]
import websockets
# pyrefly: ignore [missing-import]
from dotenv import load_dotenv

from config_manager import RuntimeConfig
from bot_ui import start_bot_polling

# Load environment variables
load_dotenv()

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
ASK_PRICE_THRESHOLD = float(os.getenv("ASK_PRICE_THRESHOLD", "0.10"))
ASK_PRICE_MIN = float(os.getenv("ASK_PRICE_MIN", "0.01"))
POLL_INTERVAL_MARKETS = int(os.getenv("POLL_INTERVAL_MARKETS", "900"))
WS_RECONNECT_DELAY = int(os.getenv("WS_RECONNECT_DELAY", "5"))
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", "1200"))
SIMULATION_MODE = os.getenv("SIMULATION_MODE", "0") == "1"

logging.basicConfig(
    level=logging.INFO,
    format='%(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("SoccerMonitor")


def parse_goal_threshold(question: str) -> Optional[float]:
    if not question:
        return None
    question_lower = question.lower()
    
    # Match pattern: under/over/o/u [number] total goals or [number] goals
    match = re.search(r'(?:under|over|o/u|u/o)\s*(\d+(?:\.\d+)?)\s*total\s*goals?', question_lower)
    if match:
        return float(match.group(1))
        
    match = re.search(r'(?:under|over|o/u|u/o)\s*(\d+(?:\.\d+)?)\s*goals?', question_lower)
    if match:
        return float(match.group(1))
        
    # Fallback: any number if "goal" is mentioned
    if "goal" in question_lower:
        match = re.search(r'(\d+(?:\.\d+)?)', question_lower)
        if match:
            return float(match.group(1))
            
    return None


@dataclass
class MarketInfo:
    event_title: str
    event_slug: str
    league: str
    market_question: str
    under_token_id: str
    fixture_id: Optional[int] = None


class LiveMatchTracker:
    def __init__(self, session: aiohttp.ClientSession, runtime_config: RuntimeConfig):
        self.session = session
        self.runtime_config = runtime_config
        self.api_key = API_FOOTBALL_KEY
        self.base_url = "https://v3.football.api-sports.io/"
        self.headers = {'x-apisports-key': self.api_key} if self.api_key else {}
        self.daily_fixtures = []  # List of {"fixture_id": int, "home_team": str, "away_team": str, "start_time": datetime}
        self.last_fetch_date = None  # YYYY-MM-DD string

    async def fetch_daily_schedule(self):
        """Fetches daily schedule for today and tomorrow and filters locally."""
        IDT = timezone(timedelta(hours=3))
        now_idt = datetime.now(IDT)

        if SIMULATION_MODE:
            now_utc = datetime.now(timezone.utc)
            self.daily_fixtures = [
                {
                    "fixture_id": 123456,
                    "home_team": "Mock FC",
                    "away_team": "Simulator United",
                    "start_time": now_utc - timedelta(minutes=10) # Started 10 minutes ago
                }
            ]
            logger.info("SIMULATION: Populated mock daily schedule.")
            self.last_fetch_date = now_idt.strftime('%Y-%m-%d')
            return

        if not self.api_key:
            logger.error("API-Football key not configured. Cannot fetch daily schedule.")
            return

        dates_to_fetch = [
            now_idt.strftime('%Y-%m-%d'),
            (now_idt + timedelta(days=1)).strftime('%Y-%m-%d')
        ]

        target_leagues = self.runtime_config.get_active_api_ids()
        fetched_fixtures = []

        try:
            for date_str in dates_to_fetch:
                url = f"{self.base_url}fixtures"
                params = {'date': date_str}
                logger.info(f"API-Football: Fetching global daily schedule for {date_str}...")
                
                async with self.session.get(url, headers=self.headers, params=params) as response:
                    if response.status != 200:
                        logger.error(f"API-Football error: {response.status}")
                        continue
                    
                    data = await response.json()
                    
                    # API-Football may return 200 with an errors block inside the JSON
                    api_errors = data.get("errors", {})
                    if api_errors:
                        logger.error(f"API-Football response errors: {api_errors}")
                        continue

                    fixtures_list = data.get("response", [])
                    logger.info(f"Retrieved {len(fixtures_list)} total global fixtures for {date_str}.")

                    for item in fixtures_list:
                        league_info = item.get("league", {})
                        league_id = league_info.get("id")
                        if league_id in target_leagues:
                            fixture_info = item.get("fixture", {})
                            fixture_id = fixture_info.get("id")
                            timestamp = fixture_info.get("timestamp")
                            teams_info = item.get("teams", {})
                            home_name = teams_info.get("home", {}).get("name", "")
                            away_name = teams_info.get("away", {}).get("name", "")
                            
                            if fixture_id and timestamp and home_name and away_name:
                                start_time_utc = datetime.fromtimestamp(timestamp, timezone.utc)
                                fetched_fixtures.append({
                                    "fixture_id": fixture_id,
                                    "home_team": home_name,
                                    "away_team": away_name,
                                    "start_time": start_time_utc
                                })

            self.daily_fixtures = fetched_fixtures
            logger.info(f"Successfully cached {len(self.daily_fixtures)} target league fixtures locally.")
            self.last_fetch_date = now_idt.strftime('%Y-%m-%d')

        except Exception as e:
            logger.error(f"Exception fetching daily schedule: {e}")

    def get_live_team_names(self) -> set:
        """Returns a set of all team names currently playing based on daily schedule."""
        now_utc = datetime.now(timezone.utc)
        live_teams = set()
        active_count = 0
        
        for fixture in self.daily_fixtures:
            start_time = fixture["start_time"]
            # Active window: kickoff <= current_time <= kickoff + 150 minutes
            end_time = start_time + timedelta(minutes=150)
            if start_time <= now_utc <= end_time:
                live_teams.add(fixture["home_team"])
                live_teams.add(fixture["away_team"])
                active_count += 1
                
        logger.info(f"Checked daily schedule. Found {active_count} active match(es) right now.")
        return live_teams

    async def fetch_live_score_on_demand(self, fixture_id: int) -> str:
        """Queries API-Football for a single fixture's live score on demand."""
        if SIMULATION_MODE:
            if self.api_key == "SIMULATE_LIMIT":
                return "[Error: API-Football daily quota of 100 calls reached]"
            return "2-1 (65' 2H)"

        if not self.api_key:
            return "N/A (API-Football key not configured)"

        try:
            url = f"{self.base_url}fixtures"
            params = {'id': str(fixture_id)}
            
            async with self.session.get(url, headers=self.headers, params=params) as response:
                if response.status == 429:
                    logger.warning("API-Football returned 429. Daily quota exceeded.")
                    return "[Error: API-Football daily quota of 100 calls reached]"
                
                if response.status != 200:
                    logger.error(f"API-Football score query HTTP error: {response.status}")
                    return f"[Error: API-Football HTTP {response.status}]"

                # Check rate limit headers
                remaining = response.headers.get('x-ratelimit-requests-remaining')
                if remaining == '0':
                    logger.warning("API-Football requests remaining is 0. Daily quota reached.")
                    return "[Error: API-Football daily quota of 100 calls reached]"

                data = await response.json()
                
                api_errors = data.get("errors", {})
                if api_errors:
                    logger.warning(f"API-Football returned error: {api_errors}")
                    error_str = str(api_errors).lower()
                    if "limit" in error_str or "quota" in error_str or "request" in error_str:
                        return "[Error: API-Football daily quota of 100 calls reached]"
                    return f"[Error: API-Football - {api_errors}]"

                fixtures = data.get("response", [])
                if not fixtures:
                    return "Match not found"

                fixture_data = fixtures[0]
                goals = fixture_data.get("goals", {})
                home_goals = goals.get("home", 0)
                away_goals = goals.get("away", 0)
                status = fixture_data.get("fixture", {}).get("status", {}).get("short", "")
                elapsed = fixture_data.get("fixture", {}).get("status", {}).get("elapsed", "")
                
                return f"{home_goals}-{away_goals} ({elapsed}' {status})"

        except Exception as e:
            logger.error(f"Exception fetching live score for fixture {fixture_id}: {e}")
            return f"[Error: Exception - {str(e)}]"


class AlertEngine:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.bot_token = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self.last_alert_time: Dict[str, float] = {}
        self.last_flush_date: Optional[str] = None

    def clear_alert_history(self, date_str: str):
        self.last_alert_time.clear()
        self.last_flush_date = date_str
        logger.info(f"New day detected: Alert history flushed for {date_str} at midnight IDT.")

    async def send_alert(self, market: MarketInfo, current_ask: float, score: str):
        if market.under_token_id in self.last_alert_time:
            logger.debug(f"Alert for {market.event_title} already sent today.")
            return

        self.last_alert_time[market.under_token_id] = time.time()

        message = (
            f"🚨 *UNDER PRICE DIP DETECTED*\n\n"
            f"⚽ *{market.event_title}*\n"
            f"🏆 {market.league}\n"
            f"📊 Market: {market.market_question}\n"
            f"💰 Lowest ASK: ${current_ask:.3f}\n"
            f"🎯 Score: {score}\n"
            f"🔗 https://polymarket.com/event/{market.event_slug}"
        )

        logger.info(f"--- ALERT TRIGGERED ---\n{message}\n-----------------------")

        if self.bot_token and self.chat_id:
            try:
                url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
                payload = {
                    "chat_id": self.chat_id,
                    "text": message,
                    "parse_mode": "Markdown"
                }
                async with self.session.post(url, json=payload) as response:
                    if response.status != 200:
                        logger.error(f"Failed to send Telegram alert: {response.status}")
                    else:
                        logger.info("Telegram alert sent successfully.")
            except Exception as e:
                logger.error(f"Exception sending Telegram alert: {e}")
        else:
            logger.info("Telegram credentials not configured. Skipping Telegram notification.")


class MarketDiscovery:
    def __init__(self, session: aiohttp.ClientSession, live_match_tracker: LiveMatchTracker, runtime_config: RuntimeConfig):
        self.session = session
        self.live_match_tracker = live_match_tracker
        self.runtime_config = runtime_config
        self.base_url = "https://gamma-api.polymarket.com/events"

    def _event_has_live_team(self, event_title: str, live_teams: set) -> bool:
        """Checks if any team in the event title fuzzy matches a live team."""
        if not live_teams:
            return False
            
        # Split "Team A vs. Team B"
        parts = event_title.split(' vs. ')
        if len(parts) != 2:
            parts = event_title.split(' vs ')
        
        for part in parts:
            part = part.strip().lower()
            for live_team in live_teams:
                # Basic fuzzy match
                if difflib.SequenceMatcher(None, part, live_team.lower()).ratio() > 0.7:
                    return True
        return False

    def find_matching_fixture_id(self, event_title: str) -> Optional[int]:
        """Fuzzy matches Polymarket event title against cached daily fixtures to get fixture ID."""
        if not self.live_match_tracker.daily_fixtures:
            return None

        parts = event_title.split(' vs. ')
        if len(parts) != 2:
            parts = event_title.split(' vs ')
        if len(parts) != 2:
            return None

        parts = [p.strip().lower() for p in parts]
        best_fixture_id = None
        best_ratio = 0.0

        for fixture in self.live_match_tracker.daily_fixtures:
            home = fixture["home_team"].lower()
            away = fixture["away_team"].lower()

            ratio1 = max(
                difflib.SequenceMatcher(None, parts[0], home).ratio(),
                difflib.SequenceMatcher(None, parts[1], away).ratio()
            )
            ratio2 = max(
                difflib.SequenceMatcher(None, parts[0], away).ratio(),
                difflib.SequenceMatcher(None, parts[1], home).ratio()
            )
            max_ratio = max(ratio1, ratio2)

            if max_ratio > best_ratio:
                best_ratio = max_ratio
                best_fixture_id = fixture["fixture_id"]

        if best_ratio > 0.7:
            return best_fixture_id
        return None

    async def fetch_markets(self, live_teams: set) -> Dict[str, MarketInfo]:
        if SIMULATION_MODE:
            logger.info("SIMULATION: Returning mock markets.")
            return {
                "MOCK_TOKEN_1": MarketInfo(
                    event_title="Mock FC vs Simulator United",
                    event_slug="mock-fc-simulator-united",
                    league="Simulation League",
                    market_question="Will there be over 2.5 goals?",
                    under_token_id="MOCK_TOKEN_1",
                    fixture_id=123456
                )
            }

        logger.info("Fetching active soccer markets from Gamma API...")
        markets_dict = {}
        skipped_not_live = 0
        
        try:
            offset = 0
            limit = 100
            while True:
                params = {
                    'active': 'true',
                    'closed': 'false',
                    'tag_slug': 'soccer',
                    'limit': str(limit),
                    'offset': str(offset)
                }
                async with self.session.get(self.base_url, params=params) as response:
                    if response.status != 200:
                        logger.error(f"Gamma API error: {response.status}")
                        break
                    
                    events = await response.json()
                    if not events:
                        break

                    allowed_leagues = self.runtime_config.get_active_polymarket_tags()
                    
                    for event in events:
                        tags = [tag.get('label', '') for tag in event.get('tags', [])]
                        lower_tags = [t.lower() for t in tags]
                        
                        if not any(al in lower_tags for al in allowed_leagues):
                            continue

                        event_title = event.get('title', 'Unknown Match')
                        if not self._event_has_live_team(event_title, live_teams):
                            skipped_not_live += 1
                            continue

                        league_tags = [t for t in tags if t not in ['Soccer', 'Sports', 'Featured', 'Games']]
                        league = league_tags[0] if league_tags else "General Soccer"
                        
                        for market in event.get('markets', []):
                            question = market.get('question', '').lower()
                            if ("total goals" in question or "over/under" in question or "o/u" in question) and "corners" not in question:
                                # Skip specific goal lines requested by user
                                if "0.5" in question or "5.5" in question:
                                    continue
                                
                                # Removed Market-level gameStartTime check as API-Football is now source of truth
                                
                                tokens = market.get('clobTokenIds')
                                outcomes = market.get('outcomes')
                                if not tokens or not outcomes:
                                    continue
                                
                                try:
                                    token_list = json.loads(tokens)
                                    outcome_list = json.loads(outcomes)
                                    if len(token_list) == len(outcome_list):
                                        under_idx = -1
                                        for idx, outcome in enumerate(outcome_list):
                                            if outcome.lower() in ("under", "no"):
                                                under_idx = idx
                                                break
                                        
                                        if under_idx != -1:
                                            under_token = token_list[under_idx]
                                            fixture_id = self.find_matching_fixture_id(event_title)
                                            markets_dict[under_token] = MarketInfo(
                                                event_title=event.get('title', 'Unknown Match'),
                                                event_slug=event.get('slug', ''),
                                                league=league,
                                                market_question=market.get('question', ''),
                                                under_token_id=under_token,
                                                fixture_id=fixture_id
                                            )
                                except json.JSONDecodeError as e:
                                    logger.warning(f"Malformed JSON in market '{market.get('question', '?')}': {e}")
                    
                    if len(events) < limit:
                        break
                    offset += limit

        except Exception as e:
            logger.error(f"Exception fetching markets: {e}")
            
        logger.info(
            f"Found {len(markets_dict)} live Over/Under markets "
            f"(skipped {skipped_not_live} events not currently live according to API-Football)."
        )
        return markets_dict



class SimulatedWebsocket:
    """A mock websocket to simulate price drops for the mock market."""
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        pass

    async def send(self, message):
        pass

    async def __aiter__(self):
        # 1. Send an initial book snapshot above threshold
        yield json.dumps({
            "event_type": "book",
            "asset_id": "MOCK_TOKEN_1",
            "asks": [{"price": "0.15", "size": "100"}]
        })
        await asyncio.sleep(5)
        # 2. Simulate a price dip to trigger alert
        logger.info("SIMULATION: Sending mock price drop to 0.05...")
        yield json.dumps({
            "event_type": "price_change",
            "price_changes": [
                {"asset_id": "MOCK_TOKEN_1", "side": "SELL", "price": "0.05", "size": "50"}
            ]
        })
        # 3. Wait a bit, then simulate price going back up
        await asyncio.sleep(5)
        logger.info("SIMULATION: Removing mock price drop...")
        yield json.dumps({
            "event_type": "price_change",
            "price_changes": [
                {"asset_id": "MOCK_TOKEN_1", "side": "SELL", "price": "0.05", "size": "0"}
            ]
        })
        # 4. Stay connected idly
        while True:
            await asyncio.sleep(60)

class OrderBookMonitor:
    def __init__(self, alert_engine: AlertEngine, live_match_tracker: LiveMatchTracker, markets_changed_event: asyncio.Event, runtime_config: RuntimeConfig):
        self.ws_url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        self.alert_engine = alert_engine
        self.live_match_tracker = live_match_tracker
        self.runtime_config = runtime_config
        self.monitored_markets: Dict[str, MarketInfo] = {}
        self.book_state: Dict[str, Dict[float, float]] = {} # token_id -> {price: size}
        self.markets_lock = asyncio.Lock()
        self.markets_changed_event = markets_changed_event

    async def set_markets(self, markets: Dict[str, MarketInfo]):
        async with self.markets_lock:
            self.monitored_markets = markets
        self.markets_changed_event.set()

    async def process_dip(self, token_id: str, current_ask: float):
        async with self.markets_lock:
            market = self.monitored_markets.get(token_id)
        if not market:
            return
        
        score = "N/A"
        if market.fixture_id:
            score = await self.live_match_tracker.fetch_live_score_on_demand(market.fixture_id)
        else:
            logger.warning(f"No fixture ID found to query score for: {market.event_title}")
            score = "Fixture not mapped"

        # Check for dead market threshold protection
        if score and not score.startswith("[Error") and score != "N/A" and score != "Fixture not mapped":
            # Parse goals scored so far
            score_match = re.match(r'^(\d+)-(\d+)', score)
            if score_match:
                home_goals = int(score_match.group(1))
                away_goals = int(score_match.group(2))
                total_goals = home_goals + away_goals
                
                # Parse goal threshold from question
                threshold = parse_goal_threshold(market.market_question)
                if threshold is not None:
                    if total_goals >= threshold:
                        logger.info(
                            f"Suppressing false dip alert for {market.event_title} ({market.market_question}). "
                            f"Current score is {score} (total {total_goals} goals), which meets/exceeds the "
                            f"threshold of {threshold}. Market is mathematically dead (Under outcome cannot win)."
                        )
                        return

        await self.alert_engine.send_alert(market, current_ask, score)

    async def _process_event(self, data: dict):
        if not isinstance(data, dict):
            return
            
        event_type = data.get("event_type")
        
        if event_type == "book":
            # Full snapshot
            asset_id = data.get("asset_id")
            asks = data.get("asks", [])
            
            if asset_id and asks:
                self.book_state[asset_id] = {float(ask.get("price", "1.0")): float(ask.get("size", "0.0")) for ask in asks}
                lowest_ask = min(self.book_state[asset_id].keys(), default=1.0)
                
                market = self.monitored_markets.get(asset_id)
                threshold = ASK_PRICE_THRESHOLD
                if market:
                    threshold = self.runtime_config.get_threshold(market.league)
                
                if ASK_PRICE_MIN < lowest_ask < threshold:
                    await self.process_dip(asset_id, lowest_ask)

        elif event_type == "price_change":
            # Incremental update
            changes = data.get("price_changes", [])
            for change in changes:
                asset_id = change.get("asset_id")
                side = change.get("side")
                price = float(change.get("price", "1.0"))
                size = float(change.get("size", "0.0"))
                
                if side == "SELL": # Asks
                    if asset_id not in self.book_state:
                        self.book_state[asset_id] = {}
                        
                    if size > 0:
                        self.book_state[asset_id][price] = size
                    else:
                        self.book_state[asset_id].pop(price, None)
                        
                    lowest_ask = min(self.book_state[asset_id].keys(), default=1.0)
                    
                    market = self.monitored_markets.get(asset_id)
                    threshold = ASK_PRICE_THRESHOLD
                    if market:
                        threshold = self.runtime_config.get_threshold(market.league)
                        
                    if ASK_PRICE_MIN < lowest_ask < threshold:
                        await self.process_dip(asset_id, lowest_ask)

    async def _handle_message(self, message: str):
        try:
            data = json.loads(message)
            if isinstance(data, list):
                for item in data:
                    await self._process_event(item)
            else:
                await self._process_event(data)
        except Exception as e:
            logger.error(f"Error handling WS message: {e}")

    async def _read_ws(self, ws):
        async for message in ws:
            await self._handle_message(message)

    async def run(self):
        while True:
            async with self.markets_lock:
                if not self.monitored_markets:
                    have_markets = False
                else:
                    have_markets = True
                    token_ids = list(self.monitored_markets.keys())

            if not have_markets:
                logger.info("No markets to monitor. Waiting...")
                await asyncio.sleep(10)
                continue
            
            subscription_message = {
                "assets_ids": token_ids,
                "type": "market",
                "custom_feature_enabled": True
            }

            self.markets_changed_event.clear()

            try:
                logger.info(f"Connecting to WS with {len(token_ids)} tokens...")
                
                if SIMULATION_MODE:
                    logger.info("SIMULATION: Using SimulatedWebsocket.")
                    ws_context = SimulatedWebsocket()
                else:
                    ws_context = websockets.connect(self.ws_url, ping_interval=10, ping_timeout=10)

                async with ws_context as ws:
                    await ws.send(json.dumps(subscription_message))
                    logger.info("Subscribed to market channels.")
                    
                    ws_task = asyncio.create_task(self._read_ws(ws))
                    event_task = asyncio.create_task(self.markets_changed_event.wait())
                    
                    done, pending = await asyncio.wait(
                        [ws_task, event_task],
                        return_when=asyncio.FIRST_COMPLETED
                    )
                    
                    for task in pending:
                        task.cancel()
                        
                    if self.markets_changed_event.is_set():
                        logger.info("Markets changed, reconnecting WS...")
                        continue # Reconnect
                        
            except websockets.ConnectionClosed:
                logger.warning("WebSocket connection closed. Reconnecting...")
            except Exception as e:
                logger.error(f"WebSocket error: {e}. Reconnecting...")
                
            await asyncio.sleep(WS_RECONNECT_DELAY)


async def main():
    logger.info("Starting Soccer Over/Under Dip Detection Daemon...")
    
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("⚠️  Telegram credentials not configured — alerts will be logged only.")
    if not API_FOOTBALL_KEY and not SIMULATION_MODE:
        logger.error("❌ API-Football key is REQUIRED for live match detection. Exiting.")
        return
        
    markets_changed_event = asyncio.Event()
    runtime_config = RuntimeConfig()
    
    async with aiohttp.ClientSession() as session:
        live_match_tracker = LiveMatchTracker(session, runtime_config)
        discovery = MarketDiscovery(session, live_match_tracker, runtime_config)
        alert_engine = AlertEngine(session)
        monitor = OrderBookMonitor(alert_engine, live_match_tracker, markets_changed_event, runtime_config)
        
        async def discovery_loop():
            # IDT (Israel Daylight Time) is UTC+3
            IDT = timezone(timedelta(hours=3))

            while True:
                now_idt = datetime.now(IDT)
                hour = now_idt.hour
                current_date_str = now_idt.strftime('%Y-%m-%d')

                # Flush alert history at midnight IDT
                if alert_engine.last_flush_date != current_date_str:
                    alert_engine.clear_alert_history(current_date_str)

                # Active window: 13:00 - 01:00 IDT (i.e., hour >= 13 OR hour < 1)
                if SIMULATION_MODE or hour >= 13 or hour < 1:
                    # Fetch daily schedule only starting at 13:00 IDT
                    if SIMULATION_MODE or hour >= 13:
                        if live_match_tracker.last_fetch_date != current_date_str:
                            logger.info("Daily schedule not fetched today. Fetching daily schedule...")
                            await live_match_tracker.fetch_daily_schedule()

                    live_teams = live_match_tracker.get_live_team_names()

                    markets = await discovery.fetch_markets(live_teams)
                    await monitor.set_markets(markets)
                    await asyncio.sleep(POLL_INTERVAL_MARKETS)
                else:
                    # Outside match hours — clear markets and sleep until 13:00 IDT
                    logger.info(f"Outside active hours ({now_idt.strftime('%H:%M')} IDT). Sleeping until 13:00 IDT...")
                    await monitor.set_markets({})

                    # Calculate seconds until 13:00 IDT today
                    next_start = now_idt.replace(hour=13, minute=0, second=0, microsecond=0)
                    if next_start <= now_idt:
                        next_start += timedelta(days=1)
                    sleep_seconds = (next_start - now_idt).total_seconds()
                    logger.info(f"Resuming in {sleep_seconds/3600:.1f} hours.")
                    await asyncio.sleep(sleep_seconds)
                
        gather_list = [discovery_loop(), monitor.run()]
        if TELEGRAM_BOT_TOKEN:
            gather_list.append(start_bot_polling(TELEGRAM_BOT_TOKEN, runtime_config, TELEGRAM_CHAT_ID))
            
        tasks = asyncio.gather(*gather_list)
        try:
            await tasks
        except asyncio.CancelledError:
            logger.info("Shutting down gracefully...")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Daemon stopped by user.")
