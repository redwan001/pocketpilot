# PocketPilot: Technical Documentation

**Competition:** 2026 Shenzhen International FinTech Competition (FinTechathon), International Track  
**Theme:** AI as a Financial Participant · **Topic A:** Personal Finance Assistant  
**Team:** Team PocketPilots · Redoun Islam (BSc, University of the People) · Md Akif Quader (PhD, Mechanical Engineering, The University of Memphis)

---

## 1. Problem and users

Young earners in emerging markets, such as a junior software engineer in Dhaka earning BDT 65,000 a month, handle
money through a salary account, a mobile wallet (bKash), cash, regular **family support** and food-delivery apps.
Mainstream budgeting tools assume cards and bank feeds, give generic tips, and cannot *do* anything.

PocketPilot is an AI agent that **participates** in the user's finances. It analyses income and expenses, produces
concrete, numeric recommendations, and carries them out in a bank account, but only within hard limits and only with
explicit approval.

Design goals map directly to the scoring rubric:

| Rubric | Weight | How PocketPilot addresses it |
|---|---|---|
| Task completion | 40% | 17 tools and a real LLM planner; end-to-end flows from insight to approved action, executed in a sandbox account with logs |
| Security & compliance | 30% | 3 permission tiers, signed one-time approvals, code-enforced limits, injection defence, PII redaction, hash-chained audit |
| Innovation & interaction | 30% | Built for mobile money and family support; "safe-to-spend today"; habit-drift alerts; one-click insight → action; works offline |

## 2. System architecture

```
          ┌────── Web UI (web/index.html) · runs the agent in the browser (Pyodide) ──────┐
          │  dashboard · chat · APPROVAL CARDS (Approve / Decline) · audit-log viewer     │
          └───────────────┬───────────────────────────────────────────▲───────────────────┘
                 /api/chat│                              /api/confirm │ (token from UI only)
          ┌───────────────▼───────────────────────────────────────────┴───────────────────┐
          │ Agent (agent.py)                                                              │
          │   Planner = Qwen3 on Workers AI (via /api/llm) | Claude | RulePlanner offline │
          └───────────────┬───────────────────────────────────────────────────────────────┘
                          │ tool calls (name + JSON args)
          ┌───────────────▼───────────────────────────────────────────────────────────────┐
          │ Toolbox (tools.py)                                                             │
          │  1 schema validation → 2 tier check → 3 rate limit → 4 policy pre-check        │
          │  READ / ADVISE: run → redact → result      ACT: issue signed one-time token    │
          └───────┬───────────────────────────────┬──────────────────────────┬────────────┘
                  │                               │                          │
       ┌──────────▼─────────┐        ┌────────────▼────────────┐   ┌────────▼─────────┐
       │ Analytics engine   │        │ Security (security.py)  │   │ Audit log        │
       │ analytics.py       │        │ policy · tokens · PII · │   │ SHA-256 chained  │
       │ categorize.py      │        │ injection guard · rate  │   │ JSONL, verify()  │
       └──────────┬─────────┘        └─────────────────────────┘   └──────────────────┘
                  │
       ┌──────────▼──────────────────────────────┐
       │ Sandbox bank (data.py + state)          │
       │ accounts, transactions, payees, budgets │
       └─────────────────────────────────────────┘
```

**Key principle: the model decides *what* to do, while code decides *whether it is allowed*.** All numbers come from the
deterministic analytics engine, and every side effect passes through the same gate regardless of which planner asked.

### 2.1 Components

