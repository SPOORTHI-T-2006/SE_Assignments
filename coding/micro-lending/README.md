# Micro-Lending & Peer Credit Risk Assessor

A small Flask and SQLite application for borrower loan requests, peer-lender decisions, credit-risk warnings, repayment schedules, and one-time repayment recording.

## Requirements

- Python 3.9 or newer
- `pip`

## Install and run on Windows

Open PowerShell in this project directory and run:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

If PowerShell blocks virtual-environment activation, run the commands without activation using `\.venv\Scripts\python.exe -m pip install -r requirements.txt` and `\.venv\Scripts\python.exe app.py`.

Open <http://127.0.0.1:5000>, register as a borrower or peer lender, and log in. The SQLite database is created automatically at `instance/micro_lending.sqlite3`.

Set a stable secret key before running outside local development, for example in PowerShell: `$env:SECRET_KEY = "replace-with-a-long-random-value"`.

## Tests

```powershell
python -m unittest -v
```

## Behavior and security notes

- Passwords are stored with Werkzeug's password hashing helpers. User identity is held in a signed Flask session; private routes check both login and the account role.
- Lenders only see borrower username, requested amount, tenure, risk tier, and any warning. Borrower financial metrics and repayment schedules are private to the borrower.
- Risk tiers use debt-to-income ratio, on-time repayment share, and employment stability. A missing profile field or absent repayment history produces a warning instead of a tier. A High tier is also stored as a warning for the borrower and lender.
- Approved requests receive equal principal-only installments. No interest rate was specified, so EMI here means a monthly share of principal; the final installment adjusts for rounding. Dates are one calendar month apart.
- Repayment recording is a local demo ledger, not a payment processor. Each payment updates the installment and inserts its transaction in one SQLite transaction; a unique constraint and serialized write transaction prevent duplicate processing.
- The built-in Flask server and default secret are for local development only. Deploy behind a production WSGI server with HTTPS, a strong secret, and appropriate operational controls.