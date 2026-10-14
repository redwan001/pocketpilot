"""Terminal chat:  python -m pocketpilot.cli [--offline]"""
from __future__ import annotations

import argparse

from .agent import Agent


def main():
    ap = argparse.ArgumentParser(description="Chat with PocketPilot in the terminal")
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()
    agent = Agent(mode="offline" if args.offline else "auto")
    print(f"PocketPilot ({agent.planner.name}) - sandbox account. Type 'quit' to exit.\n")
    while True:
        try:
            msg = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if msg.lower() in {"quit", "exit"}:
            break
        if not msg:
            continue
        r = agent.chat(msg)
        print("\npocketpilot> " + r["reply"])
        for s in r["steps"]:
            print(f"   [{s['tier'].upper()}] {s['tool']} -> {s['status']}")
        for p in r["pending"]:
            ans = input(f"\n   APPROVAL NEEDED: {p['summary']} Approve? [y/N] ").strip().lower()
            res = agent.confirm(p["token"], ans in {"y", "yes"})
            print(f"   -> {res['status']}" + (f": {res.get('error')}" if res.get("error") else ""))
        print()


if __name__ == "__main__":
    main()
