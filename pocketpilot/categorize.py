"""Rule-based transaction categorisation (deterministic and explainable)."""
from __future__ import annotations

import re

RULES = [
    # International merchants (card statements) - specific names first so they beat generic words below
    ("uber *eats", "food_delivery"), ("uber eats", "food_delivery"), ("doordash", "food_delivery"),
    ("grubhub", "food_delivery"), ("deliveroo", "food_delivery"), ("lyft", "transport"),
    ("dollar general", "groceries"), ("dollar tree", "groceries"), ("family dollar", "groceries"),
    ("walmart", "groceries"), ("kroger", "groceries"), ("sprouts", "groceries"), ("fresh market", "groceries"),
    ("aldi", "groceries"), ("publix", "groceries"), ("whole foods", "groceries"), ("trader joe", "groceries"),
    ("costco", "groceries"), ("target", "shopping"), ("supermarket", "groceries"), ("grocery", "groceries"),
    ("hypermart", "groceries"), ("walgreens", "health"), ("cvs", "health"), ("clin", "health"), ("hlth", "health"),
    ("medical", "health"), ("pharmacy", "health"), ("temu", "shopping"), ("shein", "shopping"), ("ebay", "shopping"),
    ("best buy", "electronics"), ("apple.com/bill", "subscriptions"), ("hulu", "subscriptions"),
    ("disney", "subscriptions"), ("prime video", "subscriptions"), ("starbucks", "dining_out"),
    ("mcdonald", "dining_out"), ("chipotle", "dining_out"), ("dim sum", "dining_out"), ("grill", "dining_out"),
    ("kitchen", "dining_out"), ("bbq", "dining_out"), ("restaurant depot", "groceries"), ("mlgw", "utilities"),
    ("comcast", "internet_mobile"), ("xfinity", "internet_mobile"), ("verizon", "internet_mobile"),
    ("t-mobile", "internet_mobile"), ("at&t", "internet_mobile"), ("shell oil", "transport"), ("exxon", "transport"),
    ("amazon prime", "subscriptions"), ("amazon", "shopping"), ("amzn", "shopping"),
    ("payroll", "income"), ("salary", "income"), ("bkash received", "income"), ("interest", "income"),
    ("remittance", "income"), ("refund", "income"), ("cash deposit", "income"), ("cash in", "income"),
    ("atm", "cash"), ("cash withdrawal", "cash"), ("cash wdl", "cash"), ("cash out", "cash"),
    ("dps", "savings"), ("fdr", "savings"), ("sanchayapatra", "savings"), ("savings", "savings"),
    ("loan", "loan_emi"), ("emi", "loan_emi"), ("labaid", "health"), ("diagnostic", "health"), ("installment", "loan_emi"), ("credit card bill", "loan_emi"),
    ("tuition", "education"), ("school", "education"), ("university", "education"), ("course", "education"),
    ("insurance", "insurance"), ("premium", "insurance"),
    ("vat", "fees"), ("excise", "fees"), ("charge", "fees"), (" fee", "fees"), ("sms alert", "fees"),
    ("annual fee", "fees"), ("source tax", "fees"), ("service charge", "fees"),
    ("landlord", "housing"), ("rent", "housing"),
    ("desco", "utilities"), ("dpdc", "utilities"), ("titas", "utilities"), ("wasa", "utilities"),
    ("link3", "internet_mobile"), ("grameenphone", "internet_mobile"), ("robi", "internet_mobile"),
    ("netflix", "subscriptions"), ("spotify", "subscriptions"), ("chorki", "subscriptions"),
    ("hoichoi", "subscriptions"), ("youtube premium", "subscriptions"),
    ("gym", "fitness"),
    ("send money - ", "family_support"),
    ("shwapno", "groceries"), ("meena bazar", "groceries"), ("chaldal", "groceries"),
    ("foodpanda", "food_delivery"), ("pathao food", "food_delivery"),
    ("coffee", "dining_out"), ("restaurant", "dining_out"), ("kfc", "dining_out"),
    ("pathao ride", "transport"), ("uber", "transport"), ("obhai", "transport"), ("fuel", "transport"),
    ("petrol", "transport"), ("cng", "transport"), ("shohoz", "transport"), ("biman", "transport"),
    ("bus", "transport"), ("train", "transport"), ("parking", "transport"),
    ("unimart", "groceries"), ("agora", "groceries"), ("swapno", "groceries"), ("lavender", "groceries"),
    ("pizza", "dining_out"), ("cafe", "dining_out"), ("burger", "dining_out"), ("hungrynaki", "food_delivery"),
    ("food", "food_delivery"), ("gas", "utilities"), ("water", "utilities"), ("electric", "utilities"),
    ("palli", "utilities"), ("nesco", "utilities"), ("desco", "utilities"), ("airtel", "internet_mobile"),
    ("banglalink", "internet_mobile"), ("teletalk", "internet_mobile"), ("recharge", "internet_mobile"),
    ("topup", "internet_mobile"), ("top up", "internet_mobile"), ("broadband", "internet_mobile"),
    ("amazon", "shopping"), ("othoba", "shopping"), ("chaldal", "groceries"), ("bata", "shopping"),
    ("apex", "shopping"), ("yellow", "shopping"), ("pharmacy", "health"), ("lab", "health"), ("doctor", "health"),
    ("beftn", "transfers"), ("npsb", "transfers"), ("rtgs", "transfers"), ("fund transfer", "transfers"),
    ("ft to", "transfers"), ("transfer", "transfers"), ("bkash", "transfers"), ("nagad", "transfers"),
    ("rocket", "transfers"),
    ("pharma", "health"), ("hospital", "health"), ("clinic", "health"),
    ("daraz", "shopping"), ("aarong", "shopping"),
    ("star tech", "electronics"), ("ryans", "electronics"), ("computer", "electronics"), ("mobile shop", "electronics"),
    ("pos", "shopping"), ("ecom", "shopping"),        # generic card purchases last
]

# Needs vs wants split used by the budget engine.
NEEDS = {"housing", "utilities", "internet_mobile", "groceries", "transport", "family_support", "health",
         "loan_emi", "education", "insurance", "fees", "cash", "transfers"}   # never cut unknown/committed money
WANTS = {"food_delivery", "dining_out", "subscriptions", "fitness", "shopping", "electronics", "other"}

LABELS = {
    "income": "Income", "housing": "Housing", "utilities": "Utilities",
    "internet_mobile": "Internet & mobile", "subscriptions": "Subscriptions", "fitness": "Fitness",
    "family_support": "Family support", "groceries": "Groceries", "food_delivery": "Food delivery",
    "dining_out": "Dining & coffee", "transport": "Transport", "health": "Health",
    "shopping": "Shopping", "electronics": "Electronics", "savings": "Savings", "other": "Other",
    "fees": "Bank fees & tax", "cash": "Cash withdrawals", "transfers": "Transfers out", "loan_emi": "Loans & EMI",
    "education": "Education", "insurance": "Insurance",
}


# Whole-word matching: short keywords must match a full word ("charge" must not match "recharge",
# "pos" must not match "deposit"); longer keywords may be a word prefix ("electric" -> "electricity").
_COMPILED = [(re.compile(r"(?<![a-z0-9])" + re.escape(n.strip()) + (r"(?![a-z0-9])" if len(n.strip()) <= 4 else ""), re.I), c)
             for n, c in RULES]


def categorize(txn) -> str:
    name = txn.merchant.lower()
    if txn.amount > 0:
        return "income"                       # money in: salary, allowance, transfers in, refunds
    for rx, cat in _COMPILED:
        if rx.search(name) and cat != "income":
            return cat
    return "other"
