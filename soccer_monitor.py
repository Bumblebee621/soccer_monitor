import asyncio
import json
import logging
import os
import time
import difflib
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
POLL_INTERVAL_MARKETS = int(os.getenv("POLL_INTERVAL_MARKETS", "300"))
WS_RECONNECT_DELAY = int(os.getenv("WS_RECONNECT_DELAY", "5"))

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
    threshold: float


class ScoreFetcher:
    def __init__(self):
        self.api_key = API_FOOTBALL_KEY
        self.base_url = "https://v3.football.api-sports.io/"
        self.headers = {'x-apisports-key': self.api_key} if self.api_key else {}

    async def get_live_score(self, event_title: str) -> str:
        """Fetches live score for a given event title using fuzzy matching."""
        if not self.api_key:
            return "N/A (API-Football key not configured)"

        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self.base_url}fixtures"
                params = {'live': 'all'}
                async with session.get(url, headers=self.headers, params=params) as response:
                    if response.status != 200:
                        logger.error(f"API-Football error: {response.status}")
                        return "Error fetching score"
                    
                    data = await response.json()
                    fixtures = data.get("response", [])
                    
                    best_match = None
                    best_ratio = 0.0

                    for fixture in fixtures:
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

                    if best_match and best_ratio > 0.4: # Arbitrary threshold
                        goals = best_match.get("goals", {})
                        home_goals = goals.get("home", 0)
                        away_goals = goals.get("away", 0)
                        status = best_match.get("fixture", {}).get("status", {}).get("short", "")
                        elapsed = best_match.get("fixture", {}).get("status", {}).get("elapsed", "")
                        return f"{home_goals}-{away_goals} ({elapsed}' {status})"
                    else:
                        return "Match not found in live fixtures"

        except Exception as e:
            logger.error(f"Exception fetching score: {e}")
            return "Error fetching score"


class AlertEngine:
    def __init__(self):
        self.bot_token = TELEGRAM_BOT_TOKEN
        self.chat_id = TELEGRAM_CHAT_ID
        self.last_alert_time: Dict[str, float] = {}
        self.cooldown = 300 # 5 minutes cooldown per market

    async def send_alert(self, market: MarketInfo, current_ask: float, score: str):
        current_time = time.time()
        if market.under_token_id in self.last_alert_time:
            if current_time - self.last_alert_time[market.under_token_id] < self.cooldown:
                logger.debug(f"Alert for {market.event_title} on cooldown.")
                return

        self.last_alert_time[market.under_token_id] = current_time

        message = (
            f"🚨 *UNDER PRICE ANOMALY DETECTED*\n\n"
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
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload) as response:
                        if response.status != 200:
                            logger.error(f"Failed to send Telegram alert: {response.status}")
                        else:
                            logger.info("Telegram alert sent successfully.")
            except Exception as e:
                logger.error(f"Exception sending Telegram alert: {e}")
        else:
            logger.info("Telegram credentials not configured. Skipping Telegram notification.")


class MarketDiscovery:
    def __init__(self):
        self.base_url = "https://gamma-api.polymarket.com/events"

    async def fetch_markets(self) -> Dict[str, MarketInfo]:
        logger.info("Fetching active soccer markets from Gamma API...")
        markets_dict = {}
        
        try:
            async with aiohttp.ClientSession() as session:
                params = {
                    'active': 'true',
                    'closed': 'false',
                    'tag_slug': 'soccer',
                    'limit': '100'
                }
                async with session.get(self.base_url, params=params) as response:
                    if response.status != 200:
                        logger.error(f"Gamma API error: {response.status}")
                        return markets_dict
                    
                    events = await response.json()
                    
                    for event in events:
                        tags = [tag.get('label', '') for tag in event.get('tags', [])]
                        league_tags = [t for t in tags if t not in ['Soccer', 'Sports', 'Featured']]
                        league = league_tags[0] if league_tags else "General Soccer"
                        
                        for market in event.get('markets', []):
                            question = market.get('question', '').lower()
                            if "total goals" in question or "over/under" in question:
                                tokens = market.get('clobTokenIds')
                                if not tokens:
                                    continue
                                
                                try:
                                    token_list = json.loads(tokens)
                                    if len(token_list) >= 2:
                                        # Assuming [Yes_token, No_token] and No = Under
                                        under_token = token_list[1]
                                        
                                        markets_dict[under_token] = MarketInfo(
                                            event_title=event.get('title', 'Unknown Match'),
                                            event_slug=event.get('slug', ''),
                                            league=league,
                                            market_question=market.get('question', ''),
                                            under_token_id=under_token,
                                            threshold=ASK_PRICE_THRESHOLD # Default, could parse from question
                                        )
                                except json.JSONDecodeError:
                                    pass

        except Exception as e:
            logger.error(f"Exception fetching markets: {e}")
            
        logger.info(f"Found {len(markets_dict)} Over/Under markets.")
        return markets_dict


