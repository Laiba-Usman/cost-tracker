"""Cost calculations. This is the single source of truth: the web page, the PDF
and the Excel export all use these numbers.

Definitions (same as the lab brief):
    variance      = actual - estimated        (positive = overspent)
    % variance    = variance / estimated * 100 (undefined when estimated = 0)
Only items that have an actual cost entered are compared. Items without an
actual yet are "Not spent yet" and are left out of the variance, otherwise a
project that is half finished would always look 50% under budget.
"""

ON_TRACK_BAND = 5.0  # +/- 5% counts as "On track"
COST_TYPES = ("Cash", "In-kind", "Free", "Other")


def item_result(estimated, actual):
    """Return (variance, pct, status) for one item."""
    if actual is None:
        return None, None, "Not spent yet"
    variance = actual - estimated
    if estimated == 0:
        return variance, None, ("Unbudgeted" if actual > 0 else "On track")
    pct = variance / estimated * 100
    if pct > ON_TRACK_BAND:
        status = "Over budget"
    elif pct < -ON_TRACK_BAND:
        status = "Under budget"
    else:
        status = "On track"
    return variance, pct, status


def _pct(variance, base):
    return None if not base else variance / base * 100


def _group(rows, key):
    order, groups = [], {}
    for r in rows:
        k = r[key]
        if k not in groups:
            groups[k] = {"name": k, "estimated": 0.0, "est_compared": 0.0,
                         "actual": 0.0, "items": 0, "items_with_actual": 0}
            order.append(k)
        g = groups[k]
        g["estimated"] += r["estimated"]
        g["items"] += 1
        if r["actual"] is not None:
            g["est_compared"] += r["estimated"]
            g["actual"] += r["actual"]
            g["items_with_actual"] += 1
    out = []
    for k in order:
        g = groups[k]
        g["variance"] = g["actual"] - g["est_compared"]
        g["pct"] = _pct(g["variance"], g["est_compared"])
        out.append(g)
    return out


def compute(items, contingency_pct=10.0):
    rows = []
    for it in items:
        est = float(it["estimated"])
        act = None if it.get("actual") is None else float(it["actual"])
        variance, pct, status = item_result(est, act)
        rows.append({**it, "estimated": est, "actual": act,
                     "variance": variance, "pct": pct, "status": status})

    with_actual = [r for r in rows if r["actual"] is not None]
    estimated_total = sum(r["estimated"] for r in rows)
    est_compared = sum(r["estimated"] for r in with_actual)
    actual_total = sum(r["actual"] for r in with_actual)
    variance = actual_total - est_compared
    pending = estimated_total - est_compared
    forecast = actual_total + pending           # estimate at completion
    cont_amount = estimated_total * contingency_pct / 100.0
    summary = {
        "items": len(rows),
        "items_with_actual": len(with_actual),
        "estimated_total": estimated_total,
        "est_compared": est_compared,
        "actual_total": actual_total,
        "variance": variance,
        "pct": _pct(variance, est_compared),
        "pending_estimate": pending,
        "forecast": forecast,
        "contingency_pct": contingency_pct,
        "contingency_amount": cont_amount,
        "approved_budget": estimated_total + cont_amount,
        "contingency_used_pct": (max(variance, 0) / cont_amount * 100) if cont_amount else None,
        "status_counts": {},
    }
    for r in rows:
        summary["status_counts"][r["status"]] = summary["status_counts"].get(r["status"], 0) + 1
    return {"rows": rows, "summary": summary,
            "categories": _group(rows, "category"),
            "cost_types": _group(rows, "cost_type")}


def chart_data(result, top_n=10):
    """Data for the three charts (used by the browser and by the PDF)."""
    cats = [c for c in result["categories"] if c["items_with_actual"] > 0]
    chart1 = {"labels": [c["name"] for c in cats],
              "estimated": [round(c["est_compared"], 2) for c in cats],
              "actual": [round(c["actual"], 2) for c in cats]}
    items = [r for r in result["rows"] if r["pct"] is not None]
    items.sort(key=lambda r: abs(r["pct"]), reverse=True)
    items = items[:top_n]
    chart2 = {"labels": [r["item"] for r in items],
              "pct": [round(r["pct"], 1) for r in items]}
    types = [t for t in result["cost_types"] if t["items_with_actual"] > 0 and t["actual"] > 0]
    chart3 = {"labels": [t["name"] for t in types],
              "actual": [round(t["actual"], 2) for t in types]}
    return {"by_category": chart1, "top_variance": chart2, "actual_by_type": chart3}
