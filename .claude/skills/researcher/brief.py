#!/usr/bin/env python3
"""Reads up on every prospect before the call. Standard library only.

Reads a business's own public website (the home page plus up to 3 of its about, services,
team and contact pages), pulls out the facts, and writes a one-page brief to
closer/research/<contact-id>.md that Claude then finishes with talking points and questions.

  build   fetches the site and writes the brief
  facts   prints the facts as JSON (no file written)

    python3 brief.py build --contact dana-harbor-lane
    python3 brief.py facts --url https://harborlanedental.com

It reads public pages only, one at a time, identifies itself, and stops at 4 pages. It never
looks up a person's private life, home or family: business facts only.
"""

import argparse
import html as htmllib
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join("closer", "research")
UA = "CloserSkill/1.0 (+https://github.com/Jakeschincariol/closer-skill; reads public pages only)"
MAX_PAGES = 4
WANT = re.compile(r"\b(about|our story|services|treatments|what we do|menu|team|staff|meet|doctors?|contact|pricing)\b", re.I)

TAG_RE = re.compile(r"<[^>]+>")
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
DESC_RE = re.compile(r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"']([^\"']*)[\"']", re.I)
HEAD_RE = re.compile(r"<h([12])[^>]*>(.*?)</h\1>", re.I | re.S)
LINK_RE = re.compile(r"<a[^>]+href=[\"']([^\"'#]+)[\"'][^>]*>(.*?)</a>", re.I | re.S)
PHONE_RE = re.compile(r"(?:\+1[\s.-]?)?\(?\b\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}\b")
EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
SINCE_RE = re.compile(r"\b(?:since|established|est\.?|founded in|serving [^.]{0,40} since)\s*(19\d\d|20\d\d)\b", re.I)
RATING_RE = re.compile(r"\"ratingValue\"\s*:\s*\"?([\d.]+)\"?.{0,120}?\"reviewCount\"\s*:\s*\"?(\d+)", re.I | re.S)
SOCIAL_RE = re.compile(r"https?://(?:www\.)?(facebook|instagram|linkedin|tiktok|youtube|x|twitter|yelp)\.com/[^\"'\s<>]+", re.I)
TOOLS = {"booking": re.compile(r"calendly|acuityscheduling|zocdoc|mindbody|booksy|vagaro|setmore|janeapp|nexhealth"
                               r"|book (an |your )?(appointment|online|now)", re.I),
         "ordering": re.compile(r"toasttab|chownow|order\.online|order (online|now)", re.I),
         "google ads tag": re.compile(r"googleadservices|AW-\d{6,}", re.I),
         "chat widget": re.compile(r"intercom|drift\.com|tawk\.to|livechat|podium", re.I)}


