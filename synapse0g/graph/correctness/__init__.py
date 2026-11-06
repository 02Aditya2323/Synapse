"""Empirical equivalence and regression gating."""

from .equivalence import compare
from .firewall import Verdict, VerificationReport, verify_pairs

__all__ = ["Verdict", "VerificationReport", "compare", "verify_pairs"]
