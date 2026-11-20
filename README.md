# Polymarket Live Soccer Under Monitor

`live_under_monitor.py` polls Polymarket once a minute from 14:00 up to 23:00 Israel local time. It looks for the lowest ask, the cheapest offer to sell, on these full-game Under markets in live matches from the enabled leagues.

| Full-game total | Default alert limit |
| --- | ---: |
| Under 5.5 goals | 80¢ |
| Under 6.5 goals | 85¢ |
| Under 7.5 goals | 85¢ |

The first poll at or below the limit triggers a Telegram alert. The monitor stores alert history in `live_under_alerts.sqlite3` so each market alerts at most once across restarts. Keep that file when moving the service to a new host.

## Setup & Configuration

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in the project’s `.env` file. Market prices and match status come from Polymarket’s public APIs.

## Changing a threshold in Telegram

1. Send `/menu` to your bot.
2. Tap **Edit Thresholds**.
3. Choose a league.
4. Choose an Under line.
5. Enter the limit in cents, for example `80`.

**Manage Leagues** controls which leagues the monitor checks. League selections and limits are saved in `config_state.json` and apply without a restart.

## Running the Daemon

Dependencies are declared in `pyproject.toml` and pinned in `uv.lock`.

```bash
uv sync
uv run python live_under_monitor.py
```

## Deployment

Both daemons run under `systemd` on an Oracle Cloud Always Free VM. See
[`oracle_daemon_guide.md`](oracle_daemon_guide.md) for provisioning, service installation, log
access, and day-to-day operations.
