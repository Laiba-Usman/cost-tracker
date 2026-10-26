"""Compare multiple uploaded files (budget revisions or different project files).

The user uploads 2 or 3 CSV/Excel files and this module compares them side-by-side:
  - Items that appear in all files
  - Items added or removed between files
  - Cost differences (estimated and actual) between files
  - Summary statistics per file and deltas between them
"""
import importer


def _key(item):
    """Normalise item name for matching across files."""
    return item.get("item", "").strip().lower()


def parse_uploaded_files(files):
    """Parse a list of (filename, data) tuples. Returns list of parsed file dicts.
    Each dict: {name, items (list), lookup (dict by normalised item name)}."""
    parsed = []
    for fname, data in files:
        items, warnings = importer.parse_file(fname, data, need_estimate=True)
        lookup = {}
        for it in items:
            k = _key(it)
            if k not in lookup:
                lookup[k] = it
        parsed.append({
            "name": fname,
            "items": items,
            "lookup": lookup,
            "warnings": warnings,
        })
    return parsed


def compare_files(parsed_files):
    """Compare 2 or 3 parsed files. Returns comparison result dict."""
    if len(parsed_files) < 2:
        raise ValueError("At least 2 files are needed for comparison.")
    if len(parsed_files) > 3:
        raise ValueError("At most 3 files can be compared at once.")

    file_names = [f["name"] for f in parsed_files]

    # Collect all unique items across all files
    all_keys = []
    seen = set()
    for pf in parsed_files:
        for it in pf["items"]:
            k = _key(it)
            if k not in seen:
                seen.add(k)
                all_keys.append(k)

    # Build comparison rows
    rows = []
    for k in all_keys:
        row = {"item_key": k}
        item_name = None
        category = None
        file_data = []
        present_in = []

        for i, pf in enumerate(parsed_files):
            it = pf["lookup"].get(k)
            if it:
                present_in.append(file_names[i])
                if item_name is None:
                    item_name = it["item"]
                    category = it.get("category", "Uncategorised")
                file_data.append({
                    "file": file_names[i],
                    "estimated": it.get("estimated"),
                    "actual": it.get("actual"),
                    "cost_type": it.get("cost_type", "Other"),
                })
            else:
                file_data.append({
                    "file": file_names[i],
                    "estimated": None,
                    "actual": None,
                    "cost_type": None,
                })

        # Determine change status
        if len(present_in) == len(parsed_files):
            status = "common"
        elif len(present_in) == 1:
            status = "unique"
        else:
            status = "partial"

        # Calculate differences between files
        diffs = []
        for j in range(1, len(parsed_files)):
            prev = file_data[j - 1]
            curr = file_data[j]
            est_diff = None
            act_diff = None
            if prev["estimated"] is not None and curr["estimated"] is not None:
                est_diff = curr["estimated"] - prev["estimated"]
            if prev["actual"] is not None and curr["actual"] is not None:
                act_diff = curr["actual"] - prev["actual"]
            diffs.append({
                "from_file": file_names[j - 1],
                "to_file": file_names[j],
                "estimated_diff": est_diff,
                "actual_diff": act_diff,
            })

        rows.append({
            "item": item_name,
            "category": category,
            "present_in": present_in,
            "status": status,
            "files": file_data,
            "diffs": diffs,
        })

    # Per-file summary
    file_summaries = []
    for i, pf in enumerate(parsed_files):
        total_est = sum(it.get("estimated", 0) or 0 for it in pf["items"])
        total_act = sum(it.get("actual", 0) or 0 for it in pf["items"] if it.get("actual") is not None)
        items_with_actual = sum(1 for it in pf["items"] if it.get("actual") is not None)
        file_summaries.append({
            "name": pf["name"],
            "total_items": len(pf["items"]),
            "total_estimated": round(total_est, 2),
            "total_actual": round(total_act, 2),
            "items_with_actual": items_with_actual,
        })

    # Deltas between consecutive files
    deltas = []
    for j in range(1, len(file_summaries)):
        prev, curr = file_summaries[j - 1], file_summaries[j]
        est_delta = curr["total_estimated"] - prev["total_estimated"]
        act_delta = curr["total_actual"] - prev["total_actual"]
        item_delta = curr["total_items"] - prev["total_items"]
        deltas.append({
            "from_file": prev["name"],
            "to_file": curr["name"],
            "estimated_delta": round(est_delta, 2),
            "actual_delta": round(act_delta, 2),
            "items_delta": item_delta,
        })

    # Count statuses
    common = sum(1 for r in rows if r["status"] == "common")
    unique = sum(1 for r in rows if r["status"] == "unique")
    partial = sum(1 for r in rows if r["status"] == "partial")

    return {
        "file_names": file_names,
        "rows": rows,
        "file_summaries": file_summaries,
        "deltas": deltas,
        "stats": {
            "total_unique_items": len(rows),
            "common_items": common,
            "unique_to_one_file": unique,
            "partial_overlap": partial,
        },
    }
