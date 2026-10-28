#!/usr/bin/env python3
"""Share of Answer India: ask AI engines shopper questions and score which brands they name.

Two steps:
  python soa.py run     asks every question on every engine N times, saves raw answers
  python soa.py score   scores the saved answers and writes the scorecard CSVs

Test it with no API keys:
  python soa.py run --dry-run && python soa.py score

Standard library only: no installs needed (Python 3.9+).
"""
import argparse
import csv
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).parent
DATA = ROOT / "data"
RESULTS = ROOT / "results"   # question-building files live here; each month's run in results/YYYY-MM/
OUT = RESULTS                # set per command in main()

# Models: change here or with environment variables. Check each provider's docs for the current names.
OPENAI_MODEL = os.environ.get("SOA_OPENAI_MODEL", "gpt-4.1")
GEMINI_MODEL = os.environ.get("SOA_GEMINI_MODEL", "gemini-3.8-flash")
PERPLEXITY_MODEL = os.environ.get("SOA_PERPLEXITY_MODEL", "perplexity/sonar")

# Seconds to wait for one answer. Thinking models with web search can be slow.
REQUEST_TIMEOUT = int(os.environ.get("SOA_TIMEOUT", "300"))
# Ask Gemini to think briefly (faster, cheaper). Set SOA_GEMINI_THINKING=off to leave it to the model.
GEMINI_THINKING = os.environ.get("SOA_GEMINI_THINKING", "low")

# Added to every question on every engine, so all three answer for an Indian shopper.
SHOPPER_CONTEXT = os.environ.get("SOA_CONTEXT", "I'm shopping in India.")

# ---------- scoring rules (the IP: edit freely) ----------

# Points per mention strength: named as the pick > listed > mentioned in passing.
STRENGTH_POINTS = {"pick": 3, "listed": 2, "passing": 1, "warned": 0}

PICK_WORDS = re.compile(
    r"\b(best (overall|choice|pick|option)|top (pick|choice)|i('d| would) (recommend|go with|suggest)|"
    r"go (for|with)|our pick|winner|stands out|(top|final|my|our) recommendations?|best bet|best value|the better (option|choice)|highly recommend(ed)?|most (commonly )?recommended|most clinically[- ]backed|first choice|(reasonable|good|safe|smart) choice|sabse accha|best hai|pehli pasand|best (drink )?option)\b"
    r"|बेस्ट चॉइस|सबसे अच्छा|सबसे बेहतर|पहली पसंद",
    re.I,
)
SENT_END = re.compile(r"[.?!।:](\s|$)|\n")
LIST_LINE = re.compile(r"^\s*([-*•]|\d+[.)]|#+)\s+|\*\*|\t|\|", re.M)  # bullets, numbers, bold, table rows

# A brand named next to a warning (too much sugar, avoid, no proven benefit) scores 0, not a mention.
WARN_WORDS = re.compile(
    r"(high in (added )?sugar|too much sugar|sugar (ki matra|content)[^.]{0,30}(zyada|high)|up to \d+% (added )?sugar|"
    r"\bavoid\b|not recommended|no (proven|guaranteed)|koi khaas, guaranteed fayda sabit nahi|sabit nahi|"
    r"check (the )?(added )?sugar|compare (its )?sugar|aksar added sugar|added sugar hota|proof nahi|sugar (aur|and) nutrition label|शुगर की मात्रा)",
    re.I,
)

# Where AI sends the shopper to buy.
CHANNELS = {
    "Blinkit": r"blinkit", "Zepto": r"zepto", "Swiggy Instamart": r"instamart",
    "BigBasket": r"big ?basket", "Amazon": r"amazon", "Flipkart": r"flipkart",
    "Nykaa": r"nykaa", "Tira": r"\btira\b", "Purplle": r"purplle",
    "1mg / pharmacy": r"(?<!tata )\b1mg\b(?! grogood)|pharmeasy|apollo pharmacy|pharmac(y|ies)|\bchemists?\b|netmeds",
    "Brand website / D2C": r"official (website|site|store)|brand'?s (website|site)|d2c",
    "Dealer / showroom": r"dealer|showroom|dealership",
    "Modern / local store": r"supermarket|kirana|dmart|reliance (smart|retail)|local store",
}

# Answers that question the category itself ("most kids don't need one", "plain milk is best").
GATEKEEP_WORDS = re.compile(
    r"\b(unnecessary|not necessary|(may|might|probably) not need|not needed|don'?t need|doesn'?t need|not (always|automatically) (needed|better)|"
    r"balanced diet|whole foods?|everyday foods?|plain milk|homemade|ghar ka|ghar par bana|zaroorat nahi|"
    r"no (health )?drink (alone|by itself)|akela .{0,20}nahi|nahi badha sakta|height nahi badhata|badha.? nahi sakta)\b"
    r"|घर का बना|जादू की तरह काम नहीं|ज़रूरत नहीं|जरूरत नहीं",
    re.I,
)

# Health claims to hand-check in kids' nutrition answers (could a brand legally say this itself?).
CLAIM_WORDS = re.compile(
    r"\b(height|taller|grow(th)?|immunity|brain|memory|clinically (proven|tested)|doctor[- ]recommended|"
    r"paediatrician[- ]recommended|pediatrician[- ]recommended|weight gain)\b"
    r"|हाइट|लंबाई|ग्रोथ|हड्डियों|इम्युनिटी|दिमाग",
    re.I,
)


# Where each brand wants to win. Optional column "wants_to_win" in brands.csv: question ids, stages or
# need-state words, separated by |  (e.g. "consideration|review", "EV01|EV04", "all|-EV11"; a leading - excludes).
# Left blank, these default rules decide:
#   1. A bike brand's own accessories (role says OEM or "brand's own") compete only after purchase (repeat stage).
#   2. Every other brand competes on every unbranded question in its category.
OEM_ROLE = re.compile(r"\bOEM\b|brand'?s own", re.I)
# How a target question reads, by share of answers that name the brand.
STRONG_AT, CONTESTED_AT = 50, 25  # % of answers: >= 50 strong, >= 25 contested, below that a gap


# ---------- helpers ----------

def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:  # -sig: also reads files saved from Excel
        return list(csv.DictReader(f))


def write_csv(path, rows, fields):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:  # utf-8-sig opens cleanly in Excel
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def post_json(url, body, headers, retries=3):
    data = json.dumps(body).encode()
    for attempt in range(retries):
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as r:
                return json.loads(r.read())
        except TimeoutError:
            raise RuntimeError(f"no answer within {REQUEST_TIMEOUT} seconds (set SOA_TIMEOUT to wait longer)")
        except urllib.error.HTTPError as e:
            msg = e.read().decode(errors="replace")[:300]
            if e.code in (429, 500, 502, 503) and attempt < retries - 1:
                time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"HTTP {e.code}: {msg}")
        except urllib.error.URLError as e:
            if attempt < retries - 1:
                time.sleep(5)
                continue
            raise RuntimeError(str(e))


def domain(url):
    try:
        d = urlparse(url).netloc.lower()
        return d[4:] if d.startswith("www.") else d
    except Exception:
        return ""


# ---------- engines: each returns (answer_text, [cited urls or domains]) ----------

