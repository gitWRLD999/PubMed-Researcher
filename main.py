# ============================================================
# 🧠 Autonomous Research Co-Pilot — Always-On Literature Brain
# ============================================================
# pip install google-genai requests schedule
#
# REQUIRED ENV VARS:
#   GEMINI_API_KEY, NOTION_TOKEN, NOTION_DATABASE_ID
#   PUBMED_API_KEY (optional but recommended)
#
# NOTION DATABASE MUST HAVE THESE PROPERTIES:
#   Name         → Title
#   Summary      → Rich Text
#   Methods      → Rich Text
#   Population   → Rich Text
#   EffectSizes  → Rich Text
#   Hypothesis   → Rich Text
#   Contradicts  → Rich Text
#   Link         → URL
#   Query        → Rich Text
#   Status       → Select  (options: "New", "Reviewed", "Flagged")
# ============================================================

import os, requests, json, xml.etree.ElementTree as ET, time, hashlib, schedule
from pathlib import Path
from datetime import datetime
from google import genai

# --- CONFIG ---
GEMINI_KEY   = os.getenv("GEMINI_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID  = os.getenv("NOTION_DATABASE_ID")
PUBMED_KEY   = os.getenv("PUBMED_API_KEY")
SCAN_INTERVAL_MINUTES = 60          # How often to re-scan (set to 1440 for daily)
RESULTS_PER_QUERY     = 5           # Papers fetched per keyword
SEEN_PAPERS_FILE      = "seen_pmids.json"   # Tracks papers already processed

client = genai.Client(api_key=GEMINI_KEY)


# ── Persistence: remember which PMIDs we've already pushed ──────────────────

def load_seen() -> set:
    if Path(SEEN_PAPERS_FILE).exists():
        with open(SEEN_PAPERS_FILE) as f:
            return set(json.load(f))
    return set()

def save_seen(seen: set):
    with open(SEEN_PAPERS_FILE, "w") as f:
        json.dump(list(seen), f)


# ── PubMed fetch ─────────────────────────────────────────────────────────────

def get_papers(query: str, retmax: int = RESULTS_PER_QUERY) -> list[dict]:
    base  = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    auth  = f"&api_key={PUBMED_KEY}" if PUBMED_KEY else ""
    # Sort by "most recent" so we catch new publications first
    search_url = (
        f"{base}esearch.fcgi?db=pubmed&term={requests.utils.quote(query)}"
        f"&retmode=json&retmax={retmax}&sort=date{auth}"
    )

    try:
        res = requests.get(search_url, timeout=15)
        res.raise_for_status()
        ids = res.json().get("esearchresult", {}).get("idlist", [])
    except Exception as e:
        print(f"  ⚠️  PubMed search failed: {e}")
        return []

    papers = []
    for pmid in ids:
        time.sleep(0.35)   # Respect NCBI rate limits
        fetch_url = f"{base}efetch.fcgi?db=pubmed&id={pmid}&retmode=xml{auth}"
        r = requests.get(fetch_url, timeout=15)
        if r.status_code != 200 or b"<?xml" not in r.content:
            continue
        try:
            root  = ET.fromstring(r.content)
            title = (root.find(".//ArticleTitle") or ET.Element("x")).text or "No Title"
            # Collect all abstract sections (structured abstracts have multiple)
            abstract = " ".join(
                p.text for p in root.findall(".//AbstractText") if p.text
            )
            pub_date_el = root.find(".//PubDate/Year")
            year = pub_date_el.text if pub_date_el is not None else "?"
            papers.append({
                "pmid":     pmid,
                "title":    title.strip(),
                "abstract": abstract.strip(),
                "year":     year,
                "url":      f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            })
        except ET.ParseError:
            print(f"  ⚠️  Bad XML for PMID {pmid}")
    return papers


# ── Gemini: structured single-paper analysis ─────────────────────────────────

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary":      {"type": "string"},
        "methods":      {"type": "string"},
        "population":   {"type": "string"},
        "effect_sizes": {"type": "string"},
        "hypothesis":   {"type": "string"},
    },
    "required": ["summary", "methods", "population", "effect_sizes", "hypothesis"]
}

