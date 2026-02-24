# 🔬 PubMed Research Automator

> Automated research paper discovery, AI-powered analysis, and intelligent synthesis — delivered straight to your Notion database.

An intelligent agent that monitors PubMed for new research papers, analyzes them using Google's Gemini AI, identifies contradictions across studies, and generates novel research hypotheses.

---

## ✨ Features

- **🎯 GitHub Actions UI** — Run searches with custom keywords directly from GitHub (no code commits needed!)
- **🔍 Automated PubMed Searches** — Query multiple research topics from a simple keyword file
- **🤖 AI-Powered Analysis** — Extract summaries, methods, populations, effect sizes, and hypothesis using Gemini
- **🧠 Cross-Study Synthesis** — Identify contradictions and generate novel research questions across papers
- **📊 Notion Integration** — Automatically populate a structured database with analyzed papers
- **🚫 Duplicate Prevention** — Track existing papers to avoid redundant entries
- **⚡ Rate-Limited & Respectful** — Built-in delays to respect API limits
- **⏰ Scheduled Runs** — Automatically search for new papers every Monday

---

## 🛠️ Prerequisites

- **Python 3.8+**
- **Notion Account** with API access
- **Google Gemini API Key** ([Get one here](https://ai.google.dev/))
- **PubMed API Key** (optional, but recommended for higher rate limits)

---

## 📦 Installation

### 1. Clone the Repository

```bash
git clone https://github.com/yourusername/pubmed-research-automator.git
cd pubmed-research-automator
```

### 2. Install Dependencies

```bash
pip install google-genai requests
```

### 3. Set Environment Variables

Create a `.env` file or export these variables:

```bash
export GEMINI_API_KEY="your_gemini_api_key"
export NOTION_TOKEN="your_notion_integration_token"
export NOTION_DATABASE_ID="your_database_id"
export PUBMED_API_KEY="your_pubmed_api_key"  # Optional but recommended
```

---

## 🔧 Configuration

### Notion Database Setup

Create a Notion database with the following properties:

| Property Name | Property Type | Description |
|---------------|---------------|-------------|
| `Name` | Title | Paper title |
| `Date` | Date | Publication date |
| `Summary` | Rich Text | One-sentence main finding |
| `Methods` | Rich Text | Study design & sample info |
| `Population` | Rich Text | Study participants |
| `EffectsSizes` | Rich Text | Statistical measures |
| `Hypothesis` | Rich Text | Generated research questions |
| `Contradicts` | Rich Text | Cross-study contradictions |
| `Link` | URL | PubMed link |

**⚠️ Important:** Property names are case-sensitive and must match exactly!

### Get Your Notion Integration Token

1. Go to [Notion Integrations](https://www.notion.so/my-integrations)
2. Create a new integration
3. Copy the "Internal Integration Token"
4. Share your database with the integration

### Create `keywords.txt`

Add your research queries (one per line):

```txt
# Example keywords
machine learning healthcare
CRISPR gene therapy
alzheimer's biomarkers
microbiome mental health
```

Lines starting with `#` are ignored.

**Note:** This file is used for scheduled runs. For manual runs, you can input keywords directly via GitHub Actions UI!

### GitHub Actions Setup (Optional but Recommended)

1. **Copy the workflow file** to `.github/workflows/research-copilot.yml` in your repo
2. **Add secrets** in GitHub:
   - Go to your repo → Settings → Secrets and variables → Actions
   - Add these secrets:
     - `GEMINI_API_KEY`
     - `NOTION_TOKEN`
     - `NOTION_DATABASE_ID`
     - `PUBMED_API_KEY` (optional)

3. **Enable GitHub Actions**:
   - Go to Actions tab
   - Enable workflows if prompted

Now you can run searches from GitHub's UI without committing code changes!

---

## 🚀 Usage

### Option 1: Run via GitHub Actions (Recommended)

**Manual Run with Custom Keywords:**

1. Go to your repository on GitHub
2. Click **Actions** tab
3. Select **Research Co-Pilot** workflow
4. Click **Run workflow** button
5. Enter your keywords in the text box (separate multiple with semicolons):
   ```
   CRISPR gene therapy; alzheimer biomarkers; microbiome mental health
   ```
6. Click **Run workflow**

**Scheduled Run:**

The workflow automatically runs every Monday at 9 AM UTC using keywords from `keywords.txt`.

### Option 2: Run Locally

```bash
python main.py
```

**With custom keywords via environment variable:**

```bash
export SEARCH_KEYWORDS="machine learning healthcare; CRISPR therapy"
python main.py
```

### What Happens:

1. **Fetches existing papers** from your Notion database
2. **Searches PubMed** for each keyword (from GitHub input, environment variable, or `keywords.txt`)
3. **Analyzes each new paper** with Gemini AI
4. **Synthesizes findings** across the batch
5. **Pushes results** to Notion

### Example Output

```
🚀 RESEARCH SCAN STARTED: 2026-02-24 14:30

✅ Found 12 existing paper(s) in Notion.

🔍 Querying: CRISPR gene therapy
   Found 3 new paper(s).
   🤖 Analyzing: CRISPR-Cas9 Mediated Gene Correction in Sickle Cell...
   🤖 Analyzing: Off-Target Effects of Base Editing in Human Embryos...
   🤖 Analyzing: Prime Editing Enables Precise Genome Editing in vivo...
   🧠 Synthesizing batch...
  ✅ Pushed: CRISPR-Cas9 Mediated Gene Correction...
  ✅ Pushed: Off-Target Effects of Base Editing...
  ✅ Pushed: Prime Editing Enables Precise Genome...

🏁 SCAN COMPLETE: 2026-02-24 14:35
```

---

## 🧩 How It Works

### Architecture

```
┌─────────────┐
│ keywords.txt│
└──────┬──────┘
       │
       ▼
┌─────────────────┐
│  PubMed Search  │ ◄─── Fetch latest papers
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Gemini Analysis │ ◄─── Extract insights
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│   Synthesis     │ ◄─── Find contradictions
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Notion Database │ ◄─── Store results
└─────────────────┘
```

### Key Functions

- **`get_existing_urls()`** — Fetches all paper URLs from Notion to prevent duplicates
- **`get_papers(query)`** — Searches PubMed and retrieves paper metadata
- **`analyze_paper(paper)`** — Uses Gemini to extract structured insights
- **`synthesize_batch(papers)`** — Identifies contradictions and generates hypotheses
- **`push_to_notion(paper, analysis, synthesis)`** — Inserts results into Notion

---

## 🐛 Troubleshooting

### Common Issues

#### ❌ `400 Error` from Notion

**Problem:** Property names don't match your database.

**Solution:** Check that your Notion columns are named **exactly** as shown in the configuration section. Common mistakes:
- `EffectsSizes` (no space) vs `Effect Sizes` (with space)
- `Name` vs `Title`

#### ⚠️ `Could not fetch existing URLs`

**Problem:** Notion integration lacks database permissions.

**Solution:** 
1. Go to your Notion database
2. Click "Share" → Add your integration
3. Grant "Read" and "Write" access

#### 🔒 `401 Unauthorized` from PubMed

**Problem:** Invalid or missing API key.

**Solution:** 
- Request a free API key from [NCBI](https://www.ncbi.nlm.nih.gov/account/)
- Or remove the `PUBMED_API_KEY` variable (limited to 3 requests/sec)

#### 🤖 Gemini parsing errors

**Problem:** AI response isn't valid JSON.

**Solution:** The script includes `clean_json_response()` to strip markdown. If issues persist:
- Check your `GEMINI_API_KEY` is valid
- Verify you have API quota remaining
- Try increasing `time.sleep()` delays

---

## 🎯 Customization

### Change AI Model

```python
MODEL_NAME = "gemini-3-flash-preview"  # Fast & efficient
# MODEL_NAME = "gemini-3-pro"          # More accurate
```

### Adjust Paper Count

```python
search_url = f"{base_url}esearch.fcgi?db=pubmed&term={y}&retmode=json&retmax=5&..."
#                                                                       ↑ Change this
```

### Modify Analysis Prompt

Edit the `analyze_paper()` function to extract different insights:

```python
prompt = f"""Analyze this study and return JSON with exactly these keys:
summary: one sentence main finding
limitations: study weaknesses
clinical_relevance: practical implications
...
```

---

## 📋 Roadmap

- [ ] Support for ArXiv and bioRxiv
- [ ] Automatic keyword expansion based on findings
- [ ] Email digests of weekly contradictions
- [ ] Citation network visualization
- [ ] Integration with Zotero/Mendeley

---

## 🤝 Contributing

Contributions are welcome! Please:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 🙏 Acknowledgments

- **PubMed/NCBI** for providing open access to biomedical literature
- **Google Gemini** for powerful AI analysis capabilities
- **Notion** for their flexible API and database platform

---

## 📧 Contact

Questions? Open an issue or reach out:

- **GitHub Issues:** [Report a bug](https://github.com/yourusername/pubmed-research-automator/issues)
- **Email:** your.email@example.com

---

<div align="center">

**⭐ Star this repo if it helped your research workflow!**

Made with ❤️ by researchers, for researchers

</div>