class OrderBookMonitor:
    def __init__(self, alert_engine: AlertEngine, score_fetcher: ScoreFetcher):
        self.ws_url = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
        self.alert_engine = alert_engine
        self.score_fetcher = score_fetcher
        self.monitored_markets: Dict[str, MarketInfo] = {}
        self.book_state: Dict[str, List[Dict]] = {} # token_id -> asks

    def set_markets(self, markets: Dict[str, MarketInfo]):
        self.monitored_markets = markets

    async def process_anomaly(self, token_id: str, current_ask: float):
        market = self.monitored_markets.get(token_id)
        if not market:
            return
        
        # Only fetch score if we detect an anomaly
        score = await self.score_fetcher.get_live_score(market.event_title)
        await self.alert_engine.send_alert(market, current_ask, score)

    async def _handle_message(self, message: str):
        try:
            data = json.loads(message)
            event_type = data.get("event_type")
            
            if event_type == "book":
                # Full snapshot
                for book_data in data.get("bids", []) + data.get("asks", []): # actually the payload might just be a single token book
                    pass # The docs say book event has asset_id, bids, asks.
                
                asset_id = data.get("asset_id")
                asks = data.get("asks", [])
                
                if asset_id and asks:
                    self.book_state[asset_id] = asks
                    # Check lowest ask
                    lowest_ask = min([float(ask.get("price", "1.0")) for ask in asks])
                    if lowest_ask <= ASK_PRICE_THRESHOLD:
                        await self.process_anomaly(asset_id, lowest_ask)

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
                            self.book_state[asset_id] = []
                            
                        # Update book state (simplified: we mainly care if lowest ask drops)
                        # In a real rigorous order book you'd insert/remove/update exactly.
                        # For anomaly detection, if size > 0 and price <= threshold, trigger
                        if size > 0 and price <= ASK_PRICE_THRESHOLD:
                            await self.process_anomaly(asset_id, price)

        except Exception as e:
            logger.error(f"Error handling WS message: {e}")

    async def run(self):
        while True:
            if not self.monitored_markets:
                logger.info("No markets to monitor. Waiting...")
                await asyncio.sleep(10)
                continue

            token_ids = list(self.monitored_markets.keys())
            # Websocket connection might limit number of tokens. If > 500, we'd need multiple connections.
            # Assuming < 500 for now.
            
            subscription_message = {
                "assets_ids": token_ids,
                "type": "market",
                "custom_feature_enabled": True
            }

            try:
                logger.info(f"Connecting to WS with {len(token_ids)} tokens...")
                # ping_interval/timeout handles the application-level pings required by many standard WS servers
                async with websockets.connect(self.ws_url, ping_interval=10, ping_timeout=10) as ws:
                    await ws.send(json.dumps(subscription_message))
                    logger.info("Subscribed to market channels.")
                    
                    async for message in ws:
                        await self._handle_message(message)
                        
            except websockets.ConnectionClosed:
                logger.warning("WebSocket connection closed. Reconnecting...")
            except Exception as e:
                logger.error(f"WebSocket error: {e}. Reconnecting...")
                
            await asyncio.sleep(WS_RECONNECT_DELAY)


async def main():
    logger.info("Starting Soccer Over/Under Anomaly Detection Daemon...")
    
    discovery = MarketDiscovery()
    score_fetcher = ScoreFetcher()
    alert_engine = AlertEngine()
    monitor = OrderBookMonitor(alert_engine, score_fetcher)
    
    async def discovery_loop():
        while True:
            markets = await discovery.fetch_markets()
            monitor.set_markets(markets)
            await asyncio.sleep(POLL_INTERVAL_MARKETS)
            
    # Run discovery and monitor concurrently
    await asyncio.gather(
        discovery_loop(),
        monitor.run()
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Daemon stopped by user.")
