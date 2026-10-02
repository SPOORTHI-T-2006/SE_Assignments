"""Credit risk assessment from a borrower's submitted financial profile."""

from math import isfinite


REQUIRED_FIELDS = (
    "monthly_income",
    "monthly_debt",
    "on_time_payments",
    "late_payments",
    "employment_years",
    "requested_amount",
    "tenure_months",
)


def assess_risk(profile):
    """Return a risk tier, or a warning when the profile is incomplete."""
    if not profile or any(profile.get(field) is None for field in REQUIRED_FIELDS):
        return {"tier": None, "warning": "Complete all financial profile fields to receive a risk rating."}

    try:
        values = {field: float(profile[field]) for field in REQUIRED_FIELDS}
    except (TypeError, ValueError):
        return {"tier": None, "warning": "Financial profile values must be valid numbers."}

    if not all(isfinite(value) for value in values.values()):
        return {"tier": None, "warning": "Financial profile values must be finite numbers."}

    payment_count = values["on_time_payments"] + values["late_payments"]
    if payment_count <= 0:
        return {"tier": None, "warning": "A repayment history is needed before a risk rating can be calculated."}

    debt_to_income = values["monthly_debt"] / values["monthly_income"] if values["monthly_income"] > 0 else float("inf")
    on_time_rate = values["on_time_payments"] / payment_count
    employment_years = values["employment_years"]

    if debt_to_income > 0.50 or on_time_rate < 0.70 or employment_years < 0.5:
        tier = "High"
    elif debt_to_income <= 0.30 and on_time_rate >= 0.90 and employment_years >= 2:
        tier = "Low"
    else:
        tier = "Medium"

    return {"tier": tier, "warning": None}