"""Flask application for peer lending, risk assessment, and repayments."""

import os
import sqlite3
from calendar import monthrange
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from functools import wraps
from math import isfinite

from flask import Flask, abort, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from risk_engine import assess_risk


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('borrower', 'lender')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS financial_profiles (
    user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    monthly_income REAL NOT NULL,
    monthly_debt REAL NOT NULL,
    on_time_payments INTEGER NOT NULL,
    late_payments INTEGER NOT NULL,
    employment_years REAL NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS loans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    borrower_id INTEGER NOT NULL REFERENCES users(id),
    requested_amount REAL NOT NULL CHECK (requested_amount > 0),
    tenure_months INTEGER NOT NULL CHECK (tenure_months > 0),
    risk_tier TEXT CHECK (risk_tier IN ('Low', 'Medium', 'High') OR risk_tier IS NULL),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS repayment_schedule (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_id INTEGER NOT NULL REFERENCES loans(id) ON DELETE CASCADE,
    installment_number INTEGER NOT NULL,
    due_date TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount > 0),
    status TEXT NOT NULL DEFAULT 'due' CHECK (status IN ('due', 'paid')),
    paid_at TEXT,
    UNIQUE (loan_id, installment_number)
);
CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id INTEGER NOT NULL UNIQUE REFERENCES repayment_schedule(id),
    borrower_id INTEGER NOT NULL REFERENCES users(id),
    amount REAL NOT NULL CHECK (amount > 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_id INTEGER NOT NULL UNIQUE REFERENCES loans(id) ON DELETE CASCADE,
    borrower_id INTEGER NOT NULL REFERENCES users(id),
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def get_db():
    """Open one SQLite connection per request and enable foreign keys."""
    if "db" not in g:
        g.db = sqlite3.connect(current_app_config_database())
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA busy_timeout = 5000")
    return g.db


def current_app_config_database():
    """Return the active app's configured database path without global imports."""
    from flask import current_app

    return current_app.config["DATABASE"]


def close_db(_error=None):
    """Close the request's database connection after response handling."""
    connection = g.pop("db", None)
    if connection is not None:
        connection.close()


def init_db(app):
    """Create the required tables for a new or existing database file."""
    database = app.config["DATABASE"]
    os.makedirs(os.path.dirname(os.path.abspath(database)), exist_ok=True)
    connection = sqlite3.connect(database)
    try:
        connection.executescript(SCHEMA)
        connection.commit()
    finally:
        connection.close()


def login_required(view):
    """Require a valid logged-in account on a private route."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        user = get_current_user()
        if user is None:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped


def role_required(role):
    """Require the requested role, checking the role against the database."""
    def decorate(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            user = get_current_user()
            if user is None:
                flash("Please log in to continue.", "error")
                return redirect(url_for("login"))
            if user["role"] != role:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorate


def get_current_user():
    """Load the signed-in user from SQLite instead of trusting role session data."""
    user_id = session.get("user_id")
    if user_id is None:
        return None
    return get_db().execute("SELECT id, username, role FROM users WHERE id = ?", (user_id,)).fetchone()


def add_months(base_date, months):
    """Advance a date by calendar months, clamping at the month's last day."""
    month_index = base_date.month - 1 + months
    year = base_date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(base_date.day, monthrange(year, month)[1])
    return date(year, month, day)


def generate_repayment_schedule(principal, tenure_months, start_date=None):
    """Build a monthly, principal-only schedule with cent-accurate final payment."""
    principal = Decimal(str(principal)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if principal <= 0 or tenure_months < 1:
        raise ValueError("Principal and tenure must be positive.")
    base_date = start_date or date.today()
    regular_payment = (principal / tenure_months).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    schedule = []
    paid = Decimal("0.00")
    for installment_number in range(1, tenure_months + 1):
        amount = principal - paid if installment_number == tenure_months else regular_payment
        paid += amount
        schedule.append({
            "installment_number": installment_number,
            "due_date": add_months(base_date, installment_number).isoformat(),
            "amount": amount,
        })
    return schedule


def create_app(test_config=None):
    """Configure Flask, database lifecycle hooks, and application routes."""
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("SECRET_KEY", "development-key-change-me"),
        DATABASE=os.path.join(app.instance_path, "micro_lending.sqlite3"),
    )
    if test_config:
        app.config.update(test_config)
    os.makedirs(app.instance_path, exist_ok=True)
    init_db(app)
    app.teardown_appcontext(close_db)

    @app.context_processor
    def inject_user():
        return {"current_user": get_current_user()}

    @app.route("/")
    def index():
        user = get_current_user()
        if user is None:
            return redirect(url_for("login"))
        endpoint = "borrower_dashboard" if user["role"] == "borrower" else "lender_dashboard"
        return redirect(url_for(endpoint))

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            role = request.form.get("role", "")
            if not username or len(password) < 8 or role not in ("borrower", "lender"):
                flash("Enter a username, a password of at least 8 characters, and a valid role.", "error")
            else:
                try:
                    get_db().execute(
                        "INSERT INTO users (username, password_hash, role) VALUES (?, ?, ?)",
                        (username, generate_password_hash(password), role),
                    )
                    get_db().commit()
                except sqlite3.IntegrityError:
                    flash("That username is already registered.", "error")
                else:
                    flash("Account created. Please log in.", "success")
                    return redirect(url_for("login"))
        return render_template("register.html")

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            user = get_db().execute(
                "SELECT id, password_hash FROM users WHERE username = ?",
                (request.form.get("username", "").strip(),),
            ).fetchone()
            if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
                session.clear()
                session["user_id"] = user["id"]
                return redirect(url_for("index"))
            flash("Invalid username or password.", "error")
        return render_template("login.html")

    @app.route("/logout", methods=["POST"])
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/borrower", methods=["GET", "POST"])
    @role_required("borrower")
    def borrower_dashboard():
        user = get_current_user()
        connection = get_db()
        if request.method == "POST":
            field_names = (
                "monthly_income", "monthly_debt", "on_time_payments", "late_payments",
                "employment_years", "requested_amount", "tenure_months",
            )
            raw = {field: request.form.get(field, "").strip() for field in field_names}
            if any(value == "" for value in raw.values()):
                flash("All financial and loan request fields are required.", "error")
            else:
                try:
                    profile = {
                        "monthly_income": float(raw["monthly_income"]),
                        "monthly_debt": float(raw["monthly_debt"]),
                        "on_time_payments": int(raw["on_time_payments"]),
                        "late_payments": int(raw["late_payments"]),
                        "employment_years": float(raw["employment_years"]),
                        "requested_amount": float(raw["requested_amount"]),
                        "tenure_months": int(raw["tenure_months"]),
                    }
                    if (not all(isfinite(value) for value in profile.values())
                            or profile["monthly_income"] <= 0 or profile["monthly_debt"] < 0
                            or profile["on_time_payments"] < 0 or profile["late_payments"] < 0
                            or profile["employment_years"] < 0 or profile["requested_amount"] <= 0
                            or not 1 <= profile["tenure_months"] <= 360):
                        raise ValueError
                except (TypeError, ValueError, OverflowError):
                    flash("Use valid values: income and loan must be positive; other values cannot be negative; tenure must be 1-360 months.", "error")
                else:
                    assessment = assess_risk(profile)
                    try:
                        connection.execute(
                            "INSERT INTO financial_profiles (user_id, monthly_income, monthly_debt, on_time_payments, late_payments, employment_years) "
                            "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET "
                            "monthly_income=excluded.monthly_income, monthly_debt=excluded.monthly_debt, "
                            "on_time_payments=excluded.on_time_payments, late_payments=excluded.late_payments, "
                            "employment_years=excluded.employment_years, updated_at=CURRENT_TIMESTAMP",
                            (user["id"], profile["monthly_income"], profile["monthly_debt"],
                             profile["on_time_payments"], profile["late_payments"], profile["employment_years"]),
                        )
                        cursor = connection.execute(
                            "INSERT INTO loans (borrower_id, requested_amount, tenure_months, risk_tier) VALUES (?, ?, ?, ?)",
                            (user["id"], profile["requested_amount"], profile["tenure_months"], assessment["tier"]),
                        )
                        if assessment["warning"]:
                            connection.execute(
                                "INSERT INTO warnings (loan_id, borrower_id, message) VALUES (?, ?, ?)",
                                (cursor.lastrowid, user["id"], assessment["warning"]),
                            )
                        connection.commit()
                    except sqlite3.Error:
                        connection.rollback()
                        raise
                    message = "Loan request submitted."
                    if assessment["tier"] == "High":
                        message += " High-risk warning: the lender will also be notified."
                    elif assessment["warning"]:
                        message += " A credit risk warning was recorded because the profile is incomplete."
                    flash(message, "success")
                    return redirect(url_for("borrower_dashboard"))

        profile = connection.execute("SELECT * FROM financial_profiles WHERE user_id = ?", (user["id"],)).fetchone()
        loans = connection.execute(
            "SELECT l.*, w.message AS warning_message FROM loans l LEFT JOIN warnings w ON w.loan_id = l.id "
            "WHERE l.borrower_id = ? ORDER BY l.created_at DESC, l.id DESC",
            (user["id"],),
        ).fetchall()
        return render_template("borrower.html", profile=profile, loans=loans)

    @app.route("/lender")
    @role_required("lender")
    def lender_dashboard():
        # Only lending-relevant fields are selected; borrower financial details stay private.
        loans = get_db().execute(
            "SELECT l.id, l.requested_amount, l.tenure_months, l.risk_tier, l.status, l.created_at, "
            "u.username, w.message AS warning_message FROM loans l "
            "JOIN users u ON u.id = l.borrower_id LEFT JOIN warnings w ON w.loan_id = l.id "
            "WHERE l.status = 'pending' ORDER BY l.created_at, l.id"
        ).fetchall()
        return render_template("lender.html", loans=loans)

    @app.route("/lender/loan/<int:loan_id>/decision", methods=["POST"])
    @role_required("lender")
    def decide_loan(loan_id):
        decision = request.form.get("decision")
        if decision not in ("approved", "rejected"):
            abort(400)
        connection = get_db()
        connection.execute("BEGIN IMMEDIATE")
        try:
            loan = connection.execute(
                "SELECT id, requested_amount, tenure_months, status FROM loans WHERE id = ?", (loan_id,)
            ).fetchone()
            if loan is None:
                abort(404)
            if loan["status"] != "pending":
                connection.rollback()
                flash("This loan request has already been decided.", "error")
                return redirect(url_for("lender_dashboard"))
            connection.execute("UPDATE loans SET status = ? WHERE id = ?", (decision, loan_id))
            if decision == "approved":
                schedule = generate_repayment_schedule(loan["requested_amount"], loan["tenure_months"])
                connection.executemany(
                    "INSERT INTO repayment_schedule (loan_id, installment_number, due_date, amount) VALUES (?, ?, ?, ?)",
                    [(loan_id, row["installment_number"], row["due_date"], float(row["amount"])) for row in schedule],
                )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        flash(f"Loan request {decision}.", "success")
        return redirect(url_for("lender_dashboard"))

    @app.route("/borrower/loan/<int:loan_id>/schedule")
    @role_required("borrower")
    def borrower_schedule(loan_id):
        user = get_current_user()
        connection = get_db()
        loan = connection.execute(
            "SELECT id, requested_amount, tenure_months, status FROM loans WHERE id = ? AND borrower_id = ?",
            (loan_id, user["id"]),
        ).fetchone()
        if loan is None:
            abort(404)
        schedule = connection.execute(
            "SELECT id, installment_number, due_date, amount, status, paid_at FROM repayment_schedule "
            "WHERE loan_id = ? ORDER BY installment_number", (loan_id,)
        ).fetchall()
        return render_template("schedule.html", loan=loan, schedule=schedule)

    @app.route("/borrower/loan/<int:loan_id>/repay/<int:installment_id>", methods=["POST"])
    @role_required("borrower")
    def repay_installment(loan_id, installment_id):
        user = get_current_user()
        connection = get_db()
        connection.execute("BEGIN IMMEDIATE")
        try:
            installment = connection.execute(
                "SELECT s.id, s.amount, s.status FROM repayment_schedule s JOIN loans l ON l.id = s.loan_id "
                "WHERE s.id = ? AND s.loan_id = ? AND l.borrower_id = ? AND l.status = 'approved'",
                (installment_id, loan_id, user["id"]),
            ).fetchone()
            if installment is None:
                connection.rollback()
                abort(404)
            if installment["status"] != "due":
                connection.rollback()
                flash("This installment has already been paid.", "error")
                return redirect(url_for("borrower_schedule", loan_id=loan_id))
            cursor = connection.execute(
                "UPDATE repayment_schedule SET status = 'paid', paid_at = ? WHERE id = ? AND status = 'due'",
                (datetime.utcnow().isoformat(timespec="seconds"), installment_id),
            )
            if cursor.rowcount != 1:
                raise sqlite3.IntegrityError("Installment was already paid.")
            connection.execute(
                "INSERT INTO transactions (schedule_id, borrower_id, amount) VALUES (?, ?, ?)",
                (installment_id, user["id"], installment["amount"]),
            )
            connection.commit()
        except sqlite3.IntegrityError:
            connection.rollback()
            flash("This installment has already been paid.", "error")
            return redirect(url_for("borrower_schedule", loan_id=loan_id))
        except Exception:
            connection.rollback()
            raise
        flash("Repayment recorded.", "success")
        return redirect(url_for("borrower_schedule", loan_id=loan_id))

    @app.errorhandler(403)
    def forbidden(_error):
        return render_template("error.html", message="You do not have permission to view this page."), 403

    @app.errorhandler(404)
    def not_found(_error):
        return render_template("error.html", message="The requested page or record was not found."), 404

    return app


if __name__ == "__main__":
    create_app().run(debug=False)