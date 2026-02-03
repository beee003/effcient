"""
Signal Generator - AI-powered alpha detection

Uses LLM agents to find "Sentiment Drift" in SEC filings.
Routes through Astrai for 90% cost reduction.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional

from .sec_fetcher import SECFetcher, Filing, compare_filings


class SignalType(Enum):
    """Trading signal types"""
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


@dataclass
class Signal:
    """Generated trading signal"""
    ticker: str
    signal_type: SignalType
    confidence: float  # 0-1
    rationale: str
    evidence: list[str]
    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())
    source_filings: list[str] = field(default_factory=list)


class SignalGenerator:
    """
    Multi-agent signal generation system.

    Agents:
    1. Comparator - finds changes between filings
    2. Analyst - interprets significance of changes
    3. Skeptic - challenges the analysis (red team)
    4. Synthesizer - produces final signal

    Usage:
        gen = SignalGenerator(llm_client=your_client)
        signal = gen.analyze("NVDA")
        if signal.confidence > 0.7:
            print(f"TRADE: {signal.signal_type} on {signal.ticker}")
    """

    # Prompt templates for each agent
    COMPARATOR_PROMPT = """You are a financial analyst comparing SEC 10-K filings.

TASK: Identify the most significant changes between the OLD and NEW filing sections.

Focus on:
- Removed risk disclosures (bullish signal)
- New risk disclosures (bearish signal)
- Changes in guidance language
- Tone shifts (confident -> cautious or vice versa)
- Removed/added competitive mentions

OLD FILING (Item {section}):
{old_text}

NEW FILING (Item {section}):
{new_text}

List the top 5 most significant changes. Format as JSON:
{{"changes": [{{"change": "description", "significance": "high/medium/low", "direction": "bullish/bearish/neutral"}}]}}"""

    ANALYST_PROMPT = """You are a senior equity analyst at a top hedge fund.

TASK: Interpret these filing changes and provide a trading thesis.

TICKER: {ticker}
FILING CHANGES:
{changes}

Consider:
1. What is management trying to signal (or hide)?
2. How will the street react when they notice this?
3. What's the likely price impact?

Provide your analysis as JSON:
{{"thesis": "your thesis", "conviction": 0.0-1.0, "direction": "bullish/bearish/neutral", "key_evidence": ["point1", "point2"]}}"""

    SKEPTIC_PROMPT = """You are a risk manager who challenges trading theses.

TASK: Find holes in this analysis. Be adversarial.

THESIS:
{thesis}

EVIDENCE:
{evidence}

Questions to consider:
1. Is this just boilerplate legal language?
2. Could this change be sector-wide (not alpha)?
3. Is this already priced in?
4. What's the base rate for false positives here?

