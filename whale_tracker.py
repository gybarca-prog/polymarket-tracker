import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

from polymarket_api import fetch_trades, enrich_trades_with_market_data


def telegram_send(text):
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        raise RuntimeError(
            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID GitHub Secret."
        )

    url = f"https://api.telegram.org/bot{token}/sendMessage"

    data = urllib.parse.urlencode(
        {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": "true",
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    with urllib.request.urlopen(req, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError(f"Telegram error: {result}")


def trade_usd(trade):
    try:
        return float(trade.get("size", 0)) * float(trade.get("price", 0))
    except (TypeError, ValueError):
        return 0.0


def format_usd(value):
    return f"${value:,.2f}"


def format_trade(trade, value):
    title = (
        trade.get("_market_title")
        or trade.get("title")
        or trade.get("market")
        or trade.get("question")
        or "Unknown market"
    )

    side = str(trade.get("side") or "").upper()
    price = trade.get("price", "")
    size = trade.get("size", "")

    wallet = (
        trade.get("proxyWallet")
        or trade.get("maker")
        or trade.get("user")
        or "unknown"
    )

    tx = trade.get("transactionHash") or ""

    lines = [
        "🐋 POLYMARKET LARGE TRADE",
        "",
        f"🎾🎮 Market: {title}",
        f"💰 Value: {format_usd(value)}",
        f"📈 Side: {side or 'N/A'}",
        f"💵 Price: {price}",
        f"📦 Size: {size}",
        f"👛 Wallet: {wallet}",
    ]

    if tx:
        lines.append(f"🔗 TX: {tx}")

    return "\n".join(lines)


def is_allowed_sport(trade):
    value = trade.get("_sport_allowed")

    if value is not None:
        return bool(value)

    text = " ".join(
        str(trade.get(key, ""))
        for key in (
            "title",
            "question",
            "market",
            "eventSlug",
            "slug",
            "category",
        )
    ).lower()

    tennis_words = [
        "tennis",
        "atp",
        "wta",
        "us open",
        "australian open",
        "french open",
        "roland garros",
        "wimbledon",
    ]

    esports_words = [
        "esports",
        "e-sports",
        "league of legends",
        "lol",
        "valorant",
        "counter-strike",
        "counter strike",
        "cs2",
        "dota",
        "dota 2",
        "starcraft",
        "overwatch",
        "rainbow six",
        "rocket league",
    ]

    return any(
        word in text
        for word in tennis_words + esports_words
    )


def poll(min_usd=10000, lookback=5000):
    print(
        f"Fetching latest trades (limit={lookback})..."
    )

    trades = fetch_trades(
        limit=lookback
    )

    if not trades:
        print("No trades returned.")
        return 0

    print(
        f"Received {len(trades)} trades."
    )

    print(
        "Enriching trades with market information..."
    )

    trades = enrich_trades_with_market_data(
        trades
    )

    qualifying = []

    for trade in trades:

        value = trade_usd(trade)

        if value < min_usd:
            continue

        if not is_allowed_sport(trade):
            continue

        qualifying.append(
            (trade, value)
        )

    print(
        f"Found {len(qualifying)} qualifying trades "
        f"(>= ${min_usd:,.2f}, tennis/esports)."
    )

    for trade, value in qualifying:

        message = format_trade(
            trade,
            value
        )

        print("")
        print(message)
        print("")

        try:

            telegram_send(
                message
            )

            print(
                "Telegram notification sent."
            )

        except Exception as exc:

            print(
                f"Telegram notification failed: {exc}",
                file=sys.stderr
            )

            raise

    return len(qualifying)


def main():

    parser = argparse.ArgumentParser(
        description=(
            "Polymarket tennis/esports "
            "large trade tracker"
        )
    )

    subparsers = parser.add_subparsers(
        dest="command",
        required=True
    )

    poll_parser = subparsers.add_parser(
        "poll",
        help=(
            "Check recent trades and send "
            "qualifying trades to Telegram"
        )
    )

    poll_parser.add_argument(
        "--min-usd",
        type=float,
        default=10000,
        help=(
            "Minimum trade value in USD"
        )
    )

    poll_parser.add_argument(
        "--lookback",
        type=int,
        default=5000,
        help=(
            "Number of recent trades to inspect"
        )
    )

    args = parser.parse_args()

    if args.command == "poll":

        poll(
            min_usd=args.min_usd,
            lookback=args.lookback
        )


if __name__ == "__main__":
    main()
