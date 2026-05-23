# Polymarket Soccer Dip Monitor

An asynchronous Python background daemon designed to monitor live soccer betting markets on Polymarket for specific price dips in "Over/Under" contracts.

## The "Dip" Strategy

This script automates the detection of **Post-Goal Dips** in Polymarket soccer "Under" contracts. 

In betting markets, when a goal is scored, the probability that the total amount of goals will stay "Under" a certain threshold drops significantly. This causes the price of the "Under" contract to fall sharply (a "dip"). However, as time elapses without further goals being scored, the probability of the "Under" hitting slowly increases, causing the price to climb back up due to natural **time decay** (theta). 

This tool is designed to find these momentary price drops immediately after an event shock so you can "buy the dip" and capitalize on the subsequent price rebound as match time ticks away.

## Key Features

*   **Live Match Detection (API-Football)**: Uses API-Football as the authoritative source for daily schedules. It fuzzy-matches Polymarket events against live games to ensure we only monitor matches that are actively being played.
*   **Real-time Order Book Monitoring**: Connects to Polymarket's Central Limit Order Book (CLOB) via WebSockets to track the `ASK` prices of "Under" tokens continuously.
*   **Live Score Integration & Dead Market Protection**: When a dip occurs, it queries API-Football for the live score. If the current total goals already exceed the market threshold, it suppresses the alert, preventing false positives on mathematically dead markets.
*   **Interactive Telegram Control Panel**: Manage the daemon on the fly via a Telegram Bot. Send `/menu` to view status, toggle active leagues, and adjust price thresholds directly from your phone.
*   **Persistent Runtime Configuration**: All settings are saved to a local `config_state.json` file, ensuring your active leagues and custom thresholds survive daemon restarts.
*   **Simulation Mode**: A testing mode that simulates market data and websocket events without hitting live APIs.

## How It Works

1.  **Schedule Fetching**: At the start of the active window (13:00 IDT), it fetches the daily schedule for target leagues from API-Football.
2.  **Market Discovery**: Periodically queries Polymarket's Gamma REST API for active soccer markets. It filters these events to only include those matching the live teams currently playing.
3.  **Monitoring**: Subscribes to the Polymarket WebSocket for the discovered "Under" tokens.
4.  **Dip Detection & Verification**: When an `ASK` price drops below the threshold, it fetches the live score to verify the market isn't already dead.
5.  **Alerting**: Sends a formatted alert to your Telegram chat with the match, market, lowest ask price, and the current score.

## Setup & Configuration

This project relies on environment variables for API keys and a `.env` file in the root directory:

```env
# Required: Telegram Bot for UI and Alerts
TELEGRAM_BOT_TOKEN="your_telegram_bot_token"
TELEGRAM_CHAT_ID="your_telegram_chat_id"

# Required: Live Match Detection & Score Integration (api-sports.io)
API_FOOTBALL_KEY="your_api_football_key"

# Base Tuning Variables (Can be overridden via Telegram Bot)
ASK_PRICE_THRESHOLD="0.10"       # Default global price at which to trigger a dip alert
ASK_PRICE_MIN="0.01"             # Minimum price to avoid noise
POLL_INTERVAL_MARKETS="900"      # Seconds between API-Football / Gamma API market discovery polls
WS_RECONNECT_DELAY="5"           # Seconds to wait before reconnecting WebSocket
ALERT_COOLDOWN="1200"            # Seconds to wait before alerting again for the same token

# Simulation
SIMULATION_MODE="0"              # Set to 1 to run mock data
```

**Note on Telegram Privacy Mode**: Since the bot interacts via text input for thresholds, if you are running it in a group chat, you must disable Privacy Mode via `@BotFather` (`/setprivacy` -> Disable) so it can read your messages.

## Runtime Configuration via Telegram

You no longer need to restart the daemon to change settings. Start a chat with your configured bot and send `/menu`.

The Bot UI allows you to:
*   View active leagues and current thresholds.
*   **Edit Thresholds**: Set a global threshold or configure specific overrides for individual leagues (e.g., lower threshold for high-scoring leagues).
*   **Manage Leagues**: Toggle monitoring on/off for 20+ supported leagues (Premier League, La Liga, World Cup, Champions League, MLS, etc.).

## Running the Daemon

Ensure you have the required dependencies (or use the provided `shell.nix`).

```bash
python soccer_monitor.py
```
