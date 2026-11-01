#!/usr/bin/env python3
"""Puts every booked call on your calendar. Standard library only.

  parse    turns a reply like "Thursday at 2 works" into a date and time
  free     your open slots, from the busy times Claude read off your calendar
  invite   writes a calendar invite (.ics) for a call
  book     logs the booked call AFTER you said yes and it is on the calendar (moves the deal to call-booked)

    python3 slots.py parse --text "Sorry, crazy week! Thursday at 2 works." --today 2026-10-27
    python3 slots.py free --busy busy.json --from 2026-10-28 --days 3 --length 30
    python3 slots.py invite --contact dana-harbor-lane --start 2026-10-29T14:00 --minutes 30
    python3 slots.py book --contact dana-harbor-lane --start 2026-10-29T14:00 --minutes 30 --approval "yes"

Times are in your own time zone (me.timezone in closer/pipeline.json). A busy file is a list of
{"start": "YYYY-MM-DDTHH:MM", "end": "YYYY-MM-DDTHH:MM"}.
"""

import argparse
import datetime
import importlib.util
import json
import os
import re
import sys
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
INVITES = os.path.join("closer", "invites")
DT = datetime.datetime
DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
WORK = (9, 17)


def _pipeline():
    path = os.path.join(HERE, "..", "pipeline", "deals.py")
    if not os.path.exists(path):
        sys.exit("The scheduler skill needs the pipeline skill next to it (../pipeline/deals.py).")
    spec = importlib.util.spec_from_file_location("closer_deals", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P = _pipeline()


class ScheduleError(Exception):
    pass


TIME_RE = re.compile(r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?(?=\W|$)", re.I)
NOON_RE = re.compile(r"\b(noon|midday)\b", re.I)
DAY_RE = re.compile(r"\b(next\s+)?(mon|tues?|wed(?:nes)?|thu(?:rs?)?|fri|sat(?:ur)?|sun)(?:day)?\b", re.I)
DATE_RE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})\b", re.I)


def parse_when(text, today):
    """The first date and time in a reply. Returns a datetime, or None when there is no clear time.
    "at 2" with no am/pm means business hours: 1 to 7 is pm, 8 to 12 is am (12 is noon)."""
    t = text.lower()
    day = None
    m = DATE_RE.search(t)
    if m:
        month = MONTHS.index(m.group(1)[:3]) + 1
        year = today.year if (month, int(m.group(2))) >= (today.month, today.day) else today.year + 1
        day = datetime.date(year, month, int(m.group(2)))
        t = t[:m.start()] + " " + t[m.end():]
    elif re.search(r"\btomorrow\b", t):
        day = today + datetime.timedelta(days=1)
    elif re.search(r"\btoday\b|\bthis afternoon\b|\bthis morning\b", t):
        day = today
    else:
        m = DAY_RE.search(t)
        if m:
            want = DAYS.index(m.group(2)[:3])
            # "next Monday" is read as the coming Monday; people mean both, so the confirm-time reply says the date
            ahead = (want - today.weekday()) % 7 or 7
            day = today + datetime.timedelta(days=ahead)
    if day is None:
        return None
    if NOON_RE.search(t):
        return DT(day.year, day.month, day.day, 12, 0)
    for m in TIME_RE.finditer(t):
        hour, minute, ampm = int(m.group(1)), int(m.group(2) or 0), (m.group(3) or "").replace(".", "")
        if hour > 12 or minute > 59 or (not ampm and not m.group(0).strip().startswith("at") and not m.group(2)):
            continue
        if ampm == "pm" and hour < 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        elif not ampm and 1 <= hour <= 7:
            hour += 12
        return DT(day.year, day.month, day.day, hour, minute)
    return None


def load_busy(path):
    with open(path, encoding="utf-8") as fh:
        return [(DT.fromisoformat(b["start"]), DT.fromisoformat(b["end"])) for b in json.load(fh)]


def is_free(start, minutes, busy):
    end = start + datetime.timedelta(minutes=minutes)
    return all(end <= b0 or start >= b1 for b0, b1 in busy)


def free_slots(busy, start_day, days, minutes=30, hours=WORK, step=30, limit=8):
    out = []
    day = start_day
    while len(out) < limit and (day - start_day).days < days + 14:
        if day.weekday() < 5 and (day - start_day).days < days:
            t = DT(day.year, day.month, day.day, hours[0], 0)
            stop = DT(day.year, day.month, day.day, hours[1], 0)
            while t + datetime.timedelta(minutes=minutes) <= stop and len(out) < limit:
                if is_free(t, minutes, busy):
                    out.append(t)
                t += datetime.timedelta(minutes=step)
        day += datetime.timedelta(days=1)
    return out


