"""Deterministic financial analytics: bill analysis, budgeting, forecasting and insights.

Every number the assistant shows comes from these functions, never from the language model,
so the advice is reproducible and auditable.
"""
from __future__ import annotations

import calendar
import math
import re
from collections import defaultdict
from datetime import date, timedelta
from statistics import mean, median, pstdev

from .categorize import NEEDS, WANTS, LABELS

BUFFER = 5000.0                 # minimum checking balance the assistant always protects
DEFAULT_SAVINGS_PCT = 20


def money(x: float) -> str:
    return f"BDT {x:,.0f}"


def _d(s: str) -> date:
    return date.fromisoformat(s)


def _spend(t) -> bool:
    return t.amount < 0 and t.category != "savings"


def normalize_merchant(name: str) -> str:
    return re.sub(r"[\d\s]+$", "", name.lower()).strip()


def full_months(txns, today: date) -> list[str]:
    current = today.isoformat()[:7]
    return sorted({t.date[:7] for t in txns if t.date[:7] < current})


def month_end(today: date) -> date:
    return date(today.year, today.month, calendar.monthrange(today.year, today.month)[1])


# ---------------------------------------------------------------- summaries

def monthly_summary(txns, month: str, exclude_ids: set | None = None) -> dict:
    exclude_ids = exclude_ids or set()
    ts = [t for t in txns if t.date.startswith(month) and t.id not in exclude_ids]
    income = sum(t.amount for t in ts if t.amount > 0 and t.category == "income")
    by_cat: dict[str, float] = defaultdict(float)
    for t in ts:
        if _spend(t):
            by_cat[t.category] += -t.amount
    spend = sum(by_cat.values())
    saved = sum(-t.amount for t in ts if t.category == "savings")
    return {
        "month": month,
        "income": round(income, 2),
        "spend": round(spend, 2),
        "net": round(income - spend, 2),
        "moved_to_savings": round(saved, 2),
        "savings_rate_pct": round(100 * (income - spend) / income, 1) if income else None,
        "needs": round(sum(v for k, v in by_cat.items() if k in NEEDS), 2),
        "wants": round(sum(v for k, v in by_cat.items() if k not in NEEDS), 2),
        "by_category": dict(sorted(((k, round(v, 2)) for k, v in by_cat.items()), key=lambda kv: -kv[1])),
        "share_of_spend_pct": {k: round(100 * v / spend, 1) for k, v in
                               sorted(by_cat.items(), key=lambda kv: -kv[1])} if spend else {},
    }


def avg_monthly_income(txns, today: date) -> float:
    months = full_months(txns, today)[-3:]
    if not months:
        return 0.0
    return mean(monthly_summary(txns, m)["income"] for m in months)


def category_trends(txns, today: date, exclude_ids: set | None = None) -> list[dict]:
    exclude_ids = exclude_ids or set()
    months = full_months(txns, today)[-3:]
    table: dict[str, list[float]] = defaultdict(lambda: [0.0] * len(months))
    for t in txns:
        if _spend(t) and t.date[:7] in months and t.id not in exclude_ids:
            table[t.category][months.index(t.date[:7])] += -t.amount
    out = []
    for cat, vals in table.items():
        first, last = vals[0], vals[-1]
        change = round(100 * (last - first) / first, 1) if first else None
        out.append({"category": cat, "label": LABELS.get(cat, cat), "months": months,
                    "monthly": [round(v, 2) for v in vals], "avg": round(mean(vals), 2),
                    "change_pct": change})
    return sorted(out, key=lambda r: -r["avg"])


# ---------------------------------------------------------------- bill analysis

