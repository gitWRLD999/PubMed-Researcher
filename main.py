# pip install google-generativeai requests
import os
import requests
import json
import xml.etree.ElementTree as ET
import google.generativeai as genai
import time

# --- CONFIG ---
GEMINI_KEY = os.getenv("GEMINI_API_KEY")
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("NOTION_DATABASE_ID")
PUBMED_KEY = os.getenv("PUBMED_API_KEY")

genai.configure(api_key=GEMINI_KEY)

def get_papers(query):
    # Identifying yourself to PubMed prevents 'Silent Blocking'
    base_url = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"
    auth = f"&api_key={PUBMED_KEY}" if PUBMED_KEY else ""

    search_url = f"{base_url}esearch.fcgi?db=pubmed&term={query}&retmode=json&retmax=3{auth}"

    try:
        res = requests.get(search_url)
        res.raise_for_status()
        ids = res.json().get('esearchresult', {}).get('idlist', [])
    except Exception:
        return []

    papers = []
    for pmid in ids:
        # 🕰️ Respect the server: Wait a moment between fetches
        time.sleep(0.3) 

        fetch_url = f"{base_url}efetch.fcgi?db=pubmed&id={pmid}&retmode=xml{auth}"
        fetch_res = requests.get(fetch_url)

        # This checks if we actually got the paper or another error
        if fetch_res.status_code == 200 and b"<?xml" in fetch_res.content:
            try:
                root = ET.fromstring(fetch_res.content)
                title_el = root.find(".//ArticleTitle")
                abstract_els = root.findall(".//AbstractText")

                title = title_el.text if title_el is not None else "No Title"
                abstract = " ".join([p.text for p in abstract_els if p.text is not None])
                papers.append({"title": title, "abstract": abstract, "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"})
            except ET.ParseError:
                print(f"⚠️ Bad XML for {pmid}")
                continue
    return papers

def run_agent():
    # 1. Read keywords
    with open("keywords.txt", "r") as f:
        queries = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    model = genai.GenerativeModel("gemini-3-flash-preview")  # Assuming this is a valid model in 2026

    for query in queries:
        print(f"🔎 Scanning for: {query}")
        papers = get_papers(query)

        for paper in papers:
            # 2. Gemini Analysis (using Structured Output for 2026)
            prompt = f"Analyze this study: {paper['title']}. Abstract: {paper['abstract']}. Output JSON: {{'summary': '1-sentence', 'hypothesis': 'grant idea'}}"
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    response_mime_type="application/json"
                )
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
            notion_res = requests.post(notion_url, headers=headers, json=payload)

            if notion_res.status_code == 200:
                print(f"🚀 Success! '{paper['title'][:30]}...' is now in Notion.")
            else:
                print(f"❌ Notion Error {notion_res.status_code}: {notion_res.text}")

if __name__ == "__main__":
    run_agent()
