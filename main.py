# pip install google-genai requests
import os, requests, json, xml.etree.ElementTree as ET
from google import genai

# --- CONFIG ---
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

client = genai.Client(api_key=GEMINI_KEY)

def get_papers(query):
    search_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi?db=pubmed&term={query}&retmode=json&retmax=3"
    try:
        res = requests.get(search_url)
        res.raise_for_status() # Check if the website actually loaded
        ids = res.json().get('esearchresult', {}).get('idlist', [])
    except Exception as e:
        print(f"⚠️ Search failed for {query}: {e}")
        return []

    if not ids:
        print(f"ℹ️ No new papers found for {query}.")
        return []
    
    papers = []
    for pmid in ids:
        fetch_url = f"https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id={pmid}&retmode=xml"
        fetch_res = requests.get(fetch_url)
        
        # --- THE SAFETY CHECK ---
        if fetch_res.status_code != 200 or b"<?xml" not in fetch_res.content:
            print(f"⚠️ Could not get XML for PMID {pmid}. Skipping...")
            continue
            
        try:
            root = ET.fromstring(fetch_res.content)
            title_el = root.find(".//ArticleTitle")
            abstract_els = root.findall(".//AbstractText")
            
            title = title_el.text if title_el is not None else "No Title"
            abstract = " ".join([p.text for p in abstract_els if p.text])
            papers.append({"title": title, "abstract": abstract, "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"})
        except ET.ParseError:
            print(f"⚠️ XML Parse Error for PMID {pmid}. Skipping...")
            
    return papers

def run_agent():
    # 1. Read keywords
    with open("keywords.txt", "r") as f:
        queries = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    for query in queries:
        print(f"🔎 Scanning for: {query}")
        papers = get_papers(query)
        
        for paper in papers:
            # 2. Gemini Analysis (using Structured Output for 2026)
            prompt = f"Analyze this study: {paper['title']}. Abstract: {paper['abstract']}. Output JSON: {{'summary': '1-sentence', 'hypothesis': 'grant idea'}}"
            response = client.models.generate_content(
                model="gemini-2.0-flash", 
                contents=prompt,
                config={'response_mime_type': 'application/json'}
            )
            analysis = json.loads(response.text)

            # 3. Push to Notion
            notion_url = "https://api.notion.com/v1/pages"
            headers = {"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": "2022-06-28", "Content-Type": "application/json"}
            payload = {
                "parent": {"database_id": DATABASE_ID},
                "properties": {
                    "Name": {"title": [{"text": {"content": paper['title']}}]},
                    "Summary": {"rich_text": [{"text": {"content": analysis['summary']}}]},
                    "Hypothesis": {"rich_text": [{"text": {"content": analysis['hypothesis']}}]},
                    "Link": {"url": paper['url']}
                }
            }
            requests.post(notion_url, headers=headers, json=payload)

if __name__ == "__main__":
    run_agent()
