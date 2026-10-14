"""Bank statement import: CSV / Excel (as CSV) and text-based PDF.

Everything here is deterministic and runs where the user is (inside the browser in the hosted demo), so a
statement never has to leave the user's device to be parsed.

PDF statements arrive as positioned text items (extracted in the browser with pdf.js):
    [{"page": 1, "x": 52.1, "y": 700.3, "w": 40.2, "text": "12/09/2026"}, ...]
We rebuild the lines of the table, find the header (Debit / Credit / Balance ...), then read each row.
"""
from __future__ import annotations

import csv
import io
import re
from collections import Counter
from datetime import date, timedelta

from .categorize import categorize
from .data import Payee, SandboxState, Transaction

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
DATE_RE = re.compile(
    r"^(\d{4}-\d{1,2}-\d{1,2}"                                    # 2026-09-12
    r"|\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}"                           # 12/09/2026, 12-09-26
    r"|\d{1,2}[-/ ]?[A-Za-z]{3,9}[-/ ,]*\d{2,4}"                  # 12-Sep-2026, 12 Sep 26, 12SEP2026
    r"|[A-Za-z]{3,9} \d{1,2},? \d{4}"                             # Sep 12, 2026
    r"|\d{1,2}/\d{1,2}(?![\d/]))"                                 # 09/03 (card statements: year from the period)
)
CUR = r"(?:BDT|Tk\.?|৳|USD|US\$|\$)?"
AMOUNT_RE = re.compile(r"^\(?[-+]?" + CUR + r"\s*[-+]?\d{1,3}(?:,\d{2,3})*(?:\.\d{1,2})?\)?\s*(?:Dr|Cr|DR|CR|dr|cr)?\.?$")
PDF_AMOUNT_RE = re.compile(r"^\(?[-+]?" + CUR + r"\s*[-+]?\d[\d,]*\.\d{2}\)?\s*(?:Dr|Cr|DR|CR|dr|cr)?\.?$")
CARD_HINTS = re.compile(r"credit limit|minimum payment|payment due date|new balance|available credit|card ?member", re.I)
CARD_PAYMENT = re.compile(r"payment|thank you|autopay|auto-pay", re.I)
SKIP_RE = re.compile(r"opening balance|balance b/?f|brought forward|closing balance|balance c/?f|carried forward|"
                     r"^total|grand total|page \d+|statement of account|available balance", re.I)

COLS = {
    "date": ["transaction date", "txn date", "trans date", "posting date", "date"],
    "desc": ["description", "narration", "particulars", "details", "remarks", "transaction details", "transaction"],
    "debit": ["debit", "withdrawal", "withdraw", "paid out", "money out", "dr amount", "dr."],
    "credit": ["credit", "deposit", "paid in", "money in", "cr amount", "cr."],
    "amount": ["amount", "txn amount", "transaction amount"],
    "type": ["dr/cr", "cr/dr", "type", "debit/credit"],
    "balance": ["balance", "running balance", "closing balance"],
}


class StatementError(ValueError):
    pass


# ------------------------------------------------------------------ primitives

def parse_amount(text: str) -> tuple[float | None, str | None]:
    """'1,234.50' -> (1234.5, None); '(500.00)' -> (-500, None); '200.00 Dr' -> (200, 'dr')."""
    t = (text or "").strip()
    if not t or not AMOUNT_RE.match(t):
        return None, None
    marker = None
    m = re.search(r"(Dr|Cr|DR|CR|dr|cr)\.?$", t)
    if m:
        marker = m.group(1).lower()
        t = t[:m.start()]
    neg = t.startswith("(") and t.endswith(")") or "-" in t
    digits = re.sub(r"[^\d.]", "", t)
    if not digits or digits == ".":
        return None, None
    v = float(digits)
    return (-v if neg else v), marker