def detect_recurring(txns, today: date) -> list[dict]:
    groups: dict[str, list] = defaultdict(list)
    for t in txns:
        if _spend(t):
            groups[normalize_merchant(t.merchant)].append(t)
    months = sorted({t.date[:7] for t in txns})
    out = []
    for key, ts in groups.items():
        per_month = [sum(1 for t in ts if t.date[:7] == m) for m in months]
        active = [c for c in per_month if c]
        if len(active) < 2 or median(active) > 2:
            continue
        amounts = [-t.amount for t in ts]
        med = median(amounts)
        if (max(amounts) - min(amounts)) / med > 0.6:
            continue
        days = sorted({_d(t.date) for t in ts})
        gaps = [(b - a).days for a, b in zip(days, days[1:])]
        interval = median(gaps) if gaps else 30
        typical = int(median(active))
        last = days[-1]
        out.append({
            "merchant": ts[-1].merchant,
            "category": ts[-1].category,
            "amount": round(med, 2),
            "times_per_month": typical,
            "monthly_cost": round(med * typical, 2),
            "interval_days": interval,
            "last_charged": last.isoformat(),
            "next_expected": (last + timedelta(days=round(interval))).isoformat(),
            "is_subscription": ts[-1].category in {"subscriptions", "fitness"},
        })
    return sorted(out, key=lambda r: -r["monthly_cost"])


def detect_duplicates(txns, today: date) -> list[dict]:
    billers = {normalize_merchant(r["merchant"]) for r in detect_recurring(txns, today)}
    seen: dict[tuple, object] = {}
    dups = []
    for t in sorted(txns, key=lambda t: t.date):
        if not _spend(t) or normalize_merchant(t.merchant) not in billers:
            continue
        k = (normalize_merchant(t.merchant), t.amount)
        prev = seen.get(k)
        if prev and (_d(t.date) - _d(prev.date)).days <= 3:
            dups.append({"txn_id": t.id, "duplicate_of": prev.id, "merchant": t.merchant,
                         "amount": -t.amount, "date": t.date,
                         "reason": f"{t.merchant} charged {money(-t.amount)} twice within 3 days"})
        seen[k] = t
    return dups


def detect_anomalies(txns, today: date, window_days: int = 60) -> list[dict]:
    recurring = {normalize_merchant(r["merchant"]) for r in detect_recurring(txns, today)}
    income = avg_monthly_income(txns, today)
    since = today - timedelta(days=window_days)
    out = []
    for t in txns:
        if not _spend(t) or _d(t.date) < since or normalize_merchant(t.merchant) in recurring:
            continue
        amt = -t.amount
        peers = [-p.amount for p in txns if _spend(p) and p.category == t.category and p.id != t.id]
        reason = None
        if len(peers) >= 3:
            m, s = mean(peers), pstdev(peers)
            if amt >= 3000 and amt > max(3 * median(peers), m + 3 * s):
                reason = f"{money(amt)} is far above your usual {LABELS.get(t.category, t.category).lower()} spend (~{money(median(peers))})"
        if reason is None and income >= 1000 and amt >= 0.2 * income:
            reason = f"one-off spend of {money(amt)} is {100 * amt / income:.0f}% of your monthly income"
        if reason:
            out.append({"txn_id": t.id, "date": t.date, "merchant": t.merchant,
                        "category": t.category, "amount": amt, "reason": reason})
    return out


# ---------------------------------------------------------------- budget & forecast

def budget_plan(txns, today: date, savings_pct: float = DEFAULT_SAVINGS_PCT) -> dict:
    income = avg_monthly_income(txns, today)
    anomalies = {a["txn_id"] for a in detect_anomalies(txns, today, window_days=400)}
    trends = category_trends(txns, today, exclude_ids=anomalies)
    needs = sum(r["avg"] for r in trends if r["category"] in NEEDS)
    wants = sum(r["avg"] for r in trends if r["category"] not in NEEDS)
    savings_target = income * savings_pct / 100
    # Needs are budgeted at their current cost (latest month or average, whichever is higher,
    # rounded up to BDT 100); wants are then scaled down so the savings target still fits.
    need_budget = {r["category"]: math.ceil(max(r["avg"], r["monthly"][-1]) / 100) * 100
                   for r in trends if r["category"] in NEEDS}
    allowed_wants = max(0.0, income - sum(need_budget.values()) - savings_target)
    scale = min(1.0, allowed_wants / wants) if wants else 1.0
    cats = []
    for r in trends:
        is_need = r["category"] in NEEDS
        budget = need_budget[r["category"]] if is_need else math.floor(r["avg"] * scale / 100) * 100
        cats.append({"category": r["category"], "label": r["label"], "type": "need" if is_need else "want",
                     "avg_spend": r["avg"], "budget": float(budget)})
    total_budget = sum(c["budget"] for c in cats)
    return {
        "income": round(income, 2),
        "savings_pct": savings_pct,
        "savings_target": round(savings_target, 2),
        "needs_avg": round(needs, 2),
        "wants_avg": round(wants, 2),
        "wants_cut_pct": round(100 * (1 - scale), 1),
        "categories": cats,
        "total_budget": total_budget,
        "projected_savings": round(income - total_budget, 2),
        "method": "Needs at current cost; wants scaled so savings reach the target (adapted 50/30/20).",
    }


