#!/usr/bin/env python3
"""Logs every email and call for you. Standard library only.
Writes activities into closer/pipeline.json (the schema /pipeline owns) and call notes into closer/calls/.

  sync       imports a batch of emails Claude read from your inbox (Gmail connector) as JSON,
             matches each one to a contact by address, skips ones already logged
  log        logs one email, call, meeting or note by hand
  call       logs a call and saves its notes as closer/calls/<date>-<company>.md
  timeline   everything with one contact, oldest first
  unknown    addresses in a sync file that are not contacts yet (possible new leads)

    python3 log.py sync --emails closer/inbox-2026-10-27.json
    python3 log.py log --contact dana-harbor-lane --channel email --direction in \
        --subject "Re: Quick idea" --text "Sorry, crazy week! Thursday at 2 works." --at 2026-10-27T11:48
    python3 log.py call --contact dana-harbor-lane --minutes 24 --notes-file /tmp/notes.md --at 2026-10-29T14:00
    python3 log.py timeline --contact dana-harbor-lane

A sync file is a list of {"date": "YYYY-MM-DDTHH:MM", "from": "...", "to": ["..."], "subject": "...",
"text": "..."}; "from" or "to" may be "Name <addr>". Email you sent yourself from your own inbox is
logged with approval "sent by you from your inbox".
"""

import argparse
import datetime
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
CALLS_DIR = os.path.join("closer", "calls")
ADDR_RE = re.compile(r"[\w.+'-]+@[\w-]+(\.[\w-]+)+")
SENT_BY_YOU = "sent by you from your inbox"


