import io, os, sys, zipfile
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pytest
import calc, importer, ai

def item(i, est, act, cat="A", t="Cash", name=None):
    return {"id": i, "category": cat, "item": name or "item%d" % i, "cost_type": t, "estimated": est, "actual": act}

def test_item_variance_and_percent():
    v, p, s = calc.item_result(1000, 1200)
    assert v == 200 and p == pytest.approx(20.0) and s == "Over budget"
    v, p, s = calc.item_result(1000, 700)
    assert v == -300 and p == pytest.approx(-30.0) and s == "Under budget"
    assert calc.item_result(1000, 1030)[2] == "On track"
    assert calc.item_result(1000, None) == (None, None, "Not spent yet")
    assert calc.item_result(0, 500) == (500, None, "Unbudgeted")
    assert calc.item_result(0, 0)[2] == "On track"

def test_totals_only_compare_items_with_actual():
    r = calc.compute([item(1, 1000, 1100), item(2, 500, None)], 10)
    s = r["summary"]
    assert s["estimated_total"] == 1500 and s["actual_total"] == 1100 and s["est_compared"] == 1000
    assert s["variance"] == 100 and s["pct"] == pytest.approx(10.0)
    assert s["forecast"] == 1100 + 500
    assert s["contingency_amount"] == 150 and s["approved_budget"] == 1650

def test_categories_and_types():
    r = calc.compute([item(1, 100, 150, "X", "Cash"), item(2, 100, 50, "Y", "In-kind")], 10)
    cats = {c["name"]: c for c in r["categories"]}
    assert cats["X"]["variance"] == 50 and cats["Y"]["pct"] == pytest.approx(-50.0)
    assert {t["name"] for t in r["cost_types"]} == {"Cash", "In-kind"}

def test_ai_flags_outlier_and_unbudgeted():
    items = [item(i, 1000, 1000 + d) for i, d in enumerate([0, 10, -10, 20, 5, 0, 500], 1)]
    items.append(item(9, 0, 300, name="surprise"))
    r = calc.compute(items, 10)
    flags = ai.detect_unusual(r)
    names = {f["item"] for f in flags}
    assert "item7" in names and "surprise" in names
    assert "item1" not in names
    text = " ".join(ai.summarise({"currency": "Rs."}, r, flags))
    assert "unusual" in text

def test_ai_no_actuals():
    r = calc.compute([item(1, 100, None)], 10)
    assert "No actual costs" in ai.summarise({"currency": "Rs."}, r, [])[0]

def test_import_csv_flat_and_negative_rows():
    data = b"Category,Item,Cost type,Estimated,Actual\nHW,Drive,cash,14000,13500\nHW,Bad,cash,-5,\n"
    items, warns = importer.parse_file("a.csv", data)
    assert len(items) == 1 and items[0]["actual"] == 13500 and items[0]["cost_type"] == "Cash"
    assert warns and "negative" in warns[0]

def test_import_lykos_xlsx():
    p = os.path.join(os.path.dirname(__file__), "..", "sample_data", "LYKOS_Project_Budget.xlsx")
    items, warns = importer.parse_file("b.xlsx", open(p, "rb").read())
    assert len(items) == 27 and not warns
    assert round(sum(i["estimated"] for i in items)) == 556717
    assert items[0]["category"] == "Personnel"

@pytest.mark.parametrize("name,data", [
    ("evil.exe", b"MZ..."), ("x.xlsx", b"not a zip"), ("x.csv", b""), ("x.csv", b"\x00\x01binary" * 10),
    ("big.csv", b"a" * (importer.MAX_BYTES + 1)),
])
def test_bad_uploads_rejected(name, data):
    with pytest.raises(importer.ImportError_):
        importer.parse_file(name, data)

def test_zip_without_workbook_rejected():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("hello.txt", "x")
    with pytest.raises(importer.ImportError_):
        importer.parse_file("x.xlsx", buf.getvalue())

def test_ai_analyse_full_output():
    r = calc.compute([item(1, 1000, 1100), item(2, 500, None)], 10)
    out = ai.analyse({"currency": "Rs."}, r)
    assert "variance" in out["method"] and out["summary"]