| Module | Responsibility |
|---|---|
| `data.py` | Generates a reproducible 3.5-month history for the synthetic persona (fixed seed 42), with planted test events: a duplicate charge, a large anomaly and a prompt-injection memo |
| `categorize.py` | Explainable merchant-rule categoriser; maps categories to *needs* and *wants* |
| `analytics.py` | Monthly summaries, 3-month trends, recurring-bill detection, duplicate and anomaly detection, budget planner, month-end forecast, affordability, ranked insights |
| `security.py` | `Tier`, `PolicyEngine`, `ConfirmationManager`, `RateLimiter`, `scan_untrusted` / `sanitize_untrusted`, `redact`, `AuditLog` |
| `tools.py` | 17 tools with JSON schemas, tier tags, run functions and, for ACT, policy pre-checks |
| `agent.py` | Session orchestration; `OpenModelPlanner` (Qwen3 on Cloudflare Workers AI, the live default), `ClaudePlanner` (Anthropic Messages API, optional) and `RulePlanner` (deterministic offline fallback) |
| `api.py` | Transport-independent JSON API shared by the local server and the in-browser build |
| `importer.py` | Statement import: CSV/Excel and text PDFs, bank and credit-card layouts |
| `functions/api/llm.js` | Cloudflare Pages Function: hardened gateway to Workers AI (no API key in the browser) |
| `server.py` / `cli.py` | Standard-library HTTP server (localhost only, body-size limit, CSP header) and terminal client |

### 2.2 Tool catalogue

| Tier | Tool | Purpose |
|---|---|---|
| READ | `get_balances` | Checking and savings balances |
| READ | `list_transactions` | Filtered history; memos returned as `memo_untrusted` after screening |
| READ | `spending_summary` | Month income, spend, savings rate, category breakdown, share of spend and 3-month trends |
| READ | `compare_months` | Two months side by side with exact per-category changes |
| READ | `find_recurring_bills` | Recurring bills and subscriptions with next expected date |
| READ | `find_unusual_activity` | Duplicates, anomalies, suspicious memos |
| ADVISE | `get_budget_plan` | Category budgets for a chosen savings % |
| ADVISE | `forecast_month_end` | Projected balance, bills due, safe-to-spend per day |
| ADVISE | `get_insights` | Ranked insights, each with a ready-to-run suggested action |
| ADVISE | `check_affordability` | yes / tight / no for a purchase |
| ADVISE | `plan_savings_goal` | Exact plan for a goal (amount, months): monthly target, wants to trim, feasibility |
| ACT | `set_category_budget` | Set a monthly category limit |
| ACT | `move_to_savings` | Checking → savings (own account) |
| ACT | `cancel_subscription` | Request cancellation of a detected subscription |
| ACT | `pay_bill` | Pay a saved biller |
| ACT | `transfer_to_payee` | Send money to a *saved* payee (schema enum, so arbitrary numbers are impossible) |
| ACT | `dispute_transaction` | Open a dispute for a charge |

### 2.3 Request lifecycle (ACT example: "move 10000 to savings")

1. The user message is logged, with PII redacted and override-style injection patterns flagged.
2. The planner selects `move_to_savings {"amount": 10000}`.
3. The Toolbox validates against the schema (type, minimum, no extra fields), checks the rate limit, then runs the
   **policy pre-check**: amount > 0, ≤ BDT 20,000 per transfer, ≤ BDT 30,000 per day, and checking stays ≥ BDT 5,000.
4. A one-time token is issued: `HMAC-SHA256(secret, token | tool | canonical_json(args))`, with a 300-second TTL. The
   token goes **only to the UI**; the model receives "awaiting user confirmation" and is told not to claim success.
5. The user clicks **Approve**. The token is redeemed (deleted on first use), the signature is re-verified, and the
   **policy is checked again** against current state, closing the gap between when an action is checked and when it
   runs. Then the action executes.
6. Every step is appended to the hash-chained audit log.

## 2.4 Data sources: demo account or the user's own statement

The app opens on a synthetic demo account. Users can switch to their own data with **Upload statement**:

| Format | How it's read (all in the browser) |
|---|---|
| CSV | The header row is found automatically, even below account-info rows. Columns are matched by name: Date/Txn Date; Description/Narration/Particulars; Debit/Withdrawal and Credit/Deposit, or Amount with Dr/Cr. Date order (dd/mm vs mm/dd) is detected. |
| Excel (.xlsx/.xls) | SheetJS converts the first sheet to CSV. |
| Credit-card PDF | Detected from card wording (credit limit, minimum payment). Dates without a year take it from the statement period; a single Amount column is read with card signs (purchases are spending, card payments are excluded as transfers); currency (e.g. USD) is detected and used for amounts, thresholds and limits. The dashboard shows spending totals instead of a checking balance. |
| PDF e-statement | pdf.js extracts positioned text. `importer.parse_pdf_items` rebuilds the lines, finds the Debit/Credit/Balance header and assigns each amount to the nearest column. Without a header, it uses the change in running balance, Dr/Cr markers, or keywords. It joins wrapped narration lines and skips opening/closing-balance rows. It supports password-protected PDFs and detects scanned PDFs (no text) with a clear message. |

