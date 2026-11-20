import asyncio
import html
import json
import logging
import signal
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path

import aiohttp

from bot_ui import start_bot_polling
from config_manager import RuntimeConfig, UNDER_ACTIVE_END_HOUR, UNDER_ACTIVE_START_HOUR, UNDER_DEFAULT_THRESHOLDS
from soccer_monitor import (
    DEP_GAMMA,
    DEP_TELEGRAM,
    ISRAEL_TZ,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
    AlertEngine,
    ApiHealth,
    ErrorAlertHandler,
    error_alert_loop,
    parse_game_total_line,
)

logger = logging.getLogger("LiveUnderMonitor")
EVENTS_URL = "https://gamma-api.polymarket.com/events/keyset"
BOOKS_URL = "https://clob.polymarket.com/books"
DEP_BOOKS = "Polymarket order books"
MAX_MATCH_AGE = timedelta(hours=4)


@dataclass(frozen=True)
class UnderMarket:
    league: str
    line: float
    title: str
    slug: str
    score: str


def is_active(now: datetime) -> bool:
    return UNDER_ACTIVE_START_HOUR <= now.astimezone(ISRAEL_TZ).hour < UNDER_ACTIVE_END_HOUR


def prepare_alert_history(db: sqlite3.Connection) -> None:
    db.execute("CREATE TABLE IF NOT EXISTS alerted (token_id TEXT PRIMARY KEY)")


async def fetch_live_markets(session, config: RuntimeConfig, now: datetime) -> dict[str, UnderMarket]:
    if not config.active_leagues:
        return {}

    now = now.astimezone(timezone.utc)
    earliest = now - MAX_MATCH_AGE
    params = {
        "closed": "false",
        "tag_slug": "soccer",
        "limit": "100",
        "start_time_min": earliest.isoformat(timespec="seconds"),
        "start_time_max": now.isoformat(timespec="seconds"),
    }
    markets = {}
    while True:
        async with session.get(EVENTS_URL, params=params) as response:
            response.raise_for_status()
            page = await response.json()

        for event in page["events"]:
            if event.get("live") is not True or event.get("ended") is True:
                continue
            try:
                kickoff = datetime.fromisoformat(event["startTime"].replace("Z", "+00:00"))
                if not earliest <= kickoff <= now:
                    continue
            except (AttributeError, KeyError, TypeError, ValueError):
                continue

            league = next((name for tag in event.get("tags", [])
                           if (name := config.match_league_name(tag.get("label") or ""))), "")
            if league not in config.active_leagues:
                continue

            for market in event.get("markets", []):
                if (market.get("sportsMarketType") != "totals" or market.get("closed") is True
                        or market.get("acceptingOrders") is not True):
                    continue
                line = parse_game_total_line(market.get("question") or "")
                if line not in UNDER_DEFAULT_THRESHOLDS:
                    continue
                try:
                    outcomes = market["outcomes"]
                    tokens = market["clobTokenIds"]
                    if isinstance(outcomes, str):
                        outcomes = json.loads(outcomes)
                    if isinstance(tokens, str):
                        tokens = json.loads(tokens)
                    if not isinstance(tokens, list) or not isinstance(outcomes, list) or len(tokens) != len(outcomes):
                        continue
                    token = tokens[outcomes.index("Under")]
                    if not isinstance(token, str) or not token:
                        continue
                except (KeyError, ValueError):
                    continue

                markets[token] = UnderMarket(league, line, event["title"], event["slug"], event.get("score") or "")

        cursor = page.get("next_cursor")
        if not cursor:
            return markets
        if cursor == params.get("after_cursor"):
            raise ValueError("Polymarket repeated the events page cursor")
        params["after_cursor"] = cursor


def lowest_ask(book) -> float | None:
    prices = []
    for ask in book.get("asks") or []:
        try:
            price = float(ask["price"])
            if 0 < price <= 1 and float(ask["size"]) > 0:
                prices.append(price)
        except (KeyError, TypeError, ValueError):
            continue
    return min(prices, default=None)


