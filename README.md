# Open Banking Privacy Policy Dataset

Scrapes the [Open Banking regulated providers directory](https://www.openbanking.org.uk/regulated-providers/) and builds a JSON dataset containing each provider's company name, official website, privacy policy URL, and full privacy policy text.

## Output

`data/dataset.json` — a JSON array of objects:

```json
[
  {
    "company_name": "Kroo Bank Ltd",
    "website": "https://www.kroo.com/",
    "privacy_url": "https://kroo.com/documents/privacy-policy.html",
    "privacy_text": "CUSTOMER PRIVACY NOTICE\n..."
  }
]
```

| Field | Description |
|---|---|
| `company_name` | Provider name from the Open Banking directory |
| `website` | Official website as listed on the provider profile |
| `privacy_url` | URL of the privacy policy or privacy notice page |
| `privacy_text` | Full extracted text of the privacy policy (`null` if not found) |

The last run produced **273 providers**, of which **229 (83.9%)** had a privacy policy successfully extracted.

## Requirements

- Python 3.10+
- A internet connection

## Setup

```bash
# 1. Create and activate a virtual environment
python3 -m venv venv
source venv/bin/activate        # macOS / Linux
# venv\Scripts\activate         # Windows

# 2. Install dependencies
pip install -r requirements.txt

# 3. Install the Playwright browser (one-time download, ~100 MB)
playwright install chromium
```

## Running

```bash
source venv/bin/activate
python scrape.py
```

The script will:

1. **Phase 1** — Launch a headless Chromium browser, navigate to the Open Banking directory, and click "Load More" until all providers are listed (~2–3 minutes).
2. **Phase 2** — Visit each provider's profile page and extract the company name and official website URL.
3. **Phase 3** — Visit each website, locate the privacy policy link, fetch the page, and extract clean text. PDF privacy policies are also supported.

Progress is printed to the terminal as each provider is processed. The full run takes approximately **20–30 minutes** for ~270 providers.

## Resuming an interrupted run

The script checkpoints after every provider to `data/checkpoint.json`. If the run is interrupted, simply re-run `python scrape.py` and it will pick up where it left off — already-processed providers are skipped.

To start a completely fresh run, delete the checkpoint:

```bash
rm data/checkpoint.json
```

## How it works

### Finding privacy policy links

The script fetches each company's homepage and scores every link based on:
- Link text matching keywords (`privacy policy`, `privacy notice`, `data protection`, etc.)
- URL path patterns (`/privacy`, `/privacy-policy`, `/legal/privacy`, etc.)

If no link scores above the threshold, it probes a list of common privacy paths directly (e.g. `/privacy-policy`, `/privacy-notice`).

### Text extraction

- **HTML pages** — cleaned with [trafilatura](https://trafilatura.readthedocs.io/), which strips navigation, cookie banners, and boilerplate while preserving section headings.
- **PDF documents** — text extracted page-by-page with [pdfplumber](https://github.com/jsvine/pdfplumber).

### Null results

A provider gets `null` for `privacy_text` when:
- No website is listed in the Open Banking directory
- The website blocks automated requests (403/bot protection)
- No privacy policy link can be located on the homepage or via common paths
- The privacy policy is behind a login or cookie consent wall

## Project structure

```
.
├── scrape.py          # main scraper
├── test_scrape.py     # smoke test (3 providers)
├── requirements.txt
├── data/
│   ├── checkpoint.json   # intermediate state (gitignored)
│   └── dataset.json      # final output (gitignored)
└── venv/              # virtual environment (gitignored)
```
