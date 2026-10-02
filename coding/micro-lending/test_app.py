"""Focused tests for credit risk, schedules, and one-time repayments."""

import os
import tempfile
import unittest
from datetime import date
from decimal import Decimal

from app import create_app, generate_repayment_schedule
from risk_engine import assess_risk


class MicroLendingTests(unittest.TestCase):
    """Use an isolated SQLite database for each application test."""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.app = create_app({
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "DATABASE": os.path.join(self.temp_dir.name, "test.sqlite3"),
        })
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def register_and_login(self, username, role):
        self.client.post("/register", data={
            "username": username,
            "password": "test-password-123",
            "role": role,
        })
        return self.client.post("/login", data={
            "username": username,
            "password": "test-password-123",
        }, follow_redirects=True)

    def test_risk_engine_tiers_and_incomplete_profile(self):
        low = assess_risk({
            "monthly_income": 5000, "monthly_debt": 1000, "on_time_payments": 12,
            "late_payments": 0, "employment_years": 3, "requested_amount": 1000, "tenure_months": 12,
        })
        high = assess_risk({
            "monthly_income": 2000, "monthly_debt": 1200, "on_time_payments": 3,
            "late_payments": 7, "employment_years": 0.2, "requested_amount": 1000, "tenure_months": 12,
        })
        incomplete = assess_risk({"monthly_income": 1000})
        self.assertEqual(low["tier"], "Low")
        self.assertEqual(high["tier"], "High")
        self.assertIsNone(incomplete["tier"])
        self.assertTrue(incomplete["warning"])

    def test_repayment_schedule_has_monthly_dates_and_exact_total(self):
        schedule = generate_repayment_schedule(Decimal("100.00"), 3, date(2026, 1, 31))
        self.assertEqual([item["due_date"] for item in schedule], ["2026-02-28", "2026-03-31", "2026-04-30"])
        self.assertEqual(sum(item["amount"] for item in schedule), Decimal("100.00"))
        self.assertEqual([item["amount"] for item in schedule], [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")])

    def test_repayment_cannot_be_processed_twice(self):
        self.register_and_login("borrower", "borrower")
        response = self.client.post("/borrower", data={
            "monthly_income": "4000", "monthly_debt": "500", "on_time_payments": "10",
            "late_payments": "0", "employment_years": "4", "requested_amount": "120",
            "tenure_months": "3",
        }, follow_redirects=True)
        self.assertIn(b"Loan request submitted", response.data)

        self.client.post("/logout")
        self.register_and_login("lender", "lender")
        with self.app.app_context():
            from app import get_db

            loan_id = get_db().execute("SELECT id FROM loans").fetchone()["id"]
        self.client.post(f"/lender/loan/{loan_id}/decision", data={"decision": "approved"})

        self.client.post("/logout")
        self.client.post("/login", data={"username": "borrower", "password": "test-password-123"})
        with self.app.app_context():
            from app import get_db

            installment_id = get_db().execute(
                "SELECT id FROM repayment_schedule WHERE loan_id = ? ORDER BY installment_number LIMIT 1", (loan_id,)
            ).fetchone()["id"]
        url = f"/borrower/loan/{loan_id}/repay/{installment_id}"
        first = self.client.post(url, follow_redirects=True)
        second = self.client.post(url, follow_redirects=True)
        self.assertIn(b"Repayment recorded", first.data)
        self.assertIn(b"already been paid", second.data)
        with self.app.app_context():
            from app import get_db

            count = get_db().execute("SELECT COUNT(*) AS count FROM transactions WHERE schedule_id = ?", (installment_id,)).fetchone()["count"]
            self.assertEqual(count, 1)


if __name__ == "__main__":
    unittest.main()