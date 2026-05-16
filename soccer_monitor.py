"""
Soccer Monitor Daemon
Monitors Polymarket soccer Over/Under markets for price dips.
"""

import asyncio
import json
import logging
import os
import time
import difflib
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass
from typing import Dict, List, Optional

import aiohttp
import websockets
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
API_FOOTBALL_KEY = os.getenv("API_FOOTBALL_KEY")
ASK_PRICE_THRESHOLD = float(os.getenv("ASK_PRICE_THRESHOLD", "0.10"))
POLL_INTERVAL_MARKETS = int(os.getenv("POLL_INTERVAL_MARKETS", "900"))
WS_RECONNECT_DELAY = int(os.getenv("WS_RECONNECT_DELAY", "5"))
ALERT_COOLDOWN = int(os.getenv("ALERT_COOLDOWN", "1200"))

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("SoccerMonitor")


@dataclass
class MarketInfo:
    event_title: str
    event_slug: str
    league: str
    market_question: str
    under_token_id: str


class LiveMatchTracker:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.api_key = API_FOOTBALL_KEY
        self.base_url = "https://v3.football.api-sports.io/"
        self.headers = {'x-apisports-key': self.api_key} if self.api_key else {}
        self.live_fixtures = []

    async def fetch_live(self):
        """Fetches currently live fixtures for specified leagues."""
        if not self.api_key:
            return

        try:
            url = f"{self.base_url}fixtures"
            # 39: Premier League, 140: La Liga, 135: Serie A, 78: Bundesliga, 61: Ligue 1, 1: World Cup
            params = {'live': '39-140-135-78-61-1'}
            async with self.session.get(url, headers=self.headers, params=params) as response:
                if response.status != 200:
                    logger.error(f"API-Football error: {response.status}")
                    return
                
                data = await response.json()
                self.live_fixtures = data.get("response", [])
                
                # Log API-Football limits
                remaining = response.headers.get('x-ratelimit-requests-remaining', 'Unknown')
                logger.info(f"Fetched {len(self.live_fixtures)} live fixtures from API-Football. Daily requests remaining: {remaining}")

        except Exception as e:
            logger.error(f"Exception fetching live fixtures: {e}")

    def get_live_team_names(self) -> set:
        """Returns a set of all team names currently playing."""
        teams = set()
        for fixture in self.live_fixtures:
            teams.add(fixture.get("teams", {}).get("home", {}).get("name", ""))
            teams.add(fixture.get("teams", {}).get("away", {}).get("name", ""))
        # Remove empty strings if any
        teams.discard("")
        return teams

    def get_score(self, event_title: str) -> str:
        """Fetches live score for a given event title using fuzzy matching against cached fixtures."""
        if not self.api_key:
            return "N/A (API-Football key not configured)"

        if not self.live_fixtures:
            return "No live fixtures cached"

        best_match = None
        best_ratio = 0.0

        for fixture in self.live_fixtures:
            teams = fixture.get("teams", {})
            home = teams.get("home", {}).get("name", "")
            away = teams.get("away", {}).get("name", "")
            fixture_title = f"{home} vs {away}"
            fixture_title_alt = f"{away} vs {home}"
            
            # Check similarity
            ratio1 = difflib.SequenceMatcher(None, event_title.lower(), fixture_title.lower()).ratio()
            ratio2 = difflib.SequenceMatcher(None, event_title.lower(), fixture_title_alt.lower()).ratio()
            max_ratio = max(ratio1, ratio2)

            if max_ratio > best_ratio:
                best_ratio = max_ratio
                best_match = fixture

        if best_match and best_ratio > 0.55:
            goals = best_match.get("goals", {})
            home_goals = goals.get("home", 0)
            away_goals = goals.get("away", 0)
            status = best_match.get("fixture", {}).get("status", {}).get("short", "")
            elapsed = best_match.get("fixture", {}).get("status", {}).get("elapsed", "")
            return f"{home_goals}-{away_goals} ({elapsed}' {status})"
        else:
            return "Match not found in live fixtures"


class AlertEngine:
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
        self.bot_token = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self.last_alert_time: Dict[str, float] = {}
        self.cooldown = ALERT_COOLDOWN

    async def send_alert(self, market: MarketInfo, current_ask: float, score: str):
        current_time = time.time()
        if market.under_token_id in self.last_alert_time:
            if current_time - self.last_alert_time[market.under_token_id] < self.cooldown:
                logger.debug(f"Alert for {market.event_title} on cooldown.")
                return

        self.last_alert_time[market.under_token_id] = current_time

        message = (
            f"🚨 *UNDER PRICE DIP DETECTED*\n\n"
            f"⚽ *{market.event_title}*\n"
            f"🏆 {market.league}\n"
            f"📊 Market: {market.market_question}\n"
            f"💰 Lowest ASK: ${current_ask:.3f}\n"
            # f"🎯 Score: {score}\n"
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
    def __init__(self, session: aiohttp.ClientSession):
        self.session = session
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

    async def fetch_markets(self, live_teams: set) -> Dict[str, MarketInfo]:
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
                    
                    for event in events:
                        tags = [tag.get('label', '') for tag in event.get('tags', [])]
                        lower_tags = [t.lower() for t in tags]
                        
                        allowed_leagues = {"premier league", "epl", "la liga", "laliga", "serie a", "bundesliga", "ligue 1", "fifa world cup"}
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
                                            markets_dict[under_token] = MarketInfo(
                                                event_title=event.get('title', 'Unknown Match'),
                                                event_slug=event.get('slug', ''),
                                                league=league,
                                                market_question=market.get('question', ''),
                                                under_token_id=under_token
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



class OrderBookMonitor:
    def __init__(self, alert_engine: AlertEngine, live_match_tracker: LiveMatchTracker, markets_changed_event: asyncio.Event):
        self.ws_url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        self.alert_engine = alert_engine
        self.live_match_tracker = live_match_tracker
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
        
        # Get score from cached API-Football data
        # score = self.live_match_tracker.get_score(market.event_title)
        await self.alert_engine.send_alert(market, current_ask, "")

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
                if lowest_ask < ASK_PRICE_THRESHOLD:
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
                    if lowest_ask < ASK_PRICE_THRESHOLD:
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
                async with websockets.connect(self.ws_url, ping_interval=10, ping_timeout=10) as ws:
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
    if not API_FOOTBALL_KEY:
        logger.error("❌ API-Football key is REQUIRED for live match detection. Exiting.")
        return
        
    markets_changed_event = asyncio.Event()
    
    async with aiohttp.ClientSession() as session:
        discovery = MarketDiscovery(session)
        live_match_tracker = LiveMatchTracker(session)
        alert_engine = AlertEngine(session)
        monitor = OrderBookMonitor(alert_engine, live_match_tracker, markets_changed_event)
        
        async def discovery_loop():
            while True:
                await live_match_tracker.fetch_live()
                live_teams = live_match_tracker.get_live_team_names()
                
                markets = await discovery.fetch_markets(live_teams)
                await monitor.set_markets(markets)
                await asyncio.sleep(POLL_INTERVAL_MARKETS)
                
        tasks = asyncio.gather(
            discovery_loop(),
            monitor.run()
        )
        try:
            await tasks
        except asyncio.CancelledError:
            logger.info("Shutting down gracefully...")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Daemon stopped by user.")
