"""Check that only whole-game goal totals survive market filtering.

Question strings are real, taken from the Gamma API on 2026-08-30.
Run: python tests/test_market_filter.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from soccer_monitor import parse_game_total_line

KEEP = {
    "Viking FK vs. Aalesunds FK: O/U 2.5": 2.5,
    "1. FC Cologne vs. Hertha BSC: O/U 3.5": 3.5,
    "Viking FK vs. Aalesunds FK: O/U 0.5": 0.5,  # kept here, dropped later by the line filter
}

DROP = [
    "Viking FK vs. Aalesunds FK: Viking FK O/U 1.5",              # per-team
    "1. FC Cologne vs. Hertha BSC: 1. FC Cologne O/U 2.5",        # per-team, digits in name
    "1. FC Cologne vs. Hertha BSC: 1st Half O/U 2.5",             # per-half
    "1. FC Cologne vs. Hertha BSC: Hertha BSC 2nd Half O/U 1.5",  # per-team per-half
    "1. FC Cologne vs. Real Sociedad: O/U 8.5 Corners",           # corners
    "1. FC Cologne vs. Real Sociedad: 1st Half O/U 3.5 Total Corners",
    "Will Zack Steffen win 2026 MLS Goalkeeper of the Year?",
]

if __name__ == "__main__":
    for q, line in KEEP.items():
        assert parse_game_total_line(q) == line, q
    for q in DROP:
        assert parse_game_total_line(q) is None, q
    print(f"OK: {len(KEEP)} kept, {len(DROP)} dropped")
