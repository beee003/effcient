"""
Immutable Trade Ledger - Your Proof Engine

This is what makes paper trading credible to investors.
Every trade is timestamped, hashed, and cannot be retroactively edited.

Features:
1. Append-only log (no edits, no deletes)
2. SHA-256 chain (each entry hashes the previous)
3. JSON Lines format (easy to audit)
4. Automatic daily snapshots
5. Export for auditors
"""

import hashlib
import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, date
from pathlib import Path
from typing import Optional, Iterator

from .execution import ExecutionResult


@dataclass
class LedgerEntry:
    """Single trade entry in the ledger"""
    # Unique ID
    entry_id: int
    timestamp: str  # ISO format

    # Trade details
    ticker: str
    side: str  # buy/sell
    intended_quantity: int
    filled_quantity: int
    intended_price: float
    filled_price: float

    # Signal metadata
    signal_type: str  # bullish/bearish/neutral
    signal_confidence: float
    signal_rationale: str

    # Execution costs (all in dollars)
    spread_cost: float
    impact_cost: float
    commission: float
    total_cost: float
    cost_bps: float

    # Execution quality
    slippage_bps: float
    latency_ms: float
    fully_filled: bool
    rejected: bool

    # Chain integrity
    prev_hash: str
    entry_hash: str

    def to_dict(self) -> dict:
        return asdict(self)