async def poll_once(session, config: RuntimeConfig, db: sqlite3.Connection, send_text,
                    health: ApiHealth, now: datetime) -> None:
    dependency = DEP_GAMMA
    try:
        markets = await fetch_live_markets(session, config, now)
        if config.active_leagues:
            health.ok(DEP_GAMMA, f"{len(markets)} live Under markets")
        dependency = DEP_BOOKS
        tokens = list(markets)
        books = {}
        for start in range(0, len(tokens), 100):
            batch = tokens[start:start + 100]
            async with session.post(BOOKS_URL, json=[{"token_id": token} for token in batch]) as response:
                response.raise_for_status()
                books.update((book["asset_id"], book) for book in await response.json())
        if tokens:
            health.ok(DEP_BOOKS, f"{len(books)} books")
    except (aiohttp.ClientError, asyncio.TimeoutError, KeyError, TypeError, ValueError) as exc:
        detail = f"{type(exc).__name__}: {exc}"
        logger.error("%s poll failed: %s", dependency, detail,
                     extra=health.fail(dependency, detail, exc=exc))
        return

    logger.info("Polled %d live Under markets, received %d order books", len(markets), len(books))
    for token, market in markets.items():
        if market.league not in config.active_leagues:
            continue
        price = lowest_ask(books.get(token, {}))
        threshold = config.get_under_threshold(market.league, market.line)
        if price is None or price > threshold:
            continue

        # Telegram has no idempotency key. Commit before sending to prevent a
        # restart or an ambiguous network failure from sending a second alert.
        with db:
            claimed = db.execute("INSERT OR IGNORE INTO alerted VALUES (?)", (token,)).rowcount
        if not claimed:
            continue

        message = (
            f"🚨 <b>LIVE UNDER PRICE ALERT</b>\n\n"
            f"⚽ <b>{html.escape(market.title)}</b>\n"
            f"🏆 {html.escape(market.league)}\n"
            f"📊 Full game · Under {market.line:g} goals\n"
            f"💰 Lowest ask: {price * 100:g}¢ · Limit: {threshold * 100:g}¢\n"
        )
        if market.score:
            message += f"🎯 Score: {html.escape(market.score)}\n"
        message += f"🔗 https://polymarket.com/event/{html.escape(market.slug)}"
        delivered = await send_text(message)
        if delivered:
            logger.info("Alert sent for %s, Under %g at %g¢", market.title, market.line, price * 100)
        elif delivered is False:
            with db:
                db.execute("DELETE FROM alerted WHERE token_id = ?", (token,))
        else:
            logger.warning("Uncertain Telegram delivery for %s, Under %g. Alert will be skipped on future polls.",
                           market.title, market.line)


async def run_polls(session, config, db, send_text, health):
    while True:
        now = datetime.now(ISRAEL_TZ)
        if is_active(now):
            close = now.replace(hour=UNDER_ACTIVE_END_HOUR, minute=0, second=0, microsecond=0)
            seconds_until_close = (close - now).total_seconds()
            try:
                await asyncio.wait_for(poll_once(session, config, db, send_text, health, now), seconds_until_close)
            except asyncio.TimeoutError:
                logger.info("Polling window closed during the final poll")
        await asyncio.sleep(60 - time.time() % 60)


async def main():
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        raise RuntimeError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
    int(TELEGRAM_CHAT_ID)
    config = RuntimeConfig(under_totals=True)
    health = ApiHealth((DEP_GAMMA, DEP_BOOKS, DEP_TELEGRAM))
    error_queue = asyncio.Queue(maxsize=100)
    logging.getLogger().addHandler(ErrorAlertHandler(error_queue))
    logger.info("Starting live Under monitor, active from %02d:00 to %02d:00 Israel time",
                UNDER_ACTIVE_START_HOUR, UNDER_ACTIVE_END_HOUR)

    history = Path(config.filepath).with_name("live_under_alerts.sqlite3")
    with closing(sqlite3.connect(history)) as db:
        prepare_alert_history(db)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            alerts = AlertEngine(session, health)
            tasks = asyncio.gather(
                run_polls(session, config, db, partial(alerts.send_text, report_uncertain=True), health),
                error_alert_loop(alerts, error_queue),
                start_bot_polling(TELEGRAM_BOT_TOKEN, config, TELEGRAM_CHAT_ID, health),
            )
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, tasks.cancel)
            try:
                await tasks
            except asyncio.CancelledError:
                logger.info("Live Under monitor stopped")


if __name__ == "__main__":
    asyncio.run(main())
