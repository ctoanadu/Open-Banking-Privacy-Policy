#!/usr/bin/env python3
"""
Open Banking Privacy Policy Dataset Scraper

Pipeline:
  1. Use Playwright to fully expand the regulated-providers directory
  2. For each provider: fetch profile page -> extract name + website
  3. For each website: find privacy policy URL -> extract clean text
  4. Checkpoint after every provider so runs are resumable
  5. Write final dataset.json
"""

import asyncio
import io
import json
import os
import re
import time
import random
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
import pdfplumber
import trafilatura
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from tqdm import tqdm

# ── Config ────────────────────────────────────────────────────────────────────

DIRECTORY_URL  = "https://www.openbanking.org.uk/regulated-providers/"
OB_BASE        = "https://www.openbanking.org.uk"
CHECKPOINT_FILE = Path("data/checkpoint.json")
OUTPUT_FILE     = Path("data/dataset.json")
DELAY_MIN, DELAY_MAX = 1.0, 2.5   # polite delay between requests (seconds)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-GB,en;q=0.9",
}

SKIP_DOMAINS = {
    "openbanking.org.uk", "fca.org.uk", "register.fca.org.uk",
    "twitter.com", "x.com", "linkedin.com", "facebook.com",
    "instagram.com", "youtube.com", "atlassian.net", "atlassian.com",
}

PRIVACY_LINK_KEYWORDS = [
    "privacy policy", "privacy notice", "privacy statement",
    "data protection notice", "data protection policy",
    "how we use your data", "your privacy", "privacy",
]

PRIVACY_URL_PATTERNS = [
    r"/privacy[-_]?polic",
    r"/privacy[-_]?notice",
    r"/privacy[-_]?statement",
    r"/data[-_]?protection",
    r"/legal/privacy",
    r"/about/privacy",
    r"/policies/privacy",
    r"/privacy/?$",
    r"/gdpr",
    r"privacy",          # broad fallback — catches /footer-pages/privacy-cookie-policy/ etc.
]

# Paths to probe directly when homepage link-scanning fails
PRIVACY_FALLBACK_PATHS = [
    "/privacy-policy", "/privacy", "/privacy-notice",
    "/legal/privacy-policy", "/legal/privacy",
    "/about/privacy-policy", "/data-protection",
]


# ── Checkpoint helpers ─────────────────────────────────────────────────────────

def load_checkpoint() -> dict:
    if CHECKPOINT_FILE.exists():
        return json.loads(CHECKPOINT_FILE.read_text())
    return {}


def save_checkpoint(data: dict):
    CHECKPOINT_FILE.parent.mkdir(exist_ok=True)
    CHECKPOINT_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


# ── Phase 1: directory scraping ────────────────────────────────────────────────