def analyze_paper(paper: dict) -> dict | None:
    prompt = f"""You are a biomedical research assistant. Analyze this study and return structured JSON.

Title: {paper['title']}
Abstract: {paper['abstract']}

Return ONLY valid JSON with these exact keys:
- summary: One clear sentence summarizing the main finding.
- methods: The study design / methods used (e.g. RCT, cohort, n=X, duration).
- population: Who was studied (age, condition, inclusion criteria).
- effect_sizes: Key quantitative results (OR, HR, p-values, confidence intervals). If absent, state "Not reported".
- hypothesis: One concrete new research question or grant idea this finding inspires.
"""
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        raw = json.loads(response.text)
        # Handle list-wrapped responses
        result = raw[0] if isinstance(raw, list) else raw
        # Validate expected keys came back — surface any Gemini schema drift early
        expected_keys = {"summary", "methods", "population", "effect_sizes", "hypothesis"}
        missing_keys  = expected_keys - set(result.keys())
        if missing_keys:
            print(f"  ⚠️  Gemini returned incomplete JSON — missing: {missing_keys}")
            print(f"      Got keys: {list(result.keys())}")
        return result
    except Exception as e:
        print(f"  Gemini analysis failed for '{paper['title'][:40]}': {e}")
        if hasattr(e, '__context__'):
            print(f"      raw response: {response.text[:300]}")
        return None


# ── Gemini: contradiction + cross-paper hypothesis synthesis ─────────────────

def synthesize_batch(papers_with_analysis: list[dict]) -> dict:
    """Given a batch of papers from ONE query, detect contradictions and suggest overarching hypotheses."""
    if len(papers_with_analysis) < 2:
        return {"contradictions": "N/A — only one paper in batch.", "new_hypotheses": ""}

    summaries = "\n\n".join(
        f"[{i+1}] {p['title']} ({p['year']}): {p['analysis']['summary']}"
        for i, p in enumerate(papers_with_analysis)
    )
    prompt = f"""You are a research synthesis expert. Below are summaries from {len(papers_with_analysis)} recent papers on the same topic.

{summaries}

Return ONLY valid JSON with:
- contradictions: Describe any conflicting findings between studies (mention paper numbers). If none, say "No direct contradictions detected."
- new_hypotheses: 2–3 novel research hypotheses that emerge from reading these studies together.
"""
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        raw = json.loads(response.text)
        return raw[0] if isinstance(raw, list) else raw
    except Exception as e:
        print(f"  ⚠️  Synthesis failed: {e}")
        return {"contradictions": "Synthesis error.", "new_hypotheses": ""}


# ── Notion: schema check (run once at startup) ────────────────────────────────

def check_notion_schema():
    """Fetches your Notion database and prints all property names + types.
    Run this first to confirm your property names match exactly."""
    url     = f"https://api.notion.com/v1/databases/{DATABASE_ID}"
    headers = {
        "Authorization":  f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
    }
    res = requests.get(url, headers=headers, timeout=15)
    if res.status_code != 200:
        print(f"  ❌ Could not fetch schema: {res.status_code} — {res.text}")
        return

    props = res.json().get("properties", {})
    print("\n📋 Notion database properties found:")
    for name, meta in props.items():
        print(f"   '{name}' → {meta['type']}")

    # Warn about any expected properties that are missing
    expected = {"Name", "Summary", "Methods", "Population", "EffectSizes",
                "Hypothesis", "Contradicts", "Link", "Query", "Status"}
    missing = expected - set(props.keys())
    if missing:
        print(f"\n  ⚠️  MISSING properties (create these in Notion): {missing}")
        print("  ℹ️  Property names are case-sensitive. Update the payload dict in")
        print("      push_to_notion() to match your exact Notion column names if different.\n")
    else:
        print("\n  ✅ All expected properties found.\n")


# ── Notion: push one row ──────────────────────────────────────────────────────

