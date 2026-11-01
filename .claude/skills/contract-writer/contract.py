#!/usr/bin/env python3
"""Writes the contract and sends it over (after your yes). Standard library only.

  draft    writes a plain-English services agreement for a deal from what was agreed:
           closer/contracts/<company>-agreement.md and a print-ready .html beside it
  pdf      prints the .html to PDF with headless Chrome or Chromium, if one is installed
  record   logs the agreement AFTER it went out, with your yes word for word (deal -> contract-sent)
  signed   marks the deal won when the signed copy comes back

    python3 contract.py draft --deal harbor-lane-website --services "A booking-ready website and a managed \
        Google Ads campaign for Harbor Lane Dental." --fee "$399 per month, no setup fee, billed monthly." \
        --term "Month to month. Cancel anytime with 30 days notice." --launch "Website live within 14 days of signing."
    python3 contract.py pdf --deal harbor-lane-website
    python3 contract.py record --deal harbor-lane-website --approval "yes" --at 2026-10-29T14:31
    python3 contract.py signed --deal harbor-lane-website --today 2026-11-02

This is a template, not legal advice. Have a lawyer look at your standard terms once; after that
the skill fills them in the same way every time. It never signs for anyone, and it checks the fee
against what was said on the call.
"""

import argparse
import datetime
import html as htmllib
import importlib.util
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join("closer", "contracts")
MONEY_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d{2})?")

STANDARD = [
    ("Payment", "Invoices are due within 7 days. Work pauses if an invoice is more than 14 days late."),
    ("Ownership", "Once paid, the client owns the website content and design made for them. "
                  "Ad accounts are set up in the client's name."),
    ("Confidentiality", "Each side keeps the other's private business information private."),
    ("Liability", "Neither side is liable for indirect losses. Total liability is capped at the fees paid "
                  "in the 3 months before the claim."),
    ("Changes", "Changes to this agreement are made in writing and agreed by both sides."),
]


