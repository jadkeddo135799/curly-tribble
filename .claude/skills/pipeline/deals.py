#!/usr/bin/env python3
"""The one sales board every Closer skill reads and writes. Standard library only.

Owns the schema of closer/pipeline.json: who you sell to (contacts), what you are
selling them (deals), everything that happened (activities) and who must never be
contacted again (suppression).

  init       creates closer/pipeline.json with your details
  contact    adds or updates a contact
  deal       adds or updates a deal
  move       moves a deal to a new stage (won and lost ask for a reason)
  board      the board: every open deal by stage, with its next step
  show       one deal with its contact and its history
  validate   checks the file against the schema; every skill runs it after writing

    python3 deals.py init --name "You" --company "Your Agency" --email hello@youragency.studio \
        --address "100 Congress Ave, Austin, TX 78701"
    python3 deals.py contact --id dana-harbor-lane --name "Dana Ortiz" --company "Harbor Lane Dental" \
        --email dana@harborlanedental.com --website https://harborlanedental.com
    python3 deals.py deal --id harbor-lane-website --contact dana-harbor-lane --title "Website + Google Ads" \
        --value 4788 --close 2026-11-06
    python3 deals.py move --deal harbor-lane-website --stage proposal --today 2026-10-29
    python3 deals.py board --today 2026-10-29
    python3 deals.py validate
"""

import argparse
import datetime
import json
import os
import re
import sys

DEFAULT_FILE = os.path.join("closer", "pipeline.json")
VERSION = 1

STAGES = ("lead", "contacted", "replied", "call-booked", "proposal", "contract-sent", "won", "lost")
OPEN = STAGES[:6]
CHANNELS = ("email", "call", "meeting", "linkedin", "sms", "note")
TYPES = ("opening", "followup", "reply", "call", "meeting", "note", "proposal", "contract", "winback", "stage")
DIRECTIONS = ("out", "in", None)

ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)


# A "no" or any sign of disinterest. One match stops every message to that person.
NO_PATTERNS = [r"\bnot interested\b", r"\bno,? thanks?\b", r"\bno thank you\b", r"\bunsubscribe\b",
               r"\bremove me\b", r"\btake me off\b", r"\bstop (emailing|messaging|contacting|reaching out)\b",
               r"\bdo ?n[o']?t (contact|email|message|call|reach out)\b", r"\bplease stop\b",
               r"\bleave me alone\b", r"\bnot a (good )?fit\b", r"\bwe('re| are) all set\b",
               r"\bwent with (someone|another|a different)\b", r"\bno longer (interested|needed)\b",
               r"\bpass on this\b", r"\bnot for us\b", r"\bwrong person\b", r"\bspam\b"]
NO_RE = re.compile("|".join(NO_PATTERNS), re.I)
UNSUB_RE = re.compile(r"\bunsubscribe\b|\bremove me\b|\btake me off\b|\bstop (emailing|messaging|contacting)\b"
                      r"|\bdo ?n[o']?t (contact|email|message|call)\b|\bplease stop\b|\bleave me alone\b|\bspam\b", re.I)

YES_WORDS = ("yes", "y", "send", "send it")
BATCH_WORDS = {"all", "every", "everyone", "everybody", "everything", "rest", "remaining", "them", "both",
               "bulk", "batch", "each", "others", "these", "those"}
DOUBT_WORDS = {"no", "not", "dont", "don't", "wait", "cancel", "stop", "but", "maybe", "later", "hold",
               "unless", "if", "tomorrow", "after", "before", "change", "edit"}


def parse_yes(reply):
    """An explicit yes for ONE message: 'yes' or 'yes, send it'. Never 'ok', 'sure' or 'yes to all'.
    Returns (ok, why)."""
    text = (reply or "").strip().lower()
    if not text:
        return False, "no reply"
    if text.endswith("?"):
        return False, "that is a question, not a yes"
    words = re.findall(r"[a-z']+", text)
    if not words or words[0] not in ("yes", "y"):
        return False, "only an explicit 'yes' sends a message (got %r)" % reply
    if BATCH_WORDS & set(words):
        return False, "one yes per message: say yes to each one on its own"
    if DOUBT_WORDS & set(words[1:]):
        return False, "that yes has a condition in it; ask again"
    return True, "yes"


