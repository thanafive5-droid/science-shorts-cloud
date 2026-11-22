"""1-command live YouTube Shorts views report for all 6 channels (0 API quota, ~2s).

Run: python scripts/views_report.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.analytics_adapter import analyze_channel_performance, load_channel_strategy

CHANNEL_HANDLES = {
    "physics": "@Quantum_Snap_YT_Physics",
    "chemistry": "@ReactionRush-v6u",
    "biology": "@BioPulse-Daily-yt",
    "general_science": "@CoreScienceL-v4b",
    "earth_science": "@EarthSciPulse",
    "astronomy": "@AstroSnapDaily-r4l",
}


def main() -> None:
    print(f"{'CHANNEL':16s} | {'HANDLE':26s} | {'N':>3s} | {'MEDIAN':>7s} | {'RECENT':>7s} | {'PREV':>7s} | {'TREND':17s}")
    print("-" * 102)
    flagged: list[str] = []
    for cid, handle in CHANNEL_HANDLES.items():
        perf = analyze_channel_performance(handle, limit=20)
        flag = " <-- ADAPT!" if perf.trend == "dropping" else ""
        if perf.trend == "dropping":
            flagged.append(cid)
        print(
            f"{cid:16s} | {handle:26s} | {len(perf.shorts):3d} | "
            f"{perf.median_views:7.0f} | {perf.recent_avg:7.0f} | {perf.prev_avg:7.0f} | "
            f"{perf.trend:17s}{flag}"
        )
        if perf.top_shorts:
            top = perf.top_shorts[0]
            print(f"   TOP : {top.views:,} views | {top.title}")
        if perf.bottom_shorts:
            low = perf.bottom_shorts[-1]
            print(f"   LOW : {low.views:,} views | {low.title}")
        strat = load_channel_strategy(cid)
        if strat.get("extra_directive") or strat.get("focus_topics"):
            print(f"   STRAT: {strat}")

    print("-" * 102)
    if flagged:
        print(f"CHANNELS NEEDING ADAPTATION: {', '.join(flagged)}")
    else:
        print("ALL CHANNELS STABLE/GROWING (or still gathering baseline data).")


if __name__ == "__main__":
    main()