Narrations are categorised with whole-word rules for Bangladeshi banking terms (ATM, BEFTN/NPSB/RTGS, POS, VAT and
charges, DPS/FDR, EMI and so on). "Today" becomes the day after the last transaction. Tools that need saved
payees are hidden for uploaded data. The same analytics, AI planner and security layer then run unchanged.
`scripts/make_samples.py` produces a sample PDF and CSV statement for a second persona, a university student.

## 3. Core algorithms

All algorithms are deterministic, explainable and unit-tested.

### 3.1 Categorisation

Ordered merchant-keyword rules, for example `foodpanda → food_delivery` and `send money → family_support`. Categories
are grouped into **needs** (housing, utilities, internet and mobile, groceries, transport, family support, health) and
**wants** (food delivery, dining, subscriptions, fitness, shopping, electronics). Family support is deliberately a
*need*; this is culturally important in South Asia and usually ignored by Western budgeting apps.

### 3.2 Recurring-bill detection

Outflows are grouped by normalised merchant name. A group is recurring if:

- it appears in ≥ 2 months;
- the median count per active month is ≤ 2, which excludes frequent spending such as food delivery;
- the amount spread is (max − min)/median ≤ 0.6, which allows variable utility bills.

Output per bill: median amount, typical times per month, monthly cost, median interval, last date and next expected
date (last date + median interval).

### 3.3 Duplicate-charge detection

A recurring biller charged the **same amount twice within 3 days**. Restricting this to billers avoids false
positives from fixed-price everyday items, such as two coffees at BDT 350.

### 3.4 Anomaly detection (last 60 days, non-recurring outflows)

A transaction is flagged if either rule holds:

- **Peer rule** (≥ 3 peers in the same category): amount ≥ BDT 3,000 and amount > max(3 × peer median,
  peer mean + 3σ).
- **Income rule**: amount ≥ 20% of average monthly income.

### 3.5 Budget planner (adapted 50/30/20)

1. Income is averaged over the last 3 full months.
2. Each category's 3-month average is computed, **excluding detected one-off anomalies**.
3. Each **need** is budgeted at max(average, latest month), rounded up to BDT 100. Essentials are never cut.
4. Allowed wants = income − Σ need budgets − savings target (savings % × income).
5. Wants are scaled by `min(1, allowed_wants / Σ wants)` and rounded down to BDT 100.

The plan then guarantees `income − Σ budgets ≥ savings target` whenever the target is feasible; this is
covered by a unit test. The user can choose a gentler target ("budget with 10% savings").

### 3.6 Month-end forecast and safe-to-spend

- `bills_due` = recurring bills whose next expected date falls between today and month end, excluding cancelled ones.
- `daily_flexible` = last-30-day spend that is neither recurring nor an anomaly, divided by 30.
- `projected_balance` = checking − bills_due − daily_flexible × days_left.
- `safe_to_spend_per_day` = max(0, (checking − bills_due − savings goal remaining − BDT 5,000 buffer) / days_left).

This one number turns a budget into a daily decision, which is the key interaction idea for users who think in
"how much can I spend today", not in spreadsheets.

### 3.7 Insights engine

The engine produces ranked insights, each with a quantified monthly impact and a **pre-filled action**. The UI turns
these into one-click "Ask PocketPilot to do this" buttons, which still require approval. Insight types, in priority
order:

1. Duplicate charge
2. Anomaly
3. Habit drift: a want category growing ≥ 30% over 3 months
4. Overlapping subscriptions
5. Savings-rate gap, measured excluding one-off purchases, with a safe transfer amount
6. Emergency-fund coverage in months

## 4. AI planner