class PipelineError(Exception):
    pass


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def today_arg(value):
    return datetime.date.fromisoformat(value) if value else datetime.date.today()


def empty(me=None):
    return {"version": VERSION, "me": me or {}, "contacts": [], "deals": [], "activities": [], "suppression": []}


def load(path=DEFAULT_FILE):
    if not os.path.exists(path):
        raise PipelineError("No %s yet. Run: deals.py init" % path)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save(data, path=DEFAULT_FILE):
    errors, _ = validate(data)
    if errors:
        raise PipelineError("Not saved, the pipeline would be invalid:\n  " + "\n  ".join(errors))
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    os.replace(tmp, path)


def find(items, item_id):
    for it in items:
        if it.get("id") == item_id:
            return it
    return None


def suppressed(data, email):
    email = (email or "").strip().lower()
    return bool(email) and email in [s.lower() for s in data.get("suppression", [])]


def validate(data):
    """Returns (errors, warnings). Errors block a save."""
    errors, warnings = [], []
    for key in ("me", "contacts", "deals", "activities", "suppression"):
        if key not in data:
            errors.append("missing top-level key: %s" % key)
    if errors:
        return errors, warnings
    seen = set()
    for c in data["contacts"]:
        cid = c.get("id", "")
        if not ID_RE.match(cid):
            errors.append("contact id %r must be lowercase-with-hyphens" % cid)
        if cid in seen:
            errors.append("duplicate id %s" % cid)
        seen.add(cid)
        if not c.get("name"):
            errors.append("contact %s has no name" % cid)
        if c.get("email") and not EMAIL_RE.match(c["email"]):
            errors.append("contact %s email %r does not look like an email" % (cid, c["email"]))
        if c.get("email") and suppressed(data, c["email"]) and not c.get("do_not_contact"):
            warnings.append("contact %s is on the suppression list; set do_not_contact" % cid)
    contact_ids = {c["id"] for c in data["contacts"]}
    deal_ids = set()
    for d in data["deals"]:
        did = d.get("id", "")
        if not ID_RE.match(did):
            errors.append("deal id %r must be lowercase-with-hyphens" % did)
        if did in seen or did in deal_ids:
            errors.append("duplicate id %s" % did)
        deal_ids.add(did)
        if d.get("contact_id") not in contact_ids:
            errors.append("deal %s points at unknown contact %r" % (did, d.get("contact_id")))
        if d.get("stage") not in STAGES:
            errors.append("deal %s stage %r is not one of %s" % (did, d.get("stage"), ", ".join(STAGES)))
        if not isinstance(d.get("value", 0), (int, float)) or d.get("value", 0) < 0:
            errors.append("deal %s value must be a number >= 0" % did)
        for k in ("close_date", "created", "revisit"):
            if d.get(k) and not DATE_RE.match(d[k]):
                errors.append("deal %s %s %r must be YYYY-MM-DD" % (did, k, d[k]))
        if d.get("stage") == "lost" and not d.get("lost_reason"):
            errors.append("deal %s is lost with no lost_reason" % did)
        if d.get("stage") in OPEN and not d.get("next_step"):
            warnings.append("deal %s has no next step" % did)
    act_ids = set()
    for a in data["activities"]:
        aid = a.get("id", "")
        if not aid or aid in act_ids:
            errors.append("activity id %r is missing or duplicated" % aid)
        act_ids.add(aid)
        if not STAMP_RE.match(a.get("date", "")):
            errors.append("activity %s date %r must be YYYY-MM-DD or YYYY-MM-DDTHH:MM" % (aid, a.get("date")))
        if a.get("contact_id") not in contact_ids:
            errors.append("activity %s points at unknown contact %r" % (aid, a.get("contact_id")))
        if a.get("deal_id") and a["deal_id"] not in deal_ids:
            errors.append("activity %s points at unknown deal %r" % (aid, a["deal_id"]))
        if a.get("channel") not in CHANNELS:
            errors.append("activity %s channel %r is not one of %s" % (aid, a.get("channel"), ", ".join(CHANNELS)))
        if a.get("type") not in TYPES:
            errors.append("activity %s type %r is not one of %s" % (aid, a.get("type"), ", ".join(TYPES)))
        if a.get("direction") not in DIRECTIONS:
            errors.append("activity %s direction must be out, in or null" % aid)
        if a.get("direction") == "out" and a.get("channel") in ("email", "linkedin", "sms") and not a.get("approval"):
            errors.append("activity %s went out with no recorded approval" % aid)
    return errors, warnings


