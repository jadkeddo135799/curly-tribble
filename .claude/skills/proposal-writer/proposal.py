#!/usr/bin/env python3
"""Turns the call into a priced proposal. Standard library only.

  draft    writes closer/proposals/<company>.md: their situation in their words, what you will do,
           two or three priced options with first-year totals, what it pays back, and one next step
  record   logs the proposal AFTER it went out, with your yes word for word (deal -> proposal)

    python3 proposal.py draft --deal bluebird-ads --options options.json --today 2026-10-29
    python3 proposal.py record --deal bluebird-ads --approval "yes" --at 2026-10-29T16:00

An options file is a list of {"name", "setup", "monthly", "includes": [...], "recommended": true|false}.
Prices come from you. The payback line comes from me.value_line in closer/pipeline.json: it must be
something true you can stand behind. Nothing here invents a number.
"""

import argparse
import datetime
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join("closer", "proposals")
VALID_DAYS = 14


def _pipeline():
    path = os.path.join(HERE, "..", "pipeline", "deals.py")
    if not os.path.exists(path):
        sys.exit("The proposal-writer skill needs the pipeline skill next to it (../pipeline/deals.py).")
    spec = importlib.util.spec_from_file_location("closer_deals", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P = _pipeline()


class ProposalError(Exception):
    pass


def first_year(option):
    return option.get("setup", 0) + 12 * option.get("monthly", 0)


def check_options(options):
    if not 1 <= len(options) <= 3:
        raise ProposalError("Give 1 to 3 options. More than 3 makes it harder to say yes.")
    for o in options:
        for k in ("name", "includes"):
            if not o.get(k):
                raise ProposalError("Every option needs %s." % k)
        if o.get("setup", 0) < 0 or o.get("monthly", 0) < 0 or first_year(o) == 0:
            raise ProposalError("Option %s needs a price (setup and/or monthly)." % o["name"])
    if sum(1 for o in options if o.get("recommended")) > 1:
        raise ProposalError("Recommend one option at most.")


def their_words(data, deal):
    """What they said: their replies and the call notes, newest last."""
    out = []
    for a in P.history(data, deal_id=deal["id"]):
        if a.get("direction") == "in" and a.get("text"):
            out.append(a["text"])
        if a["type"] == "call" and a.get("text"):
            out.append(a["text"])
    return out


def price_line(o):
    parts = []
    if o.get("setup"):
        parts.append("%s setup" % P.money(o["setup"]))
    if o.get("monthly"):
        parts.append("%s/month" % P.money(o["monthly"]))
    return " + ".join(parts)


def build(data, deal, options, today):
    check_options(options)
    me = data.get("me", {})
    c = P.find(data["contacts"], deal["contact_id"])
    valid = today + datetime.timedelta(days=VALID_DAYS)
    lines = ["# Proposal for %s" % c.get("company"), "",
             "Prepared for %s by %s, %s. Valid until %s." % (
                 c["name"], me.get("name"), me.get("company"), valid.strftime("%B %d, %Y").replace(" 0", " ")), "",
             "## Where you are today", ""]
    said = their_words(data, deal)
    lines += ["- %s" % s for s in said] if said else ["- [Claude: their situation, in their own words, from the call]"]
    lines += ["", "## What we'll do", "", "- [Claude: 3 to 5 lines, what they get and by when]", "",
              "## Options", "", "| | Price | First year | Includes |", "| --- | --- | --- | --- |"]
    for o in options:
        name = o["name"] + (" (recommended)" if o.get("recommended") else "")
        lines.append("| %s | %s | %s | %s |" % (name, price_line(o), P.money(first_year(o)), "; ".join(o["includes"])))
    lines += [""]
    if me.get("value_line"):
        lines += ["## What it pays back", "", me["value_line"], ""]
    lines += ["## Next step", "",
              "Reply with the option you want and I'll send the agreement the same day.", ""]
    return "\n".join(lines)


def record(data, deal_id, approval, when):
    ok, why = P.parse_yes(approval)
    if not ok:
        return False, "Not recorded: %s." % why
    deal = P.find(data["deals"], deal_id)
    if deal is None:
        return False, "No deal %s" % deal_id
    c = P.find(data["contacts"], deal["contact_id"])
    path = os.path.join(OUT_DIR, P.slug(c.get("company") or c["name"]) + ".md")
    if not os.path.exists(path):
        return False, "Not recorded: no proposal drafted for %s yet." % deal_id
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if "[Claude:" in text:
        return False, "Not recorded: the proposal still has a [Claude: ...] placeholder in it."
    amounts = [l for l in text.splitlines() if l.startswith("| ") and "$" in l]
    P.add_activity(data, date=when, contact_id=c["id"], deal_id=deal_id, channel="email", type="proposal",
                   direction="out", subject="Proposal for %s" % c.get("company"), approval=approval,
                   text="Proposal sent: " + " / ".join(l.split("|")[2].strip() for l in amounts), attachment=path)
    if P.STAGES.index(deal["stage"]) < P.STAGES.index("proposal"):
        deal["stage"] = "proposal"
    deal["next_step"] = "%s picks an option" % c["name"].split()[0]
    return True, "Logged: proposal sent to %s." % c["name"]


def main(argv=None):
    p = argparse.ArgumentParser(description="Write a priced proposal.")
    p.add_argument("--file", default=P.DEFAULT_FILE)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("draft")
    s.add_argument("--deal", required=True)
    s.add_argument("--options", required=True)
    s.add_argument("--today")
    s = sub.add_parser("record")
    s.add_argument("--deal", required=True)
    s.add_argument("--approval", required=True)
    s.add_argument("--at", required=True)
    a = p.parse_args(argv)
    try:
        data = P.load(a.file)
        if a.cmd == "draft":
            deal = P.find(data["deals"], a.deal)
            if deal is None:
                raise ProposalError("No deal %s" % a.deal)
            with open(a.options, encoding="utf-8") as fh:
                options = json.load(fh)
            md = build(data, deal, options, P.today_arg(a.today))
            c = P.find(data["contacts"], deal["contact_id"])
            os.makedirs(OUT_DIR, exist_ok=True)
            path = os.path.join(OUT_DIR, P.slug(c.get("company") or c["name"]) + ".md")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(md)
            print("Wrote %s. Fill every [Claude: ...] from the call before it goes." % path)
        else:
            ok, msg = record(data, a.deal, a.approval, a.at)
            if ok:
                P.save(data, a.file)
            print(msg)
            return 0 if ok else 1
    except (ProposalError, P.PipelineError, OSError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