**Deployed configuration (live demo):** Qwen3 30B (`@cf/qwen/qwen3-30b-a3b-fp8`) on Cloudflare Workers AI. We
compared four free-tier models that support tool calling on the same eight realistic questions
(`scripts/eval_models.py`, results in `evidence/model_eval.md`):

| Model | Result |
|---|---|
| **Qwen3 30B** | Best: right tools, multi-tool reasoning, accurate numbers, Bangla support, about 3–12 s |
| GPT-OSS 120B | Strong reasoning, but drifted into Bangla for English questions, very long, up to 36 s |
| Gemma 4 26B | Acceptable, but failed some multi-step questions |
| Llama 4 Scout | Unusable: wrote tool calls as text and claimed actions it had not taken |

The browser runs the agent (Pyodide). For each model step, the Python planner *yields* a request; JavaScript posts it
to the Pages Function `/api/llm`, which calls Workers AI and returns the reply. The same generator-based planner runs
synchronously on the server (`POCKETPILOT_LLM_URL`) and is tested offline with a scripted fake model.

Accuracy design: the model never does arithmetic that matters. Dedicated tools return exact figures:
- `compare_months`: per-category differences between two months
- `plan_savings_goal`: per-month target, trims to wants (each by at most 50%), and feasibility
- `share_of_spend_pct`: each category's share of spending

The planner also:
- adds a language hint per turn (Bangla script or Banglish, detected deterministically);
- strips stray tokens and internal tool names from replies;
- merges duplicate action requests.

### 4.1 Multi-agent design: Planner, Guardian and Verifier

PocketPilot separates duties across agents, so that no single model is trusted with money or with facts:

| Agent | Job | What it sees | Can it act? |
|---|---|---|---|
| **Pilo (planner)** | Understands the request, calls tools, writes the answer | Conversation and masked tool results | Only *requests* actions |
| **Guardian (risk reviewer)** | Reviews every money action **after** the hard policy limits pass, **before** the user sees an approval card | Only the user's own words, the previous assistant message and the proposed action, **never transaction data** (where injected text lives) | Can **block**; cannot approve |
| **Verifier (fact checker)** | Checks every figure in each reply against tool results and the user's own words | Reply, tool results | Can force **one** self-correction; marks anything still unsupported |
| Policy engine (code) | Hard limits, re-checked on approval | Account state | Enforces |

**Guardian decision.** Deterministic rules give *asked* (do the user's words ask for this kind of action, including a
"yes" to a suggestion) and risk signals: amount versus checking balance, an amount different from the one typed,
instruction-like text seen in data this turn, and a first transfer to a payee. An independent model call, with a separate
system prompt and no tools, returns `{matches_request, risk, reason}`. The model can raise a warning, but it can only
block by judging that the user did not ask for the action. Rules alone can block on hard signals, for example an action
the user never asked for, or more than 80% of the balance. Offline, the rules run alone. The review appears on the
approval card and in the audit log; a blocked action's approval token is cancelled before it reaches the user.

**Verifier (hallucination control).** Amounts, percentages and other figures above 31 are extracted from the reply.
Dates, transaction IDs, small counts and years are ignored. Each figure must match a number in this session's tool
results or the user's message, within normal rounding. If any figure is unsupported, the planner gets a "fact check"
message naming the figures and rewrites the answer once. Anything still unsupported is appended as "Not verified
against your data". Each answer shows a badge such as "✓ Verifier: 10 figures checked against your data". Tests cover
grounded answers, a corrected hallucination, an uncorrectable one, a blocked unrequested action and offline review.

### 4.0 Original planner notes

### 4.1 Claude planner

- Uses the Anthropic Messages API with native tool use. Tool schemas are generated from the same registry the
  security layer uses.
- Default model is `claude-opus-5-5`, configurable through `POCKETPILOT_MODEL`; `claude-haiku-5-5` is the low-cost
  option. Effort is `low` by default for chat-style latency.
- Server-side refusal fallback is enabled.
- The system prompt states the security rules, but **they are enforced in code, not by the prompt**.
- Tool results are JSON. Third-party text arrives already screened, in a field explicitly named `memo_untrusted`.

### 4.2 Offline planner

A deterministic intent router (regular expressions plus alias tables for payees, billers and categories) calls the
same tools and writes the reply from their results. It means:

