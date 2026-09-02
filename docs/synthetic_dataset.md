# Synthetic Persona Dataset

**Status:** Phase 1 evaluation fixture  
**Dataset:** `data/synthetic_personas.csv`

## Purpose

This fixed population models fictional Brazilian medical-doctor leads for
manual conversations, synthetic-customer simulations, behavioral evaluations,
and prompt comparisons. It is evaluation data, not evidence of real conversion
performance.

All names, circumstances, messages, and financial attributes are invented.
There are no phone numbers, email addresses, account identifiers, patient data,
or other direct identifiers.

## Schema

| Field | Type / values | Purpose |
|---|---|---|
| persona_id | `MED_001`–`MED_030` | Stable evaluation identifier |
| name | text | Fictional first name |
| age | integer, 24–75 | Behavioral context only |
| specialty | controlled text | Specialty distribution |
| career_stage | enum | `early_career`, `career_consolidation`, `high_income`, `wealth_patrimonial` |
| monthly_income_range | enum | Broad synthetic range in BRL |
| investable_assets_range | enum | Broad synthetic range in BRL |
| current_support | enum | Current planner/advisor context |
| primary_need | enum | `financial_planning`, `investment_advisory`, `both`, `out_of_scope` |
| primary_pain | controlled text | Main commercial discovery signal |
| secondary_pain | controlled text or `none` | Supporting discovery signal |
| pain_severity | integer, 0–10 | Persona behavior parameter |
| urgency | integer, 0–10 | Persona behavior parameter |
| financial_sophistication | enum | `low`, `medium`, `high` |
| communication_style | enum | How the synthetic customer communicates |
| decision_style | enum | How the synthetic customer evaluates a next step |
| hidden_objection | objection enum or `none` | Objection revealed only when naturally explored |
| expected_fit | enum | `high`, `medium`, `low`, `no_fit` |
| expected_service | enum | Expected routing decision |
| ideal_next_action | enum | Expected valid commercial outcome |
| scenario_summary | text | Stable ground truth for simulation/evaluation |
| opening_message | text | Natural initial message in Brazilian Portuguese |

The persona fields represent simulation ground truth. The Sales Agent must still
base decisions only on facts revealed during a conversation; it must not read
hidden persona attributes.

## Population design

Expected-fit distribution:

| Fit | Count |
|---|---:|
| High | 10 |
| Medium | 10 |
| Low | 5 |
| No Fit | 5 |

Career-stage distribution:

| Stage | Count |
|---|---:|
| Early Career | 7 |
| Career Consolidation | 8 |
| High Income | 9 |
| Wealth / Patrimonial | 6 |

Expected-service distribution:

| Service | Count |
|---|---:|
| Financial Planning | 8 |
| Investment Advisory | 8 |
| Both | 9 |
| No Current Fit | 5 |

The population covers 20 specialties and all 10 objection categories from the
business rules. It varies age, career stage, income/assets, current support,
financial sophistication, communication style, decision style, pain, urgency,
and readiness. Demographic or financial ranges do not determine expected fit by
themselves; the scenario's expressed need and openness to professional support
do.

## Controlled ranges

Monthly income ranges:

- `under_15k`
- `15k_25k`
- `25k_40k`
- `40k_60k`
- `60k_100k`
- `100k_plus`

Investable-asset ranges:

- `none`
- `under_50k`
- `50k_100k`
- `100k_500k`
- `500k_1m`
- `1m_3m`
- `3m_plus`

## Validation

Run:

```bash
python3 scripts/validate_personas.py
```

The validator checks the schema, row count, stable unique IDs, required fit
distribution, enum values, numeric bounds, route/need consistency, all objection
categories, broad specialty and career-stage coverage, absence of direct contact
fields, and absence of obvious contact/account data in free text.