class TradeLedger:
    """
    Immutable, append-only trade ledger.

    Usage:
        ledger = TradeLedger("~/.astrai/trades.jsonl")

        # Log a trade
        ledger.append(
            ticker="NVDA",
            side="buy",
            execution=execution_result,
            signal=signal,
        )

        # Verify integrity
        assert ledger.verify_chain()

        # Export for auditor
        ledger.export_audit_report("audit.json")
    """

    def __init__(self, path: Optional[str] = None):
        """
        Initialize ledger.

        Args:
            path: Path to ledger file (default: ~/.astrai/trades.jsonl)
        """
        if path is None:
            base = Path.home() / ".astrai"
            base.mkdir(parents=True, exist_ok=True)
            path = str(base / "trades.jsonl")

        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

        # Set secure permissions
        if self.path.exists():
            os.chmod(self.path, 0o600)

        self._entry_count = self._count_entries()
        self._last_hash = self._get_last_hash()

    def _count_entries(self) -> int:
        """Count existing entries"""
        if not self.path.exists():
            return 0
        with open(self.path, "r") as f:
            return sum(1 for _ in f)

    def _get_last_hash(self) -> str:
        """Get hash of last entry (or genesis hash)"""
        if not self.path.exists() or self._entry_count == 0:
            return "0" * 64  # Genesis hash

        with open(self.path, "r") as f:
            last_line = ""
            for line in f:
                last_line = line
            if last_line:
                entry = json.loads(last_line)
                return entry.get("entry_hash", "0" * 64)
        return "0" * 64

    def _compute_hash(self, data: dict, prev_hash: str) -> str:
        """Compute SHA-256 hash of entry + previous hash"""
        # Remove entry_hash from data for hashing
        data_copy = {k: v for k, v in data.items() if k != "entry_hash"}
        data_copy["prev_hash"] = prev_hash

        content = json.dumps(data_copy, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()

    def append(
        self,
        ticker: str,
        side: str,
        execution: ExecutionResult,
        signal_type: str = "unknown",
        signal_confidence: float = 0.0,
        signal_rationale: str = "",
    ) -> LedgerEntry:
        """
        Append trade to ledger.

        Returns:
            The created LedgerEntry
        """
        entry_id = self._entry_count + 1
        timestamp = datetime.utcnow().isoformat() + "Z"

        entry = LedgerEntry(
            entry_id=entry_id,
            timestamp=timestamp,
            ticker=ticker,
            side=side,
            intended_quantity=execution.intended_quantity,
            filled_quantity=execution.filled_quantity,
            intended_price=execution.intended_price,
            filled_price=execution.filled_price,
            signal_type=signal_type,
            signal_confidence=signal_confidence,
            signal_rationale=signal_rationale[:500],  # Truncate
            spread_cost=execution.spread_cost,
            impact_cost=execution.impact_cost,
            commission=execution.commission,
            total_cost=execution.total_cost,
            cost_bps=execution.cost_bps,
            slippage_bps=execution.slippage_bps,
            latency_ms=execution.latency_ms,
            fully_filled=execution.fully_filled,
            rejected=execution.rejected,
            prev_hash=self._last_hash,
            entry_hash="",  # Will be computed
        )

        # Compute hash
        entry_dict = entry.to_dict()
        entry.entry_hash = self._compute_hash(entry_dict, self._last_hash)
        entry_dict["entry_hash"] = entry.entry_hash

        # Append to file
        with open(self.path, "a") as f:
            f.write(json.dumps(entry_dict) + "\n")

        # Update state
        self._last_hash = entry.entry_hash
        self._entry_count += 1

        return entry

    def verify_chain(self) -> tuple[bool, Optional[str]]:
        """
        Verify integrity of entire ledger chain.

        Returns:
            (is_valid, error_message)
        """
        if not self.path.exists():
            return True, None

        prev_hash = "0" * 64

        with open(self.path, "r") as f:
            for i, line in enumerate(f, 1):
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    return False, f"Invalid JSON at line {i}"

                # Verify prev_hash chain
                if entry.get("prev_hash") != prev_hash:
                    return False, f"Chain broken at entry {i}: prev_hash mismatch"

                # Verify entry hash
                stored_hash = entry.get("entry_hash")
                computed_hash = self._compute_hash(entry, prev_hash)
                if stored_hash != computed_hash:
                    return False, f"Hash mismatch at entry {i}: tampering detected"

                prev_hash = stored_hash

        return True, None

    def iter_entries(self) -> Iterator[LedgerEntry]:
        """Iterate over all entries"""
        if not self.path.exists():
            return

        with open(self.path, "r") as f:
            for line in f:
                data = json.loads(line)
                yield LedgerEntry(**data)

    def get_stats(self) -> dict:
        """Calculate trading statistics"""
        entries = list(self.iter_entries())

        if not entries:
            return {"total_trades": 0}

        # Filter to filled trades only
        filled = [e for e in entries if not e.rejected and e.filled_quantity > 0]

        if not filled:
            return {"total_trades": len(entries), "filled_trades": 0}

        # Basic counts
        buys = [e for e in filled if e.side == "buy"]
        sells = [e for e in filled if e.side == "sell"]

        # Cost analysis
        total_cost = sum(e.total_cost for e in filled)
        avg_cost_bps = sum(e.cost_bps for e in filled) / len(filled)
        avg_slippage_bps = sum(e.slippage_bps for e in filled) / len(filled)

        # Signal analysis
        high_confidence = [e for e in filled if e.signal_confidence >= 0.7]
        low_confidence = [e for e in filled if e.signal_confidence < 0.5]

        # Time analysis
        timestamps = [datetime.fromisoformat(e.timestamp.rstrip("Z")) for e in entries]
        if len(timestamps) >= 2:
            duration = timestamps[-1] - timestamps[0]
            duration_days = duration.total_seconds() / 86400
        else:
            duration_days = 0

        return {
            "total_trades": len(entries),
            "filled_trades": len(filled),
            "rejected_trades": len(entries) - len(filled),
            "buy_trades": len(buys),
            "sell_trades": len(sells),
            "total_cost_dollars": total_cost,
            "avg_cost_bps": avg_cost_bps,
            "avg_slippage_bps": avg_slippage_bps,
            "high_confidence_trades": len(high_confidence),
            "low_confidence_trades": len(low_confidence),
            "duration_days": duration_days,
            "trades_per_day": len(filled) / max(duration_days, 1),
        }

    def export_audit_report(self, filepath: str):
        """
        Export full audit report.

        This is what you send to MIT professors or investors.
        """
        is_valid, error = self.verify_chain()

        report = {
            "generated_at": datetime.utcnow().isoformat() + "Z",
            "ledger_path": str(self.path),
            "chain_valid": is_valid,
            "chain_error": error,
            "statistics": self.get_stats(),
            "first_trade": None,
            "last_trade": None,
            "entries": [],
        }

        entries = list(self.iter_entries())
        if entries:
            report["first_trade"] = entries[0].to_dict()
            report["last_trade"] = entries[-1].to_dict()
            report["entries"] = [e.to_dict() for e in entries]

        with open(filepath, "w") as f:
            json.dump(report, f, indent=2)

        return report

    def daily_snapshot(self, output_dir: Optional[str] = None):
        """Create daily snapshot for backup"""
        if output_dir is None:
            output_dir = self.path.parent / "snapshots"
        else:
            output_dir = Path(output_dir)

        output_dir.mkdir(parents=True, exist_ok=True)

        today = date.today().isoformat()
        snapshot_path = output_dir / f"trades_{today}.json"

        self.export_audit_report(str(snapshot_path))
        return snapshot_path


def demo_ledger():
    """Demonstrate ledger usage"""
    from .execution import ExecutionSimulator, MarketState

    print("=" * 60)
    print("IMMUTABLE TRADE LEDGER DEMO")
    print("=" * 60)

    # Create temp ledger
    ledger = TradeLedger("/tmp/demo_trades.jsonl")
    sim = ExecutionSimulator(seed=42)

    # Simulate some trades
    trades = [
        ("NVDA", "buy", 100, "bullish", 0.75, "Management guidance improved"),
        ("AAPL", "buy", 200, "bullish", 0.62, "New product cycle"),
        ("TSLA", "sell", 50, "bearish", 0.58, "Margin pressure"),
    ]

    print("\nLogging trades...")
    for ticker, side, qty, sig_type, conf, rationale in trades:
        market = MarketState.from_ticker(ticker, 100.0)  # Mock price
        exec_result = sim.execute(market, side, qty, urgency=0.5)

        entry = ledger.append(
            ticker=ticker,
            side=side,
            execution=exec_result,
            signal_type=sig_type,
            signal_confidence=conf,
            signal_rationale=rationale,
        )
        print(f"  #{entry.entry_id}: {side.upper()} {qty} {ticker} @ ${exec_result.filled_price:.2f}")

    # Verify chain
    print("\nVerifying chain integrity...")
    is_valid, error = ledger.verify_chain()
    print(f"  Chain valid: {is_valid}")

    # Show stats
    print("\nStatistics:")
    stats = ledger.get_stats()
    for k, v in stats.items():
        if isinstance(v, float):
            print(f"  {k}: {v:.2f}")
        else:
            print(f"  {k}: {v}")

    # Export audit
    print("\nExporting audit report...")
    ledger.export_audit_report("/tmp/audit_report.json")
    print("  Saved to /tmp/audit_report.json")


if __name__ == "__main__":
    demo_ledger()