def ics(me, contact, start, minutes, title, where="Google Meet"):
    end = start + datetime.timedelta(minutes=minutes)
    stamp = DT.utcnow().strftime("%Y%m%dT%H%M%SZ")
    tz = me.get("timezone") or "America/Chicago"
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Closer skill//EN", "METHOD:REQUEST", "BEGIN:VEVENT",
             "UID:%s@closer-skill" % uuid.uuid4(), "DTSTAMP:%s" % stamp,
             "DTSTART;TZID=%s:%s" % (tz, start.strftime("%Y%m%dT%H%M%S")),
             "DTEND;TZID=%s:%s" % (tz, end.strftime("%Y%m%dT%H%M%S")),
             "SUMMARY:%s" % title, "LOCATION:%s" % where,
             "ORGANIZER;CN=%s:mailto:%s" % (me.get("name") or "", me.get("email") or "")]
    if contact.get("email"):
        lines.append("ATTENDEE;CN=%s;RSVP=TRUE:mailto:%s" % (contact["name"], contact["email"]))
    lines += ["END:VEVENT", "END:VCALENDAR"]
    return "\r\n".join(lines) + "\r\n"


def title_for(data, contact):
    return "%s x %s" % (contact.get("company") or contact["name"], data.get("me", {}).get("company") or "me")


def book(data, contact_id, start, minutes, approval, busy=None, at=None):
    ok, why = P.parse_yes(approval)
    if not ok:
        return False, "Not booked: %s." % why
    c = P.find(data["contacts"], contact_id)
    if c is None:
        return False, "No contact %s" % contact_id
    if c.get("do_not_contact"):
        return False, "Not booked: %s is do not contact." % c["name"]
    if busy is not None and not is_free(start, minutes, busy):
        return False, "Not booked: you are busy then. Run free for open slots."
    deal = next((d for d in data["deals"] if d["contact_id"] == contact_id and d["stage"] in P.OPEN), None)
    end = start + datetime.timedelta(minutes=minutes)
    P.add_activity(data, date=at or DT.now().strftime("%Y-%m-%dT%H:%M"), contact_id=contact_id,
                   deal_id=deal["id"] if deal else None, channel="meeting", type="meeting", direction="out",
                   subject=title_for(data, c), approval=approval, when=start.strftime("%Y-%m-%dT%H:%M"),
                   text="Booked %s, %s-%s" % (start.strftime("%a %b %d"), start.strftime("%H:%M"), end.strftime("%H:%M")))
    if deal and P.STAGES.index(deal["stage"]) < P.STAGES.index("call-booked"):
        deal["stage"] = "call-booked"
    if deal:
        deal["next_step"] = "Call %s %s" % (start.strftime("%a %b %d,"), start.strftime("%H:%M"))
    return True, "Booked %s for %s. /researcher can prep the brief now." % (c["name"], start.strftime("%a %b %d %H:%M"))


def main(argv=None):
    p = argparse.ArgumentParser(description="Book calls on your calendar.")
    p.add_argument("--file", default=P.DEFAULT_FILE)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("parse")
    s.add_argument("--text", required=True)
    s.add_argument("--today")
    s = sub.add_parser("free")
    s.add_argument("--busy", required=True)
    s.add_argument("--from", dest="start", required=True)
    s.add_argument("--days", type=int, default=5)
    s.add_argument("--length", type=int, default=30)
    s = sub.add_parser("invite")
    s.add_argument("--contact", required=True)
    s.add_argument("--start", required=True)
    s.add_argument("--minutes", type=int, default=30)
    s = sub.add_parser("book")
    s.add_argument("--contact", required=True)
    s.add_argument("--start", required=True)
    s.add_argument("--minutes", type=int, default=30)
    s.add_argument("--approval", required=True)
    s.add_argument("--busy")
    s.add_argument("--at", help="YYYY-MM-DDTHH:MM it was booked (default now)")
    a = p.parse_args(argv)
    try:
        if a.cmd == "parse":
            when = parse_when(a.text, P.today_arg(a.today))
            print(when.strftime("%Y-%m-%dT%H:%M (%A %B %d, %I:%M %p)") if when else "No clear time in that. Ask them.")
            return 0 if when else 1
        if a.cmd == "free":
            for t in free_slots(load_busy(a.busy), datetime.date.fromisoformat(a.start), a.days, a.length):
                print(t.strftime("%a %b %d  %I:%M %p"))
            return 0
        data = P.load(a.file)
        c = P.find(data["contacts"], a.contact)
        if c is None:
            raise ScheduleError("No contact %s" % a.contact)
        start = DT.fromisoformat(a.start)
        if a.cmd == "invite":
            os.makedirs(INVITES, exist_ok=True)
            path = os.path.join(INVITES, "%s-%s.ics" % (start.strftime("%Y-%m-%d"), c["id"]))
            with open(path, "w", encoding="utf-8", newline="") as fh:
                fh.write(ics(data.get("me", {}), c, start, a.minutes, title_for(data, c)))
            print("Wrote %s" % path)
        elif a.cmd == "book":
            ok, msg = book(data, a.contact, start, a.minutes, a.approval, load_busy(a.busy) if a.busy else None, a.at)
            if ok:
                P.save(data, a.file)
            print(msg)
            return 0 if ok else 1
    except (ScheduleError, P.PipelineError, OSError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
