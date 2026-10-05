"""Tailor master_cv.html to one JD with a single small Claude call.

Usage: venv/bin/python tailor.py JD_FILE COMPANY OUT_DIR
Env:   AUTO_YES=1  skip the interactive review (testing only)
       EFFORT=low  claude --effort level (default low)

Claude only returns a small JSON edit list; everything else (applying edits,
guard checks, HTML/PDF/MD output) is local and costs no tokens.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from cv_profile import load_profile, tool

HERE = Path(__file__).parent
MASTER_HTML = HERE / "master_cv.html"
USAGE_LOG = HERE / "usage_log.tsv"

SYSTEM_PROMPT = """You tailor a CV to a job description, working FOR the candidate. Goal: maximise truthful match to the JD for ATS and recruiters. Output ONLY the edit JSON; a script applies it.
Input: MASTER CV items tagged [summary], [bN] bullets grouped under role headings, [sN] skills lines; optional CONFIRMED SKILLS the candidate has but never wrote down; the JD; then JD TERMS MISSING FROM CV (computed by script).
Rules:
1. bullets: list the ids to KEEP, in the order they should appear within each role. Omitted ids are cut. Keep 2-5 bullets per role.
2. text: null keeps a bullet verbatim. Otherwise rewrite it in the JD's vocabulary and priorities, keeping every factual claim true to the original: same numbers, same scope, same employer, no tool not in that bullet. Rewrite every bullet that can carry JD language truthfully, not only the obvious ones.
3. Implied skill: true because the work shown could not have been done without it. Generic engineering competencies count (e.g. full-stack SaaS built solo -> software engineering, software development, coding, product design, deployments; RAG chatbot -> embeddings, chunking). Use the JD's exact wording inside the bullet that shows the work, in the summary, and/or add to a skills line: kind=implied, evidence=bullet id.
4. Adjacent skill: JD asks for tool X, CV shows equivalent tool Y (e.g. AWS vs GCP). NEVER put X in any bullet. Skills line only, as "X (equivalent: Y)": kind=adjacent, evidence=bullet id showing Y.
5. CONFIRMED SKILLS are true: use them freely (skills lines, summary), evidence="inventory". Never claim experience beyond them.
6. term_coverage: account for EVERY term in JD TERMS MISSING FROM CV: where = the id (summary, bN, sN) whose new text literally contains that word (a script checks), "gap" if the candidate truly lacks it, or "noise" if it is job-ad boilerplate. Never cover a real gap; list real gaps in gaps[].
7. summary: rewrite toward the role's title and top priorities under the same truth rules, or null to keep.
8. Never add any person name. Write in CV style: no "I", "my" or "me". Never change who work was for (internal vs client vs employer) or upgrade verbs (trained -> led).
9. Reasoning, terse: strategy = one sentence on which JD priorities this CV leads with. why (bullets, summary_why) = max 12 words naming the JD requirement served; null when text is null. cut_reasons = one short line per cut id."""

SCHEMA = {
    "type": "object",
    "properties": {
        "strategy": {"type": "string"},
        "summary": {"type": ["string", "null"]},
        "summary_why": {"type": ["string", "null"]},
        "bullets": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "text": {"type": ["string", "null"]},
                    "why": {"type": ["string", "null"]},
                },
                "required": ["id", "text", "why"],
            },
        },
        "term_coverage": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"term": {"type": "string"}, "where": {"type": "string"}},
                "required": ["term", "where"],
            },
        },
        "cut_reasons": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"id": {"type": "string"}, "why": {"type": "string"}},
                "required": ["id", "why"],
            },
        },
        "skills_additions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "line": {"type": "string"},
                    "add": {"type": "string"},
                    "kind": {"enum": ["implied", "adjacent"]},
                    "evidence": {"type": "string"},
                },
                "required": ["line", "add", "kind", "evidence"],
            },
        },
        "gaps": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["strategy", "summary", "summary_why", "bullets", "term_coverage", "cut_reasons",
                 "skills_additions", "gaps"],
}
INVENTORY = HERE / "skills_inventory.md"

STOPWORDS = set("""a about above across after again all also an and any are as at be because been
before being between both but by can could did do does doing during each either etc few for from
further had has have having he her here how i if in into is it its itself just like may me more most
must my no nor not now of off on once only or other our out over own per same she should so some such
than that the their them then there these they this those through to too under until up us very via
was we were what when where which while who whom why will with within without would you your yours
ability able across closely company day days deliver desire environment excellent experience
experienced fast help high including join looking make new offer opportunity part plus role roles
skills strong team teams things understanding using want way well work working world years
job jobs employment employer employee employees candidate candidates applicant applicants apply
applying application applications benefit benefits salary equal opportunity opportunities directly
adopt adoption ensure across within able responsibilities requirements qualifications preferred
required include includes get going like help helping""".split())


def top_jd_terms(jd_text, top_n=40):
    """The JD's most repeated keyword terms (2+ mentions). Local, no tokens."""
    from collections import Counter
    return [t for t, c in Counter(terms(jd_text)).most_common() if c >= 2][:top_n]


