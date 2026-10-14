"""Transport-independent JSON API used by both the local web server and the in-browser build.

The same `App.handle()` serves requests whether they arrive over HTTP (server.py) or from
JavaScript running PocketPilot inside the browser via Pyodide (the Cloudflare Pages demo).
"""
from __future__ import annotations

from pathlib import Path

from . import analytics as an
from .agent import Agent
from .categorize import LABELS
from .importer import StatementError, load_statement


class App:
    def __init__(self, mode: str = "offline", audit_path: str | Path | None = None):
        self.mode = mode
        self.audit_path = audit_path
        self.reset()

    def reset(self):
        """Back to the built-in demo account."""
        self.agent = Agent(mode=self.mode, audit_path=self.audit_path)
        self.source = {"kind": "demo", "name": "Demo account (Rafi, synthetic data)", "ai": self.mode != "offline"}
        self._gen = None

    def load_statement(self, kind: str, payload, filename: str = "", use_ai: bool = True) -> dict:
        """Replace the demo account with the user's own statement (parsed locally)."""
        state, today, info = load_statement(kind, payload)
        mode = self.mode if use_ai else "offline"
        self.agent = Agent(mode=mode, audit_path=self.audit_path, state=state, today=today)
        self.agent.audit.append("statement_loaded", format=info["format"], transactions=info["transactions"],
                                months=info["months"], ai_enabled=use_ai)       # counts only, never content
        self.source = {"kind": "upload", "name": (filename or "Your statement")[:80], "ai": use_ai and mode != "offline",
                       **info}
        self._gen = None
        return {"status": "loaded", "source": self.source}

    # Step-wise chat for callers that perform the model request themselves (the browser build):
    # chat_begin -> {"done": False, "llm_request": ...} -> caller fetches /api/llm -> chat_resume(reply) -> ...
    def chat_begin(self, message: str) -> dict:
        message = str(message)[:1000].strip()
        if not message:
            return {"done": True, "result": {"error": "empty message"}}
        self._gen = self.agent.chat_steps(message)
        return self._step(lambda: next(self._gen))

    def chat_resume(self, response: dict) -> dict:
        if self._gen is None:
            return {"done": True, "result": {"error": "no chat in progress"}}
        return self._step(lambda: self._gen.send(response))

    def _step(self, advance) -> dict:
        try:
            return {"done": False, "llm_request": advance()}
        except StopIteration as done:
            self._gen = None
            return {"done": True, "result": done.value}

    def dashboard(self) -> dict:
        a, s, today = self.agent, self.agent.state, self.agent.today
        month = today.isoformat()[:7]
        summary = an.monthly_summary(s.transactions, month)
        history = [an.monthly_summary(s.transactions, m) for m in an.full_months(s.transactions, today)[-3:]]
        history.append(summary)
        upcoming = [r for r in an.detect_recurring(s.transactions, today)
                    if r["merchant"] not in s.cancelled_subscriptions]
        upcoming.sort(key=lambda r: r["next_expected"])
        one_offs = {x["txn_id"] for x in an.detect_anomalies(s.transactions, today, window_days=400)}
        p = a.policy
        ok, _ = a.audit.verify()
        return {
            "history": [{"month": h["month"], "income": h["income"], "spend": h["spend"]} for h in history],
            "upcoming": upcoming[:6],
            "policy": {"max_single_transfer": p.max_single_transfer, "daily_outgoing_cap": p.daily_outgoing_cap,
                       "min_checking_balance": p.min_checking_balance,
                       "confirmation_ttl_seconds": p.confirmation_ttl_seconds,
                       "payees": [v.name for v in s.payees.values()]},
            "audit": {"entries": len(a.audit.entries), "verified": ok},
            "planner": a.planner.name,
            "source": self.source,
            "today": today.isoformat(),
            "balances": s.balances,
            "month": summary,
            "labels": LABELS,
            "trends": an.category_trends(s.transactions, today, exclude_ids=one_offs)[:8],
            "forecast": an.forecast_month_end(s, today),
            "insights": an.insights(s, today),
            "budgets": s.budgets,
            "cancelled": sorted(s.cancelled_subscriptions),
            "disputes": s.disputes,
            "pending": [{"token": p.token, "tool": p.tool, "summary": p.summary}
                        for p in a.toolbox.confirmations.pending()],
        }

    def handle(self, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
        body = body or {}
        if method == "GET" and path == "/api/dashboard":
            return 200, self.dashboard()
        if method == "GET" and path == "/api/audit":
            ok, bad = self.agent.audit.verify()
            return 200, {"verified": ok, "first_bad_entry": bad, "entries": self.agent.audit.entries[-60:]}
        if method == "POST" and path == "/api/chat":
            msg = str(body.get("message", ""))[:1000].strip()
            if not msg:
                return 400, {"error": "empty message"}
            return 200, self.agent.chat(msg)
        if method == "POST" and path == "/api/confirm":
            return 200, self.agent.confirm(str(body.get("token", "")), bool(body.get("approve", False)))
        if method == "POST" and path == "/api/statement":
            try:
                return 200, self.load_statement(str(body.get("kind", "")), body.get("data"),
                                                str(body.get("filename", "")), bool(body.get("use_ai", True)))
            except StatementError as e:
                return 422, {"error": str(e)}
            except Exception as e:  # never leak statement content in errors
                return 422, {"error": f"Couldn't read this file ({type(e).__name__})."}
        if method == "POST" and path == "/api/reset":
            self.reset()
            return 200, {"status": "reset"}
        return 404, {"error": "not found"}
