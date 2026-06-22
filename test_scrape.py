#!/usr/bin/env python3
"""Quick smoke test — runs the full pipeline on 3 known providers."""

import asyncio
import json
from scrape import get_all_provider_links, fetch_provider_details, find_privacy_url, extract_privacy_text
from playwright.async_api import async_playwright

TEST_PROVIDERS = [
    {"name": "Kroo Bank Ltd",              "profile_url": "https://www.openbanking.org.uk/regulated-providers/kroo-bank-ltd"},
    {"name": "United National Bank Ltd",   "profile_url": "https://www.openbanking.org.uk/regulated-providers/united-national-bank-limited"},
    {"name": "Cleo AI Ltd",                "profile_url": "https://www.openbanking.org.uk/regulated-providers/cleo-ai-ltd"},
]

async def main():
    results = []
    for p in TEST_PROVIDERS:
        print(f"\n── {p['name']} ──")
        name, website = fetch_provider_details(p["profile_url"])
        print(f"  name:    {name}")
        print(f"  website: {website}")

        privacy_url  = find_privacy_url(website) if website else None
        print(f"  privacy: {privacy_url}")

        privacy_text = extract_privacy_text(privacy_url)
        snippet      = (privacy_text[:200] + "…") if privacy_text else None
        print(f"  text:    {snippet}")

        results.append({
            "company_name": name or p["name"],
            "website":      website,
            "privacy_url":  privacy_url,
            "privacy_text": privacy_text,
        })

    print("\n── JSON output (truncated) ──")
    for r in results:
        r_display = {**r, "privacy_text": (r["privacy_text"] or "")[:100] + "…" if r["privacy_text"] else None}
        print(json.dumps(r_display, indent=2))

asyncio.run(main())