def forecast_month_end(state, today: date, savings_pct: float = DEFAULT_SAVINGS_PCT) -> dict:
    txns = state.transactions
    end = month_end(today)
    days_left = (end - today).days + 1
    balance = state.balances["checking"]
    recurring = detect_recurring(txns, today)
    rec_keys = {normalize_merchant(r["merchant"]) for r in recurring}
    anomalies = {a["txn_id"] for a in detect_anomalies(txns, today)}
    due = []
    for r in recurring:
        if normalize_merchant(r["merchant"]) in {normalize_merchant(m) for m in state.cancelled_subscriptions}:
            continue
        nxt = _d(r["next_expected"])
        if today <= nxt <= end:
            due.append({"merchant": r["merchant"], "amount": r["amount"], "date": nxt.isoformat()})
    due_total = sum(d["amount"] for d in due)
    since = today - timedelta(days=30)
    flexible = [-t.amount for t in txns if _spend(t) and _d(t.date) >= since
                and normalize_merchant(t.merchant) not in rec_keys and t.id not in anomalies]
    daily = sum(flexible) / 30
    projected = balance - due_total - daily * days_left
    month = today.isoformat()[:7]
    saved = monthly_summary(txns, month)["moved_to_savings"]
    goal_left = max(0.0, avg_monthly_income(txns, today) * savings_pct / 100 - saved)
    safe_today = max(0.0, (balance - due_total - goal_left - BUFFER) / days_left)
    return {
        "as_of": today.isoformat(),
        "checking_balance": round(balance, 2),
        "days_left": days_left,
        "bills_due": due,
        "bills_due_total": round(due_total, 2),
        "avg_daily_flexible_spend": round(daily, 2),
        "projected_month_end_balance": round(projected, 2),
        "savings_goal_remaining": round(goal_left, 2),
        "safe_to_spend_per_day": round(safe_today, 2),
    }


def affordability(state, today: date, amount: float) -> dict:
    f = forecast_month_end(state, today)
    after = f["projected_month_end_balance"] - amount
    verdict = ("yes" if after - f["savings_goal_remaining"] >= BUFFER
               else "tight" if after >= BUFFER else "no")
    return {"amount": amount, "projected_after_purchase": round(after, 2), "verdict": verdict,
            "buffer": BUFFER, "savings_goal_remaining": f["savings_goal_remaining"]}


def compare_months(txns, a: str, b: str) -> dict:
    """Side-by-side of two months with exact differences, so the model never has to do the arithmetic."""
    sa, sb = monthly_summary(txns, a), monthly_summary(txns, b)
    cats = sorted(set(sa["by_category"]) | set(sb["by_category"]),
                  key=lambda c: -abs(sb["by_category"].get(c, 0) - sa["by_category"].get(c, 0)))
    rows = []
    for c in cats:
        x, y = sa["by_category"].get(c, 0.0), sb["by_category"].get(c, 0.0)
        rows.append({"category": LABELS.get(c, c), a: round(x), b: round(y), "change": round(y - x),
                     "change_pct": round(100 * (y - x) / x, 1) if x else None})
    return {"months": [a, b],
            "income": {a: sa["income"], b: sb["income"]},
            "spend": {a: sa["spend"], b: sb["spend"], "change": round(sb["spend"] - sa["spend"]),
                      "change_pct": round(100 * (sb["spend"] - sa["spend"]) / sa["spend"], 1) if sa["spend"] else None},
            "savings_rate_pct": {a: sa["savings_rate_pct"], b: sb["savings_rate_pct"]},
            "by_category_biggest_changes_first": rows}


