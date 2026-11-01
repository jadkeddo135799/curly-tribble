#!/usr/bin/env python3
"""Keeps following up until a call is booked, and stops the moment anyone says no.
Standard library only. Reads and writes closer/pipeline.json (the schema /pipeline owns).
It drafts; it never sends.

  due        who needs a follow-up today, who replied (your turn), who is booked and who is stopped
  draft      fills the next template for one contact
  record     logs a follow-up AFTER it went out, with your yes word for word
  reply      logs their answer; a "no" stops everything, "unsubscribe" adds them to suppression
  stop       marks a person do-not-contact, forever
  templates  lists the templates

    python3 followup.py due --today 2026-10-21
    python3 followup.py draft --contact dana-harbor-lane --today 2026-10-21
    python3 followup.py record --contact dana-harbor-lane --text-file /tmp/f.txt --approval "yes" --today 2026-10-21
    python3 followup.py reply --contact dana-harbor-lane --text "Sorry, crazy week! Thursday at 2 works." \
        --at 2026-10-27T11:48

The rules, per PERSON:
  1. Stop at the first "no" or any sign of disinterest. An unsubscribe goes on the suppression list forever.
  2. Stop the moment a call is booked: the job is done, /scheduler and /sales-coach take over.
  3. A reply means it is your turn: answering them is a conversation, never a "follow-up".
  4. At most 3 follow-ups after the opening email, and the third says it is the last.
  5. Nothing after 30 days from the opening without a reply.
Cadence (the reel: Mon Oct 19, Wed 21, Fri 23, Tue 27): follow-up 1 two days after the opening,
follow-up 2 two days after that, follow-up 3 four days after that. A date on a weekend moves to Monday.
"""

import argparse
import datetime
import importlib.util
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TEMPLATES = os.path.join(HERE, "templates.json")

MAX_FOLLOWUPS = 3
GAPS = (2, 2, 4)
SILENCE_DAYS = 30
BOOKED_STAGES = ("call-booked", "proposal", "contract-sent", "won")