def _pipeline():
    path = os.path.join(HERE, "..", "pipeline", "deals.py")
    if not os.path.exists(path):
        sys.exit("The researcher skill needs the pipeline skill next to it (../pipeline/deals.py).")
    spec = importlib.util.spec_from_file_location("closer_deals", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P = _pipeline()


def text_of(fragment):
    return htmllib.unescape(re.sub(r"\s+", " ", TAG_RE.sub(" ", fragment))).strip()


def fetch(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(2_000_000).decode(resp.headers.get_content_charset() or "utf-8", "replace")


def pick_pages(home_url, page):
    """Up to MAX_PAGES-1 same-site links whose text says about / services / team / contact."""
    host = urllib.parse.urlparse(home_url).netloc
    picked = []
    for href, label in LINK_RE.findall(page):
        url = urllib.parse.urljoin(home_url, href)
        if urllib.parse.urlparse(url).netloc != host or url.rstrip("/") == home_url.rstrip("/"):
            continue
        if WANT.search(text_of(label)) and url not in picked:
            picked.append(url)
        if len(picked) >= MAX_PAGES - 1:
            break
    return picked


def facts(pages):
    """pages: {url: html}, home page first. Business facts with the page each one came from."""
    out = {"title": None, "description": None, "headings": [], "phones": [], "emails": [], "since": None,
           "rating": None, "socials": [], "tools_seen": [], "tools_missing": [], "sources": list(pages)}
    for url, page in pages.items():
        if out["title"] is None:
            m = TITLE_RE.search(page)
            out["title"] = text_of(m.group(1)) if m else None
        if out["description"] is None:
            m = DESC_RE.search(page)
            out["description"] = htmllib.unescape(m.group(1)).strip() if m else None
        for _, h in HEAD_RE.findall(page):
            t = text_of(h)
            if t and t not in out["headings"] and len(out["headings"]) < 12:
                out["headings"].append(t)
        visible = text_of(page)
        for ph in PHONE_RE.findall(visible):
            if ph not in out["phones"]:
                out["phones"].append(ph)
        for em in EMAIL_RE.findall(visible):
            if em.lower() not in out["emails"] and not em.lower().endswith((".png", ".jpg", ".webp")):
                out["emails"].append(em.lower())
        m = SINCE_RE.search(visible)
        if m and not out["since"]:
            out["since"] = int(m.group(1))
        m = RATING_RE.search(page)
        if m and not out["rating"]:
            out["rating"] = {"value": float(m.group(1)), "count": int(m.group(2)), "source": url}
        for s in SOCIAL_RE.finditer(page):
            link = s.group(0).rstrip("/")
            if link not in out["socials"]:
                out["socials"].append(link)
    every = " ".join(pages.values())
    for name, rx in TOOLS.items():
        (out["tools_seen"] if rx.search(every) else out["tools_missing"]).append(name)
    return out


def gather(url, wait=1.0):
    if not re.match(r"^https?://", url):
        url = "https://" + url
    home = fetch(url)
    pages = {url: home}
    for link in pick_pages(url, home):
        time.sleep(wait)
        try:
            pages[link] = fetch(link)
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return pages


def brief(contact, deal, f):
    lines = ["# Brief: %s" % (contact.get("company") or contact["name"]), "",
             "Prepared for the call with %s (%s)." % (contact["name"], contact.get("title") or "contact"), "",
             "## The business", ""]
    if f["title"]:
        lines.append("- Site title: %s" % f["title"])
    if f["description"]:
        lines.append("- How they describe themselves: \"%s\"" % f["description"])
    if f["since"]:
        lines.append("- In business since %d" % f["since"])
    if f["rating"]:
        lines.append("- Rating on their site: %.1f from %d reviews" % (f["rating"]["value"], f["rating"]["count"]))
    if f["phones"]:
        lines.append("- Phone: %s" % ", ".join(f["phones"][:2]))
    if f["socials"]:
        lines.append("- Social: %s" % ", ".join(f["socials"][:5]))
    lines += ["", "## What they offer (their own headings)", ""] + ["- %s" % h for h in f["headings"][:8]]
    lines += ["", "## What we noticed", ""]
    lines += ["- Seen on the site: %s" % (", ".join(f["tools_seen"]) or "none of the usual tools")]
    lines += ["- Not seen: %s (a hint, not proof: ask)" % (", ".join(f["tools_missing"]) or "-")]
    if deal and deal.get("gaps"):
        lines.append("- Prospector found: %s" % ", ".join(deal["gaps"]))
    lines += ["", "## Talking points", "", "- [Claude: 3 specific, true points from the facts above]", "",
              "## Questions to ask", "", "- How do most new customers find you today?",
              "- What happens when someone wants to book after hours?",
              "- [Claude: 2 more, specific to this business]", "", "## Sources", ""]
    lines += ["- %s" % s for s in f["sources"]]
    return "\n".join(lines) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description="Research a prospect before the call.")
    p.add_argument("--file", default=os.path.join("closer", "pipeline.json"))
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("build")
    s.add_argument("--contact", required=True)
    s.add_argument("--url", help="defaults to the contact's website")
    s = sub.add_parser("facts")
    s.add_argument("--url", required=True)
    a = p.parse_args(argv)
    try:
        if a.cmd == "facts":
            print(json.dumps(facts(gather(a.url)), indent=2))
            return 0
        data = P.load(a.file)
        c = P.find(data["contacts"], a.contact)
        if c is None:
            raise P.PipelineError("No contact %s" % a.contact)
        url = a.url or c.get("website")
        if not url:
            raise P.PipelineError("%s has no website. Pass --url." % c["name"])
        f = facts(gather(url))
        deal = next((d for d in data["deals"] if d["contact_id"] == c["id"]), None)
        os.makedirs(OUT_DIR, exist_ok=True)
        path = os.path.join(OUT_DIR, c["id"] + ".md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(brief(c, deal, f))
        print("Wrote %s from %d pages." % (path, len(f["sources"])))
    except P.PipelineError as e:
        print(str(e), file=sys.stderr)
        return 1
    except (urllib.error.URLError, OSError, ValueError) as e:
        print("Could not read the site: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
