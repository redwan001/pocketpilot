"""Generate sample bank statements (synthetic) for trying the upload feature.

    python scripts/make_samples.py   ->  web/sample_statement.csv, web/sample_statement.pdf

Persona: "Nusrat", a final-year university student with a part-time job and a family allowance. The layout
mimics a typical Bangladeshi bank e-statement: Date | Particulars | Withdrawal | Deposit | Balance, with an
opening-balance row, reference numbers, multi-line narrations, VAT/charges and multiple pages.
"""
from __future__ import annotations

import random
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
OPENING = 48500.0


def rows(seed: int = 7):
    rng = random.Random(seed)
    out = []
    def add(d, desc, amt):
        out.append((d, desc, amt))
    for mo in (7, 8, 9):
        add(date(2026, mo, 1), "BEFTN CREDIT FROM ABDUL KARIM ALLOWANCE", 12000)
        add(date(2026, mo, 5), "SALARY PART TIME TUTOR BRAC CARE", 9000)
        add(date(2026, mo, 3), "BKASH TRANSFER 0171XXXXX21 MESS RENT", -6500)
        add(date(2026, mo, 6), "GRAMEENPHONE RECHARGE INTERNET PACK", -499)
        add(date(2026, mo, 15), "POS PURCHASE NETFLIX.COM", -650)
        for _ in range(rng.randint(3, 5)):
            add(date(2026, mo, rng.randint(1, 28)), "POS PURCHASE FOODPANDA BANGLADESH", -rng.randint(250, 650))
        for _ in range(rng.randint(2, 3)):
            add(date(2026, mo, rng.randint(1, 28)), "ATM WDL DBBL ATM BOOTH DHANMONDI", -rng.choice([1000, 2000, 3000]))
        for _ in range(rng.randint(5, 8)):
            add(date(2026, mo, rng.randint(1, 28)), "NPSB POS PATHAO RIDE DHAKA", -rng.randint(90, 260))
        add(date(2026, mo, rng.randint(8, 25)), "POS PURCHASE SHWAPNO SUPERSHOP", -rng.randint(900, 1800))
        add(date(2026, mo, rng.randint(8, 25)), "ECOM DARAZ BANGLADESH LTD", -rng.randint(500, 2600))
        add(date(2026, mo, 28), "SMS ALERT CHARGE", -30)
        add(date(2026, mo, 28), "VAT ON SMS CHARGE", -4.5)
    add(date(2026, 8, 20), "TUITION FEE UNIVERSITY SEMESTER PAYMENT REF 88213344", -18000)
    add(date(2026, 9, 12), "POS PURCHASE RYANS COMPUTERS LTD", -21500)
    out.sort(key=lambda r: r[0])
    bal, res = OPENING, []
    for d, desc, amt in out:
        bal = round(bal + amt, 2)
        res.append((d, desc, amt, bal))
    return res


def write_csv(rs):
    lines = ["Sample Bank PLC,Statement of Account", "Account Name,NUSRAT JAHAN (sample)", "Account No,****4471",
             "", "Txn Date,Value Date,Particulars,Withdrawal,Deposit,Balance",
             f"01/07/2026,01/07/2026,Opening Balance,,,\"{OPENING:,.2f}\""]
    for d, desc, amt, bal in rs:
        ds = d.strftime("%d/%m/%Y")
        w, dep = (f"\"{-amt:,.2f}\"", "") if amt < 0 else ("", f"\"{amt:,.2f}\"")
        lines.append(f"{ds},{ds},{desc},{w},{dep},\"{bal:,.2f}\"")
    (WEB / "sample_statement.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------- tiny PDF writer (Helvetica text only)

def _esc(t: str) -> str:
    return t.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _w(t: str, size: float) -> float:
    widths = {",": .278, ".": .278, " ": .278, "-": .333, "/": .278}
    return sum(widths.get(ch, .556) for ch in t) * size


def pdf_pages(rs):
    pages, cur, y = [], [], 0
    cols = {"date": 40, "desc": 105, "wd_r": 400, "dep_r": 475, "bal_r": 555}

    def header(page_no):
        ops = [(40, 800, 13, "Sample Bank PLC - Statement of Account"),
               (40, 782, 9, "Account: NUSRAT JAHAN (sample)   A/C No: ****4471   Period: 01/07/2026 - 30/09/2026"),
               (470, 800, 8, f"Page {page_no}")]
        ops += [(cols["date"], 750, 9, "Date"), (cols["desc"], 750, 9, "Particulars"),
                (cols["wd_r"] - _w("Withdrawal", 9), 750, 9, "Withdrawal"),
                (cols["dep_r"] - _w("Deposit", 9), 750, 9, "Deposit"),
                (cols["bal_r"] - _w("Balance", 9), 750, 9, "Balance")]
        return ops

    def money(v):
        return f"{v:,.2f}"

    page_no = 1
    cur, y = header(page_no), 730
    cur.append((cols["desc"], y, 9, "Opening Balance"))
    cur.append((cols["bal_r"] - _w(money(OPENING), 9), y, 9, money(OPENING)))
    y -= 16
    for d, desc, amt, bal in rs:
        parts = [desc] if len(desc) <= 38 else [desc[:38].rsplit(" ", 1)[0], desc[len(desc[:38].rsplit(" ", 1)[0]) + 1:]]
        if y < 70 + 12 * len(parts):
            pages.append(cur)
            page_no += 1
            cur, y = header(page_no), 730
        ds = d.strftime("%d/%m/%Y")
        cur.append((cols["date"], y, 9, ds))
        cur.append((cols["desc"], y, 9, parts[0]))
        a = money(abs(amt))
        right = cols["wd_r"] if amt < 0 else cols["dep_r"]
        cur.append((right - _w(a, 9), y, 9, a))
        cur.append((cols["bal_r"] - _w(money(bal), 9), y, 9, money(bal)))
        for extra in parts[1:]:
            y -= 11
            cur.append((cols["desc"], y, 9, extra))
        y -= 16
    cur.append((cols["desc"], y - 6, 9, "Closing Balance"))
    cur.append((cols["bal_r"] - _w(money(rs[-1][3]), 9), y - 6, 9, money(rs[-1][3])))
    pages.append(cur)
    return pages


def pdf_items(pages):
    """The text items pdf.js would report for these pages (used by the tests)."""
    return [{"page": i, "x": x, "y": y, "w": _w(t, size), "text": t}
            for i, ops in enumerate(pages, 1) for x, y, size, t in ops]


def write_pdf_file(pages):
    objs = ["<< /Type /Catalog /Pages 2 0 R >>", None, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for ops in pages:
        stream = "\n".join(f"BT /F1 {s} Tf {x:.2f} {yy:.2f} Td ({_esc(t)}) Tj ET" for x, yy, s, t in ops)
        objs.append(f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream")
        content_id = len(objs)
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents {content_id} 0 R "
                    f"/Resources << /Font << /F1 3 0 R >> >> >>")
        kids.append(len(objs))
    objs[1] = f"<< /Type /Pages /Kids [{' '.join(f'{k} 0 R' for k in kids)}] /Count {len(kids)} >>"
    out, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode()
    out += "".join(f"{o:010d} 00000 n \n" for o in offsets).encode()
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    (WEB / "sample_statement.pdf").write_bytes(bytes(out))


def main():
    rs = rows()
    write_csv(rs)
    pages = pdf_pages(rs)
    write_pdf_file(pages)
    print(f"{len(rs)} transactions -> web/sample_statement.csv, web/sample_statement.pdf ({len(pages)} pages)")


if __name__ == "__main__":
    main()
