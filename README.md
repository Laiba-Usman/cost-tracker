# Cost Tracker (Lab Part A)

A small web app that compares **estimated** and **actual** project costs. It was built around the LYKOS budget workbook (`sample_data/LYKOS_Project_Budget.xlsx`).

## Run it
```
pip install -r requirements.txt
python app.py            # open http://127.0.0.1:5000
python -m pytest tests   # 14 tests
```
Create an account, then press **Load LYKOS demo** (imports the Excel file and a CSV of sample actual costs).

## Part A requirements
| # | Requirement | Where |
|---|---|---|
| 1 | Interface to enter data or upload Excel/CSV | "Add a cost" form and "Upload a file" box (`templates/index.html`, `static/app.js`, `importer.py`) |
| 2 | Estimated, actual, variance, % variance | `calc.py` (one place; page, PDF and Excel all use it) |
| 3 | At least two charts | Charts tab: estimated vs actual by category, % variance by item, actual by cost type (Chart.js; matplotlib in the PDF) |
| 4 | One AI feature | AI analysis tab: unusual-expense detection + written summary (`ai.py`) |
| 5 | Downloadable report | Report tab: PDF and Excel with live formulas (`report.py`) |

## Calculation rules
- variance = actual − estimated (positive = overspent)
- % variance = variance / estimated × 100 (not defined when estimated = 0; such an item is "Unbudgeted")
- Status: Over budget (> +5%), Under budget (< −5%), On track (within ±5%), Not spent yet (no actual)
- Totals compare only items that have an actual, so a half-finished project does not look under budget.
- Forecast at completion = actual so far + estimate of the items not yet spent.
- Contingency is a percentage of the total estimate (set per project).

## Upload formats
Flat table (Category, Item, Cost type, Estimated, Actual) or the LYKOS layout (category rows, items, "Total (Rs.)" column). Modes: add rows, replace all rows, or update actual costs only (matched by item name; unknown names become "Unbudgeted").
Excel files must have been saved by Excel once so formula results are stored.

## AI feature (honest description)
Not a language model. It is statistical and rule-based analysis that runs locally: modified z-score (median/MAD) on % variance, plus rules (> 25% over, unbudgeted spending, > 50% under on paid lines, duplicate names). The written summary is generated from the computed figures, so it cannot invent numbers.

## Security (Part B)
Controls: password hashing, 8-hour HttpOnly SameSite session cookie, CSRF token on every change, per-user project ownership (other users get 404), parameterised SQL, upload checks (extension, real file type, 2 MB, unzip size, 1000 rows), numeric and length validation, textContent only (no innerHTML), CSP and other headers, formula-safe Excel export, request limits (240/min per address, 10 reports/min, 20 uploads/10 min), login block after 5 failures for 15 min, security log (`instance/security.log`), generic error messages.
Run the security tests against a running copy: `BASE_URL=http://127.0.0.1:5000 python tests/security_tests.py` (10 checks, safe test data only). Saved results are in `tests/results/`.
Settings for deployment: `SECRET_KEY`, `COOKIE_SECURE=1` (Secure cookie + HSTS), `TRUST_PROXY=1` (behind your own reverse proxy), `API_LIMIT_PER_MIN`, `LOGIN_FAILS`.
Not done yet: HTTPS itself, multi-factor login, password reset, backups, dependency scanning, log rotation.

## Limits
Sample actual costs in the demo are made-up values (the project has not run yet). Rates and costs in the workbook are estimates.