def _pipeline():
    path = os.path.join(HERE, "..", "pipeline", "deals.py")
    if not os.path.exists(path):
        sys.exit("The contract-writer skill needs the pipeline skill next to it (../pipeline/deals.py).")
    spec = importlib.util.spec_from_file_location("closer_deals", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P = _pipeline()


class ContractError(Exception):
    pass


def amounts(text):
    return {re.sub(r"[\s,]", "", m) for m in MONEY_RE.findall(text or "")}


def check_fee(data, deal, fee):
    """Warnings when the fee is not what was said on the call or in the proposal."""
    said = set()
    for a in P.history(data, deal_id=deal["id"]):
        if a["type"] in ("call", "proposal", "reply", "note"):
            said |= amounts(a.get("text"))
    fee_amounts = amounts(fee)
    if not fee_amounts:
        return ["The fee has no dollar amount in it."]
    if said and not fee_amounts & said:
        return ["The fee (%s) is not an amount said on the call or in the proposal (%s). Check it." % (
            ", ".join(sorted(fee_amounts)), ", ".join(sorted(said)))]
    return []


def build(data, deal, date, sections):
    me = data.get("me", {})
    c = P.find(data["contacts"], deal["contact_id"])
    for h, t in sections:
        if not t or "[fill" in t:
            raise ContractError("The %s section is empty. Say what was agreed." % h)
    if not me.get("company") or not c.get("company"):
        raise ContractError("Both parties need a company name (me.company and the contact's company).")
    parties = (me["company"], c["company"])
    md = ["# Services Agreement", "", "**%s** and **%s**" % parties, "", date, ""]
    n = 0
    for h, t in list(sections) + STANDARD:
        n += 1
        md += ["## %d. %s" % (n, h), "", t, ""]
    md += ["## Signatures", "",
           "%s: ______________________  Date: ________" % me["company"], "",
           "%s, %s: ______________________  Date: ________" % (c["name"], c["company"]), ""]
    return "\n".join(md)


def to_html(md):
    body = []
    for line in md.splitlines():
        esc = htmllib.escape(line)
        esc = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", esc)
        if line.startswith("# "):
            body.append("<h1>%s</h1>" % esc[2:])
        elif line.startswith("## "):
            body.append("<h2>%s</h2>" % esc[3:])
        elif line.strip():
            body.append("<p>%s</p>" % esc)
    css = ("@page{size:Letter;margin:22mm}body{font:11.5pt/1.55 Georgia,serif;color:#1c1c1c;max-width:680px;"
           "margin:40px auto}h1{font:600 22pt Helvetica,Arial,sans-serif;margin:0 0 6px}h2{font:600 12pt "
           "Helvetica,Arial,sans-serif;margin:22px 0 4px}p{margin:0 0 8px}")
    return ("<!doctype html><html><head><meta charset=\"utf-8\"><title>Services Agreement</title><style>%s</style>"
            "</head><body>%s</body></html>\n") % (css, "\n".join(body))


def paths_for(data, deal):
    c = P.find(data["contacts"], deal["contact_id"])
    base = os.path.join(OUT_DIR, P.slug(c.get("company") or c["name"]) + "-agreement")
    return base + ".md", base + ".html", base + ".pdf"


def chrome():
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        if shutil.which(name):
            return shutil.which(name)
    mac = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    return mac if os.path.exists(mac) else None


def record(data, deal_id, approval, when):
    ok, why = P.parse_yes(approval)
    if not ok:
        return False, "Not recorded: %s." % why
    deal = P.find(data["deals"], deal_id)
    if deal is None:
        return False, "No deal %s" % deal_id
    md, html_path, pdf = paths_for(data, deal)
    if not os.path.exists(md):
        return False, "Not recorded: no agreement drafted for %s yet." % deal_id
    c = P.find(data["contacts"], deal["contact_id"])
    P.add_activity(data, date=when, contact_id=c["id"], deal_id=deal_id, channel="email", type="contract",
                   direction="out", subject="Your agreement with %s" % data["me"].get("company"),
                   text="Agreement sent", approval=approval,
                   attachment=pdf if os.path.exists(pdf) else html_path)
    deal["stage"] = "contract-sent"
    if not deal.get("close_date"):
        deal["close_date"] = (datetime.date.fromisoformat(when[:10]) + datetime.timedelta(days=7)).isoformat()
    deal["next_step"] = "%s signs the agreement" % c["name"].split()[0]
    return True, "Logged: agreement sent to %s. Deal is contract-sent." % c["name"]


def main(argv=None):
    p = argparse.ArgumentParser(description="Write the contract.")
    p.add_argument("--file", default=P.DEFAULT_FILE)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("draft")
    s.add_argument("--deal", required=True)
    s.add_argument("--services", required=True)
    s.add_argument("--fee", required=True)
    s.add_argument("--term", required=True)
    s.add_argument("--launch", required=True)
    s.add_argument("--date", help="the agreement date as written, e.g. 'October 29, 2026'")
    s = sub.add_parser("pdf")
    s.add_argument("--deal", required=True)
    s = sub.add_parser("record")
    s.add_argument("--deal", required=True)
    s.add_argument("--approval", required=True)
    s.add_argument("--at", required=True)
    s = sub.add_parser("signed")
    s.add_argument("--deal", required=True)
    s.add_argument("--today")
    a = p.parse_args(argv)
    try:
        data = P.load(a.file)
        deal = P.find(data["deals"], a.deal)
        if deal is None:
            raise ContractError("No deal %s" % a.deal)
        if a.cmd == "draft":
            date = a.date or datetime.date.today().strftime("%B %d, %Y").replace(" 0", " ")
            sections = [("Services", a.services), ("Fee", a.fee), ("Term", a.term), ("Launch", a.launch)]
            md = build(data, deal, date, sections)
            md_path, html_path, _ = paths_for(data, deal)
            os.makedirs(OUT_DIR, exist_ok=True)
            with open(md_path, "w", encoding="utf-8") as fh:
                fh.write(md)
            with open(html_path, "w", encoding="utf-8") as fh:
                fh.write(to_html(md))
            print("Wrote %s and %s" % (md_path, html_path))
            for w in check_fee(data, deal, a.fee):
                print("CHECK: " + w)
            print("A template, not legal advice. Read it, then: contract.py pdf, and send only on a yes.")
        elif a.cmd == "pdf":
            _, html_path, pdf = paths_for(data, deal)
            exe = chrome()
            if not exe:
                print("No Chrome found. Open %s and Print > Save as PDF." % html_path)
                return 1
            subprocess.run([exe, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                            "--print-to-pdf=" + os.path.abspath(pdf), "file://" + os.path.abspath(html_path)],
                           check=True, capture_output=True, timeout=60)
            print("Wrote %s" % pdf)
        elif a.cmd == "record":
            ok, msg = record(data, a.deal, a.approval, a.at)
            if ok:
                P.save(data, a.file)
            print(msg)
            return 0 if ok else 1
        elif a.cmd == "signed":
            P.move(data, a.deal, "won", P.today_arg(a.today), next_step="Kickoff")
            P.save(data, a.file)
            print("%s is won." % deal["title"])
    except (ContractError, P.PipelineError, OSError, subprocess.SubprocessError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