def missing_terms(jd_text, cv_text):
    cv, forms = set(terms(cv_text)), surface_forms(jd_text)
    return [forms.get(t, t) for t in top_jd_terms(jd_text) if t not in cv]


IRREGULAR = {"built": "build", "led": "lead", "ran": "run", "shipped": "ship", "architected": "architect"}


def stem(w):
    """Crude suffix stripping so inflections of a JD term count as a match."""
    if w in IRREGULAR:
        return IRREGULAR[w]
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 4:
            w = w[: -len(suf)]
            break
    return w.rstrip("e") if len(w) > 4 else w


def term_pairs(text):
    """(stemmed term, surface form) for single words + adjacent word pairs, stopwords removed."""
    raw = [w for w in re.findall(r"[a-z][a-z0-9+#./-]*[a-z0-9+#]|[a-z]", text.lower())
           if w not in STOPWORDS and len(w) > 1]
    words = [(stem(w), w) for w in raw]
    return words + [(f"{a[0]} {b[0]}", f"{a[1]} {b[1]}") for a, b in zip(words, words[1:])]


def terms(text):
    return [t for t, _ in term_pairs(text)]


def surface_forms(text):
    forms = {}
    for t, surf in term_pairs(text):
        forms.setdefault(t, surf)
    return forms


def match_report(jd_text, master_text, tailored_text, term_coverage=()):
    """ATS-style keyword overlap with the JD, before vs after. Local, no tokens."""
    from collections import Counter
    jd = Counter(terms(jd_text))
    top = top_jd_terms(jd_text)
    master_terms, tailored_terms = set(terms(master_text)), set(terms(tailored_text))
    verdict = {" ".join(stem(w) for w in c["term"].lower().split()): c["where"] for c in term_coverage}
    shown = surface_forms(jd_text)
    nice = lambda ts: ", ".join(shown.get(t, t) for t in ts) or "(none)"

    def cosine(a, b):
        dot = sum(a[k] * b[k] for k in a)
        na = sum(v * v for v in a.values()) ** 0.5
        nb = sum(v * v for v in b.values()) ** 0.5
        return dot / (na * nb) if na and nb else 0.0

    hit_before = [t for t in top if t in master_terms]
    hit_after = [t for t in top if t in tailored_terms]
    lines = [
        "# JD match report (local keyword check, no AI)",
        f"- Top JD terms (appearing 2+ times): {len(top)}",
        f"- Covered by MASTER CV:   {len(hit_before)}/{len(top)} ({100 * len(hit_before) // max(len(top), 1)}%)",
        f"- Covered by TAILORED CV: {len(hit_after)}/{len(top)} ({100 * len(hit_after) // max(len(top), 1)}%)",
        f"- Cosine similarity to JD: master {cosine(jd, Counter(terms(master_text))):.3f} -> tailored {cosine(jd, Counter(terms(tailored_text))):.3f}",
        "",
        "## Newly covered terms",
        nice([t for t in hit_after if t not in hit_before]),
        "",
    ]
    still = [t for t in top if t not in tailored_terms]
    gaps = [t for t in still if verdict.get(t) == "gap"]
    noise = [t for t in still if verdict.get(t) == "noise"]
    loose = [t for t in still if verdict.get(t) not in ("gap", "noise")]
    lines += [
        "## Still missing - declared real gaps (cover letter / interview)",
        nice(gaps),
        "",
        "## Still missing - judged job-ad boilerplate",
        nice(noise),
        "",
        "## Still missing - NOT accounted for (review these: true of you? add to skills_inventory.md)",
        nice(loose),
    ]
    return "\n".join(lines)


def clean(el):
    return " ".join(el.get_text(" ", strip=True).split())


def compact_master(soup):
    """Numbered plain-text view of the master CV - the only CV text Claude sees."""
    lines = []
    for el in soup.body.find_all(["p", "h1", "h2", "h3", "li"]):
        text = clean(el)
        if el.name in ("h2", "h3"):
            lines.append(f"{'#' * int(el.name[1])} {text}")
        elif el.get("data-id"):
            lines.append(f"[{el['data-id']}] {text}")
    return "\n".join(lines)


