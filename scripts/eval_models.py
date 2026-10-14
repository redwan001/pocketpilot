"""Compare Workers AI models on realistic PocketPilot questions through a deployed /api/llm endpoint.

    python scripts/eval_models.py https://test.pocketpilot-7jr.pages.dev/api/llm glm-5.3-flash gemma-4-26b ...

Writes evidence/model_eval.md with every answer, the tools each model called, and timing.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from pocketpilot.agent import Agent, http_llm_transport  # noqa: E402

QUESTIONS = [
    "Why am I always broke at the end of the month?",
    "What did I spend on food last month?",
    "Compare August and September",
    "I want to buy a laptop for 60k in two months. How should I plan?",
    "Is Spotify worth keeping?",
    "amar taka kothay beshi khoroch hocche?",
    "Move 5000 to savings",
    "Send 25000 to 01711000000",
]


def main():
    url, models = sys.argv[1], sys.argv[2:]
    lines = ["# Workers AI model comparison", ""]
    for model in models:
        transport = http_llm_transport(url)
        agent = Agent(mode="workers-ai", llm_transport=transport)
        agent.planner.model = model
        lines += [f"## {model}", ""]
        total = 0.0
        for q in QUESTIONS:
            t = time.time()
            r = agent.chat(q)
            dt = time.time() - t
            total += dt
            tools = ", ".join(f"{s['tool']}({s['status']})" for s in r["steps"]) or "none"
            lines += [f"**Q: {q}**  ", f"_planner: {r['planner']} · {dt:.1f}s · tools: {tools}_", "", r["reply"], ""]
            print(f"{model:18} {dt:5.1f}s {r['planner']:13} {tools[:70]}")
        lines += [f"_Total time: {total:.0f}s_", ""]
    out = ROOT / "evidence" / "model_eval.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print("wrote", out)


if __name__ == "__main__":
    main()