def savings_goal_plan(state, today: date, amount: float, months: int) -> dict:
    """Exact plan for a savings goal: what's needed per month, which wants to trim, and whether it's realistic."""
    txns = state.transactions
    one_offs = {a["txn_id"] for a in detect_anomalies(txns, today, window_days=400)}
    trends = category_trends(txns, today, exclude_ids=one_offs)
    income = avg_monthly_income(txns, today)
    typical_spend = sum(r["avg"] for r in trends)
    surplus = income - typical_spend
    need = amount / months
    gap = max(0.0, need - max(surplus, 0.0))
    cuts, remaining = [], gap
    for r in sorted((r for r in trends if r["category"] in WANTS and r["avg"] >= 300), key=lambda r: -r["avg"]):
        if remaining <= 0:
            break
        cut = min(math.ceil(r["avg"] * 0.5 / 100) * 100, math.ceil(remaining / 100) * 100)  # trim a want by at most half
        cuts.append({"category": r["category"], "label": r["label"], "current_per_month": round(r["avg"]),
                     "suggested_limit": round(r["avg"] - cut), "saves_per_month": cut})
        remaining -= cut
    saved_by_cuts = sum(c["saves_per_month"] for c in cuts)
    monthly_saving = max(surplus, 0.0) + saved_by_cuts
    feasible = monthly_saving >= need - 1
    needs = sum(r["avg"] for r in trends if r["category"] in NEEDS)
    shortfall = 0.0 if feasible else round((need - monthly_saving) * months)
    return {
        "goal_amount": amount, "months": months,
        "needed_per_month": round(need),
        "typical_monthly_income": round(income),
        "typical_monthly_spend_excl_one_offs": round(typical_spend),
        "current_monthly_leftover": round(surplus),
        "suggested_cuts": cuts,
        "monthly_saving_with_cuts": round(monthly_saving),
        "feasible_in_time": feasible,
        "months_needed_with_cuts": math.ceil(amount / monthly_saving) if monthly_saving > 0 else None,
        "shortfall_if_on_time": shortfall,
        "savings_balance": state.balances["savings"],
        "savings_after_covering_shortfall": round(state.balances["savings"] - shortfall),
        "emergency_fund_months_after": round((state.balances["savings"] - shortfall) / needs, 1) if needs else None,
        "rule": "Wants trimmed by at most 50% each; essentials and family support are never cut.",
    }


# ---------------------------------------------------------------- insights