def call_claude(user_input):
    effort = os.environ.get("EFFORT", "low")
    # Empty cwd + no tools/MCP/skills/project settings: strips Claude Code's per-call
    # overhead while keeping subscription (OAuth) auth. --bare is avoided on purpose:
    # it only accepts ANTHROPIC_API_KEY, i.e. paid API billing.
    with tempfile.TemporaryDirectory() as empty:
        proc = subprocess.run(
            ["claude", "-p",
             "--system-prompt", SYSTEM_PROMPT,
             "--json-schema", json.dumps(SCHEMA),
             "--tools", "",
             "--strict-mcp-config",
             "--disable-slash-commands",
             "--setting-sources", "local",
             "--no-session-persistence",
             "--effort", effort,
             "--output-format", "json"],
            input=user_input, capture_output=True, text=True, cwd=empty,
        )
    if proc.returncode != 0:
        sys.exit(f"claude failed ({proc.returncode}): {proc.stderr or proc.stdout}")
    resp = json.loads(proc.stdout)
    if resp.get("is_error"):
        sys.exit(f"claude returned an error: {resp.get('result')}")
    edits = resp.get("structured_output")
    if edits is None:
        edits = json.loads(resp["result"])
    return edits, resp.get("usage", {}), effort


def log_usage(company, usage, effort):
    u = usage
    total = (u.get("input_tokens", 0) + u.get("cache_creation_input_tokens", 0)
             + u.get("cache_read_input_tokens", 0) + u.get("output_tokens", 0))
    thinking = u.get("output_tokens_details", {}).get("thinking_tokens", 0)
    new = not USAGE_LOG.exists()
    with USAGE_LOG.open("a") as f:
        if new:
            f.write("timestamp\tcompany\teffort\tinput\tcache_create\tcache_read\toutput\tthinking\ttotal\n")
        f.write(f"{datetime.now():%Y-%m-%d %H:%M}\t{company}\t{effort}\t{u.get('input_tokens', 0)}\t"
                f"{u.get('cache_creation_input_tokens', 0)}\t{u.get('cache_read_input_tokens', 0)}\t"
                f"{u.get('output_tokens', 0)}\t{thinking}\t{total}\n")
    return total, thinking


def numbers(text):
    return set(re.findall(r"\d+(?:[.,/]\d+)*", text))


def guard_warnings(edits, originals):
    """Deterministic fabrication checks - no tokens."""
    warns = []
    adjacent_terms = [s["add"].split("(")[0].strip() for s in edits["skills_additions"] if s["kind"] == "adjacent"]
    for b in edits["bullets"]:
        if not b["text"] or b["id"] not in originals:
            continue
        extra = numbers(b["text"]) - numbers(originals[b["id"]])
        if extra:
            warns.append(f"{b['id']}: new number(s) not in original: {', '.join(sorted(extra))}")
        for term in adjacent_terms:
            if term and term.lower() in b["text"].lower():
                warns.append(f"{b['id']}: adjacent tool '{term}' appears inside a bullet (not allowed)")
    unknown = [b["id"] for b in edits["bullets"] if b["id"] not in originals]
    if unknown:
        warns.append(f"unknown bullet ids ignored: {', '.join(unknown)}")
    return warns


def reasoning_text(edits, originals):
    """Human-readable record of what changed and why - printed at review, saved as reasoning.md."""
    kept = {b["id"] for b in edits["bullets"]}
    cut_why = {c["id"]: c["why"] for c in edits.get("cut_reasons", [])}
    out = [f"STRATEGY: {edits.get('strategy', '(not recorded)')}", "", "=== CUT ==="]
    for bid, text in originals.items():
        if bid not in kept:
            out.append(f"  [{bid}] {text[:110]}")
            out.append(f"        WHY: {cut_why.get(bid, '(not recorded)')}")
    out += ["", "=== REWRITTEN ==="]
    for b in edits["bullets"]:
        if b["text"] and b["id"] in originals:
            out.append(f"  [{b['id']}] WAS: {originals[b['id']]}")
            out.append(f"        NOW: {b['text']}")
            out.append(f"        WHY: {b.get('why') or '(not recorded)'}\n")
    if edits.get("summary"):
        out.append(f"=== SUMMARY ===\n  NOW: {edits['summary']}\n  WHY: {edits.get('summary_why') or '(not recorded)'}\n")
    return "\n".join(out)


def review(edits, originals, soup, warns):
    """Show the edit list; let the user untick skills additions; confirm."""
    print(reasoning_text(edits, originals))
    print("=== GAPS (not added - for cover letter / interview) ===")
    for g in edits["gaps"]:
        print(f"  - {g}")
    if warns:
        print("\n=== GUARD WARNINGS ===")
        for w in warns:
            print(f"  ! {w}")

    adds = edits["skills_additions"]
    print("\n=== SKILLS ADDITIONS (all ticked) ===")
    for i, s in enumerate(adds, 1):
        print(f"  [x] {i}. ({s['kind']}, evidence {s['evidence']}) -> {s['line']}: {s['add']}")

    if os.environ.get("AUTO_YES") == "1":
        return adds
    drop = input("\nNumbers to UNTICK (e.g. 2 4), blank keeps all: ").split()
    adds = [s for i, s in enumerate(adds, 1) if str(i) not in drop]
    if input("Generate PDF? (y/n) ").strip().lower() != "y":
        sys.exit("Stopped - nothing generated.")
    return adds


