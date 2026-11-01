#!/usr/bin/env python3
"""Tells you what you'll close this month. Standard library only.

  month    the forecast for one month: commit, likely (weighted) and best case, deal by deal
  rates    your real win rate from closed deals, and how long a deal takes to close
  health   deals that make the forecast wrong: past close dates, no close date, gone quiet

    python3 forecast.py month --month 2026-11 --today 2026-10-29
    python3 forecast.py rates
    python3 forecast.py health --today 2026-10-29

Commit = deals at contract-sent closing this month. Likely = every open deal closing this month,
times the chance for its stage. Best case = every open deal closing this month. With 10 or more
closed deals of your own, the stage chances are scaled to your real win rate. It is an estimate,
not a promise, and it says what it is based on.
"""

import argparse
import datetime
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
STAGE_CHANCE = {"lead": 0.05, "contacted": 0.10, "replied": 0.20, "call-booked": 0.30, "proposal": 0.50,
                "contract-sent": 0.80, "won": 1.0, "lost": 0.0}
BASE_WIN = 0.25  # the win rate the stage chances above assume for a deal that reached a call
MIN_HISTORY = 10
QUIET_DAYS = 14


def _pipeline():
    path = os.path.join(HERE, "..", "pipeline", "deals.py")
    if not os.path.exists(path):
        sys.exit("The forecaster skill needs the pipeline skill next to it (../pipeline/deals.py).")
    spec = importlib.util.spec_from_file_location("closer_deals", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P = _pipeline()
D = datetime.date


def counted_closed(data):
    """Won and lost deals that count toward a win rate (an unsubscribe from cold email is not a lost sale)."""
    won = [d for d in data["deals"] if d["stage"] == "won"]
    lost = [d for d in data["deals"] if d["stage"] == "lost" and "unsubscribed" not in (d.get("lost_reason") or "")]
    return won, lost


def rates(data):
    won, lost = counted_closed(data)
    n = len(won) + len(lost)
    days = []
    for d in won:
        if d.get("created") and d.get("close_date"):
            days.append((D.fromisoformat(d["close_date"]) - D.fromisoformat(d["created"])).days)
    return {"won": len(won), "lost": len(lost), "win_rate": (len(won) / n) if n else None,
            "avg_days_to_close": round(sum(days) / len(days)) if days else None, "enough": n >= MIN_HISTORY}


def chances(data):
    r = rates(data)
    if not r["enough"]:
        return dict(STAGE_CHANCE), "standard stage chances (fewer than %d closed deals of your own)" % MIN_HISTORY
    scale = r["win_rate"] / BASE_WIN
    out = {k: (min(0.95, v * scale) if 0 < v < 1 else v) for k, v in STAGE_CHANCE.items()}
    return out, "stage chances scaled to your win rate of %d%% over %d closed deals" % (
        round(100 * r["win_rate"]), r["won"] + r["lost"])


def month_bounds(month):
    y, m = (int(x) for x in month.split("-"))
    start = D(y, m, 1)
    end = D(y + (m == 12), m % 12 + 1, 1) - datetime.timedelta(days=1)
    return start, end


def forecast(data, month, today):
    start, end = month_bounds(month)
    ch, basis = chances(data)
    rows, commit, likely, best = [], 0.0, 0.0, 0.0
    won_in_month = sum(d.get("value", 0) for d in data["deals"] if d["stage"] == "won" and d.get("close_date")
                       and start <= D.fromisoformat(d["close_date"]) <= end)
    for d in data["deals"]:
        if d["stage"] not in P.OPEN or not d.get("close_date"):
            continue
        close = D.fromisoformat(d["close_date"])
        if not start <= close <= end:
            continue
        v = d.get("value", 0)
        w = v * ch[d["stage"]]
        rows.append({"deal_id": d["id"], "title": d["title"], "stage": d["stage"], "value": v, "chance": ch[d["stage"]],
                     "weighted": w, "close": d["close_date"]})
        likely += w
        best += v
        if d["stage"] == "contract-sent":
            commit += v
    rows.sort(key=lambda r: -r["weighted"])
    return {"month": month, "won": won_in_month, "commit": commit, "likely": round(likely), "best": best,
            "rows": rows, "basis": basis}


def health(data, today):
    out = []
    for d in data["deals"]:
        if d["stage"] not in P.OPEN:
            continue
        if not d.get("close_date"):
            if P.STAGES.index(d["stage"]) >= P.STAGES.index("call-booked"):
                out.append((d["id"], "no close date, so it is in no month's forecast"))
        elif D.fromisoformat(d["close_date"]) < today:
            out.append((d["id"], "close date %s has passed: move it or close the deal" % d["close_date"]))
        quiet = (today - D.fromisoformat(P.last_touch(data, d))).days
        if quiet >= QUIET_DAYS and d["stage"] not in ("lead",):
            out.append((d["id"], "quiet for %d days" % quiet))
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description="What you'll close this month.")
    p.add_argument("--file", default=P.DEFAULT_FILE)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("month")
    s.add_argument("--month", help="YYYY-MM (default: this month)")
    s.add_argument("--today")
    sub.add_parser("rates")
    s = sub.add_parser("health")
    s.add_argument("--today")
    a = p.parse_args(argv)
    try:
        data = P.load(a.file)
        if a.cmd == "month":
            today = P.today_arg(a.today)
            f = forecast(data, a.month or today.strftime("%Y-%m"), today)
            print("%s forecast" % f["month"])
            print("  Won so far:  %s" % P.money(f["won"]))
            print("  Commit:      %s  (contracts out)" % P.money(f["commit"]))
            print("  Likely:      %s  (weighted)" % P.money(f["likely"]))
            print("  Best case:   %s  (everything closing this month)" % P.money(f["best"]))
            for r in f["rows"]:
                print("    %-30s %-13s %9s x %3d%% = %9s  close %s" % (
                    r["title"][:30], r["stage"], P.money(r["value"]), round(100 * r["chance"]),
                    P.money(r["weighted"]), r["close"]))
            print("  Based on: %s." % f["basis"])
        elif a.cmd == "rates":
            r = rates(data)
            if r["win_rate"] is None:
                print("No closed deals yet.")
            else:
                print("Won %d, lost %d: %d%% win rate.%s" % (r["won"], r["lost"], round(100 * r["win_rate"]),
                      "" if r["enough"] else " Too few to trust yet."))
            if r["avg_days_to_close"] is not None:
                print("A won deal takes %d days on average." % r["avg_days_to_close"])
        else:
            items = health(data, P.today_arg(a.today))
            for did, why in items:
                print("%-24s %s" % (did, why))
            if not items:
                print("Every open deal has a close date in the future and recent activity.")
    except (P.PipelineError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