def ask_openai(q):
    key = os.environ["OPENAI_API_KEY"]
    body = {
        "model": OPENAI_MODEL,
        "input": q,
        "tools": [{"type": "web_search", "user_location": {"type": "approximate", "country": "IN"}}],
    }
    r = post_json("https://api.openai.com/v1/responses", body, {"Authorization": f"Bearer {key}"})
    text, cites = [], []
    for item in r.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    text.append(part.get("text", ""))
                    cites += [a.get("url", "") for a in part.get("annotations", []) if a.get("url")]
    return "\n".join(text), cites


def ask_gemini(q):
    key = os.environ["GEMINI_API_KEY"]
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
    global GEMINI_THINKING
    body = {"contents": [{"parts": [{"text": q}]}], "tools": [{"google_search": {}}]}
    if GEMINI_THINKING != "off":
        body["generationConfig"] = {"thinkingConfig": {"thinkingLevel": GEMINI_THINKING}}
    try:
        r = post_json(url, body, {"x-goog-api-key": key})
    except RuntimeError as ex:
        if "HTTP 400" in str(ex) and "think" in str(ex).lower() and "generationConfig" in body:
            print("   (this Gemini model doesn't take a thinking setting; carrying on without it)", flush=True)
            GEMINI_THINKING = "off"
            del body["generationConfig"]
            r = post_json(url, body, {"x-goog-api-key": key})
        else:
            raise
    if not r.get("candidates"):
        raise RuntimeError(f"Gemini returned no answer: {json.dumps(r)[:300]}")
    cand = r["candidates"][0]
    text = "".join(p.get("text", "") for p in cand.get("content", {}).get("parts", []) if not p.get("thought"))
    chunks = cand.get("groundingMetadata", {}).get("groundingChunks", [])
    # Gemini returns redirect links; the title holds the source domain.
    cites = [c.get("web", {}).get("title", "") for c in chunks if c.get("web")]
    return text, cites


def ask_perplexity(q):
    # Perplexity's Agent API (Sonar chat completions were retired in Sept 2026).
    # It has no location setting; the "I'm shopping in India" line in every question does that job.
    key = os.environ["PERPLEXITY_API_KEY"]
    body = {
        "model": PERPLEXITY_MODEL,
        "input": q,
        "tools": [{"type": "web_search"}],
        "tool_choice": {"type": "web_search"},  # always search, as the app does
    }
    r = post_json("https://api.perplexity.ai/v1/responses", body, {"Authorization": f"Bearer {key}"})
    if r.get("status") in ("failed", "cancelled"):
        raise RuntimeError(f"Perplexity status {r.get('status')}: {str(r.get('error'))[:200]}")
    text, cites = [], []
    for item in r.get("output", []):
        if item.get("type") == "message":
            for part in item.get("content", []):
                if part.get("type") == "output_text":
                    text.append(part.get("text", ""))
                    cites += [a.get("url", "") for a in part.get("annotations", []) if a.get("url")]
        elif item.get("type") == "search_results":
            cites += [s.get("url", "") for s in item.get("results", []) if s.get("url")]
    answer = "\n".join(text) or r.get("output_text", "")
    if not answer:
        raise RuntimeError(f"Perplexity returned no text: {json.dumps(r)[:300]}")
    return answer, list(dict.fromkeys(cites))


def ask_fake(q, brands):
    """Dry run: a made-up answer so you can test the whole pipeline without keys."""
    picks = random.sample(brands, k=min(len(brands), random.randint(1, 4)))
    lines = [f"Here are some options. I'd recommend **{picks[0]['brand']}** as the best overall."]
    lines += [f"- **{b['brand']}**: a popular choice." for b in picks[1:]]
    lines.append(random.choice(["You can order it on Blinkit or Amazon.", "Visit a dealer for a test ride.",
                                "Available at Nykaa and pharmacies.", ""]))
    return "\n".join(lines), random.sample(["https://www.amazon.in/x", "https://www.youtube.com/y",
                                            "https://www.reddit.com/z", "https://www.nykaa.com/a"], k=2)


ENGINES = {"chatgpt": ("OPENAI_API_KEY", ask_openai),
           "gemini": ("GEMINI_API_KEY", ask_gemini),
           "perplexity": ("PERPLEXITY_API_KEY", ask_perplexity)}


# ---------- step 1: run ----------

