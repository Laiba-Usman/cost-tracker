"""AI feature: unusual-expense detection and a written cost analysis.

How it works (all local, no data leaves the computer):
  1. Statistics: the modified z-score (median / MAD, robust to outliers) of the
     % variance of every item that has an actual cost. |z| > 3.5 is unusual.
  2. Rules: > 25% over estimate, spending on a line that had no budget, an
     estimate that is more than 50% unused on a paid line, duplicate item names.
  3. A plain-language summary is generated from the computed facts (no numbers
     are invented; every sentence is built from the project's own figures).
"""
import statistics

Z_LIMIT = 3.5
BIG_OVERRUN = 25.0
BIG_UNDERRUN = -50.0


def _money(x, cur):
    return "%s %s" % (cur, format(round(x), ",d"))


def detect_unusual(result):
    flags = {}

    def add(r, reason):
        f = flags.setdefault(r["id"], {"id": r["id"], "item": r["item"], "category": r["category"],
                                       "estimated": r["estimated"], "actual": r["actual"],
                                       "variance": r["variance"], "pct": r["pct"], "reasons": []})
        f["reasons"].append(reason)

    measured = [r for r in result["rows"] if r["pct"] is not None]
    if len(measured) >= 5:
        vals = [r["pct"] for r in measured]
        med = statistics.median(vals)
        mad = statistics.median([abs(v - med) for v in vals])
        if mad > 0:
            for r in measured:
                z = 0.6745 * (r["pct"] - med) / mad
                if abs(z) > Z_LIMIT:
                    add(r, "Statistical outlier: %+.1f%% against a typical %+.1f%% (modified z-score %.1f)." % (r["pct"], med, z))
    seen = {}
    for r in result["rows"]:
        if r["status"] == "Unbudgeted":
            add(r, "Money was spent on a line that has no estimate.")
        elif r["pct"] is not None:
            if r["pct"] > BIG_OVERRUN:
                add(r, "More than %d%% over the estimate." % BIG_OVERRUN)
            elif r["pct"] < BIG_UNDERRUN and r["cost_type"] != "Free":
                add(r, "More than %d%% under the estimate; check that the actual cost is complete." % -BIG_UNDERRUN)
        key = r["item"].strip().lower()
        if key in seen:
            add(r, "Same item name appears more than once; check for a duplicate entry.")
        seen[key] = True
    return sorted(flags.values(), key=lambda f: -abs(f["pct"] if f["pct"] is not None else 100))


def summarise(project, result, flags):
    s, cur = result["summary"], project.get("currency") or "Rs."
    out = []
    if s["items_with_actual"] == 0:
        return ["No actual costs have been entered yet, so there is nothing to compare. Enter actual costs or upload a file that has an Actual column."]
    share = s["items_with_actual"] / s["items"] * 100
    word = "over" if s["variance"] > 0 else "under"
    if s["variance"] == 0:
        out.append("Actual cost matches the estimate for the items recorded so far.")
    else:
        pct = "" if s["pct"] is None else " (%.1f%%)" % abs(s["pct"])
        out.append("%d of %d items (%.0f%%) have an actual cost. They cost %s against an estimate of %s, which is %s%s %s the estimate." % (
            s["items_with_actual"], s["items"], share, _money(s["actual_total"], cur),
            _money(s["est_compared"], cur), _money(abs(s["variance"]), cur), pct, word))
    cats = [c for c in result["categories"] if c["items_with_actual"] and c["variance"] > 0]
    if cats:
        top = max(cats, key=lambda c: c["variance"])
        out.append("The largest overspend by category is %s: %s over (%s)." % (
            top["name"], _money(top["variance"], cur), "%.1f%%" % top["pct"] if top["pct"] is not None else "no estimate"))
    over = sorted([r for r in result["rows"] if r["variance"] and r["variance"] > 0], key=lambda r: -r["variance"])[:3]
    if over:
        out.append("Biggest overspends by item: " + "; ".join("%s (+%s)" % (r["item"], _money(r["variance"], cur)) for r in over) + ".")
    cash = next((t for t in result["cost_types"] if t["name"] == "Cash"), None)
    if cash and cash["items_with_actual"]:
        out.append("Real cash spent so far is %s against %s estimated for those items (%s %s)." % (
            _money(cash["actual"], cur), _money(cash["est_compared"], cur), _money(abs(cash["variance"]), cur),
            "over" if cash["variance"] > 0 else "under" if cash["variance"] < 0 else "difference"))
    if s["contingency_used_pct"] is not None and s["variance"] > 0:
        u = s["contingency_used_pct"]
        out.append("The overspend uses %.0f%% of the %s contingency (%.0f%% of the estimate)%s." % (
            u, _money(s["contingency_amount"], cur), s["contingency_pct"],
            ". The contingency is fully used" if u >= 100 else ""))
    if s["pending_estimate"] > 0:
        ok = s["forecast"] <= s["approved_budget"]
        out.append("%s of estimated cost is still to be spent. If those items follow their estimates, the final cost will be %s, which is %s the approved budget of %s (estimate plus contingency)." % (
            _money(s["pending_estimate"], cur), _money(s["forecast"], cur),
            "within" if ok else "above", _money(s["approved_budget"], cur)))
    if flags:
        out.append("%d item(s) look unusual and are listed below." % len(flags))
    recs = []
    if s["contingency_used_pct"] is not None and s["contingency_used_pct"] >= 100:
        recs.append("The contingency is used up: review scope or ask for more budget before spending more.")
    if any(r["status"] == "Unbudgeted" for r in result["rows"]):
        recs.append("Add the unbudgeted items to the estimate so the next plan includes them.")
    if flags:
        recs.append("Check the flagged items first; some may be data-entry mistakes rather than real overspending.")
    if not recs:
        recs.append("No action needed now. Keep entering actual costs as they happen.")
    out.append("Suggested next steps: " + " ".join(recs))
    return out


def analyse(project, result):
    flags = detect_unusual(result)
    return {"summary": summarise(project, result, flags), "flags": flags,
            "method": "Modified z-score on %% variance (|z| > %.1f) plus rules (over %d%%, unbudgeted, under %d%%, duplicates). Runs locally; nothing is sent to an outside service." % (Z_LIMIT, BIG_OVERRUN, -BIG_UNDERRUN)}
