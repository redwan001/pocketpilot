# Submission checklist (deadline: 20 Oct 2026, 23:59 Beijing = 21:59 Bangladesh)

| # | Required item | Where it is | Status | Your action |
|---|---|---|---|---|
| — | Registration (form + passport bio pages) | `docs/EMAILS.md` §1 | ⏳ | Fill in the form, attach the passports, send **now** |
| 01 | Technical documentation | `docs/TECHNICAL_DOCUMENTATION.md` | ✅ | Export to PDF |
| 02 | Presentation deck (≤10 min) | Slides artifact (13 slides with speaker notes) | ✅ | Team details and GitHub link filled in; download PDF/PPTX |
| 03 | Demo video (≤5 min, optional) | `docs/DEMO_VIDEO_SCRIPT.md` | ⏳ | Record following the script; upload unlisted |
| 04 | Source code + README + deploy steps + tests | https://github.com/redwan001/pocketpilot | ✅ | Push future changes with `git push` |
| 05 | Security self-assessment | `docs/SECURITY_SELF_ASSESSMENT.md` | ✅ | Export to PDF |
| 06 | Execution evidence | `evidence/` + video | ✅ | Nothing (regenerate with `python scripts/run_demo.py` if code changes) |

## Before sending
- [ ] `python -m unittest discover -s tests` shows **OK**.
- [ ] Live demo opens: https://pocketpilot-7jr.pages.dev
- [ ] No `[placeholders]` left in the deck, the docs or the emails.
- [ ] Both members can explain: the three permission tiers, how an approval works, why the AI can't move money
      itself, and how a statement is read.
- [ ] Sent before 21:59 Bangladesh time on 20 Oct. You can resend updates until then; the latest version counts.

## Exporting the Markdown docs to PDF
Open the `.md` file in VS Code, run "Markdown: Open Preview", then print to PDF.
Or paste it into Google Docs and use File › Download › PDF.

## Pushing to GitHub
1. Create an empty **public** repository named `pocketpilot` on github.com.
2. In this folder, run:
```bash
git remote add origin https://github.com/<your-username>/pocketpilot.git
```
```bash
git push -u origin main
```
