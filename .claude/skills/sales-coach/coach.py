#!/usr/bin/env python3
"""Listens to your sales call and tells you what to say next. Standard library only.

It reads the call's live captions as text (Google Meet's own captions, which Claude in
Chrome reads off the page, or any captions or transcript file), one line per turn:

    You: So that's the site, with online booking built in.
    Dana: It looks great, but honestly, it's too expensive for us right now.

and for every line from the other side it shows you, and only you:
  - an objection (price, timing, think-it-over, someone else decides, already has someone,
    does it work, send me info) and the answer to say next, filled from your offer
  - a buying signal ("how soon could we start?") and that it is time to ask for the close
  - a question they asked, so you answer it before you move on

  listen   reads a captions file once, or keeps reading it with --follow during the call
  card     the answer for one objection, filled from your offer
  review   after the call: talk time, questions you asked, objections and the next step

    python3 coach.py listen --captions closer/calls/live.txt --me You --follow
    python3 coach.py card --objection price
    python3 coach.py review --captions closer/calls/2026-10-29-harbor-lane.txt --me You

Your offer comes from "me" in closer/pipeline.json: offer, price, plan_b, value_line, proof.
Every answer it suggests must be true for you: it fills in your words, it never invents a number.
It keeps no audio. It never speaks on the call: you decide what to say.
"""

import argparse
import importlib.util
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OBJECTIONS = os.path.join(HERE, "objections.json")

LINE_RE = re.compile(r"^\s*(?:\[?\d{1,2}:\d{2}(?::\d{2})?\]?\s*)?([^:]{1,40}):\s*(.+?)\s*$")
BUYING = re.compile(r"how soon|when (can|could) (we|you) start|what('s| is) (the )?next step|send (me )?(the|a) "
                    r"(contract|agreement|invoice)|how do (we|i) (get started|sign up|start)|sounds good|let'?s do it"
                    r"|i'?m in\b|where do i sign|can we start", re.I)
QUESTION = re.compile(r"\?\s*$|^(how|what|when|where|why|who|can|could|do|does|is|are|will|would)\b", re.I)