Respond with JSON:
{{"concerns": ["concern1", "concern2"], "false_positive_risk": 0.0-1.0, "recommendation": "proceed/reject/reduce_size"}}"""

    def __init__(
        self,
        llm_client=None,
        sec_fetcher: Optional[SECFetcher] = None,
        use_astrai: bool = True,
    ):
        """
        Initialize signal generator.

        Args:
            llm_client: LLM client with .chat() method (or None for mock)
            sec_fetcher: SEC filing fetcher
            use_astrai: Route through Astrai for cost optimization
        """
        self.llm = llm_client
        self.fetcher = sec_fetcher or SECFetcher()
        self.use_astrai = use_astrai

    def _call_llm(self, prompt: str, model: str = "sonnet") -> str:
        """Call LLM (routes through Astrai if enabled)"""
        if self.llm is None:
            # Mock response for testing
            return json.dumps({"mock": True, "prompt_length": len(prompt)})

        # In production, this would call Astrai API
        # which routes to the optimal provider
        return self.llm.chat(prompt, model=model)

    def analyze(
        self,
        ticker: str,
        sections: list[str] = ["1A", "7"],
    ) -> Optional[Signal]:
        """
        Analyze a ticker for trading signals.

        Args:
            ticker: Stock ticker (e.g., "NVDA")
            sections: 10-K sections to analyze

        Returns:
            Signal object or None if no signal
        """
        # Step 1: Fetch filings
        filings = self.fetcher.get_filings(ticker, form_type="10-K", limit=2)
        if len(filings) < 2:
            return None

        new_filing, old_filing = filings[0], filings[1]

        # Fetch content
        self.fetcher.fetch_content(new_filing)
        self.fetcher.fetch_content(old_filing)

        all_changes = []

        # Step 2: Compare each section
        for section in sections:
            old_section = self.fetcher.get_section(old_filing.content, section)
            new_section = self.fetcher.get_section(new_filing.content, section)

            if not old_section or not new_section:
                continue

            # Quick diff stats
            diff = compare_filings(old_section, new_section)

            # LLM comparison (use cheaper model for extraction)
            prompt = self.COMPARATOR_PROMPT.format(
                section=section,
                old_text=old_section[:15000],  # Truncate for context
                new_text=new_section[:15000],
            )

            response = self._call_llm(prompt, model="haiku")  # Cheap model
            try:
                changes = json.loads(response)
                all_changes.extend(changes.get("changes", []))
            except json.JSONDecodeError:
                pass

        if not all_changes:
            return Signal(
                ticker=ticker,
                signal_type=SignalType.NEUTRAL,
                confidence=0.0,
                rationale="No significant changes detected",
                evidence=[],
                source_filings=[f.accession_number for f in filings],
            )

        # Step 3: Analyst interpretation (use smarter model)
        analyst_prompt = self.ANALYST_PROMPT.format(
            ticker=ticker,
            changes=json.dumps(all_changes, indent=2),
        )

        analyst_response = self._call_llm(analyst_prompt, model="sonnet")
        try:
            analysis = json.loads(analyst_response)
        except json.JSONDecodeError:
            analysis = {"thesis": "Parse error", "conviction": 0.0, "direction": "neutral", "key_evidence": []}

        # Step 4: Skeptic review
        skeptic_prompt = self.SKEPTIC_PROMPT.format(
            thesis=analysis.get("thesis", ""),
            evidence=json.dumps(analysis.get("key_evidence", [])),
        )

        skeptic_response = self._call_llm(skeptic_prompt, model="sonnet")
        try:
            skeptic = json.loads(skeptic_response)
        except json.JSONDecodeError:
            skeptic = {"concerns": [], "false_positive_risk": 0.5, "recommendation": "reduce_size"}

        # Step 5: Synthesize final signal
        base_confidence = analysis.get("conviction", 0.5)
        risk_penalty = skeptic.get("false_positive_risk", 0.5)
        final_confidence = base_confidence * (1 - risk_penalty * 0.5)

        direction = analysis.get("direction", "neutral")
        signal_type = {
            "bullish": SignalType.BULLISH,
            "bearish": SignalType.BEARISH,
        }.get(direction, SignalType.NEUTRAL)

        # Reduce confidence if skeptic says reject
        if skeptic.get("recommendation") == "reject":
            final_confidence *= 0.3

        return Signal(
            ticker=ticker,
            signal_type=signal_type,
            confidence=final_confidence,
            rationale=analysis.get("thesis", "No thesis"),
            evidence=analysis.get("key_evidence", []) + skeptic.get("concerns", []),
            source_filings=[f.accession_number for f in filings],
        )

    def scan_universe(
        self,
        tickers: list[str],
        min_confidence: float = 0.6,
    ) -> list[Signal]:
        """
        Scan multiple tickers for signals.

        Args:
            tickers: List of tickers to analyze
            min_confidence: Minimum confidence to include

        Returns:
            List of signals above threshold, sorted by confidence
        """
        signals = []
        for ticker in tickers:
            try:
                signal = self.analyze(ticker)
                if signal and signal.confidence >= min_confidence:
                    signals.append(signal)
            except Exception as e:
                print(f"Error analyzing {ticker}: {e}")

        # Sort by confidence descending
        signals.sort(key=lambda s: s.confidence, reverse=True)
        return signals