def next_id(data, prefix="a"):
    n = 0
    for a in data["activities"]:
        m = re.match(r"^%s(\d+)$" % prefix, a.get("id", ""))
        if m:
            n = max(n, int(m.group(1)))
    return "%s%d" % (prefix, n + 1)


def add_activity(data, **fields):
    act = {"id": next_id(data), "date": None, "contact_id": None, "deal_id": None, "channel": None,
           "type": None, "direction": None, "subject": None, "text": ""}
    act.update(fields)
    data["activities"].append(act)
    return act


def history(data, contact_id=None, deal_id=None):
    out = [a for a in data["activities"]
           if (contact_id is None or a["contact_id"] == contact_id) and (deal_id is None or a.get("deal_id") == deal_id)]
    return sorted(out, key=lambda a: a["date"])


def last_touch(data, deal):
    acts = history(data, deal_id=deal["id"]) or history(data, contact_id=deal["contact_id"])
    return acts[-1]["date"][:10] if acts else deal.get("created")


def upsert_contact(data, **f):
    c = find(data["contacts"], f["id"])
    if c is None:
        c = {"id": f["id"], "name": None, "title": None, "company": None, "email": None, "phone": None,
             "website": None, "source": None, "notes": "", "do_not_contact": False}
        data["contacts"].append(c)
    for k, v in f.items():
        if v is not None:
            c[k] = v
    return c


def upsert_deal(data, today, **f):
    d = find(data["deals"], f["id"])
    if d is None:
        d = {"id": f["id"], "contact_id": None, "title": None, "stage": "lead", "value": 0, "currency": "USD",
             "close_date": None, "created": today.isoformat(), "next_step": None, "lost_reason": None}
        data["deals"].append(d)
    for k, v in f.items():
        if v is not None:
            d[k] = v
    return d


def move(data, deal_id, stage, today, reason=None, next_step=None):
    d = find(data["deals"], deal_id)
    if d is None:
        raise PipelineError("No deal %s" % deal_id)
    if stage not in STAGES:
        raise PipelineError("Stage must be one of: " + ", ".join(STAGES))
    if stage == "lost" and not reason:
        raise PipelineError("Say why it was lost (--reason): it is what /win-back reads later.")
    old = d["stage"]
    d["stage"] = stage
    if stage == "lost":
        d["lost_reason"] = reason
    if stage == "won":
        d["close_date"] = today.isoformat()
    if next_step is not None:
        d["next_step"] = next_step
    if stage in ("won", "lost"):
        d["next_step"] = d.get("next_step") if stage == "won" else None
    add_activity(data, date=today.isoformat(), contact_id=d["contact_id"], deal_id=d["id"], channel="note",
                 type="stage", text="%s -> %s%s" % (old, stage, (": " + reason) if reason else ""))
    return d


def money(n):
    return "${:,.0f}".format(n)


def board(data, today):
    lines = []
    total = 0
    for stage in OPEN:
        deals = [d for d in data["deals"] if d["stage"] == stage]
        if not deals:
            continue
        sub = sum(d.get("value", 0) for d in deals)
        total += sub
        lines.append("%s (%d, %s)" % (stage.upper(), len(deals), money(sub)))
        for d in sorted(deals, key=lambda d: -d.get("value", 0)):
            c = find(data["contacts"], d["contact_id"]) or {}
            idle = (today - datetime.date.fromisoformat(last_touch(data, d))).days
            flag = "  [quiet %d days]" % idle if idle >= 14 else ""
            lines.append("  %-28s %-22s %9s  next: %s%s" % (
                d["title"][:28], (c.get("company") or c.get("name") or "")[:22], money(d.get("value", 0)),
                d.get("next_step") or "(none set)", flag))
    won = [d for d in data["deals"] if d["stage"] == "won"]
    lines.append("")
    lines.append("Open: %s across %d deals. Won to date: %s." % (
        money(total), len([d for d in data["deals"] if d["stage"] in OPEN]), money(sum(d.get("value", 0) for d in won))))
    return "\n".join(lines)


