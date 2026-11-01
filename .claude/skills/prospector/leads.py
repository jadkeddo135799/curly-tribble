#!/usr/bin/env python3
"""Finds new leads and writes the cold outreach. Standard library only.

  audit      checks one business's public website for the gaps you sell a fix for
             (no online booking, not mobile-friendly, slow, no ads tag, no reviews, no online ordering)
  rank       audits every row of a leads CSV, slowly, and ranks them -> closer/leads.json
  add        puts the top leads on the board as contacts + deals (stage lead), skipping anyone suppressed
  draft      writes the opening email for one lead from the gap it found, with the legal footer
  record     logs the opening AFTER it went out, with your yes word for word (moves the deal to contacted)

    python3 leads.py audit https://harborlanedental.com
    python3 leads.py rank --csv leads.csv --offer "booking-ready websites"
    python3 leads.py add --top 10 --value 2500 --today 2026-10-19
    python3 leads.py draft --contact dana-harbor-lane
    python3 leads.py record --contact dana-harbor-lane --text-file /tmp/opening.txt --approval "yes" --at 2026-10-19T09:02

A leads CSV has the columns: name, who, type, email, website (and optionally city, phone, notes).
Find rows from public listings (a Google Maps search, a chamber of commerce list, a directory),
never from behind a login. The audit reads one public page per site, identifies itself, and waits
between sites. A tag on a page is a hint, not proof: say "looks like", never "you are not".
"""

import argparse
import csv
import datetime
import html as htmllib
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OPENERS = os.path.join(HERE, "openers.json")
LEADS_FILE = os.path.join("closer", "leads.json")
UA = "CloserSkill/1.0 (+https://github.com/Jakeschincariol/closer-skill; one public page per site)"
DAILY_CAP = 50
SLOW_SECONDS = 4.0

BOOKING = re.compile(r"calendly\.com|acuityscheduling|zocdoc|mindbodyonline|booksy|vagaro|squareup\.com/appointments"
                     r"|square\.site/book|setmore|simplybook|schedulicity|janeapp|nexhealth|localmed|opencare"
                     r"|book (an |your )?(appointment|online|now|a class)|schedule (an |your )?(appointment|visit)"
                     r"|request (an )?appointment|booking", re.I)
ORDERING = re.compile(r"toasttab|order\.online|square\.site|chownow|olo\.com|doordash|ubereats|grubhub"
                      r"|order (online|now|ahead|pickup)", re.I)
ADS = re.compile(r"googleadservices|AW-\d{6,}|gtag\(\s*['\"]config['\"]\s*,\s*['\"]AW-|googleads\.g\.doubleclick", re.I)
REVIEWS = re.compile(r"aggregateRating|reviewCount|testimonial|reviews|elfsight|birdeye|podium|trustindex", re.I)
VIEWPORT = re.compile(r"<meta[^>]+name=[\"']viewport[\"'][^>]*>", re.I)
TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

# reason key -> (score, the plain-English "why")
GAPS = {
    "no-booking": (3, "No online booking"),
    "not-mobile": (3, "Site not mobile-friendly"),
    "slow": (2, "Site loads in %.0f s"),
    "no-ads": (2, "No Google Ads tag on the site"),
    "no-ordering": (2, "No online ordering"),
    "no-reviews": (1, "No reviews on site"),
    "no-site": (4, "No website found"),
}
NEEDS_BOOKING = re.compile(r"dent|chiro|spa|salon|yoga|gym|fitness|clinic|physio|therapy|vet|groom|barber|studio|med", re.I)
NEEDS_ORDERING = re.compile(r"bakery|cafe|coffee|restaurant|pizza|deli|kitchen|bar\b|food|catering", re.I)


