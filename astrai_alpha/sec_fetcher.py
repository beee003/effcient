"""
SEC EDGAR Filing Fetcher

Pulls 10-K, 10-Q, 8-K filings for analysis.
Uses SEC's public EDGAR API (no key required).
"""

import json
import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.request import urlopen, Request
from urllib.error import HTTPError


@dataclass
class Filing:
    """SEC filing metadata and content"""
    ticker: str
    cik: str
    form_type: str  # 10-K, 10-Q, 8-K
    filed_date: str
    accession_number: str
    document_url: str
    content: Optional[str] = None


class SECFetcher:
    """
    Fetch SEC filings from EDGAR.

    Usage:
        fetcher = SECFetcher()
        filings = fetcher.get_filings("NVDA", form_type="10-K", limit=2)
        for f in filings:
            fetcher.fetch_content(f)
            print(f.content[:1000])
    """

    BASE_URL = "https://data.sec.gov"
    SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"

    # SEC requires a User-Agent with contact info
    USER_AGENT = "Astrai Alpha research@astrai.dev"

    # CIK lookup cache
    _cik_cache: dict[str, str] = {}

    def __init__(self, rate_limit_delay: float = 0.15):
        """
        Initialize fetcher.

        Args:
            rate_limit_delay: Seconds between requests (SEC asks for 10 req/sec max)
        """
        self.rate_limit_delay = rate_limit_delay
        self._last_request = 0.0

    def _request(self, url: str) -> str:
        """Make rate-limited request to SEC"""
        # Respect rate limit
        elapsed = time.time() - self._last_request
        if elapsed < self.rate_limit_delay:
            time.sleep(self.rate_limit_delay - elapsed)

        req = Request(url, headers={"User-Agent": self.USER_AGENT})
        try:
            with urlopen(req, timeout=30) as resp:
                self._last_request = time.time()
                return resp.read().decode("utf-8")
        except HTTPError as e:
            if e.code == 404:
                return ""
            raise

    def get_cik(self, ticker: str) -> Optional[str]:
        """Get CIK (Central Index Key) for a ticker"""
        ticker = ticker.upper()

        if ticker in self._cik_cache:
            return self._cik_cache[ticker]

        # Fetch company tickers JSON
        url = f"{self.BASE_URL}/files/company_tickers.json"
        data = json.loads(self._request(url))

        # Build lookup
        for entry in data.values():
            t = entry.get("ticker", "").upper()
            cik = str(entry.get("cik_str", "")).zfill(10)
            self._cik_cache[t] = cik
            if t == ticker:
                return cik

        return None

    def get_filings(
        self,
        ticker: str,
        form_type: str = "10-K",
        limit: int = 5,
    ) -> list[Filing]:
        """
        Get recent filings for a company.

        Args:
            ticker: Stock ticker (e.g., "NVDA")
            form_type: Filing type (10-K, 10-Q, 8-K)
            limit: Max filings to return
        """
        cik = self.get_cik(ticker)
        if not cik:
            return []

        # Fetch submissions JSON
        url = f"{self.BASE_URL}/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type={form_type}&dateb=&owner=include&count={limit}&output=atom"

        # Actually, use the JSON API
        submissions_url = f"{self.BASE_URL}/submissions/CIK{cik}.json"
        data = json.loads(self._request(submissions_url))

        filings = []
        recent = data.get("filings", {}).get("recent", {})

        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])

        for i, form in enumerate(forms):
            if form == form_type and len(filings) < limit:
                acc = accessions[i].replace("-", "")
                doc_url = f"{self.BASE_URL}/Archives/edgar/data/{cik}/{acc}/{primary_docs[i]}"

                filings.append(Filing(
                    ticker=ticker.upper(),
                    cik=cik,
                    form_type=form,
                    filed_date=dates[i],
                    accession_number=accessions[i],
                    document_url=doc_url,
                ))

        return filings

    def fetch_content(self, filing: Filing) -> str:
        """
        Fetch full filing content.
        Returns cleaned text (HTML tags stripped).
        """
        if filing.content:
            return filing.content

        raw = self._request(filing.document_url)

        # Strip HTML tags for text analysis
        text = re.sub(r'<[^>]+>', ' ', raw)
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()

        filing.content = text
        return text

    def get_section(self, content: str, section: str) -> str:
        """
        Extract specific section from 10-K.

        Sections:
            - "1A" = Risk Factors
            - "7" = MD&A (Management Discussion)
            - "7A" = Market Risk
        """
        # Common section header patterns
        patterns = {
            "1A": r"Item\s*1A[\.\s]*Risk\s*Factors(.*?)(?=Item\s*1B|Item\s*2|$)",
            "7": r"Item\s*7[\.\s]*Management.s\s*Discussion(.*?)(?=Item\s*7A|Item\s*8|$)",
            "7A": r"Item\s*7A[\.\s]*Quantitative.*?Market\s*Risk(.*?)(?=Item\s*8|$)",
        }

        pattern = patterns.get(section)
        if not pattern:
            return ""

        match = re.search(pattern, content, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()[:50000]  # Cap at 50k chars
        return ""


def compare_filings(old: str, new: str) -> dict:
    """
    Compare two filing sections to find changes.
    Returns dict with added, removed, and changed sentences.
    """
    def sentences(text: str) -> set[str]:
        # Simple sentence splitting
        sents = re.split(r'[.!?]+', text)
        return {s.strip().lower() for s in sents if len(s.strip()) > 20}

    old_sents = sentences(old)
    new_sents = sentences(new)

    added = new_sents - old_sents
    removed = old_sents - new_sents

    return {
        "added_count": len(added),
        "removed_count": len(removed),
        "added": list(added)[:20],  # Top 20
        "removed": list(removed)[:20],
    }
