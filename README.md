# Polymarket Soccer Dip Monitor

An asynchronous Python background daemon designed to monitor live soccer betting markets on Polymarket for specific price dips in "Over/Under" contracts.

## The "Dip" Strategy

This script automates the detection of **Post-Goal Dips** in Polymarket soccer "Under" contracts. 

In betting markets, when a goal is scored, the probability that the total amount of goals will stay "Under" a certain threshold drops significantly. This causes the price of the "Under" contract to fall sharply (a "dip"). However, as time elapses without further goals being scored, the probability of the "Under" hitting slowly increases, causing the price to climb back up due to natural **time decay** (theta). 

This tool is designed to find these momentary price drops immediately after an event shock so you can "buy the dip" and capitalize on the subsequent price rebound as match time ticks away.

## How It Works

1. **Market Discovery**: Periodically queries Polymarket's Gamma REST API to find active soccer markets (Premier League, La Liga, Serie A, etc.). It identifies "Total Goals" or "Over/Under" markets and extracts the unique token ID for the "Under" outcome.
2. **Real-time Order Book Monitoring**: Connects to Polymarket's Central Limit Order Book (CLOB) via WebSockets. It subscribes to the live order book streams for the discovered "Under" tokens, continuously tracking the `ASK` prices.
3. **Dip Detection**: When the lowest `ASK` price for an "Under" contract drops to or below the `ASK_PRICE_THRESHOLD` (default: $0.10), the script triggers a dip alert.
4. **Alerting**: Formats a message containing the match name, market question, and the current low price, and sends it to a configured Telegram chat (or prints to the terminal).

## Setup & Configuration

This project relies on environment variables for configuration. Create a `.env` file in the root directory:

```env
# Optional: Telegram Alerting
TELEGRAM_BOT_TOKEN="your_telegram_bot_token"
TELEGRAM_CHAT_ID="your_telegram_chat_id"

# Optional: Live Score Integration (api-sports.io)
API_FOOTBALL_KEY="your_api_football_key"

# Tuning Variables
ASK_PRICE_THRESHOLD="0.10"       # Price at which to trigger a dip alert
POLL_INTERVAL_MARKETS="300"      # Seconds between Gamma API market discovery polls
WS_RECONNECT_DELAY="5"           # Seconds to wait before reconnecting WebSocket
ALERT_COOLDOWN="1200"            # Seconds to wait before alerting again for the same token
```

## Running the Daemon

First, ensure you have the required dependencies installed (e.g., `aiohttp`, `websockets`, `python-dotenv`).

```bash
python soccer_monitor.py
```