def _pipeline():
    path = os.path.join(HERE, "..", "pipeline", "deals.py")
    if not os.path.exists(path):
        sys.exit("The prospector skill needs the pipeline skill next to it (../pipeline/deals.py).")
    spec = importlib.util.spec_from_file_location("closer_deals", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P = _pipeline()


class ProspectError(Exception):
    pass


def fetch(url, timeout=15):
    if not re.match(r"^https?://", url):
        url = "https://" + url
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    start = time.time()
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read(2_000_000).decode(resp.headers.get_content_charset() or "utf-8", "replace")
    return body, time.time() - start


def analyze(page, seconds, business_type=""):
    """The gaps on one page. page=None means no site could be loaded."""
    if page is None:
        k = "no-site"
        return [{"key": k, "score": GAPS[k][0], "why": GAPS[k][1]}], None
    found = []

    def gap(key, *fmt):
        score, why = GAPS[key]
        found.append({"key": key, "score": score, "why": why % fmt if fmt else why})

    if NEEDS_BOOKING.search(business_type or "") and not BOOKING.search(page):
        gap("no-booking")
    if NEEDS_ORDERING.search(business_type or "") and not ORDERING.search(page):
        gap("no-ordering")
    if not VIEWPORT.search(page):
        gap("not-mobile")
    if seconds is not None and seconds >= SLOW_SECONDS:
        gap("slow", seconds)
    if not ADS.search(page):
        gap("no-ads")
    if not REVIEWS.search(page):
        gap("no-reviews")
    m = TITLE.search(page)
    title = htmllib.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else None
    return sorted(found, key=lambda g: -g["score"]), title


def audit(url, business_type=""):
    try:
        page, secs = fetch(url)
    except (urllib.error.URLError, OSError, ValueError) as e:
        gaps, _ = analyze(None, None)
        return {"url": url, "ok": False, "error": str(e)[:120], "gaps": gaps, "title": None, "seconds": None}
    gaps, title = analyze(page, secs, business_type)
    return {"url": url, "ok": True, "gaps": gaps, "title": title, "seconds": round(secs, 1)}


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for need in ("name", "website"):
            if not r.get(need):
                raise ProspectError("Every row needs %s (row: %s)" % (need, r))
    return rows


def rank(rows, audits):
    out = []
    for r, a in zip(rows, audits):
        score = sum(g["score"] for g in a["gaps"])
        out.append(dict(r, score=score, why=a["gaps"][0]["why"] if a["gaps"] else None,
                        gaps=[g["key"] for g in a["gaps"]], audit=a))
    return sorted(out, key=lambda r: (-r["score"], r["name"]))


def add_leads(data, ranked, top, value, today):
    added, skipped = [], []
    for r in ranked[:top]:
        email = (r.get("email") or "").strip().lower()
        if email and P.suppressed(data, email):
            skipped.append((r["name"], "on the suppression list"))
            continue
        cid = P.slug("%s %s" % ((r.get("who") or "").split(" ")[0], r["name"]))
        if P.find(data["contacts"], cid) or any(c.get("email") == email for c in data["contacts"] if email):
            skipped.append((r["name"], "already on the board"))
            continue
        P.upsert_contact(data, id=cid, name=r.get("who") or r["name"], title=r.get("title") or None,
                         company=r["name"], email=email or None, website=r["website"], source="prospector",
                         notes="Why: %s" % (r.get("why") or "-"))
        P.upsert_deal(data, today, id=P.slug(r["name"]), contact_id=cid, title=r.get("offer") or "New business",
                      stage="lead", value=value, next_step="Opening email: %s" % (r.get("why") or "intro"),
                      gaps=r.get("gaps", []))
        added.append(cid)
    return added, skipped


def load_openers():
    with open(OPENERS, encoding="utf-8") as fh:
        return json.load(fh)


def footer(me):
    if not me.get("address"):
        raise ProspectError("No postal address in closer/pipeline.json (me.address). US law (CAN-SPAM) "
                            "needs one in every commercial email. Add it: deals.py init or edit me.address.")
    return "\n\n--\n%s, %s\n%s\nNot interested? Reply \"no thanks\" and I won't email again." % (
        me.get("name") or "", me.get("company") or "", me["address"])


def short_name(company):
    """What people call the business in a sentence: "Harbor Lane Dental" -> "Harbor Lane".
    Keeps the full name when trimming would leave a single generic word."""
    trimmed = re.sub(r"\s+(dental|fitness|co\.?|company|llc|inc\.?|ltd\.?|studio|spa|loft|bakery|group)\s*$", "",
                     company, flags=re.I).strip()
    return trimmed if len(trimmed.split()) >= 2 else company


def draft(data, openers, contact_id, gap=None, offer=None):
    c = P.find(data["contacts"], contact_id)
    if c is None:
        raise ProspectError("No contact %s" % contact_id)
    if c.get("do_not_contact") or P.suppressed(data, c.get("email")):
        raise ProspectError("%s is do not contact. No email." % c["name"])
    if not c.get("email"):
        raise ProspectError("%s has no email address. Find one published on their site, or call." % c["name"])
    if any(a["type"] == "opening" for a in P.history(data, contact_id=contact_id)):
        raise ProspectError("%s already got an opening email. /follow-up takes it from here." % c["name"])
    deal = next((d for d in data["deals"] if d["contact_id"] == contact_id), {})
    key = gap or next((g for g in deal.get("gaps", []) if g in openers), "generic")
    t = openers.get(key, openers["generic"])
    me = data.get("me", {})
    fields = {"first": c["name"].split()[0], "company": c.get("company") or c["name"],
              "short": short_name(c.get("company") or c["name"]),
              "offer": offer or me.get("offer") or "[fill: offer]", "me": (me.get("name") or "").split(" ")[0]}
    body = t["body"].format(**fields) + footer(me)
    return {"to": c["email"], "subject": t["subject"].format(**fields), "body": body, "gap": key,
            "missing": re.findall(r"\[fill: (\w+)\]", body)}


def record(data, contact_id, text, approval, when, subject=None):
    ok, why = P.parse_yes(approval)
    if not ok:
        return False, "Not recorded: %s." % why
    c = P.find(data["contacts"], contact_id)
    if c is None:
        return False, "No contact %s" % contact_id
    if c.get("do_not_contact") or P.suppressed(data, c.get("email")):
        return False, "Not recorded: %s is do not contact." % c["name"]
    if "[fill:" in text:
        return False, "Not recorded: the message still has a [fill: ...] in it."
    day = when[:10]
    today_count = len([a for a in data["activities"] if a["type"] == "opening" and a["date"][:10] == day])
    if today_count >= DAILY_CAP:
        return False, "Not recorded: %d opening emails today is the cap. Send the rest tomorrow." % DAILY_CAP
    deal = next((d for d in data["deals"] if d["contact_id"] == contact_id and d["stage"] in P.OPEN), None)
    P.add_activity(data, date=when, contact_id=contact_id, deal_id=deal["id"] if deal else None, channel="email",
                   type="opening", direction="out", subject=subject, text=text, approval=approval)
    if deal and deal["stage"] == "lead":
        deal["stage"] = "contacted"
        deal["next_step"] = "Follow-up 1 (/follow-up)"
    return True, "Opening to %s logged (%d of %d today). /follow-up has it now." % (c["name"], today_count + 1, DAILY_CAP)


def main(argv=None):
    p = argparse.ArgumentParser(description="Find leads and write the cold outreach.")
    p.add_argument("--file", default=P.DEFAULT_FILE)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("audit")
    s.add_argument("url")
    s.add_argument("--type", default="")
    s = sub.add_parser("rank")
    s.add_argument("--csv", required=True)
    s.add_argument("--offer")
    s.add_argument("--out", default=LEADS_FILE)
    s.add_argument("--wait", type=float, default=2.0)
    s = sub.add_parser("add")
    s.add_argument("--leads", default=LEADS_FILE)
    s.add_argument("--top", type=int, default=10)
    s.add_argument("--value", type=float, default=0)
    s.add_argument("--today")
    s = sub.add_parser("draft")
    s.add_argument("--contact", required=True)
    s.add_argument("--gap")
    s.add_argument("--offer")
    s = sub.add_parser("record")
    s.add_argument("--contact", required=True)
    s.add_argument("--text")
    s.add_argument("--text-file")
    s.add_argument("--subject")
    s.add_argument("--approval", required=True)
    s.add_argument("--at", required=True)
    a = p.parse_args(argv)
    try:
        if a.cmd == "audit":
            r = audit(a.url, a.type)
            print("%s  %s" % (r["url"], "(%s s)" % r["seconds"] if r["ok"] else "could not load: " + r["error"]))
            for g in r["gaps"]:
                print("  gap: %s" % g["why"])
            if not r["gaps"]:
                print("  no gaps found")
            return 0
        if a.cmd == "rank":
            rows = read_csv(a.csv)
            audits = []
            for i, r in enumerate(rows):
                if i:
                    time.sleep(a.wait)
                audits.append(audit(r["website"], r.get("type", "")))
                print("  checked %s" % r["name"], file=sys.stderr)
            ranked = rank(rows, audits)
            for r in ranked:
                r["offer"] = a.offer or r.get("offer")
            os.makedirs(os.path.dirname(a.out) or ".", exist_ok=True)
            with open(a.out, "w", encoding="utf-8") as fh:
                json.dump(ranked, fh, indent=2)
            for r in ranked:
                print("%3d  %-30s %-20s %s" % (r["score"], r["name"][:30], (r.get("who") or "")[:20], r["why"] or "-"))
            print("Saved %d leads to %s" % (len(ranked), a.out))
            return 0
        data = P.load(a.file)
        if a.cmd == "add":
            with open(a.leads, encoding="utf-8") as fh:
                ranked = json.load(fh)
            value = int(a.value) if a.value == int(a.value) else a.value
            added, skipped = add_leads(data, ranked, a.top, value, P.today_arg(a.today))
            P.save(data, a.file)
            print("Added %d leads: %s" % (len(added), ", ".join(added)))
            for name, why in skipped:
                print("  skipped %s: %s" % (name, why))
        elif a.cmd == "draft":
            d = draft(data, load_openers(), a.contact, a.gap, a.offer)
            print("To: %s\nSubject: %s\n\n%s" % (d["to"], d["subject"], d["body"]))
            if d["missing"]:
                print("\nFill before it can go: " + ", ".join(d["missing"]))
        elif a.cmd == "record":
            text = a.text
            if a.text_file:
                with open(a.text_file, encoding="utf-8") as fh:
                    text = fh.read().strip()
            if not text:
                raise ProspectError("Give --text or --text-file: exactly what went out.")
            ok, msg = record(data, a.contact, text, a.approval, a.at, a.subject)
            if ok:
                P.save(data, a.file)
            print(msg)
            return 0 if ok else 1
    except (ProspectError, P.PipelineError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
