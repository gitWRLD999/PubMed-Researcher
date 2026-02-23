# pip install google-genai requests
import os, requests, json, xml.etree.ElementTree as ET, time
from datetime import datetime
from google import genai

# --- CONFIG ---
GEMINI_KEY   = os.getenv("GEMINI_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID  = os.getenv("NOTION_DATABASE_ID")
PUBMED_KEY   = os.getenv("PUBMED_API_KEY")

client = genai.Client(api_key=GEMINI_KEY)


# ── Step 1: Pull existing Notion URLs to prevent duplicates ───────────────────
# GitHub Actions runners are ephemeral — local files vanish between runs.
# So we query Notion itself as the source of truth for what's already been pushed.

def get_existing_urls():
    url     = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    headers = {"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"}
    existing = set()
    payload  = {"page_size": 100}
    while True:
        res  = requests.post(url, headers=headers, json=payload)
        data = res.json()
        for page in data.get("results", []):
            link = page.get("properties", {}).get("Link", {}).get("url")
            if link:
                existing.add(link)
        if data.get("has_more"):
            payload["start_cursor"] = data["next_cursor"]
        else:
            break
    print(f"  Found {len(existing)} existing paper(s) in Notion.")
    return existing


# ── Step 2: Fetch papers from PubMed ─────────────────────────────────────────

def get_papers(query):
    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    auth     = f"&api_key={PUBMED_KEY}" if PUBMED_KEY else ""
    search_url = f"{base_url}esearch.fcgi?db=pubmed&term={query}&retmode=json&retmax=5&sort=date{auth}"

    try:
        res = requests.get(search_url)
        res.raise_for_status()
        ids = res.json().get('esearchresult', {}).get('idlist', [])
    except Exception:
        return []

    papers = []
    for pmid in ids:
        time.sleep(0.3)
        fetch_url = f"{base_url}efetch.fcgi?db=pubmed&id={pmid}&retmode=xml{auth}"
        fetch_res = requests.get(fetch_url)
        if fetch_res.status_code == 200 and b"<?xml" in fetch_res.content:
            try:
                root         = ET.fromstring(fetch_res.content)
                title_el     = root.find(".//ArticleTitle")
                abstract_els = root.findall(".//AbstractText")
                title        = title_el.text if title_el is not None else "No Title"
                abstract     = " ".join([p.text for p in abstract_els if p.text])

                # Pull publication date for the Notion Date field
                year_el  = root.find(".//PubDate/Year")
                month_el = root.find(".//PubDate/Month")
                year     = year_el.text if year_el is not None else str(datetime.now().year)
                month    = month_el.text if month_el is not None else "01"
                month_map = {"Jan":"01","Feb":"02","Mar":"03","Apr":"04","May":"05","Jun":"06",
                             "Jul":"07","Aug":"08","Sep":"09","Oct":"10","Nov":"11","Dec":"12"}
                month = month_map.get(month, month.zfill(2))

                papers.append({
                    "title":    title,
                    "abstract": abstract,
                    "pub_date": f"{year}-{month}-01",
                    "url":      f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
                })
            except ET.ParseError:
                print(f"  Bad XML for {pmid}")
                continue
    return papers


# ── Step 3: Analyze a single paper with Gemini ───────────────────────────────

def analyze_paper(paper):
    prompt = f"""Analyze this study and return JSON with exactly these keys:
- summary: one sentence main finding
- methods: study design, sample size, duration
- population: who was studied (age, condition, etc.)
- effect_sizes: key stats like OR, HR, p-values. If absent say 'Not reported'
- hypothesis: one concrete new research question this inspires

Title: {paper['title']}
Abstract: {paper['abstract']}"""

    response = client.models.generate_content(
        model="gemini-2.5-flash-preview-04-17",
        contents=prompt,
        config={'response_mime_type': 'application/json'}
    )
    raw = json.loads(response.text)
    result = raw[0] if isinstance(raw, list) else raw
    # Fill any missing keys so Notion never gets an empty value
    for k in ("summary", "methods", "population", "effect_sizes", "hypothesis"):
        if k not in result:
            result[k] = "Not extracted"
    return result


# ── Step 4: Cross-paper synthesis — contradictions + new hypotheses ───────────

def synthesize_batch(analyzed_papers):
    if len(analyzed_papers) < 2:
        return {"contradictions": "Only one paper this run.", "new_hypotheses": ""}

    summaries = "\n\n".join(
        f"[{i+1}] {p['title']}: {p['analysis']['summary']}"
        for i, p in enumerate(analyzed_papers)
    )
    prompt = f"""You are a research synthesis expert. Review these {len(analyzed_papers)} papers:

{summaries}

Return JSON with exactly these keys:
- contradictions: conflicting findings between papers (cite paper numbers). If none, say 'No direct contradictions detected.'
- new_hypotheses: 2-3 novel research hypotheses that emerge from reading these together."""

    response = client.models.generate_content(
        model="gemini-2.5-flash-preview-04-17",
        contents=prompt,
        config={'response_mime_type': 'application/json'}
    )
    raw = json.loads(response.text)
    return raw[0] if isinstance(raw, list) else raw


# ── Step 5: Push one paper to Notion ─────────────────────────────────────────

def push_to_notion(paper, analysis, synthesis, query):
    notion_url = "https://api.notion.com/v1/pages"
    headers    = {"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"}

    contradiction_note = synthesis.get("contradictions", "")
    new_hypotheses     = synthesis.get("new_hypotheses", "")

    full_hypothesis = analysis.get("hypothesis", "")
    if new_hypotheses:
        full_hypothesis += f"\n\n[Cross-paper]\n{new_hypotheses}"

    is_flagged = (
        bool(contradiction_note)
        and "no direct contradictions" not in contradiction_note.lower()
        and "only one paper"           not in contradiction_note.lower()
    )

    def rt(text):
        return [{"text": {"content": str(text)[:2000]}}]

    payload = {
        "parent": {"database_id": DATABASE_ID},
        "properties": {
            "Name":         {"title":     [{"text": {"content": paper['title']}}]},
            "Date":         {"date":      {"start": paper["pub_date"]}},
            "Summary":      {"rich_text": rt(analysis.get("summary", ""))},
            "Methods":      {"rich_text": rt(analysis.get("methods", ""))},
            "Population":   {"rich_text": rt(analysis.get("population", ""))},
            "EffectsSizes": {"rich_text": rt(analysis.get("effect_sizes", ""))},
            "Hypothesis":   {"rich_text": rt(full_hypothesis)},
            "Contradicts":  {"rich_text": rt(contradiction_note)},
            "Link":         {"url":       paper['url']},
            "Query":        {"rich_text": rt(query)},
            "Status":       {"select":    {"name": "Flagged" if is_flagged else "New"}},
        }
    }

    notion_res = requests.post(notion_url, headers=headers, json=payload)
    if notion_res.status_code == 200:
        print(f"  Pushed: '{paper['title'][:50]}...'")
        return True
    else:
        print(f"  Notion Error {notion_res.status_code}: {notion_res.json().get('message', notion_res.text)}")
        return False


# ── Main ──────────────────────────────────────────────────────────────────────

def run_agent():
    print(f"\n{'='*55}")
    print(f"Scan started: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'='*55}")

    existing_urls = get_existing_urls()

    with open("keywords.txt", "r") as f:
        queries = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    for query in queries:
        print(f"\nQuery: {query}")
        papers    = get_papers(query)
        new_papers = [p for p in papers if p["url"] not in existing_urls]

        if not new_papers:
            print("  No new papers since last run.")
            continue
        print(f"  {len(new_papers)} new paper(s) to process.")

        # Analyze each paper individually
        analyzed = []
        for paper in new_papers:
            print(f"  Analyzing: {paper['title'][:60]}...")
            try:
                analysis = analyze_paper(paper)
                analyzed.append({**paper, "analysis": analysis})
            except Exception as e:
                print(f"  Gemini failed: {e}")
            time.sleep(1)

        if not analyzed:
            continue

        # Cross-paper synthesis for this query's batch
        print(f"  Synthesizing {len(analyzed)} paper(s)...")
        try:
            synthesis = synthesize_batch(analyzed)
            print(f"  Contradictions: {synthesis.get('contradictions','')[:80]}")
        except Exception as e:
            print(f"  Synthesis failed: {e}")
            synthesis = {"contradictions": "", "new_hypotheses": ""}

        # Push each paper to Notion
        for entry in analyzed:
            success = push_to_notion(entry, entry["analysis"], synthesis, query)
            if success:
                existing_urls.add(entry["url"])  # block cross-query dupes in same run
            time.sleep(0.5)

    print(f"\nScan complete: {datetime.now().strftime('%Y-%m-%d %H:%M')}")


if __name__ == "__main__":
    run_agent()
