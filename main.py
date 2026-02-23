# ============================================================
# Autonomous Research Co-Pilot — Always-On Literature Brain
# ============================================================
# pip install google-genai requests
#
# REQUIRED GITHUB SECRETS:
#   GEMINI_API_KEY, NOTION_TOKEN, NOTION_DATABASE_ID
#   PUBMED_API_KEY (optional but recommended to avoid rate limits)
#
# NOTION DATABASE PROPERTY NAMES (must match exactly):
#   Name          -> Title
#   Date          -> Date
#   Summary       -> Rich Text
#   Methods       -> Rich Text
#   Population    -> Rich Text
#   EffectsSizes  -> Rich Text   <- double-S, matches your Notion column
#   Hypothesis    -> Rich Text
#   Contradicts   -> Rich Text
#   Link          -> URL
#   Query         -> Rich Text
#   Status        -> Select      (options: New, Reviewed, Flagged)
# ============================================================

import os, requests, json, xml.etree.ElementTree as ET, time
from datetime import datetime
from google import genai

# --- CONFIG ---
GEMINI_KEY   = os.getenv("GEMINI_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID  = os.getenv("NOTION_DATABASE_ID")
PUBMED_KEY   = os.getenv("PUBMED_API_KEY")
RESULTS_PER_QUERY = 5   # Papers fetched per keyword

client = genai.Client(api_key=GEMINI_KEY)


# -- Deduplication: query Notion directly -------------------------------------
# GitHub Actions runners are ephemeral -- local files vanish between runs.
# We check Notion itself for existing URLs so we never push duplicates.

def get_existing_pubmed_urls() -> set:
    """Fetch all Link values already in the Notion database."""
    url     = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    headers = {
        "Authorization":  f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type":   "application/json",
    }
    existing_urls = set()
    payload = {"page_size": 100}

    while True:
        res = requests.post(url, headers=headers, json=payload, timeout=15)
        if res.status_code != 200:
            print(f"  Warning: Could not fetch existing Notion pages ({res.status_code})")
            break
        data = res.json()
        for page in data.get("results", []):
            link_prop = page.get("properties", {}).get("Link", {})
            link_val  = link_prop.get("url")
            if link_val:
                existing_urls.add(link_val)
        if data.get("has_more"):
            payload["start_cursor"] = data["next_cursor"]
        else:
            break

    print(f"  Found {len(existing_urls)} existing paper(s) already in Notion.")
    return existing_urls


# -- PubMed fetch -------------------------------------------------------------

def get_papers(query: str, retmax: int = RESULTS_PER_QUERY) -> list:
    base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    auth = f"&api_key={PUBMED_KEY}" if PUBMED_KEY else ""

    search_url = (
        f"{base}esearch.fcgi?db=pubmed&term={requests.utils.quote(query)}"
        f"&retmode=json&retmax={retmax}&sort=date{auth}"
    )
    try:
        res = requests.get(search_url, timeout=15)
        res.raise_for_status()
        ids = res.json().get("esearchresult", {}).get("idlist", [])
    except Exception as e:
        print(f"  PubMed search failed: {e}")
        return []

    papers = []
    for pmid in ids:
        time.sleep(0.35)
        fetch_url = f"{base}efetch.fcgi?db=pubmed&id={pmid}&retmode=xml{auth}"
        r = requests.get(fetch_url, timeout=15)
        if r.status_code != 200 or b"<?xml" not in r.content:
            continue
        try:
            root     = ET.fromstring(r.content)
            title_el = root.find(".//ArticleTitle")
            title    = "".join(title_el.itertext()).strip() if title_el is not None else "No Title"
            if not title:
                title = "No Title"
            abstract = " ".join(
                p.text for p in root.findall(".//AbstractText") if p.text
            )
            year_el  = root.find(".//PubDate/Year")
            month_el = root.find(".//PubDate/Month")
            year     = year_el.text  if year_el  is not None else str(datetime.now().year)
            month    = month_el.text if month_el is not None else "01"
            month_map = {
                "Jan":"01","Feb":"02","Mar":"03","Apr":"04","May":"05","Jun":"06",
                "Jul":"07","Aug":"08","Sep":"09","Oct":"10","Nov":"11","Dec":"12"
            }
            month = month_map.get(month, month.zfill(2))
            papers.append({
                "pmid":     pmid,
                "title":    title.strip(),
                "abstract": abstract.strip(),
                "pub_date": f"{year}-{month}-01",
                "url":      f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
            })
        except ET.ParseError:
            print(f"  Bad XML for PMID {pmid}")
    return papers


# -- Gemini: single-paper structured analysis ---------------------------------

def analyze_paper(paper: dict):
    prompt = f"""You are a biomedical research assistant. Analyze this study and return structured JSON.

Title: {paper['title']}
Abstract: {paper['abstract']}

Return ONLY valid JSON with exactly these keys:
- summary: One clear sentence summarizing the main finding.
- methods: Study design and methods used (e.g. RCT, cohort, n=X, duration).
- population: Who was studied (age range, condition, inclusion criteria).
- effect_sizes: Key quantitative results (OR, HR, p-values, confidence intervals). If not reported, say "Not reported".
- hypothesis: One concrete new research question or grant idea this finding inspires.
"""
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        raw    = json.loads(response.text)
        result = raw[0] if isinstance(raw, list) else raw
        for k in ("summary","methods","population","effect_sizes","hypothesis"):
            if k not in result:
                result[k] = "Not extracted"
        return result
    except Exception as e:
        print(f"  Gemini analysis failed for '{paper['title'][:50]}': {e}")
        return None


# -- Gemini: cross-paper contradiction + hypothesis synthesis -----------------

def synthesize_batch(papers_with_analysis: list) -> dict:
    if len(papers_with_analysis) < 2:
        return {
            "contradictions": "Only one new paper this run — no cross-paper comparison possible.",
            "new_hypotheses": ""
        }

    summaries = "\n\n".join(
        f"[{i+1}] {p['title']} ({p['pub_date'][:4]}): {p['analysis']['summary']}"
        for i, p in enumerate(papers_with_analysis)
    )
    prompt = f"""You are a research synthesis expert reviewing {len(papers_with_analysis)} recent papers on the same topic.

{summaries}

Return ONLY valid JSON with exactly these keys:
- contradictions: Describe any conflicting findings between studies, referencing paper numbers. If none found, say "No direct contradictions detected."
- new_hypotheses: 2-3 novel research hypotheses that emerge from reading these studies together.
"""
    try:
        response = client.models.generate_content(
            model="gemini-2.0-flash-lite",
            contents=prompt,
            config={"response_mime_type": "application/json"},
        )
        raw = json.loads(response.text)
        return raw[0] if isinstance(raw, list) else raw
    except Exception as e:
        print(f"  Synthesis failed: {e}")
        return {"contradictions": "Synthesis unavailable.", "new_hypotheses": ""}


# -- Notion: push one row -----------------------------------------------------

def push_to_notion(paper: dict, analysis: dict, synthesis: dict, query: str) -> bool:
    url     = "https://api.notion.com/v1/pages"
    headers = {
        "Authorization":  f"Bearer {NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type":   "application/json",
    }

    def rt(text: str) -> list:
        # "type" key is required -- omitting it causes Notion to silently drop the field
        return [{"type": "text", "text": {"content": str(text)[:2000]}}]

    contradiction_note = synthesis.get("contradictions", "")
    new_hypotheses     = synthesis.get("new_hypotheses", "")

    full_hypothesis = "\n\n".join(filter(None, [
        analysis.get("hypothesis", ""),
        f"[Cross-paper hypotheses]\n{new_hypotheses}" if new_hypotheses else "",
    ]))

    is_flagged = (
        bool(contradiction_note)
        and "no direct contradictions" not in contradiction_note.lower()
        and "unavailable"              not in contradiction_note.lower()
        and "only one"                 not in contradiction_note.lower()
    )

    payload = {
        "parent": {"database_id": DATABASE_ID},
        "properties": {
            "Name":         {"title":     rt(paper["title"])},
            "Date":         {"date":      {"start": paper["pub_date"]}},
            "Summary":      {"rich_text": rt(analysis.get("summary", ""))},
            "Methods":      {"rich_text": rt(analysis.get("methods", ""))},
            "Population":   {"rich_text": rt(analysis.get("population", ""))},
            "EffectsSizes": {"rich_text": rt(analysis.get("effect_sizes", ""))},
            "Hypothesis":   {"rich_text": rt(full_hypothesis)},
            "Contradicts":  {"rich_text": rt(contradiction_note)},
            "Link":         {"url":       paper["url"]},
            "Query":        {"rich_text": rt(query)},
            "Status":       {"select":    {"name": "Flagged" if is_flagged else "New"}},
        },
    }

    res = requests.post(url, headers=headers, json=payload, timeout=15)
    if res.status_code == 200:
        return True
    else:
        print(f"  Notion error {res.status_code}:")
        try:
            err = res.json()
            print(f"    message: {err.get('message', 'none')}")
            print(f"    code:    {err.get('code', 'none')}")
        except Exception:
            print(f"    raw: {res.text}")
        return False


# -- Main ---------------------------------------------------------------------

def run_scan():
    print(f"\n{'='*60}")
    print(f"Scan started: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*60}")

    # Pull existing Notion URLs once for the whole run — deduplication source of truth
    existing_urls = get_existing_pubmed_urls()

    with open("keywords.txt") as f:
        queries = [l.strip() for l in f if l.strip() and not l.startswith("#")]

    for query in queries:
        print(f"\nQuery: {query}")
        papers = get_papers(query)

        new_papers = [p for p in papers if p["url"] not in existing_urls]
        if not new_papers:
            print("  No new papers since last run.")
            continue
        print(f"  {len(new_papers)} new paper(s) to process.")

        # Step 1: Analyze each paper individually
        analyzed = []
        for paper in new_papers:
            print(f"  Analyzing: {paper['title'][:65]}...")
            analysis = analyze_paper(paper)
            if analysis:
                analyzed.append({**paper, "analysis": analysis})
            time.sleep(1)

        if not analyzed:
            continue

        # Step 2: Cross-paper synthesis
        print(f"  Synthesizing batch of {len(analyzed)} paper(s)...")
        synthesis = synthesize_batch(analyzed)
        print(f"  Contradictions: {synthesis.get('contradictions','')[:100]}")

        # Step 3: Push to Notion
        for entry in analyzed:
            success = push_to_notion(entry, entry["analysis"], synthesis, query)
            if success:
                print(f"  Pushed: '{entry['title'][:55]}...'")
                existing_urls.add(entry["url"])  # prevent cross-query dupes within same run
            time.sleep(0.5)

    print(f"\nScan complete: {datetime.now().strftime('%Y-%m-%d %H:%M')}")


if __name__ == "__main__":
    run_scan()