async def get_all_provider_links(playwright) -> list[dict]:
    """Expand the full directory with Playwright and return list of {name, profile_url}."""
    browser = await playwright.chromium.launch(headless=True)
    page    = await browser.new_page()
    await page.set_extra_http_headers(HEADERS)

    print("Loading Open Banking directory …")
    await page.goto(DIRECTORY_URL, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(3000)  # let JS render the initial list

    # Keep clicking "Load More" until no new providers are added.
    # The button stays visible even after all results are loaded, so we
    # detect the end by comparing provider counts before and after each click.
    clicks = 0
    stale_clicks = 0
    while stale_clicks < 2:  # stop after 2 consecutive clicks with no new content
        btn = await page.query_selector(
            "button:has-text('Load more'), a:has-text('Load more'), "
            "button:has-text('Load More'), a:has-text('Load More')"
        )
        if not btn:
            break

        # Count providers currently visible
        before = len(await page.query_selector_all("a[href*='/regulated-providers/']"))

        await btn.scroll_into_view_if_needed()
        await btn.click()
        await page.wait_for_timeout(2500)
        clicks += 1

        after = len(await page.query_selector_all("a[href*='/regulated-providers/']"))
        new_count = after - before
        print(f"  'Load More' clicked {clicks}× — {new_count} new entries (total {after})")

        if new_count == 0:
            stale_clicks += 1
        else:
            stale_clicks = 0  # reset if we got new content

    print(f"Directory fully expanded after {clicks} clicks.")

    html = await page.content()
    await browser.close()

    soup      = BeautifulSoup(html, "lxml")
    seen_urls = set()
    providers = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "/regulated-providers/" in href and href.rstrip("/") != "/regulated-providers":
            full_url = urljoin(OB_BASE, href).rstrip("/")
            if full_url not in seen_urls:
                seen_urls.add(full_url)
                name = a.get_text(strip=True)
                if name:
                    providers.append({"name": name, "profile_url": full_url})

    return providers


# ── Phase 2: provider profile ──────────────────────────────────────────────────

def fetch_provider_details(profile_url: str) -> tuple[str | None, str | None]:
    """Return (company_name, website_url) from an OB provider profile page."""
    try:
        r    = httpx.get(profile_url, headers=HEADERS, timeout=20, follow_redirects=True)
        soup = BeautifulSoup(r.text, "lxml")

        # Company name from <h1>
        h1   = soup.find("h1")
        name = h1.get_text(strip=True) if h1 else None

        # Narrow to main content — skip nav/header/footer where stray links live
        main = (
            soup.find("main")
            or soup.find(id=re.compile(r"main|content", re.I))
            or soup.find(class_=re.compile(r"main|content|entry|article", re.I))
            or soup  # fallback to full page
        )

        # Official website: first external link in main content that isn't FCA/social/nav
        website = None
        for a in main.find_all("a", href=True):
            href   = a["href"]
            domain = urlparse(href).netloc.lower().lstrip("www.")
            if (
                href.startswith("http")
                and not any(skip in domain for skip in SKIP_DOMAINS)
            ):
                website = href
                break

        return name, website

    except Exception as exc:
        print(f"    [profile error] {profile_url}: {exc}")
        return None, None


# ── Phase 3: find privacy URL ─────────────────────────────────────────────────

def _score_link(text: str, href: str) -> int:
    score = 0
    text  = text.lower()
    href_lower = href.lower()

    for kw in PRIVACY_LINK_KEYWORDS:
        if kw in text:
            score += 3 if len(kw) > 8 else 1

    for pat in PRIVACY_URL_PATTERNS:
        if re.search(pat, href_lower):
            score += 2

    return score


def find_privacy_url(homepage_url: str) -> str | None:
    """
    Fetch the company homepage, score every link, return the most likely
    privacy-policy URL. Falls back to probing common paths if the homepage
    is blocked or yields no privacy link.
    """
    base       = homepage_url
    best_url   = None
    best_score = 0

    try:
        r    = httpx.get(homepage_url, headers=HEADERS, timeout=20, follow_redirects=True)
        base = str(r.url)

        if r.status_code < 400:
            soup = BeautifulSoup(r.text, "lxml")
            for a in soup.find_all("a", href=True):
                href      = a["href"].strip()
                link_text = a.get_text(strip=True)
                full_url  = urljoin(base, href)
                score     = _score_link(link_text, href)
                if score > best_score:
                    best_score = score
                    best_url   = full_url

    except Exception as exc:
        print(f"    [homepage error] {homepage_url}: {exc}")

    if best_score >= 2:
        return best_url

    # Fallback: probe common privacy paths directly
    parsed = urlparse(base)
    root   = f"{parsed.scheme}://{parsed.netloc}"
    for path in PRIVACY_FALLBACK_PATHS:
        candidate = root + path
        try:
            probe = httpx.head(candidate, headers=HEADERS, timeout=10, follow_redirects=True)
            if probe.status_code < 400:
                return str(probe.url)
        except Exception:
            continue

    return None


# ── Phase 3b: extract privacy text ────────────────────────────────────────────

def extract_privacy_text(privacy_url: str) -> str | None:
    """Fetch a privacy page (HTML or PDF) and return clean plain text."""
    if not privacy_url:
        return None
    try:
        r            = httpx.get(privacy_url, headers=HEADERS, timeout=30, follow_redirects=True)
        content_type = r.headers.get("content-type", "")

        # ── PDF ──────────────────────────────────────────────────────────────
        if "pdf" in content_type or privacy_url.lower().endswith(".pdf"):
            pages_text = []
            with pdfplumber.open(io.BytesIO(r.content)) as pdf:
                for pg in pdf.pages:
                    t = pg.extract_text()
                    if t:
                        pages_text.append(t)
            text = "\n\n".join(pages_text).strip()
            return text or None

        # ── HTML via trafilatura ──────────────────────────────────────────────
        text = trafilatura.extract(
            r.text,
            include_tables=True,
            include_links=False,
            no_fallback=False,
            favor_precision=False,
        )
        return text or None

    except Exception as exc:
        print(f"    [privacy text error] {privacy_url}: {exc}")
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    Path("data").mkdir(exist_ok=True)
    checkpoint = load_checkpoint()

    # ── Phase 1: discover all provider profile URLs ───────────────────────────
    if "providers" not in checkpoint:
        async with async_playwright() as pw:
            providers = await get_all_provider_links(pw)
        checkpoint["providers"] = providers
        save_checkpoint(checkpoint)
        print(f"\nFound {len(providers)} providers in directory.\n")
    else:
        providers = checkpoint["providers"]
        print(f"Loaded {len(providers)} providers from checkpoint.\n")

    # ── Phase 2 + 3: process each provider ───────────────────────────────────
    results: dict = checkpoint.get("results", {})

    for i, provider in enumerate(tqdm(providers, desc="Providers")):
        profile_url = provider["profile_url"]

        if profile_url in results:
            continue  # already done — skip

        tqdm.write(f"\n[{i+1}/{len(providers)}] {provider['name']}")

        # Profile page → name + website
        name, website = fetch_provider_details(profile_url)
        name = name or provider["name"]

        if not website:
            tqdm.write(f"  No website found.")
            results[profile_url] = {
                "company_name": name,
                "website":      None,
                "privacy_url":  None,
                "privacy_text": None,
            }
            checkpoint["results"] = results
            save_checkpoint(checkpoint)
            time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))
            continue

        tqdm.write(f"  Website:  {website}")

        # Homepage → privacy URL
        privacy_url = find_privacy_url(website)
        tqdm.write(f"  Privacy:  {privacy_url}")

        # Privacy page → clean text
        privacy_text = extract_privacy_text(privacy_url)

        results[profile_url] = {
            "company_name": name,
            "website":      website,
            "privacy_url":  privacy_url,
            "privacy_text": privacy_text,
        }
        checkpoint["results"] = results
        save_checkpoint(checkpoint)
        time.sleep(random.uniform(DELAY_MIN, DELAY_MAX))

    # ── Write final output ────────────────────────────────────────────────────
    dataset = list(results.values())
    OUTPUT_FILE.write_text(json.dumps(dataset, indent=2, ensure_ascii=False))

    with_text = sum(1 for r in dataset if r["privacy_text"])
    print(f"\nDone.  {len(dataset)} providers written to {OUTPUT_FILE}")
    print(f"  {with_text} with privacy text  |  {len(dataset) - with_text} null")


if __name__ == "__main__":
    asyncio.run(main())