def _pipeline():
    path = os.path.join(HERE, "..", "pipeline", "deals.py")
    if not os.path.exists(path):
        sys.exit("The record-keeper skill needs the pipeline skill next to it (../pipeline/deals.py).")
    spec = importlib.util.spec_from_file_location("closer_deals", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P = _pipeline()


class LogError(Exception):
    pass


def addr(text):
    m = ADDR_RE.search(text or "")
    return m.group(0).lower() if m else None


def by_email(data):
    return {c["email"].lower(): c for c in data["contacts"] if c.get("email")}


def open_deal(data, contact_id):
    deals = [d for d in data["deals"] if d["contact_id"] == contact_id]
    open_ = [d for d in deals if d["stage"] in P.OPEN]
    return (open_ or deals or [None])[0]


def already(data, contact_id, date, subject, direction):
    for a in data["activities"]:
        if a["contact_id"] == contact_id and a["date"][:16] == date[:16] and a.get("direction") == direction:
            # same person, same minute, same way: the same email, unless both have subjects that differ
            if not subject or not a.get("subject") or a["subject"] == subject:
                return True
    return False


def classify(data, contact_id, direction):
    """opening / followup / reply for an email, from what is already logged."""
    if direction == "in":
        return "reply"
    acts = P.history(data, contact_id=contact_id)
    if not any(a["type"] == "opening" for a in acts):
        return "opening"
    if any(a.get("direction") == "in" for a in acts):
        return "reply"
    return "followup"


def sync(data, emails):
    me = (data.get("me", {}).get("email") or "").lower()
    contacts = by_email(data)
    logged, skipped, unknown = [], [], set()
    for e in sorted(emails, key=lambda e: e["date"]):
        sender = addr(e.get("from"))
        to = [addr(t) for t in (e.get("to") or [])]
        if sender == me or (sender not in contacts and any(t in contacts for t in to)):
            direction, other = "out", next((t for t in to if t in contacts), None)
        else:
            direction, other = "in", sender
        c = contacts.get(other)
        if c is None:
            unknown.add(other)
            continue
        if already(data, c["id"], e["date"], e.get("subject"), direction):
            skipped.append(e)
            continue
        deal = open_deal(data, c["id"])
        fields = dict(date=e["date"][:16], contact_id=c["id"], deal_id=deal["id"] if deal else None, channel="email",
                      type=classify(data, c["id"], direction), direction=direction, subject=e.get("subject"),
                      text=(e.get("text") or "").strip())
        if direction == "out":
            fields["approval"] = e.get("approval") or SENT_BY_YOU
        logged.append(P.add_activity(data, **fields))
    return logged, skipped, sorted(u for u in unknown if u)


def save_call_notes(data, contact, when, minutes, notes):
    os.makedirs(CALLS_DIR, exist_ok=True)
    name = "%s-%s.md" % (when[:10], P.slug(contact.get("company") or contact["name"]))
    path = os.path.join(CALLS_DIR, name)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("# Call: %s, %s\n\n" % (contact["name"], contact.get("company") or ""))
        fh.write("- When: %s\n- Length: %s min\n\n" % (when.replace("T", " "), minutes))
        fh.write(notes.strip() + "\n")
    return path


def summary(notes, limit=200):
    lines = [l.strip("-* ").strip() for l in notes.splitlines() if l.strip() and not l.startswith("#")]
    text = " ".join(lines)
    return text if len(text) <= limit else text[:limit - 3].rsplit(" ", 1)[0] + "..."


def timeline(data, contact_id):
    c = P.find(data["contacts"], contact_id)
    if c is None:
        raise LogError("No contact %s" % contact_id)
    out = ["%s, %s" % (c["name"], c.get("company") or "")]
    for a in P.history(data, contact_id=contact_id):
        arrow = {"out": "->", "in": "<-"}.get(a.get("direction"), "  ")
        what = a.get("subject") or ""
        text = a.get("text") or ""
        out.append("  %-16s %s %-8s %-9s %s" % (a["date"], arrow, a["channel"], a["type"],
                                                (what + ": " if what else "") + text[:80]))
    return "\n".join(out)


def main(argv=None):
    p = argparse.ArgumentParser(description="Log every email and call.")
    p.add_argument("--file", default=P.DEFAULT_FILE)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sync")
    s.add_argument("--emails", required=True)
    s = sub.add_parser("log")
    s.add_argument("--contact", required=True)
    s.add_argument("--channel", required=True, choices=P.CHANNELS)
    s.add_argument("--direction", choices=("in", "out"))
    s.add_argument("--type", choices=P.TYPES)
    s.add_argument("--subject")
    s.add_argument("--text")
    s.add_argument("--text-file")
    s.add_argument("--approval", help="the yes that sent it, word for word, or 'sent by you from your inbox'")
    s.add_argument("--deal")
    s.add_argument("--at", required=True)
    s = sub.add_parser("call")
    s.add_argument("--contact", required=True)
    s.add_argument("--minutes", type=int, required=True)
    s.add_argument("--notes-file", required=True)
    s.add_argument("--at", required=True)
    s = sub.add_parser("timeline")
    s.add_argument("--contact", required=True)
    s = sub.add_parser("unknown")
    s.add_argument("--emails", required=True)
    a = p.parse_args(argv)
    try:
        data = P.load(a.file)
        if a.cmd in ("sync", "unknown"):
            with open(a.emails, encoding="utf-8") as fh:
                emails = json.load(fh)
            logged, skipped, unknown = sync(data, emails)
            if a.cmd == "unknown":
                print("\n".join(unknown) or "Every address is a contact already.")
                return 0
            P.save(data, a.file)
            print("Logged %d emails, %d already logged." % (len(logged), len(skipped)))
            for act in logged:
                print("  %s %s %s" % (act["date"], act["direction"], act.get("subject")))
            if unknown:
                print("Not contacts yet (ignored): " + ", ".join(unknown))
        elif a.cmd == "log":
            c = P.find(data["contacts"], a.contact)
            if c is None:
                raise LogError("No contact %s" % a.contact)
            text = a.text
            if a.text_file:
                with open(a.text_file, encoding="utf-8") as fh:
                    text = fh.read().strip()
            kind = a.type or ({"call": "call", "meeting": "meeting", "note": "note"}.get(a.channel)
                              or classify(data, c["id"], a.direction))
            deal = P.find(data["deals"], a.deal) if a.deal else open_deal(data, c["id"])
            fields = dict(date=a.at, contact_id=c["id"], deal_id=deal["id"] if deal else None, channel=a.channel,
                          type=kind, direction=a.direction, subject=a.subject, text=text or "")
            if a.approval:
                fields["approval"] = a.approval
            act = P.add_activity(data, **fields)
            P.save(data, a.file)
            print("Logged %s %s for %s" % (act["channel"], act["type"], c["name"]))
        elif a.cmd == "call":
            c = P.find(data["contacts"], a.contact)
            if c is None:
                raise LogError("No contact %s" % a.contact)
            with open(a.notes_file, encoding="utf-8") as fh:
                notes = fh.read()
            path = save_call_notes(data, c, a.at, a.minutes, notes)
            deal = open_deal(data, c["id"])
            P.add_activity(data, date=a.at, contact_id=c["id"], deal_id=deal["id"] if deal else None,
                           channel="call", type="call", direction=None, subject=None,
                           text="Call, %d min. %s" % (a.minutes, summary(notes)), minutes=a.minutes, notes=path)
            P.save(data, a.file)
            print("Logged the call and saved notes to %s" % path)
        elif a.cmd == "timeline":
            print(timeline(data, a.contact))
    except (LogError, P.PipelineError, OSError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