def show(data, deal_id):
    d = find(data["deals"], deal_id)
    if d is None:
        raise PipelineError("No deal %s" % deal_id)
    c = find(data["contacts"], d["contact_id"]) or {}
    out = ["%s  [%s]  %s" % (d["title"], d["stage"], money(d.get("value", 0))),
           "%s, %s  %s" % (c.get("name"), c.get("company"), c.get("email") or ""),
           "next: %s   close: %s" % (d.get("next_step") or "-", d.get("close_date") or "-"), ""]
    for a in history(data, deal_id=d["id"]):
        arrow = {"out": "->", "in": "<-"}.get(a.get("direction"), "  ")
        out.append("  %s %s %-8s %s" % (a["date"], arrow, a["channel"], a.get("subject") or a.get("text", "")[:70]))
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description="The Closer pipeline.")
    p.add_argument("--file", default=DEFAULT_FILE)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("init")
    for k in ("name", "company", "email", "address", "timezone", "phone", "offer", "price", "plan_b", "value_line", "proof"):
        s.add_argument("--" + k)
    s = sub.add_parser("contact")
    for k in ("id", "name", "title", "company", "email", "phone", "website", "source", "notes"):
        s.add_argument("--" + k)
    s = sub.add_parser("deal")
    for k in ("id", "contact", "title", "stage", "close", "next", "currency", "revisit"):
        s.add_argument("--" + k)
    s.add_argument("--value", type=float)
    s.add_argument("--today")
    s = sub.add_parser("move")
    s.add_argument("--deal", required=True)
    s.add_argument("--stage", required=True)
    s.add_argument("--reason")
    s.add_argument("--next")
    s.add_argument("--today")
    s = sub.add_parser("board")
    s.add_argument("--today")
    s = sub.add_parser("show")
    s.add_argument("--deal", required=True)
    sub.add_parser("validate")
    a = p.parse_args(argv)
    try:
        if a.cmd == "init":
            if os.path.exists(a.file):
                raise PipelineError("%s already exists; nothing changed." % a.file)
            me = {k: getattr(a, k) for k in ("name", "company", "email", "address", "timezone", "phone", "offer", "price", "plan_b", "value_line", "proof")}
            save(empty(me), a.file)
            print("Created %s" % a.file)
            if not me.get("address"):
                print("Add --address before any cold email: the law needs a postal address in it.")
            return 0
        data = load(a.file)
        if a.cmd == "contact":
            if not a.id:
                a.id = slug("%s %s" % (a.name or "", a.company or ""))
            upsert_contact(data, id=a.id, name=a.name, title=a.title, company=a.company, email=a.email,
                           phone=a.phone, website=a.website, source=a.source, notes=a.notes)
            save(data, a.file)
            print("Saved contact %s" % a.id)
        elif a.cmd == "deal":
            value = int(a.value) if a.value is not None and a.value == int(a.value) else a.value
            upsert_deal(data, today_arg(a.today), id=a.id, contact_id=a.contact, title=a.title, stage=a.stage,
                        value=value, close_date=a.close, next_step=a.next, currency=a.currency,
                        revisit=a.revisit)
            save(data, a.file)
            print("Saved deal %s" % a.id)
        elif a.cmd == "move":
            d = move(data, a.deal, a.stage, today_arg(a.today), a.reason, a.next)
            save(data, a.file)
            print("%s is now %s" % (d["title"], d["stage"]))
        elif a.cmd == "board":
            print(board(data, today_arg(a.today)))
        elif a.cmd == "show":
            print(show(data, a.deal))
        elif a.cmd == "validate":
            errors, warnings = validate(data)
            for e in errors:
                print("ERROR " + e)
            for w in warnings:
                print("warn  " + w)
            print("OK" if not errors else "%d errors" % len(errors))
            return 1 if errors else 0
    except PipelineError as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
