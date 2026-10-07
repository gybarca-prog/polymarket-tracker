"""Thin read-only clients for Polymarket public APIs."""

import json
import urllib.parse
import urllib.request


DATA_BASE = "https://data-api.polymarket.com"
GAMMA_BASE = "https://gamma-api.polymarket.com"

_UA = {
    "User-Agent": "polymarket-whale-tracker/2.0"
}


def _get(base, path, params=None, timeout=20):
    url = f"{base}{path}"

    if params:
        url += "?" + urllib.parse.urlencode(
            params,
            doseq=True
        )

    req = urllib.request.Request(
        url,
        headers=_UA
    )

    with urllib.request.urlopen(
        req,
        timeout=timeout
    ) as resp:
        return json.loads(
            resp.read().decode("utf-8")
        )


# ============================================================
# DATA API
# ============================================================

def fetch_trades(limit=500, offset=0):
    """
    Fetch recent Polymarket trades.

    Polymarket is queried in pages of up to 500 trades.
    This allows the tracker to inspect more than 500 trades
    without relying on a single oversized API request.
    """

    if limit <= 0:
        return []

    page_size = 500
    all_trades = []

    current_offset = offset

    while len(all_trades) < limit:

        remaining = limit - len(all_trades)
        current_limit = min(page_size, remaining)

        page = _get(
            DATA_BASE,
            "/trades",
            {
                "limit": current_limit,
                "offset": current_offset
            }
        )

        if not isinstance(page, list) or not page:
            break

        all_trades.extend(page)

        if len(page) < current_limit:
            break

        current_offset += len(page)

    return all_trades[:limit]


def fetch_positions(wallet, limit=200):
    return _get(
        DATA_BASE,
        "/positions",
        {
            "user": wallet,
            "limit": limit,
            "sortBy": "CURRENT",
            "sortDirection": "DESC"
        }
    )


def fetch_value(wallet):
    data = _get(
        DATA_BASE,
        "/value",
        {
            "user": wallet
        }
    )

    return (
        data[0]["value"]
        if data
        else 0.0
    )


def usd(trade):
    """Trade USD value = shares × price."""

    return (
        float(trade.get("size", 0))
        * float(trade.get("price", 0))
    )


def trade_key(t):
    return (
        f"{t.get('transactionHash', '')}:"
        f"{t.get('asset', '')}:"
        f"{t.get('timestamp', '')}"
    )


# ============================================================
# MARKET / SPORT FILTER
# ============================================================

TENNIS_KEYWORDS = {
    "tennis",
    "atp",
    "wta",
    "grand slam",
    "australian open",
    "french open",
    "roland garros",
    "wimbledon",
    "us open tennis",
    "miami open",
    "indian wells",
    "cincinnati open",
    "madrid open",
    "itf",
    "challenger",
    "atp challenger",
    "queens club",
    "china open",
    "tokyo open",
}


ESPORTS_KEYWORDS = {
    "esports",
    "esport",
    "counter-strike",
    "counter strike",
    "counterstrike",
    "cs2",
    "valorant",
    "league of legends",
    "dota",
    "dota 2",
    "starcraft",
    "starcraft 2",
    "overwatch",
    "rainbow six",
    "rainbow six siege",
    "rocket league",
    "call of duty",
    "pubg",
    "fortnite",
    "apex legends",
    "tekken",
    "street fighter",
    "fighting game",
}


def _market_text(market):
    """Collect searchable market information."""

    fields = [
        market.get("question"),
        market.get("title"),
        market.get("description"),
        market.get("slug"),
        market.get("category"),
    ]

    return " ".join(
        str(value)
        for value in fields
        if value
    ).lower()


def _keyword_match(text):
    """Return True for tennis or esports."""

    if any(
        keyword in text
        for keyword in TENNIS_KEYWORDS
    ):
        return True

    if any(
        keyword in text
        for keyword in ESPORTS_KEYWORDS
    ):
        return True

    return False


def fetch_market_by_condition(condition_id):
    """Find a market from its conditionId."""

    if not condition_id:
        return None

    try:
        data = _get(
            GAMMA_BASE,
            "/markets",
            {
                "condition_ids": condition_id,
                "limit": 1
            }
        )

        if isinstance(data, list) and data:
            return data[0]

    except Exception:
        return None

    return None


def fetch_market_tags(market_id):
    """
    Get tags for a Gamma market.

    Failure returns an empty list so one bad market
    does not stop the entire tracker.
    """

    if not market_id:
        return []

    try:
        data = _get(
            GAMMA_BASE,
            f"/markets/{market_id}/tags"
        )

        if isinstance(data, list):
            return data

    except Exception:
        return []

    return []


def market_is_tennis_or_esports(market):
    """
    Check market title/question/description/slug/category
    and Polymarket tags.
    """

    if not market:
        return False

    text = _market_text(market)

    if _keyword_match(text):
        return True

    market_id = (
        market.get("id")
        or market.get("market_id")
    )

    tags = fetch_market_tags(market_id)

    for tag in tags:

        if isinstance(tag, dict):

            tag_text = " ".join(
                str(
                    tag.get(field, "")
                )
                for field in (
                    "label",
                    "name",
                    "slug"
                )
            ).lower()

        else:
            tag_text = str(tag).lower()

        if _keyword_match(tag_text):
            return True

    return False


def enrich_trades_with_market_data(trades):
    """
    Add market information to every trade.

    The same conditionId is only looked up once,
    preventing unnecessary Gamma API requests.
    """

    cache = {}

    for trade in trades:

        condition_id = (
            trade.get("conditionId")
            or trade.get("condition_id")
        )

        if not condition_id:
            trade["_sport_allowed"] = False
            continue

        if condition_id not in cache:
            cache[condition_id] = (
                fetch_market_by_condition(
                    condition_id
                )
            )

        market = cache[condition_id]

        if not market:
            trade["_sport_allowed"] = False
            continue

        trade["_market"] = market

        if not trade.get("title"):
            trade["title"] = (
                market.get("question")
                or market.get("title")
                or ""
            )

        trade["_sport_allowed"] = (
            market_is_tennis_or_esports(
                market
            )
        )

    return trades