def _pipeline():
    path = os.path.join(HERE, "..", "pipeline", "deals.py")
    if not os.path.exists(path):
        return None
    spec = importlib.util.spec_from_file_location("closer_deals", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


P = _pipeline()


def load_objections():
    with open(OBJECTIONS, encoding="utf-8") as fh:
        lib = json.load(fh)
    for o in lib.values():
        o["re"] = re.compile("|".join(r"\b%s\b" % h for h in o["hear"]), re.I)
    return lib


def offer_from(pipeline_file):
    if P is None or not os.path.exists(pipeline_file):
        return {}
    return P.load(pipeline_file).get("me", {})


def fill(text, offer):
    def rep(m):
        v = offer.get(m.group(1))
        return v if v else "[your %s]" % m.group(1).replace("_", " ")
    return re.sub(r"\{(\w+)\}", rep, text)


def detect(text, lib):
    """The objection key heard in one line, or None. The first match in the library order wins."""
    for key, o in lib.items():
        if o["re"].search(text):
            return key
    return None


def parse_line(line):
    m = LINE_RE.match(line)
    if not m:
        return None, line.strip()
    return m.group(1).strip(), m.group(2).strip()


def coach_line(who, text, me, lib, offer):
    """What to show you for one caption line. None when it is your own line or nothing to say."""
    if not text or (who and who.lower() in (me.lower(), "you")):
        return None
    key = detect(text, lib)
    if key:
        o = lib[key]
        return {"kind": "objection", "who": who, "heard": text, "key": key, "label": o["label"],
                "say": fill(o["say"], offer), "why": o["why"]}
    if BUYING.search(text):
        return {"kind": "buying", "who": who, "heard": text, "label": "Buying signal",
                "say": "Great. The next step is %s. Want me to send that over today?" % (
                    offer.get("next_step") or "a short agreement"),
                "why": "They are asking how to buy. Stop selling and agree the next step."}
    if QUESTION.search(text):
        return {"kind": "question", "who": who, "heard": text, "label": "They asked a question",
                "say": "Answer it directly before you move on.", "why": text}
    return None


def show(card):
    bar = "-" * 60
    return "%s\n%s  (%s: \"%s\")\nSay this next: %s\n  %s\n%s" % (
        bar, card["label"], card["who"] or "them", card["heard"][:90], card["say"], card["why"], bar)


def listen(lines, me, lib, offer):
    cards = []
    for line in lines:
        who, text = parse_line(line)
        card = coach_line(who, text, me, lib, offer)
        if card:
            cards.append(card)
    return cards


def follow(path, me, lib, offer, poll=1.0):
    print("Listening to %s. Ctrl-C to stop." % path)
    pos = 0
    try:
        while True:
            if os.path.exists(path):
                with open(path, encoding="utf-8") as fh:
                    fh.seek(pos)
                    chunk = fh.read()
                    pos = fh.tell()
                for card in listen(chunk.splitlines(), me, lib, offer):
                    print(show(card), flush=True)
            time.sleep(poll)
    except KeyboardInterrupt:
        print("\nStopped listening.")


def review(lines, me, lib):
    mine = theirs = 0
    my_questions, objections, signals = [], [], []
    for line in lines:
        who, text = parse_line(line)
        if not text:
            continue
        words = len(text.split())
        if who and who.lower() in (me.lower(), "you"):
            mine += words
            if text.rstrip().endswith("?"):
                my_questions.append(text)
        else:
            theirs += words
            k = detect(text, lib)
            if k:
                objections.append((k, text))
            elif BUYING.search(text):
                signals.append(text)
    total = mine + theirs or 1
    last_mine = [parse_line(l)[1] for l in lines if (parse_line(l)[0] or "").lower() in (me.lower(), "you")]
    next_step = bool(last_mine) and bool(re.search(r"\b(monday|tuesday|wednesday|thursday|friday|tomorrow|next week"
                                                     r"|send|invite|calendar|agreement|contract|proposal)\b",
                                                     " ".join(last_mine[-3:]), re.I))
    return {"talk_share": round(100.0 * mine / total), "questions_asked": len(my_questions),
            "objections": objections, "buying_signals": signals, "next_step_agreed": next_step}


def main(argv=None):
    p = argparse.ArgumentParser(description="Tells you what to say next on a sales call.")
    p.add_argument("--file", default=os.path.join("closer", "pipeline.json"), help="for your offer (me)")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("listen")
    s.add_argument("--captions", required=True)
    s.add_argument("--me", default="You", help="your name as it shows in the captions")
    s.add_argument("--follow", action="store_true")
    s = sub.add_parser("card")
    s.add_argument("--objection", required=True)
    s = sub.add_parser("review")
    s.add_argument("--captions", required=True)
    s.add_argument("--me", default="You")
    a = p.parse_args(argv)
    lib = load_objections()
    offer = offer_from(a.file)
    if a.cmd == "card":
        if a.objection not in lib:
            print("No objection %s. Try: %s" % (a.objection, ", ".join(lib)), file=sys.stderr)
            return 1
        o = lib[a.objection]
        print("%s\nSay this next: %s\n  %s" % (o["label"], fill(o["say"], offer), o["why"]))
        return 0
    if a.cmd == "listen" and a.follow:
        follow(a.captions, a.me, lib, offer)
        return 0
    with open(a.captions, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    if a.cmd == "listen":
        cards = listen(lines, a.me, lib, offer)
        for c in cards:
            print(show(c))
        if not cards:
            print("Nothing to coach yet.")
    else:
        r = review(lines, a.me, lib)
        print("You talked %d%% of the time%s." % (r["talk_share"], " (aim for under 50%)" if r["talk_share"] > 50 else ""))
        print("You asked %d questions." % r["questions_asked"])
        for k, t in r["objections"]:
            print("Objection (%s): %s" % (k, t))
        for t in r["buying_signals"]:
            print("Buying signal: %s" % t)
        print("Next step agreed: %s" % ("yes" if r["next_step_agreed"] else "NO. Send a recap with one clear next step."))
    return 0


if __name__ == "__main__":
    sys.exit(main())
