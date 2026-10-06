#!/usr/bin/env python3
"""
Polymarket Whale Tracker

Telegram alert:
- minimum $10,000 trade
- no smart-money filter
- no PnL / win-rate filter
- no spike alerts
- no consensus alerts
"""

import argparse
import sys
import time
from datetime import datetime, timezone

import db as DB
import notifier
from polymarket_api import (
    fetch_trades,
    fetch_positions,
    fetch_value,
    usd,
    trade_key,
)


# ----------------------------- formatting -----------------------------

def fmt_money(x):
    return f"${x:,.2f}"


def short(a):
    return f"{a[:6]}...{a[-4:]}" if a and len(a) > 12 else a


def ts_str(ts):
    return datetime.fromtimestamp(
        int(ts),
        tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")


def name_of(t):
    return (
        t.get("name")
        or t.get("pseudonym")
        or short(t.get("proxyWallet", ""))
    )


def trade_line(t):
    arrow = "🟢 BUY " if t.get("side") == "BUY" else "🔴 SELL"

    return (
        f"{arrow} {fmt_money(usd(t)):>12}  "
        f"{name_of(t)[:20]:<20}  "
        f"{(t.get('outcome') or '?')[:8]:<8} "
        f"@ {float(t.get('price', 0)):.3f} | "
        f"{(t.get('title') or '')[:50]}"
    )


def alert_text(t, reasons, score=None):
    arrow = "🟢 *BUY*" if t.get("side") == "BUY" else "🔴 *SELL*"

    tags = " ".join(
        {
            "whale": "🐋WHALE",
            "watchlist": "⭐WATCHLIST",
        }.get(r, r)
        for r in reasons
    )

    lines = [
        f"{tags}",
        (
            f"{arrow} {fmt_money(usd(t))} "
            f"@ {float(t.get('price', 0)):.3f} "
            f"({float(t.get('price', 0)) * 100:.0f}%)"
        ),
        f"*{t.get('title', '')}*",
        (
            f"Outcome: *{t.get('outcome', '?')}*  |  "
            f"Trader: `{name_of(t)}`"
        ),
    ]

    wallet = t.get("proxyWallet", "")

    if wallet:
        lines.append(
            f"https://polymarket.com/profile/{wallet}"
        )

    return "\n".join(lines)


# ----------------------------- commands -----------------------------

def cmd_scan(args):
    trades = fetch_trades(limit=args.lookback)

    whales = [
        t for t in trades
        if usd(t) >= args.min_usd
    ]

    whales.sort(key=usd, reverse=True)

    print(
        f"\n🐋 WHALE SCAN — "
        f"{len(whales)} trade >= {fmt_money(args.min_usd)} "
        f"(dari {len(trades)} terakhir)\n"
        + "-" * 104
    )

    for t in whales[:args.top]:
        print(trade_line(t))

    print("-" * 104)

    print(
        f"Total volume whale: "
        f"{fmt_money(sum(usd(t) for t in whales))}\n"
    )


def cmd_watch(args):
    conn = DB.connect(args.db)

    follow = (
        {
            a.strip().lower()
            for a in args.follow.split(",")
            if a.strip()
        }
        if args.follow
        else None
    )

    seen = set()
    chans = notifier.configured()

    print(
        f"👀 WATCH — min {fmt_money(args.min_usd)}"
        + (f", follow {len(follow)}" if follow else "")
        + (
            f", alerts→{chans}"
            if chans
            else ", alerts→terminal only"
        )
        + f", every {args.interval}s. Ctrl+C to stop.\n"
    )

    try:
        while True:

            try:
                trades = fetch_trades(
                    limit=args.lookback
                )

            except Exception as e:
                print(
                    f"⚠️ fetch error: {e}",
                    file=sys.stderr
                )

                time.sleep(args.interval)
                continue

            for t in sorted(
                trades,
                key=lambda x: x.get("timestamp", 0)
            ):

                k = trade_key(t)

                if k in seen:
                    continue

                seen.add(k)

                DB.insert_trade(
                    conn,
                    t,
                    usd(t)
                )

                reasons = _evaluate(
                    t,
                    args,
                    follow
                )

                if reasons:

                    print(
                        f"[{ts_str(t.get('timestamp'))}] "
                        f"{trade_line(t)} "
                        f"<= {','.join(reasons)}"
                    )

                    if chans:
                        notifier.notify(
                            alert_text(
                                t,
                                reasons
                            )
                        )

            conn.commit()

            if len(seen) > 20000:
                seen = set(
                    list(seen)[-10000:]
                )

            time.sleep(args.interval)

    except KeyboardInterrupt:
        print("\n👋 Stopped.")

    finally:
        conn.commit()
        conn.close()


def cmd_poll(args):
    """
    One-shot poll.

    Alerts ONLY:
    - trades >= minimum USD
    - optional watchlist trades

    Smart-money, spike and consensus are disabled.
    """

    conn = DB.connect(args.db)

    follow = _watchlist_set(conn)

    trades = fetch_trades(
        limit=args.lookback
    )

    # Store trades in database.
    for t in trades:
        DB.insert_trade(
            conn,
            t,
            usd(t)
        )

    conn.commit()

    new_alerts = 0

    for t in sorted(
        trades,
        key=lambda x: x.get("timestamp", 0)
    ):

        k = trade_key(t)

        reasons = _evaluate(
            t,
            args,
            follow
        )

        if not reasons:
            continue

        if DB.already_alerted(
            conn,
            k
        ):
            continue

        chans = notifier.notify(
            alert_text(
                t,
                reasons
            )
        )

        DB.mark_alerted(
            conn,
            k,
            ",".join(reasons)
        )

        new_alerts += 1

        print(
            f"ALERT [{','.join(reasons)}] "
            f"{trade_line(t)} "
            f"-> {chans or 'terminal'}"
        )

    conn.commit()
    conn.close()

    print(
        f"\n✅ poll done. "
        f"{len(trades)} trades scanned, "
        f"{new_alerts} new alerts sent."
    )


def _evaluate(t, args, follow):
    """
    Decide whether a trade should generate an alert.

    Current rules:
    - >= minimum USD -> whale
    - watchlisted wallet -> watchlist

    NO smart-money.
    NO PnL.
    NO win-rate.
    NO spike.
    NO consensus.
    """

    reasons = []

    wallet = (
        t.get("proxyWallet") or ""
    ).lower()

    value = usd(t)

    # Optional watchlist alert.
    if follow and wallet in follow:
        reasons.append("watchlist")

    # Main whale condition.
    if value >= args.min_usd:
        reasons.append("whale")

    return reasons


def _watchlist_set(conn):
    return {
        w["wallet"]
        for w in DB.watchlist_all(conn)
    }


def cmd_wallet(args):
    w = args.address

    val = fetch_value(w)

    positions = fetch_positions(
        w,
        limit=200
    )

    trades = [
        t
        for t in fetch_trades(limit=1000)
        if (
            t.get("proxyWallet") or ""
        ).lower() == w.lower()
    ]

    open_pos = [
        p
        for p in positions
        if float(
            p.get("currentValue", 0)
        ) > 0.01
    ]

    total_pnl = sum(
        float(p.get("cashPnl", 0))
        for p in positions
    )

    print(
        f"\n💼 WALLET {w}\n"
        + "-" * 88
    )

    print(
        f"Saldo posisi sekarang : "
        f"{fmt_money(val)}"
    )

    print(
        f"Total PnL (all-time)  : "
        f"{fmt_money(total_pnl)}"
    )

    print(
        f"Posisi terbuka        : "
        f"{len(open_pos)}"
    )

    print("\nTop posisi terbuka:")

    for p in sorted(
        open_pos,
        key=lambda x: float(
            x.get("currentValue", 0)
        ),
        reverse=True
    )[:10]:

        print(
            f"  "
            f"{fmt_money(float(p.get('currentValue', 0))):>12}  "
            f"PnL "
            f"{fmt_money(float(p.get('cashPnl', 0))):>12}"
            f"  {(p.get('outcome') or '?')[:8]:<8} | "
            f"{(p.get('title') or '')[:46]}"
        )

    print("\nTrade terakhir:")

    for t in sorted(
        trades,
        key=lambda x: x.get("timestamp", 0),
        reverse=True
    )[:10]:

        print(
            f"  [{ts_str(t.get('timestamp'))}] "
            f"{trade_line(t)}"
        )

    print()


def cmd_leaderboard(args):
    trades = fetch_trades(
        limit=args.lookback
    )

    agg = {}

    for t in trades:

        w = t.get(
            "proxyWallet",
            ""
        )

        a = agg.setdefault(
            w,
            {
                "name": name_of(t),
                "vol": 0.0,
                "n": 0
            }
        )

        a["vol"] += usd(t)
        a["n"] += 1

    ranked = sorted(
        agg.items(),
        key=lambda kv: kv[1]["vol"],
        reverse=True
    )

    print(
        f"\n🏆 LEADERBOARD — "
        f"dari {len(trades)} trade terakhir\n"
        + "-" * 80
    )

    print(
        f"{'#':>2}  "
        f"{'Volume':>14}  "
        f"{'Trades':>6}  "
        f"{'Trader':<20}  Wallet"
    )

    for i, (w, a) in enumerate(
        ranked[:args.top],
        1
    ):

        print(
            f"{i:>2}  "
            f"{fmt_money(a['vol']):>14}  "
            f"{a['n']:>6}  "
            f"{a['name'][:20]:<20}  "
            f"{short(w)}"
        )

    print()


def cmd_watchlist(args):
    conn = DB.connect(args.db)

    if args.action == "add":

        DB.watchlist_add(
            conn,
            args.address,
            args.label or ""
        )

        print(
            f"⭐ Added {args.address} "
            f"({args.label or 'no label'})"
        )

    elif args.action == "remove":

        DB.watchlist_remove(
            conn,
            args.address
        )

        print(
            f"🗑️ Removed {args.address}"
        )

    elif args.action == "list":

        wl = DB.watchlist_all(conn)

        print(
            f"\n⭐ WATCHLIST ({len(wl)})\n"
            + "-" * 60
        )

        for w in wl:
            print(
                f"  {short(w['wallet'])}  "
                f"{w['label']}"
            )

        print()

    elif args.action == "pnl":

        wl = DB.watchlist_all(conn)

        print(
            f"\n⭐ WATCHLIST PnL ({len(wl)})\n"
            + "-" * 78
        )

        print(
            f"{'Wallet':<16}  "
            f"{'Open val':>12}  "
            f"{'Realized PnL':>14}  "
            f"{'WinRate':>8}  Label"
        )

        for w in wl:

            try:

                from smartmoney import score_wallet

                s = score_wallet(
                    w["wallet"]
                )

                print(
                    f"{short(w['wallet']):<16}  "
                    f"{fmt_money(s['cur_value']):>12}  "
                    f"{fmt_money(s['realized_pnl']):>14}  "
                    f"{s['winrate']*100:>6.0f}%  "
                    f"{w['label']}"
                )

            except Exception as e:

                print(
                    f"{short(w['wallet']):<16}  "
                    f"(error: {e})"
                )

        print()

    conn.close()


# ----------------------------- CLI -----------------------------

def _add_filter_args(p, default_min):

    p.add_argument(
        "--min-usd",
        type=float,
        default=default_min
    )

    p.add_argument(
        "--lookback",
        type=int,
        default=500
    )

    p.add_argument(
        "--db",
        type=str,
        default="tracker.db"
    )


def main():

    p = argparse.ArgumentParser(
        description="Polymarket Whale Tracker"
    )

    sub = p.add_subparsers(
        dest="cmd",
        required=True
    )

    # SCAN
    sc = sub.add_parser("scan")

    sc.add_argument(
        "--min-usd",
        type=float,
        default=10000
    )

    sc.add_argument(
        "--lookback",
        type=int,
        default=500
    )

    sc.add_argument(
        "--top",
        type=int,
        default=30
    )

    sc.set_defaults(
        func=cmd_scan
    )

    # WATCH
    wt = sub.add_parser("watch")

    _add_filter_args(
        wt,
        10000
    )

    wt.add_argument(
        "--interval",
        type=int,
        default=15
    )

    wt.add_argument(
        "--follow",
        type=str,
        default=""
    )

    wt.set_defaults(
        func=cmd_watch
    )

    # POLL
    pl = sub.add_parser("poll")

    _add_filter_args(
        pl,
        10000
    )

    pl.set_defaults(
        func=cmd_poll
    )

    # WALLET
    wallet = sub.add_parser("wallet")

    wallet.add_argument(
        "address"
    )

    wallet.set_defaults(
        func=cmd_wallet
    )

    # LEADERBOARD
    lb = sub.add_parser(
        "leaderboard"
    )

    lb.add_argument(
        "--lookback",
        type=int,
        default=1000
    )

    lb.add_argument(
        "--top",
        type=int,
        default=20
    )

    lb.set_defaults(
        func=cmd_leaderboard
    )

    # WATCHLIST
    wlc = sub.add_parser(
        "watchlist"
    )

    wlc.add_argument(
        "action",
        choices=[
            "add",
            "remove",
            "list",
            "pnl"
        ]
    )

    wlc.add_argument(
        "address",
        nargs="?",
        default=""
    )

    wlc.add_argument(
        "--label",
        type=str,
        default=""
    )

    wlc.add_argument(
        "--db",
        type=str,
        default="tracker.db"
    )

    wlc.set_defaults(
        func=cmd_watchlist
    )

    args = p.parse_args()

    args.func(args)


if __name__ == "__main__":
    main()