def cmd_run(args):
    questions = read_csv(DATA / "questions.csv")
    brands = read_csv(DATA / "brands.csv")
    if args.category:
        questions = [q for q in questions if q["category"] == args.category]
    engines = args.engines.split(",")
    if not args.dry_run:
        missing = [ENGINES[e][0] for e in engines if not os.environ.get(ENGINES[e][0])]
        if missing:
            sys.exit(f"Missing API keys: {', '.join(missing)}. Set them, or use --engines to skip one, "
                     f"or --dry-run to test.")
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"Wave {OUT.name}: answers go to {OUT}", flush=True)
    out_path = OUT / "answers.jsonl"
    done = set()
    if out_path.exists():  # resume: skip answers already saved
        for line in open(out_path, encoding="utf-8"):
            a = json.loads(line)
            done.add((a["question_id"], a["engine"], a["run"]))
    total = len(questions) * len(engines) * args.runs
    n = 0
    with open(out_path, "a", encoding="utf-8") as f:
        for q in questions:
            prompt = f"{q['question']} {SHOPPER_CONTEXT}".strip()
            cat_brands = [b for b in brands if b["category"] == q["category"]]
            for e in engines:
                for run in range(1, args.runs + 1):
                    n += 1
                    if (q["id"], e, run) in done:
                        continue
                    print(f"[{n}/{total}] {q['id']} {e} run {run}", flush=True)
                    try:
                        if args.dry_run:
                            text, cites = ask_fake(prompt, cat_brands)
                        else:
                            text, cites = ENGINES[e][1](prompt)
                        err = ""
                    except Exception as ex:  # keep going; failures are logged, rerun to retry
                        text, cites, err = "", [], str(ex)
                        print(f"   error: {err[:200]}", flush=True)
                    if err:
                        continue
                    rec = {"question_id": q["id"], "category": q["category"], "stage": q["stage"],
                           "branded": q["branded"], "engine": e, "run": run, "prompt": prompt,
                           "answer": text, "citations": cites,
                           "asked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                           "dry_run": args.dry_run}
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    f.flush()
                    if not args.dry_run:
                        time.sleep(args.pause)
    print(f"Saved to {out_path}")


# ---------- step 2: score ----------

STRENGTH_RANK = {"pick": 3, "warned": 2, "listed": 1, "passing": 0}


def judge_mention(answer, pos):
    """How one mention of a brand reads: pick, warned, listed or passing."""
    line_start = answer.rfind("\n", 0, pos) + 1
    line_end = answer.find("\n", pos)
    line = answer[line_start: line_end if line_end != -1 else len(answer)]
    # Sentence = text between boundaries (. ? ! : and the Hindi danda), within the line.
    before = [m.end() for m in SENT_END.finditer(answer, line_start, pos)]
    sent_start = before[-1] if before else line_start
    after = SENT_END.search(answer, pos)
    sentence = answer[sent_start: after.end() if after else len(answer)]
    # A label earlier on the same line counts too: "Best overall: X", "| Top pick | X |".
    lead_in = answer[line_start:pos]
    first_para_end = answer.find("\n")
    in_opening = pos < (first_para_end if first_para_end != -1 else len(answer)) < 700
    # Warning context = the rest of the brand's line, plus the next line when the brand's line is a short heading.
    ctx_end = line_end if line_end != -1 else len(answer)
    if ctx_end - line_start < 80 and line_end != -1 and "\t" not in line and "|" not in line:
        nxt_end = answer.find("\n", line_end + 1)
        nxt_end = nxt_end if nxt_end != -1 else len(answer)
        if not LIST_LINE.search(answer[line_end + 1:nxt_end]):  # next line is body text, not another item
            ctx_end = nxt_end
    context = answer[sent_start:ctx_end]
    picked = PICK_WORDS.search(sentence) or PICK_WORDS.search(lead_in) or in_opening
    if WARN_WORDS.search(context) and not PICK_WORDS.search(sentence):
        return "warned"
    if picked:  # named in the opening summary = the answer's pick
        return "pick"
    if LIST_LINE.search(line):
        return "listed"
    return "passing"


def find_mentions(answer, cat_brands):
    """Return {brand: (first_position, strength)}. Strength is the strongest of all the brand's mentions,
    so a brand listed in a table and recommended at the end counts as the pick."""
    result = {}
    for b in cat_brands:
        spots = sorted({m.start() for alias in b["aliases"].split("|") if alias.strip()
                        for m in re.finditer(r"(?<![\w])" + re.escape(alias.strip()) + r"(?![\w])", answer, re.I)})
        if not spots:
            continue
        strength = max((judge_mention(answer, p_) for p_ in spots), key=lambda s_: STRENGTH_RANK[s_])
        result[b["brand"]] = (spots[0], strength)
    return result


def wants_to_win(brand, qid, q):
    """Is this question one the brand means to compete on? Returns (yes/no, what decided it)."""
    spec = (brand.get("wants_to_win") or "").strip()
    if spec:
        def hit(tok):
            tok = tok.lower()
            return tok == "all" or tok == qid.lower() or tok == q["stage"].lower() or tok in q["need_state"].lower()
        toks = [t.strip() for t in spec.split("|") if t.strip()]
        inc = [t for t in toks if not t.startswith("-")]
        exc = [t[1:] for t in toks if t.startswith("-")]
        yes = any(hit(t) for t in inc) and not any(hit(t) for t in exc)
        return yes, "your list"
    if OEM_ROLE.search(brand.get("role", "")) or OEM_ROLE.search(brand["brand"]):
        return q["stage"] == "repeat", "default: own accessories compete after purchase"
    return True, "default: competes on every question"


def cmd_score(args):
    answers = [json.loads(l) for l in open(OUT / "answers.jsonl", encoding="utf-8")]
    brands = read_csv(DATA / "brands.csv")
    by_cat = defaultdict(list)
    for b in brands:
        by_cat[b["category"]].append(b)

    long_rows, claim_rows = [], []
    for i, a in enumerate(answers):
        mentions = find_mentions(a["answer"], by_cat[a["category"]])
        ranked = sorted(mentions.items(), key=lambda kv: kv[1][0])
        a["_mentions"] = {b: (rank + 1, s) for rank, (b, (_, s)) in enumerate(ranked)}
        a["_channels"] = [c for c, pat in CHANNELS.items() if re.search(pat, a["answer"], re.I)]
        a["_domains"] = [d for d in (domain(c) if c.startswith("http") else c.lower() for c in a["citations"]) if d]
        for b in by_cat[a["category"]]:
            rank, strength = a["_mentions"].get(b["brand"], ("", ""))
            long_rows.append({"answer_no": i, "question_id": a["question_id"], "category": a["category"],
                              "stage": a["stage"], "branded": a["branded"], "engine": a["engine"],
                              "run": a["run"], "brand": b["brand"], "mentioned": int(bool(strength)),
                              "rank": rank, "strength": strength,
                              "points": STRENGTH_POINTS.get(strength, 0)})
        if a["category"] == "kids_protein" and a["_mentions"]:
            # Only claim words within ~200 characters of a brand name count.
            spots = [pos for b in by_cat["kids_protein"] for alias in b["aliases"].split("|")
                     for pos in (m.start() for m in re.finditer(re.escape(alias), a["answer"], re.I))]
            hits = sorted(set(m.group(0).lower() for m in CLAIM_WORDS.finditer(a["answer"])
                              if any(abs(m.start() - p) < 200 for p in spots)))
            if hits:
                claim_rows.append({"answer_no": i, "question_id": a["question_id"], "engine": a["engine"],
                                   "run": a["run"], "brands_named": "; ".join(a["_mentions"]),
                                   "claim_words": "; ".join(hits), "answer": a["answer"][:1500]})

    # Scorecard: unbranded questions only (head-to-head questions force the names in).
    sc = []
    pool = [a for a in answers if args.include_branded or a["branded"] == "no"]
    for cat, cat_brands in by_cat.items():
        for eng in sorted({a["engine"] for a in pool}) + ["ALL"]:
            group = [a for a in pool if a["category"] == cat and (eng == "ALL" or a["engine"] == eng)]
            if not group:
                continue
            total_points = sum(STRENGTH_POINTS[s] for a in group for _, s in a["_mentions"].values()) or 1
            for b in cat_brands:
                hits = [a["_mentions"][b["brand"]] for a in group if b["brand"] in a["_mentions"]]
                sc.append({
                    "category": cat, "engine": eng, "brand": b["brand"], "role": b["role"],
                    "answers": len(group),
                    "share_of_mentions_%": round(100 * len(hits) / len(group), 1),
                    "pick_rate_%": round(100 * sum(1 for _, s in hits if s == "pick") / len(group), 1),
                    "first_named_%": round(100 * sum(1 for r, _ in hits if r == 1) / len(group), 1),
                    "named_with_warning_%": round(100 * sum(1 for _, s in hits if s == "warned") / len(group), 1),
                    "weighted_share_%": round(100 * sum(STRENGTH_POINTS[s] for _, s in hits) / total_points, 1),
                })
    sc.sort(key=lambda r: (r["category"], r["engine"], -r["weighted_share_%"]))

    # Category gatekeeping: % of answers that steer the shopper away from buying anything packaged.
    gate_rows = []
    for cat in by_cat:
        for eng in sorted({a["engine"] for a in pool}) + ["ALL"]:
            group = [a for a in pool if a["category"] == cat and (eng == "ALL" or a["engine"] == eng)]
            if group:
                g = sum(1 for a in group if GATEKEEP_WORDS.search(a["answer"]))
                nb = sum(1 for a in group if not a["_mentions"])
                gate_rows.append({"category": cat, "engine": eng, "answers": len(group),
                                  "questions_the_category_%": round(100 * g / len(group), 1),
                                  "no_brand_named_%": round(100 * nb / len(group), 1)})

    # By journey stage (all engines).
    stage_rows = []
    for cat, cat_brands in by_cat.items():
        for stage in sorted({a["stage"] for a in pool if a["category"] == cat}):
            group = [a for a in pool if a["category"] == cat and a["stage"] == stage]
            for b in cat_brands:
                n = sum(1 for a in group if b["brand"] in a["_mentions"])
                stage_rows.append({"category": cat, "stage": stage, "brand": b["brand"], "answers": len(group),
                                   "share_of_mentions_%": round(100 * n / len(group), 1)})

    # Citations and channels.
    cite_rows, chan_rows = [], []
    for cat in by_cat:
        group = [a for a in answers if a["category"] == cat]
        for d, n in Counter(d for a in group for d in set(a["_domains"])).most_common(25):
            cite_rows.append({"category": cat, "source_domain": d, "answers_citing": n,
                              "share_%": round(100 * n / len(group), 1)})
        for c, n in Counter(c for a in group for c in a["_channels"]).most_common():
            chan_rows.append({"category": cat, "channel": c, "answers_naming": n,
                              "share_%": round(100 * n / len(group), 1)})

    # Hand-check sample: about 10% of answers, at least 2 per category, with the script's reading beside each.
    random.seed(42)
    sample = []
    for cat in by_cat:
        group = [(i, a) for i, a in enumerate(answers) if a["category"] == cat]
        for i, a in random.sample(group, k=min(len(group), max(2, len(group) // 10))):
            sample.append({"answer_no": i, "question_id": a["question_id"], "engine": a["engine"],
                           "run": a["run"], "prompt": a["prompt"],
                           "script_reading": "; ".join(f"{b} #{r} {s}" for b, (r, s) in
                                                       sorted(a["_mentions"].items(), key=lambda x: x[1][0])),
                           "my_reading": "", "agree_y_n": "", "answer": a["answer"][:2000]})

    # Where to win: each brand on each unbranded question, judged only where it means to compete.
    questions = {q["id"]: q for q in read_csv(DATA / "questions.csv")}
    win_rows, win_summary = [], []
    for cat, cat_brands in by_cat.items():
        qids = sorted({a["question_id"] for a in pool if a["category"] == cat})
        for b in cat_brands:
            counts = Counter()
            for qid in qids:
                q = questions.get(qid, {"stage": "", "need_state": ""})
                is_target, why = wants_to_win(b, qid, q)
                group = [a for a in pool if a["question_id"] == qid]
                hits = [a["_mentions"][b["brand"]] for a in group if b["brand"] in a["_mentions"]]
                named = 100 * len(hits) / len(group)
                warned = 100 * sum(1 for _, s in hits if s == "warned") / len(group)
                picked = 100 * sum(1 for _, s in hits if s == "pick") / len(group)
                if not is_target:
                    read = "Not a target, but named" if named >= STRONG_AT else "Not a target"
                elif warned and warned >= named / 2:
                    read = "Named with warnings"
                elif named >= STRONG_AT:
                    read = "Strong"
                elif named >= CONTESTED_AT:
                    read = "Contested"
                else:
                    read = "Gap that matters"
                counts[read] += 1
                win_rows.append({"category": cat, "brand": b["brand"], "question_id": qid, "stage": q["stage"],
                                 "need_state": q["need_state"], "wants_to_win": "yes" if is_target else "no",
                                 "decided_by": why, "answers": len(group), "named_%": round(named, 1),
                                 "pick_%": round(picked, 1), "warned_%": round(warned, 1), "read": read,
                                 "question": q.get("question", "")})
            win_summary.append({"category": cat, "brand": b["brand"],
                                "target_questions": sum(counts[k] for k in counts if not k.startswith("Not a target")),
                                **{k: counts[k] for k in ("Strong", "Contested", "Gap that matters",
                                                          "Named with warnings", "Not a target, but named")}})
    order = {"Gap that matters": 0, "Named with warnings": 1, "Contested": 2, "Strong": 3,
             "Not a target, but named": 4, "Not a target": 5}
    win_rows.sort(key=lambda r: (r["category"], r["brand"], order[r["read"]], r["question_id"]))

    write_csv(OUT / "where_to_win.csv", win_rows, list(win_rows[0].keys()))
    write_csv(OUT / "where_to_win_summary.csv", win_summary, list(win_summary[0].keys()))
    write_csv(OUT / "scores_by_answer.csv", long_rows, list(long_rows[0].keys()))
    write_csv(OUT / "scorecard.csv", sc, list(sc[0].keys()))
    write_csv(OUT / "scorecard_by_stage.csv", stage_rows, list(stage_rows[0].keys()))
    write_csv(OUT / "category_gatekeeping.csv", gate_rows, list(gate_rows[0].keys()))
    write_csv(OUT / "citations.csv", cite_rows, ["category", "source_domain", "answers_citing", "share_%"])
    write_csv(OUT / "channels.csv", chan_rows, ["category", "channel", "answers_naming", "share_%"])
    write_csv(OUT / "claims_to_check.csv", claim_rows,
              ["answer_no", "question_id", "engine", "run", "brands_named", "claim_words", "answer"])
    write_csv(OUT / "hand_check_sample.csv", sample, list(sample[0].keys()))

    print(f"Scored {len(answers)} answers ({len(pool)} unbranded in scorecard). Files in {OUT}/")
    for cat in by_cat:
        top = [r for r in sc if r["category"] == cat and r["engine"] == "ALL"][:3]
        print(f"  {cat}: " + ", ".join(f"{r['brand']} {r['share_of_mentions_%']}%" for r in top))
    print("Where to win (gaps that matter, out of target questions):")
    for s in win_summary:
        if s["target_questions"]:
            print(f"  {s['brand']}: {s['Gap that matters']} of {s['target_questions']}")


# ---------- step 0: build the questions (rules you can change) ----------
#
# data/question_bank.csv holds every candidate question with its need state, type (occasion / benefit /
# barrier / channel / head_to_head), variant (plain / hinglish / price / city / persona) and a short search
# phrase. A question can carry a {city} or {price} slot; data/variants.csv fills it in.
# "python soa.py questions" scores every candidate on the rules below, explains each decision, and with
# --apply writes the kept ones to data/questions.csv. Override any decision with keep = yes / no in the bank.

SUGGEST_URL = "https://suggestqueries.google.com/complete/search?client=firefox&hl=en&gl=in&ie=utf8&oe=utf8&q="
# Search volume, automatic: collect what Google autocomplete (India) suggests for each category's main
# terms (data/categories.csv), then count how much of it mentions each question's keywords.
# "charging comes up in 4% of what people type about electric scooters" = High.
POOL_SEEDS = ["{t}", "best {t} for", "best {t} under", "which {t}", "{t} for", "{t} vs", "is {t}", "{t} problems",
              "should i buy {t}", "{t} kaunsa"] + ["{t} " + ch for ch in "abcdefghijklmnopqrstuvwxyz"]
AUTO_VOLUME = [(4, "High"), (1, "Medium"), (0.0001, "Low"), (0, "None")]  # % of the category's suggestions
# Search volume, manual (data/volume.csv): High / Medium / Low / None, or monthly searches from Keyword Planner.
MANUAL_VOLUME = [(10000, "High"), (1000, "Medium"), (1, "Low"), (0, "None")]
# Recency: mentions of the question's keywords in last-30-days research files (data/signals/*.md).
RECENCY = [(10, "High"), (3, "Medium"), (1, "Low"), (0, "None")]
VOLUME_POINTS = {"High": 3, "Medium": 2, "Low": 1, "None": 0, "Not checked": 1}
RECENCY_POINTS = {"High": 2, "Medium": 1, "Low": 0, "None": 0, "No file": 0}
# Rule: a question must ask for a choice, or no brand can win it.
ASKS_FOR_CHOICE = re.compile(
    r"\b(which|best|suggest|recommend|should i|worth|where (can|should) i|what .{0,40}(buy|switch to|get)|vs)\b"
    r"|kaunsa|kaunsi|kaun sa|lena chahiye", re.I)
MAX_PER_STAGE = 4        # questions kept per category per journey stage (head-to-heads not counted)
MAX_PER_NEED_STATE = 2   # so one need state can't dominate a category
HEAD_TO_HEAD_MAX = 1     # per category
COVERAGE = ["hinglish", "price", "city"]  # each category keeps at least one of each, if the bank has one


def level(value, scale):
    return next(name for floor, name in scale if value >= floor)


def autocomplete(phrase):
    url = SUGGEST_URL + urllib.parse.quote(phrase)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = json.loads(r.read().decode("utf-8", errors="replace"))
    return data[1] if len(data) > 1 else []


def expand_bank(bank, variants):
    """Fill {city} / {price} slots: one candidate per value in data/variants.csv."""
    out = []
    for q in bank:
        slots = re.findall(r"\{(\w+)\}", q["question"])
        if not slots:
            out.append(dict(q))
            continue
        slot = slots[0]
        values = [v for v in variants if v["category"] == q["category"] and v["slot"] == slot]
        for i, v in enumerate(values):
            c = dict(q)
            c["id"] = f"{q['id']}{'abcdefghij'[i]}"
            c["question"] = q["question"].replace("{" + slot + "}", v["value"])
            c["search_phrase"] = q["search_phrase"].replace("{" + slot + "}", v["search_value"])
            c["source"] = f"{q['source']} ({v['value']})"
            out.append(c)
    return out


def cmd_questions(args):
    bank = read_csv(DATA / "question_bank.csv")
    variants = read_csv(DATA / "variants.csv") if (DATA / "variants.csv").exists() else []
    cands = expand_bank(bank, variants)
    if args.category:
        cands = [c for c in cands if c["category"] == args.category]
    OUT.mkdir(parents=True, exist_ok=True)

    # Volume: manual file wins; otherwise automatic (Google answers are cached, so reruns are quick).
    manual = {}
    if (DATA / "volume.csv").exists():
        for r in read_csv(DATA / "volume.csv"):
            v = r.get("volume", "").strip()
            if v:
                manual[r["search_phrase"].strip().lower()] = (
                    level(float(v.replace(",", "")), MANUAL_VOLUME) if v.replace(",", "").replace(".", "").isdigit()
                    else v.capitalize(), "your volume file")
    heads = {r["category"]: [t.strip() for t in r["head_terms"].split("|") if t.strip()]
             for r in read_csv(DATA / "categories.csv")} if (DATA / "categories.csv").exists() else {}
    cache_path = OUT / "autocomplete_cache.json"
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}

    def suggest(q):
        if q not in cache:
            try:
                cache[q] = autocomplete(q)
                time.sleep(0.4)
            except Exception as ex:
                print(f"  could not check '{q}': {str(ex)[:100]}", flush=True)
                return None
        return cache[q]

    pools = {}
    if not args.no_volume:
        for cat in dict.fromkeys(c["category"] for c in cands):
            pool = {}  # suggestion -> the main terms it came from
            for t in heads.get(cat, []):
                for seed in POOL_SEEDS:
                    for sug in suggest(seed.format(t=t)) or []:
                        pool.setdefault(sug.lower(), set()).add(t.lower())
            if pool:
                pools[cat] = pool
                print(f"  {cat}: {len(pool)} things shoppers type about {' / '.join(heads[cat])}", flush=True)
            cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")
    for c in cands:
        p = c["search_phrase"].strip().lower()
        c["_pool_hits"] = 0
        if p in manual:
            c["_volume"], c["_volume_from"] = manual[p]
            continue
        if args.no_volume:
            c["_volume"], c["_volume_from"] = "Not checked", "volume check off"
            continue
        kws = [k.strip().lower() for k in c["keywords"].split("|") if k.strip()]
        # Leave out main terms that already contain the topic ("protein powder for kids" for a protein question).
        skip = {t.lower() for t in heads.get(c["category"], []) if any(k in t.lower() for k in kws)}
        pool = [s_ for s_, ts in pools.get(c["category"], {}).items() if ts - skip]
        hits = sum(1 for s_ in pool if any(re.search(r"(?<![\w])" + re.escape(k), s_) for k in kws))
        exact = None if hits else suggest(p)
        c["_pool_hits"] = hits
        if pool and hits:
            share = 100 * hits / len(pool)
            c["_volume"] = level(share, AUTO_VOLUME)
            c["_volume_from"] = f"{hits} of {len(pool)} Google suggestions ({share:.1f}%)"
        elif exact:
            c["_volume"], c["_volume_from"] = "Low", f"exact phrase only ({len(exact)} suggestions)"
        elif exact is None and not pool:
            c["_volume"], c["_volume_from"] = "Not checked", "Google not reachable"
        else:
            c["_volume"], c["_volume_from"] = "None", "no Google suggestions"
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=0), encoding="utf-8")

    # Recency: last-30-days research files. Files in data/signals/<category>/ count for that category only;
    # files directly in data/signals/ count for every category.
    sig_dir = Path(args.signals) if args.signals else DATA / "signals"

    def texts(folder):
        if not folder.exists():
            return []
        return [f.read_text(encoding="utf-8", errors="replace")
                for f in sorted(folder.glob("*.md")) + sorted(folder.glob("*.txt"))]
    shared = texts(sig_dir)
    signal_text = {cat: "\n".join(shared + texts(sig_dir / cat)) for cat in {c["category"] for c in cands}}
    for c in cands:
        text = signal_text[c["category"]]
        if not text:
            c["_recency"], c["_mentions"] = "No file", 0
            continue
        n = sum(len(re.findall(r"(?<![\w])" + re.escape(k.strip()), text, re.I))
                for k in c["keywords"].split("|") if k.strip())
        c["_recency"], c["_mentions"] = level(n, RECENCY), n
    files = any(signal_text.values())

    for i, c in enumerate(cands):
        c["_order"] = i
        c["_choice"] = bool(ASKS_FOR_CHOICE.search(c["question"]))
        c["_score"] = VOLUME_POINTS.get(c["_volume"], 1) + RECENCY_POINTS[c["_recency"]]

    # Select, category by category.
    max_stage = args.max_per_stage
    for cat in dict.fromkeys(c["category"] for c in cands):
        group = sorted([c for c in cands if c["category"] == cat],
                       key=lambda c: (c["keep"].strip().lower() != "yes", -c["_score"], -c["_pool_hits"], c["_order"]))
        stage_n, ns_n, h2h = Counter(), Counter(), 0
        for c in group:
            keep = c["keep"].strip().lower()
            reserve = False
            if keep == "no":
                d, why = "drop", "you set keep = no"
            elif keep == "yes":
                d, why = "keep", "you set keep = yes"
            elif not c["_choice"]:
                d, why = "drop", "doesn't ask for a choice, so no brand can win it"
            elif c["_volume"] == "None" and c["_recency"] in ("None", "No file"):
                d, why = "drop", "no sign that shoppers ask this (no Google suggestions, no recent talk)"
            elif c["type"] == "head_to_head" and h2h >= HEAD_TO_HEAD_MAX:
                d, why, reserve = "drop", f"only {HEAD_TO_HEAD_MAX} head-to-head per category", True
            elif ns_n[c["need_state"]] >= MAX_PER_NEED_STATE:
                d, why, reserve = "drop", f"need state already has {MAX_PER_NEED_STATE} questions", True
            elif c["type"] != "head_to_head" and stage_n[c["stage"]] >= max_stage:
                d, why, reserve = "drop", f"{c['stage']} stage already has {max_stage} higher-scoring questions", True
            else:
                d, why = "keep", f"scores {c['_score']} and fits the {c['stage']} stage"
            c["_decision"], c["_why"], c["_reserve"] = d, why, reserve
            if d == "keep":
                ns_n[c["need_state"]] += 1
                if c["type"] == "head_to_head":
                    h2h += 1
                else:
                    stage_n[c["stage"]] += 1
        # Coverage: at least one Hinglish, price and city question per category, if the bank has one.
        for var in COVERAGE:
            if any(c["_decision"] == "keep" and c["variant"] == var for c in group):
                continue
            spare = [c for c in group if c["variant"] == var and c["_reserve"]]
            if spare:
                spare[0]["_decision"] = "keep"
                spare[0]["_why"] = f"kept so the category has at least one {var} question"

    review = []
    for c in sorted(cands, key=lambda c: c["_order"]):
        review.append({
            "decision": c["_decision"], "id": c["id"], "category": c["category"], "stage": c["stage"],
            "need_state": c["need_state"], "type": c["type"], "variant": c["variant"], "question": c["question"],
            "why": c["_why"], "score": c["_score"], "search_volume": c["_volume"], "volume_from": c["_volume_from"],
            "last_30_days": c["_recency"], "recent_mentions": c["_mentions"],
            "asks_for_a_choice": "yes" if c["_choice"] else "no", "search_phrase": c["search_phrase"],
            "source": c["source"], "branded": c["branded"]})
    write_csv(OUT / "question_review.csv", review, list(review[0].keys()))
    kept = [r for r in review if r["decision"] == "keep"]
    print(f"{len(kept)} of {len(review)} candidate questions kept. Reasons in {OUT / 'question_review.csv'}")
    for cat in dict.fromkeys(r["category"] for r in review):
        k = [r for r in kept if r["category"] == cat]
        print(f"  {cat}: {len(k)} kept ({', '.join(sorted({r['variant'] for r in k}))})")
    if not files:
        print("  Tip: add last-30-days research files to data/signals/ to score recency too.")
    if args.apply:
        target = DATA / "questions.csv"
        if target.exists():
            backup = DATA / f"questions_backup_{datetime.now().strftime('%Y%m%d_%H%M')}.csv"
            backup.write_bytes(target.read_bytes())
            print(f"  Old questions saved as {backup.name}")
        write_csv(target, [{"id": r["id"], "category": r["category"], "stage": r["stage"], "branded": r["branded"],
                            "need_state": r["need_state"], "variant": r["variant"], "question": r["question"],
                            "why": r["why"]} for r in kept],
                  ["id", "category", "stage", "branded", "need_state", "variant", "question", "why"])
        print(f"  Wrote {len(kept)} questions to {target}. Run 'python soa.py run' next.")
    else:
        print("  Nothing changed yet. Check the review file, edit the bank if you like, then add --apply.")


def cmd_ideas(args):
    """Real shopper language: Google autocomplete expansions of a topic, to turn into candidate questions."""
    t = args.topic.strip()
    seeds = [f"best {t} for", f"which {t}", f"{t} for", f"{t} vs", f"is {t}", f"{t} problems", f"{t} under",
             f"should i buy {t}", f"{t} kaunsa"] + [f"{t} {ch}" for ch in "abcdefghijklmnopqrstuvwxyz"]
    rows, seen = [], set()
    for s in seeds:
        try:
            for sug in autocomplete(s):
                if sug.lower() not in seen:
                    seen.add(sug.lower())
                    rows.append({"seed": s, "what_shoppers_type": sug})
            time.sleep(0.4)
        except Exception as ex:
            print(f"  could not check '{s}': {str(ex)[:100]}")
    if not rows:
        sys.exit("No suggestions came back. Check your internet connection.")
    slug = re.sub(r"\W+", "_", t.lower()).strip("_")
    path = OUT / f"question_ideas_{slug}.csv"
    write_csv(path, rows, ["seed", "what_shoppers_type"])
    print(f"{len(rows)} things shoppers type about '{t}', saved to {path}")


# ---------- between runs: what happened to each brand ----------

EVENT_PROMPT = (
    "List notable marketing or business events for the brand {brand} (owned by {owner}) in India between {since} "
    "and {until}: ad campaigns, product launches or reformulations, price changes, offers, recalls, regulatory or "
    "legal action, and major news coverage. Only events with a date in that window and a source you found. "
    "Reply with only a JSON array of objects with keys date (YYYY-MM-DD), event (at most 15 words) and source_url. "
    "If there were none, reply with []."
)
EVENT_FIELDS = ["date", "brand", "category", "event", "source"]


def read_events():
    path = DATA / "events.csv"
    return read_csv(path) if path.exists() else []


def cmd_events(args):
    review = RESULTS / "events_review.csv"
    if args.apply:
        if not review.exists():
            sys.exit("Nothing to apply yet. Run 'python soa.py events' first.")
        have = read_events()
        seen = {(e.get("date", ""), e.get("brand", ""), e.get("event", "").strip().lower()) for e in have}
        added = 0
        for r in read_csv(review):
            if r.get("keep", "").strip().lower() == "no":
                continue
            k = (r["date"], r["brand"], r["event"].strip().lower())
            if k not in seen:
                have.append({f: r.get(f, "") for f in EVENT_FIELDS})
                seen.add(k)
                added += 1
        have.sort(key=lambda e: (e.get("date", ""), e.get("brand", "")))
        write_csv(DATA / "events.csv", have, EVENT_FIELDS)
        print(f"Added {added} events to {DATA / 'events.csv'} ({len(have)} in total).")
        return
    if not os.environ.get("PERPLEXITY_API_KEY"):
        sys.exit("Missing PERPLEXITY_API_KEY. Set it first (this step searches the web through Perplexity).")
    found = waves()
    since = args.since or (max(json.loads(l)["asked_at"][:10] for l in open(RESULTS / found[-1] / "answers.jsonl",
                                                                          encoding="utf-8")) if found else None)
    if not since:
        sys.exit("No earlier run found. Give a start date: --since 2026-10-26")
    until = args.until or datetime.now().strftime("%Y-%m-%d")
    brands = [b for b in read_csv(DATA / "brands.csv")
              if (args.all_brands or "seen in answers" not in b.get("role", ""))
              and (not args.category or b["category"] == args.category)]
    print(f"Looking for events from {since} to {until} for {len(brands)} brands...", flush=True)
    rows = []
    for b in brands:
        q = EVENT_PROMPT.format(brand=b["brand"].replace(" (OEM)", ""), owner=b.get("owner", ""), since=since, until=until)
        try:
            text, cites = ask_perplexity(q)
        except Exception as ex:
            print(f"  {b['brand']}: could not search ({str(ex)[:100]})", flush=True)
            continue
        m = re.search(r"\[.*\]", text, re.S)
        try:
            items = json.loads(m.group(0)) if m else []
        except ValueError:
            items = []
        kept = 0
        for it in items if isinstance(items, list) else []:
            d = str(it.get("date", ""))[:10]
            url = str(it.get("source_url", ""))
            if not (re.fullmatch(r"\d{4}-\d{2}-\d{2}", d) and since <= d <= until and url.startswith("http")):
                continue  # no date in the window, or no source: not kept
            rows.append({"keep": "", "date": d, "brand": b["brand"], "category": b["category"],
                         "event": str(it.get("event", "")).strip()[:200], "source": url})
            kept += 1
        print(f"  {b['brand']}: {kept} event(s)", flush=True)
        time.sleep(args.pause)
    rows.sort(key=lambda r: (r["category"], r["date"], r["brand"]))
    write_csv(review, rows, ["keep"] + EVENT_FIELDS)
    print(f"{len(rows)} events found. Check them in {review}: open each source, put 'no' in the keep column for any "
          f"that are wrong or don't matter, then run 'python soa.py events --apply'.")


# ---------- step 3: compare two months ----------

BIG_MOVE, MOVE = 15, 8  # brand level, change in percentage points; smaller moves are within normal run-to-run noise
QUESTION_MOVE = 34      # one question has only ~6 answers, so it takes 2+ answers changing to count


def waves():
    return sorted(d.name for d in RESULTS.iterdir() if d.is_dir() and re.fullmatch(r"\d{4}-\d{2}", d.name)
                  and (d / "answers.jsonl").exists()) if RESULTS.exists() else []


def load_wave(w):
    path = RESULTS / w / "answers.jsonl"
    if not path.exists():
        sys.exit(f"No answers for wave {w} (looked for {path}). Waves found: {', '.join(waves()) or 'none'}")
    return [json.loads(l) for l in open(path, encoding="utf-8")]


def brand_sentence(answer, brand_row):
    for alias in brand_row["aliases"].split("|"):
        m = re.search(r"(?<![\w])" + re.escape(alias.strip()) + r"(?![\w])", answer, re.I)
        if m:
            a = max(answer.rfind(".", 0, m.start()), answer.rfind("\n", 0, m.start())) + 1
            b = min([x for x in (answer.find(".", m.end()), answer.find("\n", m.end())) if x != -1] or [len(answer)])
            return re.sub(r"\s+", " ", answer[a:b + 1]).strip()[:300]
    return ""


def cmd_compare(args):
    found = waves()
    if len(args.waves) >= 2:
        a_w, b_w = args.waves[:2]
    elif len(args.waves) == 1:
        a_w, b_w = args.waves[0], (found[-1] if found else "")
    else:
        a_w, b_w = (found[-2], found[-1]) if len(found) >= 2 else ("", "")
    if not a_w or not b_w or a_w == b_w:
        sys.exit(f"Need two waves to compare. Waves found: {', '.join(found) or 'none'}")
    A, B = load_wave(a_w), load_wave(b_w)
    # Like for like: same questions, same engines, unbranded only.
    common_q = {x["question_id"] for x in A} & {x["question_id"] for x in B}
    common_e = {x["engine"] for x in A} & {x["engine"] for x in B}
    A = [x for x in A if x["question_id"] in common_q and x["engine"] in common_e and x["branded"] == "no"]
    B = [x for x in B if x["question_id"] in common_q and x["engine"] in common_e and x["branded"] == "no"]
    brands = read_csv(DATA / "brands.csv")
    need = {q["id"]: q.get("need_state", "") for q in read_csv(DATA / "questions.csv")}
    by_cat = defaultdict(list)
    for b in brands:
        by_cat[b["category"]].append(b)
    for x in A + B:
        x["_m"] = find_mentions(x["answer"], by_cat[x["category"]])
        x["_ch"] = {c for c, pat in CHANNELS.items() if re.search(pat, x["answer"], re.I)}
        x["_dom"] = {d for d in (domain(c) if c.startswith("http") else c.lower() for c in x["citations"]) if d}

    def stats(group, brand):
        n = len(group) or 1
        hits = [g["_m"][brand] for g in group if brand in g["_m"]]
        return {"named": 100 * len(hits) / n, "pick": 100 * sum(1 for _, s_ in hits if s_ == "pick") / n,
                "warned": 100 * sum(1 for _, s_ in hits if s_ == "warned") / n}

    def label(d):
        return "Big move" if abs(d) >= BIG_MOVE else "Move" if abs(d) >= MOVE else "Within noise"

    tag = f"{a_w}_vs_{b_w}"
    brand_rows, q_rows = [], []
    for cat, cbs in by_cat.items():
        ga, gb = [x for x in A if x["category"] == cat], [x for x in B if x["category"] == cat]
        if not ga or not gb:
            continue
        for b in cbs:
            sa, sb = stats(ga, b["brand"]), stats(gb, b["brand"])
            if not (sa["named"] or sb["named"]):
                continue
            d = sb["named"] - sa["named"]
            # Drivers: the questions where this brand moved most.
            moves = []
            for qid in sorted({x["question_id"] for x in ga}):
                qa = [x for x in ga if x["question_id"] == qid]
                qb = [x for x in gb if x["question_id"] == qid]
                ta, tb = stats(qa, b["brand"]), stats(qb, b["brand"])
                dq = tb["named"] - ta["named"]
                if ta["named"] or tb["named"]:
                    before = next((brand_sentence(x["answer"], b) for x in qa if b["brand"] in x["_m"]), "")
                    after = next((brand_sentence(x["answer"], b) for x in qb if b["brand"] in x["_m"]), "")
                    q_rows.append({"category": cat, "brand": b["brand"], "question_id": qid,
                                   "need_state": need.get(qid, ""), "question": qa[0]["prompt"],
                                   f"named_{a_w}_%": round(ta["named"], 1), f"named_{b_w}_%": round(tb["named"], 1),
                                   "change_pts": round(dq, 1),
                                   "read": "Big move" if abs(dq) >= QUESTION_MOVE else "Within noise",
                                   f"what_AI_said_{a_w}": before, f"what_AI_said_{b_w}": after})
                    moves.append((dq, qid))
            moves.sort(key=lambda m: -abs(m[0]))
            drivers = "; ".join(f"{q} {m:+.0f}" for m, q in moves[:3] if abs(m) >= MOVE)
            brand_rows.append({"category": cat, "brand": b["brand"],
                               f"named_{a_w}_%": round(sa["named"], 1), f"named_{b_w}_%": round(sb["named"], 1),
                               "named_change_pts": round(d, 1),
                               f"pick_{a_w}_%": round(sa["pick"], 1), f"pick_{b_w}_%": round(sb["pick"], 1),
                               "pick_change_pts": round(sb["pick"] - sa["pick"], 1),
                               "warned_change_pts": round(sb["warned"] - sa["warned"], 1),
                               "read": label(d), "driven_by_questions": drivers})
    brand_rows.sort(key=lambda r: (r["category"], -abs(r["named_change_pts"])))
    q_rows.sort(key=lambda r: (r["category"], r["brand"], -abs(r["change_pts"])))

    # Sources and channels gained or lost: the evidence for why.
    src_rows, chan_rows = [], []
    for cat in by_cat:
        ga, gb = [x for x in A if x["category"] == cat], [x for x in B if x["category"] == cat]
        if not ga or not gb:
            continue
        ca, cb = Counter(d for x in ga for d in x["_dom"]), Counter(d for x in gb for d in x["_dom"])
        for d in set(ca) | set(cb):
            pa, pb = 100 * ca[d] / len(ga), 100 * cb[d] / len(gb)
            if abs(pb - pa) >= MOVE:
                src_rows.append({"category": cat, "source": d, f"cited_{a_w}_%": round(pa, 1),
                                 f"cited_{b_w}_%": round(pb, 1), "change_pts": round(pb - pa, 1),
                                 "status": "new" if not ca[d] else "gone" if not cb[d] else "up" if pb > pa else "down"})
        ka, kb = Counter(c for x in ga for c in x["_ch"]), Counter(c for x in gb for c in x["_ch"])
        for c in set(ka) | set(kb):
            pa, pb = 100 * ka[c] / len(ga), 100 * kb[c] / len(gb)
            chan_rows.append({"category": cat, "channel": c, f"{a_w}_%": round(pa, 1), f"{b_w}_%": round(pb, 1),
                              "change_pts": round(pb - pa, 1)})
    src_rows.sort(key=lambda r: (r["category"], -abs(r["change_pts"])))
    chan_rows.sort(key=lambda r: (r["category"], -abs(r["change_pts"])))

    # Your log of what happened in between (campaigns, launches, price cuts, news).
    events = []
    if (DATA / "events.csv").exists():
        start = min(x["asked_at"][:10] for x in A)
        end = max(x["asked_at"][:10] for x in B)
        events = [e for e in read_csv(DATA / "events.csv") if start <= e.get("date", "") <= end]

    out = RESULTS / "compare"
    write_csv(out / f"brands_{tag}.csv", brand_rows, list(brand_rows[0].keys()) if brand_rows else ["category"])
    write_csv(out / f"questions_{tag}.csv", q_rows, list(q_rows[0].keys()) if q_rows else ["category"])
    write_csv(out / f"sources_{tag}.csv", src_rows, list(src_rows[0].keys()) if src_rows else ["category"])
    write_csv(out / f"channels_{tag}.csv", chan_rows, list(chan_rows[0].keys()) if chan_rows else ["category"])

    lines = [f"# Share of Answer: {a_w} vs {b_w}", "",
             f"Like for like: {len(common_q)} questions, engines {', '.join(sorted(common_e))}, "
             f"{len(A)} vs {len(B)} unbranded answers. Moves of {BIG_MOVE}+ points are big; under {MOVE} is noise.", ""]
    for cat in by_cat:
        rows = [r for r in brand_rows if r["category"] == cat]
        if not rows:
            continue
        lines += [f"## {cat}", ""]
        moved = [r for r in rows if r["read"] != "Within noise"]
        qmoved = [r for r in q_rows if r["category"] == cat and r["read"] == "Big move"]
        if not moved and not qmoved:
            lines.append("No brand moved beyond normal noise.")
        for r in moved:
            lines.append(f"- **{r['brand']}**: named {r[f'named_{a_w}_%']}% → {r[f'named_{b_w}_%']}% "
                         f"({r['named_change_pts']:+} pts, {r['read'].lower()}); pick {r['pick_change_pts']:+} pts. "
                         f"Driven by: {r['driven_by_questions'] or 'spread across questions'}.")
        if qmoved:
            lines += ["", "Big moves on single questions (a brand can win or lose one need state while its average holds):"]
            for r in qmoved[:8]:
                lines.append(f"- {r['brand']} on {r['question_id']} ({r['need_state']}): "
                             f"{r[f'named_{a_w}_%']}% → {r[f'named_{b_w}_%']}% ({r['change_pts']:+} pts)")
        srcs = [r for r in src_rows if r["category"] == cat][:5]
        if srcs:
            lines.append("")
            lines.append("Sources AI leaned on differently: " + "; ".join(
                f"{r['source']} ({r['status']}, {r['change_pts']:+} pts)" for r in srcs) + ".")
        ev = [e for e in events if e.get("category", "") in ("", cat)]
        if ev:
            lines.append("")
            lines.append("What happened in between: " + "; ".join(
                f"{e['date']} {e.get('brand', '')}: {e.get('event', '')}" for e in ev) + ".")
        lines.append("")
    lines += ["The script shows what moved and the evidence. Why it moved is your call: check the "
              f"before/after quotes in questions_{tag}.csv, the sources, and your events log."]
    (out / f"summary_{tag}.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Compared {a_w} with {b_w}. Read {out / f'summary_{tag}.md'} first; details in the CSVs beside it.")
    for r in [r for r in brand_rows if r["read"] == "Big move"][:8]:
        print(f"  {r['category']}: {r['brand']} {r['named_change_pts']:+} pts ({r['driven_by_questions']})")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="ask the questions and save answers")
    r.add_argument("--engines", default="chatgpt,gemini,perplexity")
    r.add_argument("--runs", type=int, default=3, help="times to ask each question (answers vary)")
    r.add_argument("--category", help="only one category, e.g. kids_protein")
    r.add_argument("--pause", type=float, default=1.0, help="seconds between calls")
    r.add_argument("--dry-run", action="store_true", help="fake answers, no keys needed")
    r.add_argument("--wave", help="month label, default this month (e.g. 2026-11)")
    s = sub.add_parser("score", help="score saved answers into CSVs")
    s.add_argument("--include-branded", action="store_true", help="include head-to-head questions")
    s.add_argument("--wave", help="which month to score, default the latest")
    qb = sub.add_parser("questions", help="pick the questions from data/question_bank.csv using the rules")
    qb.add_argument("--apply", action="store_true", help="write the kept questions to data/questions.csv")
    qb.add_argument("--category", help="only one category")
    qb.add_argument("--signals", help="folder of last-30-days research files (default data/signals)")
    qb.add_argument("--no-volume", action="store_true", help="skip the Google volume check")
    qb.add_argument("--max-per-stage", type=int, default=MAX_PER_STAGE)
    ib = sub.add_parser("ideas", help="see what shoppers type about a topic (Google autocomplete, India)")
    ib.add_argument("topic", help='e.g. "electric scooter"')
    ev = sub.add_parser("events", help="find what happened to each brand since the last run (Perplexity web search)")
    ev.add_argument("--apply", action="store_true", help="add the kept events to data/events.csv")
    ev.add_argument("--since", help="start date, default the last run's date")
    ev.add_argument("--until", help="end date, default today")
    ev.add_argument("--category", help="only one category")
    ev.add_argument("--all-brands", action="store_true", help="include brands added as 'seen in answers'")
    ev.add_argument("--pause", type=float, default=1.0)
    cp = sub.add_parser("compare", help="what changed between two months, and the evidence")
    cp.add_argument("waves", nargs="*", help="e.g. 2026-10 2026-11; default the last two")
    a = p.parse_args()
    global OUT
    if a.cmd == "run":
        OUT = RESULTS / ("dry-run" if a.dry_run else (a.wave or datetime.now().strftime("%Y-%m")))
    elif a.cmd == "score":
        w = a.wave or (waves()[-1] if waves() else None)
        if not w and (RESULTS / "answers.jsonl").exists():
            OUT = RESULTS  # older single-run layout
        elif not w:
            sys.exit("No answers yet. Run 'python soa.py run' first.")
        else:
            OUT = RESULTS / w
            print(f"Scoring wave {w}", flush=True)
    {"run": cmd_run, "score": cmd_score, "questions": cmd_questions, "ideas": cmd_ideas,
     "compare": cmd_compare, "events": cmd_events}[a.cmd](a)


if __name__ == "__main__":
    main()