def apply_edits(soup, edits, adds):
    # Group kept bullets by their <ul>, preserving Claude's order within each role
    by_id = {li["data-id"]: li for li in soup.find_all("li", attrs={"data-id": True})}
    kept_order = [b for b in edits["bullets"] if b["id"] in by_id]
    for ul in soup.find_all("ul"):
        ul_ids = [li["data-id"] for li in ul.find_all("li", recursive=False)]
        wanted = [b for b in kept_order if b["id"] in ul_ids]
        if not wanted:  # never leave a role empty
            continue
        originals = {i: by_id[i] for i in ul_ids}
        for li in list(ul.find_all("li", recursive=False)):
            li.extract()
        for b in wanted:
            li = originals[b["id"]]
            if b["text"]:
                li.clear()
                li.string = b["text"]
            ul.append(li)
    if edits["summary"]:
        s = soup.find(attrs={"data-id": "summary"})
        s.clear()
        s.string = edits["summary"]
    for a in adds:
        line = soup.find(attrs={"data-id": a["line"]})
        if line:
            line.append(f", {a['add']}")


def main():
    args = sys.argv[1:]
    reuse = None
    if "--reuse-edits" in args:  # re-render from a saved edits.json: no Claude call, no tokens
        i = args.index("--reuse-edits")
        reuse = Path(args[i + 1])
        del args[i:i + 2]
    jd_path, company, out_dir = args[0], args[1], Path(args[2])
    out_dir.mkdir(parents=True, exist_ok=True)

    soup = BeautifulSoup(MASTER_HTML.read_text(encoding="utf-8"), "html.parser")
    originals = {li["data-id"]: clean(li) for li in soup.find_all("li", attrs={"data-id": True})}
    master_text = compact_master(soup)
    jd_text = Path(jd_path).read_text(encoding="utf-8")

    if reuse:
        edits = json.loads(reuse.read_text(encoding="utf-8"))
        print(f"Reusing {reuse} - no Claude call, 0 tokens")
    else:
        inventory = "\n".join(l for l in (INVENTORY.read_text(encoding="utf-8").splitlines() if INVENTORY.exists() else [])
                              if l.strip() and not l.lstrip().startswith("#"))
        missing = missing_terms(jd_text, master_text + "\n" + inventory)
        edits, usage, effort = call_claude(
            "=== MASTER CV ===\n" + master_text
            + ("\n\n=== CONFIRMED SKILLS (true, not yet in CV) ===\n" + inventory if inventory else "")
            + "\n\n=== JOB DESCRIPTION ===\n" + jd_text
            + "\n\n=== JD TERMS MISSING FROM CV ===\n" + ", ".join(missing))
        total, thinking = log_usage(company, usage, effort)
        print(f"Tokens this job: {total:,} (thinking {thinking:,}, effort={effort}) - logged to {USAGE_LOG.name}")
    (out_dir / "edits.json").write_text(json.dumps(edits, indent=2), encoding="utf-8")

    warns = guard_warnings(edits, originals)
    adds = review(edits, originals, soup, warns)
    apply_edits(soup, edits, adds)

    (out_dir / "reasoning.md").write_text(reasoning_text(edits, originals) + "\n\n=== GAPS ===\n"
                                          + "\n".join(f"- {g}" for g in edits["gaps"]), encoding="utf-8")
    report = match_report(jd_text, master_text, compact_master(soup), edits.get("term_coverage", []))
    (out_dir / "match_report.md").write_text(report, encoding="utf-8")
    print("\n" + "\n".join(report.splitlines()[1:5]))

    html_out = out_dir / f"cv_{company}.html"
    html_out.write_text(str(soup), encoding="utf-8")
    pdf_out = out_dir / f"{load_profile()['pdf_name']}_{company}.pdf"
    subprocess.run([tool("weasyprint"), str(html_out), str(pdf_out),
                    "--base-url", f"{HERE}/"], check=True)
    body = str(soup.body)
    md = subprocess.run([tool("pandoc"), "-f", "html", "-t", "gfm"],
                        input=body, capture_output=True, text=True, check=True).stdout
    (out_dir / f"cv_{company}.md").write_text(md, encoding="utf-8")
    print(f"Saved: {pdf_out}")


if __name__ == "__main__":
    main()