def parse_date(text: str, day_first: bool = True, period_end: date | None = None) -> date | None:
    t = (text or "").strip().replace(",", " ")
    m = re.match(r"^(\d{1,2})/(\d{1,2})$", t)
    if m:                                        # no year: take it from the statement period
        if period_end is None:
            return None
        a, b = int(m.group(1)), int(m.group(2))
        d, mo = (a, b) if day_first else (b, a)
        got = _safe_date(period_end.year, mo, d)
        if got and got > period_end + timedelta(days=31):     # e.g. a Dec transaction on a Jan statement
            got = _safe_date(period_end.year - 1, mo, d)
        return got
    m = re.match(r"^(\d{4})-(\d{1,2})-(\d{1,2})", t)
    if m:
        y, mo, d = map(int, m.groups())
        return _safe_date(y, mo, d)
    m = re.match(r"^(\d{1,2})[-/.](\d{1,2})[-/.](\d{2,4})", t)
    if m:
        a, b, y = int(m.group(1)), int(m.group(2)), _year(m.group(3))
        d, mo = (a, b) if day_first else (b, a)
        return _safe_date(y, mo, d)
    m = re.match(r"^(\d{1,2})[-/ ]?([A-Za-z]{3,9})[-/ ]*(\d{2,4})", t)
    if m and m.group(2)[:3].lower() in MONTHS:
        return _safe_date(_year(m.group(3)), MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
    m = re.match(r"^([A-Za-z]{3,9}) (\d{1,2}) +(\d{4})", t)
    if m and m.group(1)[:3].lower() in MONTHS:
        return _safe_date(int(m.group(3)), MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
    return None


def _year(y: str) -> int:
    v = int(y)
    return v + 2000 if v < 100 else v


def _safe_date(y, mo, d):
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def detect_day_first(samples: list[str]) -> bool:
    """Decide dd/mm vs mm/dd for numeric dates. Bangladesh uses dd/mm, so that's the default."""
    for s in samples:
        for m in re.finditer(r"(?<![\d/])(\d{1,2})[-/.](\d{1,2})[-/.]\d{2,4}(?![\d/])", s or ""):
            a, b = int(m.group(1)), int(m.group(2))
            if a > 12:
                return True
            if b > 12:
                return False
    return True


def clean_merchant(desc: str) -> str:
    d = re.sub(r"\s+", " ", desc or "").strip()
    d = re.sub(r"\b(?:ref|txn|trx|tran|id|no)[:#.\s-]*[A-Z0-9-]{5,}\b", "", d, flags=re.I)
    d = re.sub(r"\b\d{6,}\b", "", d)                       # long reference numbers
    d = re.sub(r"\s{2,}", " ", d).strip(" -/:,")
    return d[:48] or "Unknown"


def guess_channel(desc: str) -> str:
    d = desc.lower()
    if "bkash" in d or "nagad" in d or "rocket" in d:
        return "bkash"
    if "atm" in d or "cash" in d:
        return "cash"
    if any(k in d for k in ("pos", "card", "visa", "master", "ecom", "online")):
        return "card"
    return "bank_transfer"


# ------------------------------------------------------------------ CSV / Excel

def _find_col(headers: list[str], key: str) -> int | None:
    norm = [re.sub(r"\s+", " ", (h or "").strip().lower()) for h in headers]
    for cand in COLS[key]:
        for i, h in enumerate(norm):
            if h == cand:
                return i
    for cand in COLS[key]:
        for i, h in enumerate(norm):
            if cand in h and not (key == "date" and "value" in h):
                return i
    return None


def parse_csv(text: str) -> dict:
    text = text.lstrip("﻿")
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [r for r in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in r)]
    header_i = next((i for i, r in enumerate(rows[:40])
                     if _find_col(r, "date") is not None and _find_col(r, "desc") is not None), None)
    if header_i is None:
        raise StatementError("Couldn't find the table header. The file needs a Date column and a "
                             "Description / Narration / Particulars column.")
    hdr = rows[header_i]
    c = {k: _find_col(hdr, k) for k in COLS}
    if c["debit"] is None and c["credit"] is None and c["amount"] is None:
        raise StatementError("Couldn't find amount columns (Debit/Credit, Withdrawal/Deposit, or Amount).")
    body = rows[header_i + 1:]
    day_first = detect_day_first([r[c["date"]] for r in body if len(r) > c["date"]])
    out, skipped = [], 0
    for r in body:
        cell = lambda k: r[c[k]].strip() if c[k] is not None and c[k] < len(r) else ""
        d = parse_date(cell("date"), day_first)
        desc = cell("desc")
        if d is None or SKIP_RE.search(desc):
            skipped += 1
            continue
        amt = None
        if c["debit"] is not None or c["credit"] is not None:
            dr, _ = parse_amount(cell("debit"))
            cr, _ = parse_amount(cell("credit"))
            if dr:
                amt = -abs(dr)
            elif cr:
                amt = abs(cr)
        if amt is None and c["amount"] is not None:
            v, marker = parse_amount(cell("amount"))
            if v is not None:
                kind = (cell("type") or marker or "").lower()
                if kind.startswith("dr") or kind.startswith("debit") or kind == "d":
                    v = -abs(v)
                elif kind.startswith("cr") or kind.startswith("credit") or kind == "c":
                    v = abs(v)
                amt = v
        if not amt:
            skipped += 1
            continue
        bal, _ = parse_amount(cell("balance"))
        out.append({"date": d, "amount": amt, "desc": desc, "balance": bal})
    cur = "USD" if "$" in text and not re.search(r"BDT|Tk\.|৳", text) else "BDT"
    return {"rows": out, "skipped": skipped, "format": "csv", "currency": cur, "account_type": "bank"}


# ------------------------------------------------------------------ PDF (text items from pdf.js)

def _lines(items: list[dict]) -> list[list[dict]]:
    lines = []
    for page in sorted({it.get("page", 1) for it in items}):
        its = sorted((it for it in items if it.get("page", 1) == page and str(it.get("text", "")).strip()),
                     key=lambda it: (-float(it["y"]), float(it["x"])))
        cur, cur_y = [], None
        for it in its:
            y = float(it["y"])
            if cur_y is not None and abs(y - cur_y) > 3.0:
                lines.append(sorted(cur, key=lambda i: float(i["x"])))
                cur = []
            if not cur:
                cur_y = y
            cur.append(it)
        if cur:
            lines.append(sorted(cur, key=lambda i: float(i["x"])))
    return lines


def _merge_cells(line: list[dict]) -> list[dict]:
    """Join text items that touch into cells; a visible gap starts a new cell."""
    cells = []
    for it in line:
        x, w, t = float(it["x"]), float(it.get("w") or 0), str(it["text"]).strip()
        if cells and x - cells[-1]["x2"] < 4.0:
            cells[-1]["text"] += ("" if cells[-1]["text"].endswith(("-", "/")) else " ") + t
            cells[-1]["x2"] = max(cells[-1]["x2"], x + w)
        else:
            cells.append({"x1": x, "x2": x + w, "text": t})
    for c in cells:
        c["text"] = c["text"].strip()
        c["xc"] = (c["x1"] + c["x2"]) / 2
    return cells


def parse_pdf_items(items: list[dict]) -> dict:
    if not items:
        raise StatementError("This PDF has no readable text. It may be a scanned image; please download the "
                             "statement as CSV or Excel from your bank, or a text (e-statement) PDF.")
    lines = [_merge_cells(l) for l in _lines(items)]
    all_text = " ".join(str(it.get("text", "")) for it in items)
    card = bool(CARD_HINTS.search(all_text))
    currency = "USD" if re.search(r"\$\s?\d|U\.S\. dollars|USD", all_text) and not re.search(r"BDT|Tk\.|৳|taka", all_text, re.I) else "BDT"
    cols: dict[str, float] = {}
    rows, prev_bal, current = [], None, None
    skipped = 0
    day_first = detect_day_first([" ".join(c["text"] for c in l) for l in lines if l])
    full_dates = [parse_date(m.group(0), day_first) for m in
                  re.finditer(r"(?<![\d/])\d{1,2}/\d{1,2}/\d{2,4}(?![\d/])", all_text)]
    full_dates = [d for d in full_dates if d]
    period_end = None
    pm = re.search(r"(?:opening\s*/\s*closing date|closing date|statement date|statement period|billing period|billing cycle)"
                   r"[^0-9]{0,40}(\d{1,2}/\d{1,2}/\d{2,4})(?:\s*(?:-|to|–)\s*(\d{1,2}/\d{1,2}/\d{2,4}))?", all_text, re.I)
    if pm:
        period_end = parse_date(pm.group(2) or pm.group(1), day_first)
    if period_end is None and full_dates:            # fall back to the most common year on the statement
        common = Counter(d.year for d in full_dates).most_common(1)[0][0]
        period_end = max(d for d in full_dates if d.year == common)
    for cells in lines:
        joined = " ".join(c["text"] for c in cells)
        low = joined.lower()
        if ("debit" not in cols and "amount" in low and re.search(r"transaction|description|merchant|details|particulars", low)
                and not re.search(r"debit|withdraw|deposit", low)):
            for c in cells:                                  # single "Amount" column (card statements)
                if "amount" in c["text"].lower():
                    cols = {"amount": c["xc"]}
            continue
        if re.search(r"debit|withdraw", low) and re.search(r"credit|deposit", low) and "balance" in low:
            for c in cells:                                  # header row: remember column positions
                t = c["text"].lower()
                if re.search(r"debit|withdraw", t):
                    cols["debit"] = c["xc"]
                elif re.search(r"credit|deposit", t):
                    cols["credit"] = c["xc"]
                elif "balance" in t:
                    cols["balance"] = c["xc"]
            continue
        if SKIP_RE.search(low):
            amts = [parse_amount(c["text"])[0] for c in cells if PDF_AMOUNT_RE.match(c["text"])]
            if amts and re.search(r"opening|b/?f|brought", low):
                prev_bal = amts[-1]
            current = None
            continue
        first = cells[0]["text"] if cells else ""
        dm = DATE_RE.match(first) or DATE_RE.match(joined)
        d = parse_date(dm.group(0), day_first, period_end) if dm else None
        amount_cells = [c for c in cells if PDF_AMOUNT_RE.match(c["text"])]
        if d is None:
            if current is not None and not amount_cells and len(joined) < 120:
                current["desc"] += " " + joined                 # wrapped description line
            continue
        text_cells = [c for c in cells if c not in amount_cells]
        desc = " ".join(c["text"] for c in text_cells)
        desc = DATE_RE.sub("", desc, count=1).strip()
        desc = re.sub(r"^" + DATE_RE.pattern[1:], "", desc).strip()   # drop a value-date column too
        if not amount_cells:
            continue
        debit = credit = bal = None
        if "amount" in cols or (card and not cols):
            near = [c for c in amount_cells if "amount" not in cols or abs(c["xc"] - cols["amount"]) < 60] or amount_cells
            v, marker = parse_amount(near[-1]["text"])
            if v is None:
                continue
            if card:                                         # card: purchases positive, payments/credits negative
                if v < 0 or marker == "cr":
                    if CARD_PAYMENT.search(desc):
                        skipped += 1                         # paying the card is a transfer, not income
                        continue
                    credit = abs(v)
                else:
                    debit = abs(v)
            elif v < 0 or marker == "dr":
                debit = abs(v)
            else:
                credit = abs(v)
        elif cols:
            for c in amount_cells:
                v, marker = parse_amount(c["text"])
                near = min(cols, key=lambda k: abs(cols[k] - c["xc"]))
                if near == "debit":
                    debit = abs(v)
                elif near == "credit":
                    credit = abs(v)
                else:
                    bal = -abs(v) if marker == "dr" else v
        else:
            vals = [parse_amount(c["text"]) for c in amount_cells]
            if len(vals) >= 2:
                bal = vals[-1][0]
                amt_v, marker = vals[-2]
            else:
                amt_v, marker = vals[0]
            if marker == "dr" or amt_v < 0:
                debit = abs(amt_v)
            elif marker == "cr":
                credit = abs(amt_v)
            elif bal is not None and prev_bal is not None:
                (credit, debit) = (abs(amt_v), None) if bal > prev_bal else (None, abs(amt_v))
            elif re.search(r"salary|deposit|received|credit|interest|refund|cash in", desc, re.I):
                credit = abs(amt_v)
            else:
                debit = abs(amt_v)
        amt = -debit if debit else credit if credit else None
        if amt is None:
            continue
        current = {"date": d, "amount": amt, "desc": desc, "balance": bal}
        rows.append(current)
        if bal is not None:
            prev_bal = bal
    if not rows:
        raise StatementError("Couldn't find any transactions in this PDF. If it's from a bank whose layout we don't "
                             "support yet, try the CSV or Excel download.")
    return {"rows": rows, "skipped": skipped, "format": "pdf", "account_type": "credit_card" if card else "bank",
            "currency": currency}


# ------------------------------------------------------------------ build a sandbox account from rows

def build_state(parsed: dict) -> tuple[SandboxState, date, dict]:
    rows = sorted(parsed["rows"], key=lambda r: r["date"])
    if len(rows) < 3:
        raise StatementError("Found fewer than 3 transactions; please upload a longer statement.")
    txns = []
    for i, r in enumerate(rows, 1):
        t = Transaction(f"T{i:04d}", r["date"].isoformat(), round(float(r["amount"]), 2),
                        clean_merchant(r["desc"]), r["desc"][:200], guess_channel(r["desc"]))
        t.category = categorize(t)
        txns.append(t)
    card = parsed.get("account_type") == "credit_card"
    last_bal = None if card else next((r["balance"] for r in reversed(rows) if r.get("balance") is not None), None)
    net = sum(t.amount for t in txns)
    checking = 0.0 if card else last_bal if last_bal is not None else max(net, 0.0)
    today = rows[-1]["date"] + timedelta(days=1)
    state = SandboxState(balances={"checking": round(checking, 2), "savings": 0.0}, transactions=txns,
                         payees={}, currency=parsed.get("currency", "BDT"),
                         account_type=parsed.get("account_type", "bank"))
    months = sorted({t.date[:7] for t in txns})
    info = {"transactions": len(txns), "from": txns[0].date, "to": txns[-1].date, "months": len(months),
            "format": parsed["format"], "skipped_rows": parsed.get("skipped", 0),
            "balance_source": "statement" if last_bal is not None else "not shown on a card statement" if card
            else "estimated from transactions",
            "account_type": parsed.get("account_type", "bank"), "currency": parsed.get("currency", "BDT"),
            "uncategorised": sum(t.category == "other" for t in txns)}
    if len(months) < 2:
        info["warning"] = "Less than two months of data: trends and budgets will be limited."
    return state, today, info


def load_statement(kind: str, payload) -> tuple[SandboxState, date, dict]:
    if kind == "csv":
        parsed = parse_csv(str(payload))
    elif kind == "pdf_items":
        parsed = parse_pdf_items(list(payload))
    else:
        raise StatementError("Unsupported file type.")
    return build_state(parsed)


def sample_payees() -> dict:
    """Saved payees for the demo persona only; uploaded statements start with none."""
    return {"ammu": Payee("Ammu (family)", "bKash ****5521", "family")}
