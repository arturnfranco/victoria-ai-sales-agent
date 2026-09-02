#!/usr/bin/env python3
"""Validate the fixed Phase 1 synthetic-persona dataset."""

from __future__ import annotations

import csv
import re
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATASET = ROOT / "data" / "synthetic_personas.csv"

REQUIRED_COLUMNS = [
    "persona_id",
    "name",
    "age",
    "specialty",
    "career_stage",
    "monthly_income_range",
    "investable_assets_range",
    "current_support",
    "primary_need",
    "primary_pain",
    "secondary_pain",
    "pain_severity",
    "urgency",
    "financial_sophistication",
    "communication_style",
    "decision_style",
    "hidden_objection",
    "expected_fit",
    "expected_service",
    "ideal_next_action",
    "scenario_summary",
    "opening_message",
]

ALLOWED = {
    "career_stage": {
        "early_career",
        "career_consolidation",
        "high_income",
        "wealth_patrimonial",
    },
    "monthly_income_range": {
        "under_15k",
        "15k_25k",
        "25k_40k",
        "40k_60k",
        "60k_100k",
        "100k_plus",
    },
    "investable_assets_range": {
        "none",
        "under_50k",
        "50k_100k",
        "100k_500k",
        "500k_1m",
        "1m_3m",
        "3m_plus",
    },
    "current_support": {
        "none",
        "self_managed",
        "accountant",
        "financial_planner",
        "investment_advisor",
        "private_bank",
    },
    "primary_need": {
        "financial_planning",
        "investment_advisory",
        "both",
        "out_of_scope",
    },
    "financial_sophistication": {"low", "medium", "high"},
    "hidden_objection": {
        "none",
        "price",
        "trust",
        "time",
        "existing_advisor",
        "do_it_myself",
        "partner_decision",
        "not_priority",
        "bad_previous_experience",
        "wants_free_advice",
        "wants_immediate_recommendation",
    },
    "expected_fit": {"high", "medium", "low", "no_fit"},
    "expected_service": {
        "financial_planning",
        "investment_advisory",
        "both",
        "no_current_fit",
    },
    "ideal_next_action": {
        "schedule_meeting",
        "continue_discovery",
        "nurture_or_close",
        "close_helpfully",
    },
}

EXPECTED_FIT_COUNTS = {"high": 10, "medium": 10, "low": 5, "no_fit": 5}
EXPECTED_CAREER_STAGE_COUNTS = {
    "early_career": 7,
    "career_consolidation": 8,
    "high_income": 9,
    "wealth_patrimonial": 6,
}
EXPECTED_SERVICE_COUNTS = {
    "financial_planning": 8,
    "investment_advisory": 8,
    "both": 9,
    "no_current_fit": 5,
}
REQUIRED_OBJECTIONS = ALLOWED["hidden_objection"] - {"none"}
FORBIDDEN_COLUMNS = {
    "email",
    "phone",
    "phone_number",
    "tax_id",
    "cpf",
    "account_number",
    "address",
}
CONTACT_OR_ACCOUNT_PATTERN = re.compile(
    r"(?:\b\d{3}\.\d{3}\.\d{3}-\d{2}\b)|"
    r"(?:\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b)|"
    r"(?:\b(?:\+?55\s*)?(?:\(?\d{2}\)?\s*)?9?\d{4}[- ]?\d{4}\b)"
)


def validate() -> list[str]:
    errors: list[str] = []

    with DATASET.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        columns = reader.fieldnames or []

    if columns != REQUIRED_COLUMNS:
        errors.append("CSV columns do not exactly match the documented schema")
    if FORBIDDEN_COLUMNS.intersection(columns):
        errors.append("CSV contains forbidden direct-identifier columns")
    if len(rows) != 30:
        errors.append(f"expected 30 personas, found {len(rows)}")

    expected_ids = [f"MED_{number:03d}" for number in range(1, 31)]
    actual_ids = [row.get("persona_id", "") for row in rows]
    if actual_ids != expected_ids:
        errors.append("persona IDs must be unique and ordered MED_001 through MED_030")

    for row_number, row in enumerate(rows, start=2):
        persona_id = row.get("persona_id") or f"row {row_number}"

        for column in REQUIRED_COLUMNS:
            if not row.get(column, "").strip():
                errors.append(f"{persona_id}: missing {column}")

        for column, allowed_values in ALLOWED.items():
            value = row.get(column)
            if value not in allowed_values:
                errors.append(f"{persona_id}: invalid {column}={value!r}")

        for column, lower, upper in (
            ("age", 24, 75),
            ("pain_severity", 0, 10),
            ("urgency", 0, 10),
        ):
            try:
                value = int(row[column])
            except (KeyError, TypeError, ValueError):
                errors.append(f"{persona_id}: {column} must be an integer")
            else:
                if not lower <= value <= upper:
                    errors.append(
                        f"{persona_id}: {column} must be between {lower} and {upper}"
                    )

        if row.get("expected_fit") == "no_fit":
            if row.get("primary_need") != "out_of_scope":
                errors.append(f"{persona_id}: no_fit must have out_of_scope need")
            if row.get("expected_service") != "no_current_fit":
                errors.append(f"{persona_id}: no_fit must route to no_current_fit")
            if row.get("ideal_next_action") != "close_helpfully":
                errors.append(f"{persona_id}: no_fit must close helpfully")
        elif row.get("primary_need") != row.get("expected_service"):
            errors.append(f"{persona_id}: in-scope need and service route differ")

        free_text = " ".join(
            row.get(column, "")
            for column in ("name", "scenario_summary", "opening_message")
        )
        if CONTACT_OR_ACCOUNT_PATTERN.search(free_text):
            errors.append(f"{persona_id}: possible direct contact/account data in text")

    fit_counts = Counter(row["expected_fit"] for row in rows)
    if dict(fit_counts) != EXPECTED_FIT_COUNTS:
        errors.append(
            f"fit distribution must be {EXPECTED_FIT_COUNTS}, found {dict(fit_counts)}"
        )

    career_stage_counts = Counter(row["career_stage"] for row in rows)
    if dict(career_stage_counts) != EXPECTED_CAREER_STAGE_COUNTS:
        errors.append(
            "career-stage distribution must be "
            f"{EXPECTED_CAREER_STAGE_COUNTS}, found {dict(career_stage_counts)}"
        )

    service_counts = Counter(row["expected_service"] for row in rows)
    if dict(service_counts) != EXPECTED_SERVICE_COUNTS:
        errors.append(
            f"service distribution must be {EXPECTED_SERVICE_COUNTS}, "
            f"found {dict(service_counts)}"
        )

    represented_objections = {row["hidden_objection"] for row in rows}
    missing_objections = REQUIRED_OBJECTIONS - represented_objections
    if missing_objections:
        errors.append(f"missing objection categories: {sorted(missing_objections)}")

    if len({row["specialty"] for row in rows}) != 20:
        errors.append("dataset must preserve its distribution across 20 specialties")
    if {row["career_stage"] for row in rows} != ALLOWED["career_stage"]:
        errors.append("dataset must cover every career stage")

    return errors


def main() -> int:
    errors = validate()
    if errors:
        print("Synthetic persona validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("Synthetic persona validation passed: 30 fictional personas")
    print("Fit distribution: 10 high, 10 medium, 5 low, 5 no_fit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
