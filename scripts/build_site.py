"""Build the static, serverless demo (for Cloudflare Pages or any static host) into site/.

    python scripts/build_site.py
    npx wrangler pages deploy site --project-name pocketpilot

The page loads Pyodide (Python compiled to WebAssembly) and runs the same `pocketpilot`
package in the visitor's browser, using the offline planner. No server, no API key, and every
visitor gets an isolated sandbox account.
"""
from __future__ import annotations

import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SITE = ROOT / "site"
MODULES = ["__init__", "agent", "agents", "analytics", "api", "categorize", "data", "importer", "security", "tools"]

HEADERS = """/*
  X-Content-Type-Options: nosniff
  X-Frame-Options: DENY
  Referrer-Policy: no-referrer
  Permissions-Policy: camera=(), microphone=(), geolocation=()
  Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval' 'wasm-unsafe-eval' https://cdn.jsdelivr.net; connect-src 'self' https://cdn.jsdelivr.net; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'

/py/*
  Content-Type: text/plain; charset=utf-8
"""


def main():
    if SITE.exists():
        shutil.rmtree(SITE)
    (SITE / "py" / "pocketpilot").mkdir(parents=True)
    shutil.copy(ROOT / "web" / "index.html", SITE / "index.html")
    for sample in ("sample_statement.pdf", "sample_statement.csv"):
        shutil.copy(ROOT / "web" / sample, SITE / sample)
    for m in MODULES:
        shutil.copy(ROOT / "pocketpilot" / f"{m}.py", SITE / "py" / "pocketpilot" / f"{m}.py")
    (SITE / "_headers").write_text(HEADERS, encoding="utf-8")
    files = sorted(p.relative_to(SITE).as_posix() for p in SITE.rglob("*") if p.is_file())
    print(f"Built {len(files)} files into {SITE}:\n  " + "\n  ".join(files))


if __name__ == "__main__":
    main()