def push_to_notion(paper: dict, analysis: dict, synthesis: dict, query: str) -> bool:
    url     = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization":  f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type":   "application/json",
    }

    def rt(text: str) -> list:
        # Notion rich_text blocks — "type" is required or fields are silently dropped
        return [{"type": "text", "text": {"content": str(text)[:2000]}}]

    # Build the contradiction note: only include if the paper is mentioned
    contradiction_note = synthesis.get("contradictions", "")
    new_hypotheses     = synthesis.get("new_hypotheses", "")

    # Combine cross-paper hypothesis with paper-specific hypothesis
    full_hypothesis = "\n\n".join(filter(None, [
        analysis.get("hypothesis", ""),
        f"[Cross-paper] {new_hypotheses}" if new_hypotheses else "",
    ]))

    payload = {
        "parent": {"database_id": DATABASE_ID},
        "properties": {
            "Name":        {"title":     rt(paper["title"])},
            "Summary":     {"rich_text": rt(analysis.get("summary", ""))},
            "Methods":     {"rich_text": rt(analysis.get("methods", ""))},
            "Population":  {"rich_text": rt(analysis.get("population", ""))},
            "EffectSizes": {"rich_text": rt(analysis.get("effect_sizes", ""))},
            "Hypothesis":  {"rich_text": rt(full_hypothesis)},
            "Contradicts": {"rich_text": rt(contradiction_note)},
            "Link":        {"url":       paper["url"]},
            "Query":       {"rich_text": rt(query)},
            "Status":      {
                "select": {
                    "name": "Flagged" if "contradiction" in contradiction_note.lower()
                            and paper["title"][:10] in contradiction_note
                            else "New"
                }
            },
        },
    }

    # Log fields being sent so name mismatches are immediately obvious
    print(f"  Sending fields: {list(payload['properties'].keys())}")

    res = requests.post(url, headers=headers, json=payload, timeout=15)
    if res.status_code == 200:
        return True
    else:
        # Print full error so we can actually see what Notion rejects
        print(f"  Notion error {res.status_code}:")
        try:
            err = res.json()
            print(f"     message: {err.get('message', 'no message')}")
            print(f"     code:    {err.get('code', 'no code')}")
        except Exception:
            print(f"     raw: {res.text}")
        return False


# ── Main scan cycle ───────────────────────────────────────────────────────────

def run_scan():
    print(f"\n{'='*60}")
    print(f"🔬 Scan started — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    seen = load_seen()

    with open("keywords.txt") as f:
        queries = [l.strip() for l in f if l.strip() and not l.startswith("#")]

    for query in queries:
        print(f"\n🔎 Query: {query}")
        papers = get_papers(query)

        # Filter out already-processed papers
        new_papers = [p for p in papers if p["pmid"] not in seen]
        if not new_papers:
            print("  ✅ No new papers since last scan.")
            continue

        print(f"  📄 {len(new_papers)} new paper(s) found.")

        # Step 1: Analyze each paper individually
        analyzed = []
        for paper in new_papers:
            print(f"  🤖 Analyzing: {paper['title'][:60]}…")
            analysis = analyze_paper(paper)
            if analysis:
                analyzed.append({**paper, "analysis": analysis})
            time.sleep(1)   # Avoid Gemini rate limits

        if not analyzed:
            continue

        # Step 2: Cross-paper synthesis (contradictions + new hypotheses)
        print(f"  🔗 Synthesizing batch of {len(analyzed)} paper(s)…")
        synthesis = synthesize_batch(analyzed)
        if synthesis.get("contradictions", "") not in ("N/A — only one paper in batch.", ""):
            print(f"  ⚡ Contradiction note: {synthesis['contradictions'][:120]}…")

        # Step 3: Push each paper to Notion
        for entry in analyzed:
            success = push_to_notion(entry, entry["analysis"], synthesis, query)
            if success:
                print(f"  🚀 Pushed: '{entry['title'][:50]}…'")
                seen.add(entry["pmid"])
            time.sleep(0.5)

    save_seen(seen)
    print(f"\n✅ Scan complete. Next scan in {SCAN_INTERVAL_MINUTES} min.")


# ── Scheduler ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Research Co-Pilot starting up...")
    # Validate Notion property names before doing any work
    check_notion_schema()
    run_scan()   # Run immediately on launch

    schedule.every(SCAN_INTERVAL_MINUTES).minutes.do(run_scan)

    while True:
        schedule.run_pending()
        time.sleep(30)