- judges can run the full system without an API key;
- tests are reproducible;
- the product still works if the LLM API is unavailable. The agent falls back automatically and logs a
  `planner_error`.

## 5. Security design (summary)

See `SECURITY_SELF_ASSESSMENT.md` for the full threat model and risk list.

| Control | Implementation |
|---|---|
| Least privilege | Tier tags; ACT is never executed by a model call |
| Human-in-the-loop | One-time, expiring, HMAC-signed confirmation tokens kept from the model |
| Hard limits | `PolicyEngine`: per-transfer cap, daily cap, saved payees only, minimum balance; re-checked on execution |
| Input validation | JSON-schema validation, `additionalProperties: false`, enums for payees and billers, numeric bounds, string lengths |
| Prompt-injection defence | Third-party text screened and withheld; payees are an enum, so injected numbers cannot become recipients; override-style text in user messages triggers a warning |
| Privacy | Phone, card, NID and email redaction on all tool output and logs; synthetic data only; localhost-only server |
| Accountability | Append-only SHA-256 hash-chained audit log with `verify()` and a UI integrity badge |
| Abuse resistance | Action rate limit (10 per minute), request-size limit, unknown tools rejected |
| Compliance | No investment-product advice (system prompt); sandbox-only actions; explainable, reproducible numbers |

## 6. Evaluation and execution evidence

- **48 automated tests** (`python -m unittest discover -s tests -v`) cover analytics correctness, every security
  primitive, end-to-end agent flows, the AI tool loop with a scripted fake model, and statement import (bank PDF/CSV,
  PDF without headers, credit-card PDF, invalid files).
- **Live AI evaluation** (`scripts/eval_models.py`, `evidence/model_eval.md`): 8 realistic questions answered by the
  deployed Qwen3 planner, with the tools each answer used.
- **Statement import:** the sample 2-page bank PDF yields 68/68 transactions with the exact closing balance.
- **Demo video** (4:08) recorded automatically against the live site, with checks that every approval completes.
- **Scripted sandbox session** (`python scripts/run_demo.py`) runs 15 user turns. Results:
  - 4 approved actions executed: a dispute, a cancellation, a budget limit, and a BDT 10,000 transfer to savings.
  - 1 action declined by the user, so nothing happened.
  - 3 attacks or violations refused: an unknown recipient, a transfer over the limit, and an injection-style command.
  - 54 audit entries, with the hash chain verified.

  Outputs: `evidence/operation_log.jsonl`, `evidence/demo_transcript.md`, `evidence/summary.json`.

## 7. Deployment

**Live:** https://pocketpilot-7jr.pages.dev on Cloudflare Pages. It's a static site, with the Python package running
in the browser through Pyodide, plus one Pages Function bound to Workers AI. Rebuild and deploy with
`python scripts/build_site.py`, then `npx wrangler pages deploy site --project-name pocketpilot`.

**Local:** Python 3.10+ with no third-party packages for the core.

1. Run `python -m pocketpilot.server --offline` and open `http://127.0.0.1:8000`.
2. To use the live AI from the local server, set `POCKETPILOT_LLM_URL=https://pocketpilot-7jr.pages.dev/api/llm`.
   For Claude instead, run `pip install anthropic` and set `ANTHROPIC_API_KEY`.

In production, the sandbox state would be replaced by a bank or wallet API adapter behind the same `Toolbox`
interface; the security layer is unchanged.

## 8. Limitations and future work

- Categorisation is rule-based. Next step: let the LLM categorise unknown merchants, with user correction.
- The sandbox is in-memory. Next step: persistent storage and open-banking or mobile-wallet connectors.
- The open model (Qwen3) can still phrase things imperfectly, for example a wrong Bangla word. Numbers come from tools.
  Next step: automatically check that every number in a reply appears in a tool result.
- The free Workers AI quota allows roughly 100 chats a day; beyond that the offline planner answers.
- Statement layouts vary by bank. Tested on bank-style and credit-card-style PDFs; more layouts can be added to the
  importer.
- Future work: savings goals with automation rules, shared family budgets, and SMS or e-statement ingestion.
