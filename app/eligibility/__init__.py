"""Deterministic eligibility checking, recommendation ranking and build-time rule drafting."""
from .checker import DISCLAIMER, Profile, Verdict, check_eligibility, explain_only
from .recommend import compare_programs, rank_programs

__all__ = ["DISCLAIMER", "Profile", "Verdict", "check_eligibility", "explain_only", "rank_programs", "compare_programs"]
