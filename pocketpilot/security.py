"""Security & compliance layer.

Defence in depth around the agent:
  1. Permission tiers   - READ / ADVISE run automatically, ACT always needs a human confirmation.
  2. Policy engine      - hard limits enforced in code, outside the model's control.
  3. Confirmation tokens- one-time, expiring, HMAC-bound to the exact action + arguments.
  4. Untrusted-data guard - prompt-injection screening of free text (e.g. transfer memos).
  5. PII redaction      - phone / card / NID / email masked before text reaches the model or logs.
  6. Audit log          - append-only, SHA-256 hash-chained, verifiable.
  7. Rate limiting      - caps the number of actions per minute.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class Tier(str, Enum):
    READ = "read"        # view data, no side effects
    ADVISE = "advise"    # compute recommendations, no side effects
    ACT = "act"          # changes money or account state -> human confirmation required


# ---------------------------------------------------------------- PII redaction

_PII_PATTERNS = [
    ("card", re.compile(r"\b(?:\d[ -]?){12,15}(\d{4})\b"), lambda m: f"****{m.group(1)}"),
    ("phone", re.compile(r"(?<!\d)(?:\+?880|0)1[3-9]\d{2}(\d{6})(?!\d)"), lambda m: f"01*******{m.group(1)[-2:]}"),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), lambda m: "[email]"),
    ("nid", re.compile(r"(?<!\d)(\d{10}|\d{13}|\d{17})(?!\d)"), lambda m: "[id]"),
]


def redact(text: str) -> str:
    for _, pattern, repl in _PII_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def redact_obj(obj):
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, list):
        return [redact_obj(x) for x in obj]
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    return obj


# ---------------------------------------------------------------- prompt-injection guard

_OVERRIDE_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above|earlier) (instructions|rules|prompts)",
    r"disregard (the |all |your )?(previous|prior|above|system)",
    r"\b(system|assistant|developer)\s*:",
    r"you are now", r"new instructions", r"override", r"jailbreak",
    r"<\|.*?\|>", r"\[\[.*?\]\]",
]
# Payment commands are normal from the user, but never legitimate inside third-party data.
_MONEY_PATTERNS = [r"\btransfer\b.*\b\d{3,}", r"\bsend\b.*\b\d{3,}\b.*\bto\b"]
_OVERRIDE_RE = re.compile("|".join(_OVERRIDE_PATTERNS), re.IGNORECASE)
_DATA_RE = re.compile("|".join(_OVERRIDE_PATTERNS + _MONEY_PATTERNS), re.IGNORECASE)


def scan_untrusted(text: str, third_party: bool = True) -> dict:
    """Flag instruction-like text. third_party=True also flags payment commands (memos etc.)."""
    rx = _DATA_RE if third_party else _OVERRIDE_RE
    hits = [m.group(0) for m in rx.finditer(text or "")]
    return {"suspicious": bool(hits), "matches": hits}


def sanitize_untrusted(text: str, limit: int = 80) -> str:
    """Return a version of third-party free text that is safe to show the model."""
    if not text:
        return ""
    if scan_untrusted(text)["suspicious"]:
        return "[memo withheld: contains instructions - possible prompt injection]"
    return redact(text)[:limit]


# ---------------------------------------------------------------- policy engine

@dataclass
class Policy:
    max_single_transfer: float = 20000.0
    daily_outgoing_cap: float = 30000.0
    min_checking_balance: float = 5000.0
    max_actions_per_minute: int = 10
    confirmation_ttl_seconds: int = 300
    currency: str = "BDT"


class PolicyViolation(Exception):
    pass


class PolicyEngine:
    def __init__(self, policy: Policy | None = None):
        self.policy = policy or Policy()
        self._outgoing_today: dict[str, float] = {}

    def check_outgoing(self, state, amount: float, day: str, payee_key: str | None = None):
        p = self.policy
        if amount <= 0:
            raise PolicyViolation("Amount must be positive.")
        if payee_key is not None and payee_key not in state.payees:
            raise PolicyViolation("Payee is not on your saved payee list. Add it in the banking app first.")
        if amount > p.max_single_transfer:
            raise PolicyViolation(f"{p.currency} {amount:,.0f} exceeds the single-transfer limit of {p.currency} {p.max_single_transfer:,.0f}.")
        if self._outgoing_today.get(day, 0) + amount > p.daily_outgoing_cap:
            raise PolicyViolation(f"This would exceed today's outgoing cap of {p.currency} {p.daily_outgoing_cap:,.0f}.")
        if state.balances["checking"] - amount < p.min_checking_balance:
            raise PolicyViolation(f"This would leave less than the {p.currency} {p.min_checking_balance:,.0f} safety buffer in checking.")

    def record_outgoing(self, amount: float, day: str):
        self._outgoing_today[day] = self._outgoing_today.get(day, 0) + amount


class RateLimiter:
    def __init__(self, per_minute: int):
        self.per_minute = per_minute
        self._events: list[float] = []

    def allow(self) -> bool:
        now = time.monotonic()
        self._events = [t for t in self._events if now - t < 60]
        if len(self._events) >= self.per_minute:
            return False
        self._events.append(now)
        return True


# ---------------------------------------------------------------- confirmation tokens

def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


@dataclass
class PendingAction:
    token: str
    tool: str
    args: dict
    summary: str
    created: float
    signature: str


class ConfirmationManager:
    """Issues one-time tokens; an action only runs if the user approves that exact token."""

    def __init__(self, ttl_seconds: int = 300, secret: bytes | None = None, clock=time.time):
        self.ttl = ttl_seconds
        self._secret = secret or secrets.token_bytes(32)
        self._pending: dict[str, PendingAction] = {}
        self._clock = clock

    def _sign(self, token: str, tool: str, args: dict) -> str:
        msg = f"{token}|{tool}|{_canonical(args)}".encode()
        return hmac.new(self._secret, msg, hashlib.sha256).hexdigest()

    def issue(self, tool: str, args: dict, summary: str) -> PendingAction:
        token = secrets.token_urlsafe(12)
        pa = PendingAction(token, tool, dict(args), summary, self._clock(), self._sign(token, tool, args))
        self._pending[token] = pa
        return pa

    def pending(self) -> list[PendingAction]:
        self._expire()
        return list(self._pending.values())

    def _expire(self):
        now = self._clock()
        for t in [t for t, p in self._pending.items() if now - p.created > self.ttl]:
            del self._pending[t]

    def redeem(self, token: str) -> PendingAction:
        self._expire()
        pa = self._pending.pop(token, None)          # one-time: removed on first use
        if pa is None:
            raise PolicyViolation("Confirmation token is invalid, already used, or expired.")
        if not hmac.compare_digest(pa.signature, self._sign(pa.token, pa.tool, pa.args)):
            raise PolicyViolation("Confirmation token does not match the action (tampering detected).")
        return pa

    def cancel(self, token: str) -> PendingAction | None:
        return self._pending.pop(token, None)


# ---------------------------------------------------------------- audit log

class AuditLog:
    GENESIS = "0" * 64

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else None
        self.entries: list[dict] = []
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text("", encoding="utf-8")

    def append(self, event: str, **data) -> dict:
        prev = self.entries[-1]["hash"] if self.entries else self.GENESIS
        body = {"seq": len(self.entries) + 1, "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "event": event, "data": redact_obj(data), "prev": prev}
        body["hash"] = hashlib.sha256((prev + _canonical({k: v for k, v in body.items() if k != "hash"})).encode()).hexdigest()
        self.entries.append(body)
        if self.path:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(_canonical(body) + "\n")
        return body

    @classmethod
    def verify_entries(cls, entries: list[dict]) -> tuple[bool, int | None]:
        prev = cls.GENESIS
        for e in entries:
            body = {k: v for k, v in e.items() if k != "hash"}
            if e["prev"] != prev:
                return False, e["seq"]
            if hashlib.sha256((prev + _canonical(body)).encode()).hexdigest() != e["hash"]:
                return False, e["seq"]
            prev = e["hash"]
        return True, None

    def verify(self) -> tuple[bool, int | None]:
        return self.verify_entries(self.entries)