def insights(state, today: date) -> list[dict]:
    txns = state.transactions
    out = []
    plan = budget_plan(txns, today)
    budgets = {c["category"]: c["budget"] for c in plan["categories"]}

    for r in category_trends(txns, today):
        if r["category"] in WANTS and r["change_pct"] and r["change_pct"] >= 30 and r["monthly"][-1] >= 2000:
            extra = r["monthly"][-1] - r["monthly"][0]
            out.append({
                "id": f"drift_{r['category']}", "kind": "habit_drift",
                "title": f"{r['label']} up {r['change_pct']:.0f}% in {len(r['months'])} months",
                "detail": f"{money(r['monthly'][0])} in {r['months'][0]} -> {money(r['monthly'][-1])} in {r['months'][-1]}.",
                "monthly_impact": round(extra, 2),
                "action": {"tool": "set_category_budget",
                           "args": {"category": r["category"], "monthly_limit": budgets.get(r["category"], r["avg"])}},
            })

    recurring = detect_recurring(txns, today)
    subs = [r for r in recurring if r["category"] == "subscriptions"
            and r["merchant"] not in state.cancelled_subscriptions]
    if len(subs) >= 2:
        total = sum(s["monthly_cost"] for s in subs)
        top = max(subs, key=lambda s: s["monthly_cost"])
        out.append({
            "id": "subs_overlap", "kind": "subscriptions",
            "title": f"{len(subs)} streaming/media subscriptions cost {money(total)}/month",
            "detail": ", ".join(f"{s['merchant']} {money(s['monthly_cost'])}" for s in subs)
                      + f". Cancelling {top['merchant']} alone saves {money(top['monthly_cost'] * 12)}/year.",
            "monthly_impact": round(top["monthly_cost"], 2),
            "action": {"tool": "cancel_subscription", "args": {"merchant": top["merchant"]}},
        })

    disputed = {d["txn_id"] for d in state.disputes}
    for d in detect_duplicates(txns, today):
        if d["txn_id"] in disputed:
            continue
        out.append({
            "id": f"dup_{d['txn_id']}", "kind": "duplicate_charge",
            "title": f"Possible duplicate charge: {d['merchant']} {money(d['amount'])}",
            "detail": d["reason"] + f" (on {d['date']}). Dispute it to get the money back.",
            "monthly_impact": d["amount"],
            "action": {"tool": "dispute_transaction", "args": {"txn_id": d["txn_id"], "reason": "duplicate charge"}},
        })

    for a in detect_anomalies(txns, today):
        out.append({
            "id": f"anom_{a['txn_id']}", "kind": "anomaly",
            "title": f"Unusual spend: {a['merchant']} {money(a['amount'])} on {a['date']}",
            "detail": a["reason"][:1].upper() + a["reason"][1:] + ". If you don't recognise it, dispute it.",
            "monthly_impact": 0.0, "action": None,
        })

    months = full_months(txns, today)[-3:]
    if months:
        one_offs = {a["txn_id"] for a in detect_anomalies(txns, today, window_days=400)}
        rates = [monthly_summary(txns, m, one_offs)["savings_rate_pct"] or 0 for m in months]
        avg_rate = mean(rates)
        if avg_rate < DEFAULT_SAVINGS_PCT:
            f = forecast_month_end(state, today)
            movable = max(0.0, state.balances["checking"] - f["bills_due_total"] - BUFFER
                          - f["avg_daily_flexible_spend"] * f["days_left"])
            amount = float(int(min(movable, f["savings_goal_remaining"]) // 500 * 500))
            out.append({
                "id": "savings_rate", "kind": "savings",
                "title": f"Savings rate is {avg_rate:.0f}% (target {DEFAULT_SAVINGS_PCT}%)",
                "detail": f"Excluding one-off purchases, last {len(months)} months: " + ", ".join(f"{m} {r:.0f}%" for m, r in zip(months, rates))
                          + (f". You can safely move {money(amount)} to savings now." if amount else "."),
                "monthly_impact": amount,
                "action": {"tool": "move_to_savings", "args": {"amount": amount}} if amount else None,
            })

    needs = plan["needs_avg"] or 1
    cover = state.balances["savings"] / needs
    if state.balances["savings"] <= 0:
        out.append({
            "id": "emergency_fund", "kind": "resilience",
            "title": "No emergency fund in this account",
            "detail": f"Aim for 3 months of essentials ({money(3 * needs)}). Even {money(max(500, round(needs * 0.05, -2)))} "
                      "a month into a separate savings account or DPS builds it steadily.",
            "monthly_impact": 0.0, "action": None,
        })
    else:
        out.append({
            "id": "emergency_fund", "kind": "resilience",
            "title": f"Emergency fund covers {cover:.1f} months of essentials",
            "detail": f"Savings {money(state.balances['savings'])} vs essentials {money(needs)}/month. "
                      + ("Healthy (3-6 months recommended)." if cover >= 3 else "Aim for at least 3 months."),
            "monthly_impact": 0.0, "action": None,
        })

    kind_rank = {"duplicate_charge": 0, "anomaly": 1, "habit_drift": 2, "subscriptions": 3, "savings": 4, "resilience": 5}
    return sorted(out, key=lambda i: (kind_rank.get(i["kind"], 9), -i["monthly_impact"]))