def _pipeline():
    path = os.path.join(HERE, "..", "pipeline", "deals.py")
    if not os.path.exists(path):
        sys.exit("The follow-up skill needs the pipeline skill next to it (../pipeline/deals.py).")
    spec = importlib.util.spec_from_file_location("closer_deals", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P = _pipeline()
D = datetime.date


class FollowupError(Exception):
    pass


def weekday(day):
    while day.weekday() >= 5:
        day += datetime.timedelta(days=1)
    return day


def day_of(stamp):
    return D.fromisoformat(stamp[:10])


def contact_state(data, contact_id, today):
    """What is true for this person today. state is one of:
    due, waiting, your-turn, booked, stopped, not-started."""
    c = P.find(data["contacts"], contact_id)
    if c is None:
        raise FollowupError("No contact %s" % contact_id)
    acts = P.history(data, contact_id=contact_id)
    deals = [d for d in data["deals"] if d["contact_id"] == contact_id]
    st = {"contact_id": contact_id, "name": c["name"], "company": c.get("company"), "followups_sent": 0,
          "followup": None, "due": None, "reason": ""}
    if c.get("do_not_contact") or P.suppressed(data, c.get("email")) or "DO NOT CONTACT" in (c.get("notes") or ""):
        return dict(st, state="stopped", reason="do not contact")
    for a in acts:
        if a.get("direction") == "in" and P.NO_RE.search(a.get("text", "")):
            return dict(st, state="stopped", reason="they said no on %s" % a["date"][:10])
    if any(d["stage"] == "lost" for d in deals) and not any(d["stage"] in P.OPEN for d in deals):
        return dict(st, state="stopped", reason="deal lost: /win-back decides if and when to try again")
    if any(d["stage"] == "won" for d in deals):
        return dict(st, state="booked", reason="customer: deal won, nothing to chase")
    if any(a["type"] == "meeting" for a in acts) or any(d["stage"] in BOOKED_STAGES for d in deals):
        return dict(st, state="booked", reason="call booked: no more follow-ups")
    openings = [a for a in acts if a["type"] == "opening" and a.get("direction") == "out"]
    if not openings:
        return dict(st, state="not-started", reason="no opening email yet (/prospector sends it)")
    opened = day_of(openings[0]["date"])
    after = [a for a in acts if a["date"] >= openings[0]["date"]]
    last_in = [a for a in after if a.get("direction") == "in"]
    last_out = [a for a in after if a.get("direction") == "out"]
    if last_in and (not last_out or last_in[-1]["date"] > last_out[-1]["date"]):
        return dict(st, state="your-turn", reason="they replied %s: %s" % (last_in[-1]["date"][:10],
                                                                         last_in[-1].get("text", "")[:80]))
    sent = [a for a in after if a["type"] == "followup" and a.get("direction") == "out"]
    st["followups_sent"] = len(sent)
    if len(sent) >= MAX_FOLLOWUPS:
        return dict(st, state="stopped", reason="%d follow-ups sent, no reply: that is the limit" % MAX_FOLLOWUPS)
    base = day_of(sent[-1]["date"]) if sent else opened
    due = weekday(base + datetime.timedelta(days=GAPS[len(sent)]))
    if (due - opened).days > SILENCE_DAYS or (today - opened).days > SILENCE_DAYS:
        return dict(st, state="stopped", reason="%d days since the opening with no reply" % SILENCE_DAYS)
    st.update(followup=len(sent) + 1, due=due.isoformat())
    if today >= due:
        return dict(st, state="due", reason="follow-up %d of %d" % (len(sent) + 1, MAX_FOLLOWUPS))
    return dict(st, state="waiting", reason="follow-up %d of %d is due %s" % (len(sent) + 1, MAX_FOLLOWUPS, due))


def due_report(data, today):
    order = {"due": 0, "your-turn": 1, "waiting": 2, "booked": 3, "not-started": 4, "stopped": 5}
    people = [contact_state(data, c["id"], today) for c in data["contacts"]]
    return sorted(people, key=lambda p: (order[p["state"]], p["name"]))


def load_templates():
    with open(TEMPLATES, encoding="utf-8") as fh:
        return json.load(fh)


def opening_subject(data, contact_id):
    for a in P.history(data, contact_id=contact_id):
        if a["type"] == "opening" and a.get("subject"):
            return a["subject"]
    return None


def draft(data, templates, contact_id, today, template=None, hook=None):
    st = contact_state(data, contact_id, today)
    if st["state"] == "stopped":
        raise FollowupError("No draft: %s is stopped (%s)." % (st["name"], st["reason"]))
    if st["state"] == "booked":
        raise FollowupError("No draft: %s already booked a call." % st["name"])
    if template is None:
        if st["state"] == "your-turn":
            template = "confirm-time"
        elif st["followup"]:
            template = "followup-%d" % st["followup"]
        else:
            raise FollowupError("Nothing to follow up with %s yet (%s)." % (st["name"], st["reason"]))
    if template not in templates:
        raise FollowupError("No template %s. Try: %s" % (template, ", ".join(templates)))
    c = P.find(data["contacts"], contact_id)
    me = data.get("me", {})
    fields = {"first": c["name"].split()[0], "company": c.get("company") or "", "hook": hook,
              "me": (me.get("name") or "").split()[0] if me.get("name") else None,
              "my_company": me.get("company")}
    missing = []

    def fill(text):
        def rep(m):
            v = fields.get(m.group(1))
            if not v:
                missing.append(m.group(1))
                return "[fill: %s]" % m.group(1)
            return v
        return re.sub(r"\{(\w+)\}", rep, text)

    t = templates[template]
    subj = opening_subject(data, contact_id)
    return {"template": template, "to": c.get("email"), "subject": ("Re: " + subj) if subj else fill(t["subject"]),
            "body": fill(t["body"]), "missing": sorted(set(missing)), "followup": st["followup"]}


def record(data, contact_id, text, approval, when, subject=None):
    ok, why = P.parse_yes(approval)
    if not ok:
        return False, "Not recorded: %s." % why
    today = day_of(when)
    st = contact_state(data, contact_id, today)
    if st["state"] not in ("due", "your-turn"):
        return False, "Not recorded: a stop rule applies (%s: %s)." % (st["state"], st["reason"])
    if "[fill:" in text:
        return False, "Not recorded: the message still has a [fill: ...] in it."
    deals = [d for d in data["deals"] if d["contact_id"] == contact_id and d["stage"] in P.OPEN]
    kind = "followup" if st["state"] == "due" else "reply"
    P.add_activity(data, date=when, contact_id=contact_id, deal_id=deals[0]["id"] if deals else None,
                   channel="email", type=kind, direction="out",
                   subject=subject or ("Re: " + (opening_subject(data, contact_id) or "")).strip(),
                   text=text, approval=approval)
    if kind == "reply":
        return True, "Logged your reply to %s." % st["name"]
    nxt = contact_state(data, contact_id, today)
    if nxt["state"] == "stopped":
        return True, "Follow-up %d of %d logged. That was the last one." % (st["followup"], MAX_FOLLOWUPS)
    return True, "Follow-up %d of %d logged. Next is due %s." % (st["followup"], MAX_FOLLOWUPS, nxt["due"])


TIME_RE = re.compile(r"\b(mon|tue|wed|thu|fri|sat|sun)[a-z]*\b.*?\b(at\s+)?\d{1,2}(:\d{2})?\s*(am|pm)?\b"
                     r"|\b\d{1,2}(:\d{2})?\s*(am|pm)\b.*\bworks\b|\bworks\b", re.I)


def log_reply(data, contact_id, text, when):
    c = P.find(data["contacts"], contact_id)
    if c is None:
        raise FollowupError("No contact %s" % contact_id)
    deals = [d for d in data["deals"] if d["contact_id"] == contact_id and d["stage"] in P.OPEN]
    P.add_activity(data, date=when, contact_id=contact_id, deal_id=deals[0]["id"] if deals else None,
                   channel="email", type="reply", direction="in", subject=None, text=text)
    if P.UNSUB_RE.search(text):
        mark_stop(data, contact_id, day_of(when), said=text)
        return "%s asked to be removed. Stopped forever and added to suppression." % c["name"]
    if P.NO_RE.search(text):
        for d in deals:
            P.move(data, d["id"], "lost", day_of(when), reason="said no: " + text[:80])
        return "%s said no. Every follow-up to them is stopped." % c["name"]
    for d in deals:
        if d["stage"] in ("lead", "contacted"):
            d["stage"] = "replied"
            d["next_step"] = "Answer %s" % c["name"].split()[0]
    if TIME_RE.search(text):
        return "%s replied with a time. Your turn: confirm it, then /scheduler books it." % c["name"]
    return "%s replied. Your turn: answer them (template confirm-time, or write it)." % c["name"]


def mark_stop(data, contact_id, today, said=None):
    c = P.find(data["contacts"], contact_id)
    if c is None:
        raise FollowupError("No contact %s" % contact_id)
    c["do_not_contact"] = True
    note = "DO NOT CONTACT since %s%s" % (today.isoformat(), (": " + said[:80]) if said else "")
    c["notes"] = ((c.get("notes") or "") + " " + note).strip()
    if c.get("email") and not P.suppressed(data, c["email"]):
        data["suppression"].append(c["email"].lower())
    for d in data["deals"]:
        if d["contact_id"] == contact_id and d["stage"] in P.OPEN:
            P.move(data, d["id"], "lost", today, reason="unsubscribed")
    return "%s is do not contact, forever." % c["name"]


def main(argv=None):
    p = argparse.ArgumentParser(description="Follow up until a call is booked.")
    p.add_argument("--file", default=P.DEFAULT_FILE)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("due")
    s.add_argument("--today")
    s = sub.add_parser("draft")
    s.add_argument("--contact", required=True)
    s.add_argument("--template")
    s.add_argument("--hook")
    s.add_argument("--today")
    s = sub.add_parser("record")
    s.add_argument("--contact", required=True)
    s.add_argument("--text")
    s.add_argument("--text-file")
    s.add_argument("--subject")
    s.add_argument("--approval", required=True)
    s.add_argument("--at", help="YYYY-MM-DDTHH:MM it went out (default now)")
    s.add_argument("--today")
    s = sub.add_parser("reply")
    s.add_argument("--contact", required=True)
    s.add_argument("--text", required=True)
    s.add_argument("--at")
    s = sub.add_parser("stop")
    s.add_argument("--contact", required=True)
    s.add_argument("--said")
    s.add_argument("--today")
    sub.add_parser("templates")
    a = p.parse_args(argv)
    try:
        if a.cmd == "templates":
            for k, t in load_templates().items():
                print("%-14s %s" % (k, t["use"]))
            return 0
        data = P.load(a.file)
        now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M")
        if a.cmd == "due":
            today = P.today_arg(a.today)
            groups = {}
            for st in due_report(data, today):
                groups.setdefault(st["state"], []).append(st)
            titles = [("due", "DUE NOW"), ("your-turn", "YOUR TURN (they replied)"), ("waiting", "WAITING"),
                      ("booked", "BOOKED"), ("not-started", "NOT STARTED"), ("stopped", "STOPPED")]
            for key, title in titles:
                if key in groups:
                    print(title)
                    for st in groups[key]:
                        print("  %s (%s): %s" % (st["name"], st["company"], st["reason"]))
                    print("")
        elif a.cmd == "draft":
            d = draft(data, load_templates(), a.contact, P.today_arg(a.today), a.template, a.hook)
            print("To: %s\nSubject: %s\n\n%s" % (d["to"], d["subject"], d["body"]))
            if d["missing"]:
                print("\nFill before it can go: " + ", ".join(d["missing"]))
        elif a.cmd == "record":
            text = a.text
            if a.text_file:
                with open(a.text_file, encoding="utf-8") as fh:
                    text = fh.read().strip()
            if not text:
                raise FollowupError("Give --text or --text-file: exactly what went out.")
            when = a.at or (a.today + "T09:00" if a.today else now)
            ok, msg = record(data, a.contact, text, a.approval, when, a.subject)
            if ok:
                P.save(data, a.file)
            print(msg)
            return 0 if ok else 1
        elif a.cmd == "reply":
            msg = log_reply(data, a.contact, a.text, a.at or now)
            P.save(data, a.file)
            print(msg)
        elif a.cmd == "stop":
            msg = mark_stop(data, a.contact, P.today_arg(a.today), a.said)
            P.save(data, a.file)
            print(msg)
    except (FollowupError, P.PipelineError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
