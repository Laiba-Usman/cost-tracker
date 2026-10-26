"""PERT (Program Evaluation and Review Technique) analysis for cost items.

PERT uses three estimates for each cost item:
    Optimistic (O):   Best-case scenario — minimum possible cost
    Most Likely (M):  Most probable cost (the current 'estimated' value)
    Pessimistic (P):  Worst-case scenario — maximum possible cost

PERT Expected Cost = (O + 4*M + P) / 6
Standard Deviation  = (P - O) / 6
Variance (σ²)       = ((P - O) / 6)²
Confidence range     = Expected ± 2σ  (≈ 95% confidence)

The module also computes project-level PERT totals and a risk assessment.
"""
import math


def item_pert(optimistic, most_likely, pessimistic):
    """Calculate PERT metrics for a single cost item.
    Returns dict with expected, std_dev, variance, range_low, range_high."""
    o, m, p = float(optimistic), float(most_likely), float(pessimistic)
    if o > m or m > p:
        return None  # invalid: O ≤ M ≤ P required
    expected = (o + 4 * m + p) / 6.0
    std_dev = (p - o) / 6.0
    variance = std_dev ** 2
    return {
        "optimistic": o,
        "most_likely": m,
        "pessimistic": p,
        "expected": round(expected, 2),
        "std_dev": round(std_dev, 2),
        "variance": round(variance, 2),
        "range_low": round(expected - 2 * std_dev, 2),
        "range_high": round(expected + 2 * std_dev, 2),
    }


def compute_pert(items):
    """Compute PERT analysis for a list of items.
    Each item must have: id, item, category, estimated, optimistic, pessimistic.
    'estimated' is treated as Most Likely (M).
    Returns dict with rows (per-item PERT) and summary (project-level totals).
    """
    rows = []
    total_expected = 0.0
    total_variance = 0.0
    skipped = 0

    for it in items:
        o = it.get("optimistic")
        p = it.get("pessimistic")
        m = it.get("estimated", 0)

        if o is None or p is None:
            skipped += 1
            continue

        o, m, p = float(o), float(m), float(p)
        pert = item_pert(o, m, p)
        if pert is None:
            skipped += 1
            continue

        rows.append({
            "id": it.get("id"),
            "item": it.get("item", ""),
            "category": it.get("category", ""),
            **pert,
        })
        total_expected += pert["expected"]
        total_variance += pert["variance"]

    total_std_dev = math.sqrt(total_variance) if total_variance > 0 else 0.0

    # Risk level based on coefficient of variation
    cv = (total_std_dev / total_expected * 100) if total_expected > 0 else 0
    if cv < 5:
        risk_level = "Low"
        risk_note = "The project estimates are tightly clustered. Minimal cost uncertainty."
    elif cv < 15:
        risk_level = "Medium"
        risk_note = "Moderate uncertainty in cost estimates. Some items have wide ranges."
    else:
        risk_level = "High"
        risk_note = "High uncertainty in cost estimates. Several items have very wide optimistic-to-pessimistic ranges."

    summary = {
        "total_items": len(rows),
        "skipped_items": skipped,
        "total_expected": round(total_expected, 2),
        "total_std_dev": round(total_std_dev, 2),
        "total_variance": round(total_variance, 2),
        "range_low": round(total_expected - 2 * total_std_dev, 2),
        "range_high": round(total_expected + 2 * total_std_dev, 2),
        "confidence": "95%",
        "coefficient_of_variation": round(cv, 1),
        "risk_level": risk_level,
        "risk_note": risk_note,
    }

    return {"rows": rows, "summary": summary}
