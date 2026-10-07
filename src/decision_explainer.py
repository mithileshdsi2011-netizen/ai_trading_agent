"""Trade decision explainability — formatted BUY / SKIP rationale for logs.

This module does not change any trading decisions. It only produces human-readable
explanations so every decision in the logs is transparent.
"""
from typing import Dict, Optional, Any


class DecisionExplainer:
    """Builds plain-text explanations for trading decisions."""

    @staticmethod
    def _check_emoji(condition: bool) -> str:
        return "✓" if condition else "✗"

    @staticmethod
    def format_buy(
        symbol: str,
        score_result: Dict[str, Any],
        mtf_result: Optional[Dict[str, Any]],
        confidence: float,
        overall_score: float,
        rr: float,
        regime: str,
    ) -> str:
        """Return a multi-line explanation for an approved BUY."""
        comp = score_result.get("components", {})
        total = score_result.get("total_score", 0)
        mtf_ok = mtf_result.get("aligned", True) if mtf_result else True

        lines = [
            f"",
            f"╔═══════════════════════════════════════════════════════════════╗",
            f"║  DECISION EXPLANATION — BUY {symbol:<15}                 ║",
            f"╚═══════════════════════════════════════════════════════════════╝",
            f"",
            f"  Trade Score:        {total}/100",
            f"  Overall AI Score:   {overall_score:.2f}",
            f"  Confidence:         {confidence:.0f}%",
            f"  Risk:Reward:        {rr:.2f}",
            f"  Regime:             {regime}",
            f"",
            f"  Score Breakdown",
            f"  ────────────────────────",
        ]
        for name in ("trend", "rsi", "macd", "volume", "sector", "sentiment", "regime"):
            val = comp.get(name, 0)
            lines.append(f"  {name.capitalize():<15} +{val}")
        lines.append(f"  MTF               {'PASS' if mtf_ok else 'FAIL'}")
        lines += [
            f"",
            f"  Decision:           BUY",
            f"  Reason:             Strong setup, MTF aligned" if mtf_ok else f"  Reason:             Setup strong but MTF not aligned",
            f"",
        ]
        return "\n".join(lines)

    @staticmethod
    def format_skip(
        symbol: str,
        reason: str,
        score_result: Optional[Dict[str, Any]] = None,
        mtf_result: Optional[Dict[str, Any]] = None,
        confidence: Optional[float] = None,
        overall_score: Optional[float] = None,
        rr: Optional[float] = None,
        regime: Optional[str] = None,
    ) -> str:
        """Return a multi-line explanation for a SKIP."""
        lines = [
            f"",
            f"╔═══════════════════════════════════════════════════════════════╗",
            f"║  DECISION EXPLANATION — SKIP {symbol:<14}                 ║",
            f"╚═══════════════════════════════════════════════════════════════╝",
            f"",
            f"  Rejected because:",
            f"  {reason}",
        ]

        if score_result is not None:
            comp = score_result.get("components", {})
            total = score_result.get("total_score", 0)
            mtf_ok = mtf_result.get("aligned", False) if mtf_result else False

            lines += [
                f"",
                f"  Trade Score:        {total}/100",
                f"  Confidence:         {confidence:.0f}%" if confidence is not None else "  Confidence:         n/a",
                f"  Overall Score:      {overall_score:.2f}" if overall_score is not None else "  Overall Score:      n/a",
                f"  Risk:Reward:        {rr:.2f}" if rr is not None else "  Risk:Reward:        n/a",
                f"  Regime:             {regime}" if regime is not None else "  Regime:             n/a",
                f"",
                f"  Score Breakdown",
                f"  ────────────────────────",
            ]
            for name in ("trend", "rsi", "macd", "volume", "sector", "sentiment", "regime"):
                val = comp.get(name, 0)
                lines.append(f"  {name.capitalize():<15} +{val}")
            lines.append(f"  MTF               {'PASS' if mtf_ok else 'FAIL'}")
            lines.append(f"")

        lines += [
            f"  Decision:           SKIP",
            f"",
        ]
        return "\n".join(lines)
