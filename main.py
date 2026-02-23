# pip install google-genai requests
import os, requests, json, xml.etree.ElementTree as ET, time, re
from datetime import datetime
from google import genai

# --- CONFIG ---
GEMINI_KEY   = os.getenv("GEMINI_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID  = os.getenv("NOTION_DATABASE_ID")
PUBMED_KEY   = os.getenv("PUBMED_API_KEY")

# Initialize Gemini Client
client = genai.Client(api_key=GEMINI_KEY)
MODEL_NAME = "gemini-2.0-flash" # High-speed stable version

def clean_json_response(text):
    """Strips markdown code blocks from AI response to ensure valid JSON parsing."""
    text = re.sub(r'^```json\s*', '', text, flags=re.MULTILINE)
    text = re.sub(r'```$', '', text, flags=re.MULTILINE)
    return text.strip()

# ── Step 1: Pull existing Notion URLs to prevent duplicates ───────────────────

def get_existing_urls():
    url     = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    headers = {
        "Authorization": f"Bearer {NOTION_TOKEN}", 
        "Notion-Version": "2022-06-28", 
        "Content-Type": "application/json"
    }
    existing = set()
    payload  = {"page_size": 100}
    try:
        while True:
            res  = requests.post(url, headers=headers, json=payload)
            res.raise_for_status()
            data = res.json()
            for page in data.get("results", []):
                link = page.get("properties", {}).get("Link", {}).get("url")
                if link:
                    existing.add(link)
            if data.get("has_more"):
                payload["start_cursor"] = data["next_cursor"]
            else:
                break
    except Exception as e:
        print(f"⚠️ Warning: Could not fetch existing URLs ({e}). Proceeding anyway.")
    
    print(f"✅ Found {len(existing)} existing paper(s) in Notion.")
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
    except Exception as e:
        print(f"❌ PubMed Search Error: {e}")
        return []

    papers = []
    for pmid in ids:
        time.sleep(0.3) # Respect NCBI rate limits
        fetch_url = f"{base_url}efetch.fcgi?db=pubmed&id={pmid}&retmode=xml{auth}"
        fetch_res = requests.get(fetch_url)
        if fetch_res.status_code == 200:
            try:
                root         = ET.fromstring(fetch_res.content)
                title_el     = root.find(".//ArticleTitle")
                abstract_els = root.findall(".//AbstractText")
                title        = title_el.text if title_el is not None else "No Title"
                abstract     = " ".join([p.text for p in abstract_els if p.text])

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
            except Exception as e:
                print(f"⚠️ XML Parsing Error for {pmid}: {e}")
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
        model=MODEL_NAME,
        contents=prompt,
        config={'response_mime_type': 'application/json'}
    )
    
    cleaned_text = clean_json_response(response.text)
    result = json.loads(cleaned_text)
    
    if isinstance(result, list): result = result[0]
    
    # Defaults for Notion safety
    for k in ("summary", "methods", "population", "effect_sizes", "hypothesis"):
        if k not in result: result[k] = "Not extracted"
    return result

# ── Step 4: Batch Synthesis ──────────────────────────────────────────────────

def synthesize_batch(analyzed_papers):
    if len(analyzed_papers) < 2:
        return {"contradictions": "No contradictions: only one paper analyzed.", "new_hypotheses": "N/A"}

    summaries = "\n\n".join(
        f"[{i+1}] {p['title']}: {p['analysis']['summary']}"
        for i, p in enumerate(analyzed_papers)
    )
    prompt = f"""Compare these {len(analyzed_papers)} papers and return JSON:
- contradictions: Look for conflicting findings. If none, say 'No direct contradictions.'
- new_hypotheses: 2 novel research questions arising from the overlap of these specific studies.

{summaries}"""

    response = client.models.generate_content(
        model=MODEL_NAME,
        contents=prompt,
        config={'response_mime_type': 'application/json'}
    )
    
    cleaned_text = clean_json_response(response.text)
    result = json.loads(cleaned_text)
    return result[0] if isinstance(result, list) else result

# ── Step 5: Push to Notion ───────────────────────────────────────────────────

def push_to_notion(paper, analysis, synthesis, query):
    notion_url = "https://api.notion.com/v1/pages"
    headers    = {
        "Authorization": f"Bearer {NOTION_TOKEN}", 
        "Notion-Version": "2022-06-28", 
        "Content-Type": "application/json"
    }

    contradiction_note = synthesis.get("contradictions", "")
    new_hypotheses     = synthesis.get("new_hypotheses", "")
    full_hypothesis    = f"{analysis.get('hypothesis', '')}\n\n[Batch Synthesis]\n{new_hypotheses}"

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
            "Status":       {"select":    {"name": "New"}},
        }
    }

    res = requests.post(notion_url, headers=headers, json=payload)
    if res.status_code == 200:
        print(f"  ✅ Pushed: {paper['title'][:40]}...")
        return True
    else:
        # Crucial Debugging Step:
        error_data = res.json()
        print(f"  ❌ Notion Error {res.status_code}: {error_data.get('message')}")
        if "properties" in str(error_data):
            print("     💡 Check your Notion Column names! (e.g., 'EffectsSizes' vs 'Effect Sizes')")
        return False

# ── Main ──────────────────────────────────────────────────────────────────────

def run_agent():
    print(f"\n🚀 RESEARCH SCAN STARTED: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    
    existing_urls = get_existing_urls()

    if not os.path.exists("keywords.txt"):
        print("❌ Error: keywords.txt not found.")
        return

    with open("keywords.txt", "r") as f:
        queries = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    for query in queries:
        print(f"\n🔍 Querying: {query}")
        papers     = get_papers(query)
        new_papers = [p for p in papers if p["url"] not in existing_urls]

        if not new_papers:
            print("   No new papers found.")
            continue
        
        print(f"   Found {len(new_papers)} new paper(s).")

        analyzed = []
        for paper in new_papers:
            try:
                print(f"   🤖 Analyzing: {paper['title'][:50]}...")
                analysis = analyze_paper(paper)
                analyzed.append({**paper, "analysis": analysis})
            except Exception as e:
                print(f"   ⚠️ Gemini failed for this paper: {e}")
            time.sleep(1)

        if not analyzed: continue

        print(f"   🧠 Synthesizing batch...")
        try:
            synthesis = synthesize_batch(analyzed)
        except:
            synthesis = {"contradictions": "Synthesis unavailable.", "new_hypotheses": ""}

        for entry in analyzed:
            success = push_to_notion(entry, entry["analysis"], synthesis, query)
            if success:
                existing_urls.add(entry["url"])
            time.sleep(0.5)

    print(f"\n🏁 SCAN COMPLETE: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

if __name__ == "__main__":
    run_agent()


if __name__ == "__main__":
    run_agent()
