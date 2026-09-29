#!/usr/bin/env python
# =====================================================================
# Lakeview Retail — CFO Cash-Flow Shortfall demo — data generator
#
# Deterministic (seed=42). Builds 12 tables into
#   serverless_stable_genie_cfo_catalog.lakeview_retail
# via Databricks Connect (serverless), profile fe-vm-genie-cfo.
#
# PLANTED PROBLEM (reconciles exactly):
#   Plan quarter-end cash (2026-12-31) = $30.0M
#   Projected (forecast run 2026-11-05) = $22.0M  -> $8.0M shortfall
#     INVENTORY_TRAP       -$3.5M  (aged Women's Outerwear FW25)
#     COLLECTIONS_SLOWDOWN -$2.5M  ($1.7M wholesale disputes + $0.8M settlement slip)
#     AP_ACCELERATION      -$2.0M  (early pay + lost discount + surcharge + expedite)
#   Prior run (2026-10-22) projected $25.0M -> current run shows ~$3.0M
#   deterioration in the last 2 weeks (level + velocity alert).
#
# Run:
#   DATABRICKS_CONFIG_PROFILE=fe-vm-genie-cfo \
#   uv run --with pandas --with numpy --with pyarrow \
#          --with "databricks-connect>=15.1,<16.0" data/generate_data.py
# =====================================================================
import datetime as dt
import numpy as np
import pandas as pd

SEED = 42
rng = np.random.default_rng(SEED)

CAT = "serverless_stable_genie_cfo_catalog"
SCH = "lakeview_retail"

START = dt.date(2024, 11, 1)
ALERT = dt.date(2026, 11, 5)     # "today" in the demo
QEND = dt.date(2026, 12, 31)     # fiscal quarter-end
PRIOR_RUN = dt.date(2026, 10, 22)  # forecast run 2 weeks before alert
DATE_END = dt.date(2026, 12, 31)   # dim_date horizon

# Exact reconciliation targets (USD)
T_INVENTORY = 3_500_000.0
T_WHOLESALE = 1_700_000.0
T_SETTLEMENT = 800_000.0
T_COLLECTIONS = T_WHOLESALE + T_SETTLEMENT   # 2.5M
T_AP = 2_000_000.0
PLAN_QEND_CASH = 30_000_000.0
PROJ_QEND_CASH = 22_000_000.0                # 30 - 8
PRIOR_PROJ_QEND = 25_000_000.0               # -> 3M deterioration


def scale_to(vals, target):
    """Scale a positive array so it sums to exactly `target` (2dp)."""
    vals = np.asarray(vals, dtype=float)
    vals = vals * (target / vals.sum())
    vals = np.round(vals, 2)
    # fix rounding residual on the last element
    vals[-1] = round(vals[-1] + (target - vals.sum()), 2)
    return vals


# =====================================================================
# DIMENSIONS
# =====================================================================
def build_dim_date():
    days = pd.date_range(START, DATE_END, freq="D")
    df = pd.DataFrame({"date_key": days})
    d = df["date_key"]
    week_end = d + pd.to_timedelta((5 - d.dt.weekday) % 7, unit="D")  # next Saturday
    return pd.DataFrame({
        "date_key": d.dt.date,
        "day_of_month": d.dt.day,
        "month_num": d.dt.month,
        "month_name": d.dt.strftime("%B"),
        "quarter_num": d.dt.quarter,
        "year_num": d.dt.year,
        "fiscal_quarter": "FY" + d.dt.year.astype(str) + "-Q" + d.dt.quarter.astype(str),
        "fiscal_year": d.dt.year,
        "week_of_year": d.dt.isocalendar().week.astype(int).values,
        "week_ending_date": week_end.dt.date,
        "day_of_week": d.dt.strftime("%a"),
        "is_weekend": d.dt.weekday >= 5,
        "is_quarter_end": d.dt.is_quarter_end,
    })


# department -> list of (category, n_skus)
DEPT_CATS = {
    "Women's": [("Outerwear", 60), ("Tops", 50), ("Denim", 30), ("Dresses", 20)],
    "Men's": [("Outerwear", 25), ("Tops", 45), ("Denim", 30), ("Activewear", 20)],
    "Kids": [("Tops", 35), ("Bottoms", 25), ("Outerwear", 20)],
    "Footwear": [("Sneakers", 35), ("Boots", 25), ("Sandals", 20)],
    "Accessories": [("Bags", 25), ("Belts", 15), ("Hats", 20)],
}


def build_dim_product(supplier_ids, trap_supplier_id):
    rows = []
    i = 0
    for dept, cats in DEPT_CATS.items():
        for cat, n in cats:
            for _ in range(n):
                i += 1
                pid = f"SKU-{i:05d}"
                is_trap = (dept == "Women's" and cat == "Outerwear")
                season = "FW25" if (cat == "Outerwear" or rng.random() < 0.3) else None
                seasonal = season is not None
                cost = round(float(rng.uniform(18, 120)), 2)
                price = round(cost * float(rng.uniform(2.0, 2.8)), 2)
                sup = trap_supplier_id if is_trap else rng.choice(supplier_ids)
                rows.append((pid, pid.replace("SKU-", "LV"), f"{cat} Style {i}",
                             dept, cat, f"{cat}-{rng.integers(1,6)}",
                             ("FW25" if is_trap else season), bool(seasonal or is_trap),
                             cost, price, sup))
    df = pd.DataFrame(rows, columns=[
        "product_id", "sku", "product_name", "department", "category",
        "subcategory", "season", "is_seasonal", "unit_cost", "unit_price",
        "primary_supplier_id"])
    return df


REGIONS = ["West", "Midwest", "South", "Northeast"]
STATE_BY_REGION = {"West": ["CA", "WA", "AZ"], "Midwest": ["IL", "OH", "MI"],
                   "South": ["TX", "FL", "GA"], "Northeast": ["NY", "MA", "PA"]}


def build_dim_store():
    rows = []
    for n in range(1, 41):
        region = REGIONS[(n - 1) % 4]
        state = rng.choice(STATE_BY_REGION[region])
        rows.append((f"ST-{n:03d}", f"Lakeview Store {n}", "STORE", region,
                     state, f"City{n}",
                     START - dt.timedelta(days=int(rng.integers(400, 3000)))))
    for n in range(1, 4):
        region = REGIONS[n % 4]
        rows.append((f"DC-{n:02d}", f"Lakeview DC {n}", "DC", region,
                     rng.choice(STATE_BY_REGION[region]), f"DCCity{n}",
                     START - dt.timedelta(days=2500)))
    rows.append(("ECOM", "Lakeview.com", "ECOM", "National", None, None,
                 START - dt.timedelta(days=2500)))
    return pd.DataFrame(rows, columns=[
        "store_id", "store_name", "location_type", "region", "state",
        "city", "open_date"])


def build_dim_customer():
    rows = []
    # 3 disputing wholesale accounts (planted)
    disputers = [("CUST-01", "Meridian Department Stores", "Department store"),
                 ("CUST-02", "Coastline Boutiques", "Boutique"),
                 ("CUST-03", "Summit Retail Group", "Department store")]
    for cid, name, seg in disputers:
        rows.append((cid, name, "WHOLESALE", seg, rng.choice(REGIONS), 60,
                     START - dt.timedelta(days=int(rng.integers(500, 2500)))))
    # more wholesale
    for n in range(4, 27):
        rows.append((f"CUST-{n:02d}", f"Wholesale Account {n}", "WHOLESALE",
                     rng.choice(["Department store", "Boutique", "Specialty"]),
                     rng.choice(REGIONS), int(rng.choice([30, 45, 60])),
                     START - dt.timedelta(days=int(rng.integers(500, 2500)))))
    # consumer settlement partners (planted slippage)
    rows.append(("CUST-27", "Zephyr Marketplace", "MARKETPLACE", "Marketplace",
                 "National", 14, START - dt.timedelta(days=1500)))
    rows.append(("CUST-28", "Nexus Marketplace", "MARKETPLACE", "Marketplace",
                 "National", 14, START - dt.timedelta(days=1500)))
    rows.append(("CUST-29", "PayLater Co", "BNPL", "BNPL partner",
                 "National", 30, START - dt.timedelta(days=1200)))
    rows.append(("CUST-30", "SplitPay", "BNPL", "BNPL partner",
                 "National", 30, START - dt.timedelta(days=1200)))
    return pd.DataFrame(rows, columns=[
        "customer_id", "customer_name", "channel", "segment", "region",
        "credit_terms_days", "onboarded_date"])


def build_dim_supplier():
    rows = []
    # 2 planted AP suppliers first
    rows.append(("SUP-01", "Highland Mills", "Outerwear", "Vietnam", 60, 2.00, "APAC"))
    rows.append(("SUP-02", "Pacific Textile Co", "Fabric", "China", 60, 1.50, "APAC"))
    cats = ["Tops", "Denim", "Footwear", "Accessories", "Activewear", "Dresses", "Fabric"]
    countries = ["China", "Vietnam", "India", "Bangladesh", "Portugal", "USA"]
    for n in range(3, 26):
        rows.append((f"SUP-{n:02d}", f"Supplier {n}", rng.choice(cats),
                     rng.choice(countries), int(rng.choice([30, 45, 60])),
                     round(float(rng.uniform(0, 2)), 2), rng.choice(["APAC", "EMEA", "Americas"])))
    return pd.DataFrame(rows, columns=[
        "supplier_id", "supplier_name", "category", "country",
        "default_terms_days", "early_pay_discount_pct", "region"])


# =====================================================================
# FACTS
# =====================================================================
def build_fact_sales(dim_product, dim_store):
    """~130K sparse daily sales lines. Trap SKUs sell weakly in recent months."""
    sell_locs = dim_store[dim_store.location_type.isin(["STORE", "ECOM"])].store_id.values
    all_days = pd.date_range(START, ALERT, freq="D")
    trap_ids = set(dim_product[(dim_product.department == "Women's") &
                               (dim_product.category == "Outerwear")].product_id)
    cost_map = dict(zip(dim_product.product_id, dim_product.unit_cost))
    price_map = dict(zip(dim_product.product_id, dim_product.unit_price))
    prod_ids = dim_product.product_id.values

    N = 130_000
    day_idx = rng.integers(0, len(all_days), size=N)
    days = all_days[day_idx].date
    stores = rng.choice(sell_locs, size=N)
    prods = rng.choice(prod_ids, size=N)

    # Suppress trap-SKU sales in the recent window (Aug-Nov 2026): drop ~75% of them
    recent_cut = dt.date(2026, 8, 1)
    is_trap = np.array([p in trap_ids for p in prods])
    is_recent = days >= recent_cut
    drop = is_trap & is_recent & (rng.random(N) < 0.75)
    keep = ~drop
    days, stores, prods = days[keep], stores[keep], prods[keep]
    n = len(days)

    units = rng.integers(1, 5, size=n)
    unit_price = np.array([price_map[p] for p in prods])
    unit_cost = np.array([cost_map[p] for p in prods])
    gross = np.round(units * unit_price, 2)
    disc_rate = rng.choice([0.0, 0.1, 0.2, 0.3], size=n, p=[0.55, 0.2, 0.15, 0.1])
    disc = np.round(gross * disc_rate, 2)
    net = np.round(gross - disc, 2)
    cogs = np.round(units * unit_cost, 2)
    return pd.DataFrame({
        "sale_id": [f"S{i:08d}" for i in range(n)],
        "date_key": days, "product_id": prods, "store_id": stores,
        "units_sold": units, "gross_sales_amt": gross, "discount_amt": disc,
        "net_sales_amt": net, "cogs_amt": cogs})


def build_fact_inventory(dim_product, dim_store):
    """Weekly snapshots (recent 40 weeks). Trap: aged Women's Outerwear = $3.5M @ cost, 90+ bucket, latest week."""
    locs = dim_store[dim_store.location_type.isin(["STORE", "DC"])].store_id.values
    cost_map = dict(zip(dim_product.product_id, dim_product.unit_cost))
    trap = dim_product[(dim_product.department == "Women's") &
                       (dim_product.category == "Outerwear")]
    nontrap = dim_product[~dim_product.product_id.isin(trap.product_id)]

    weeks = pd.date_range(end=pd.Timestamp("2026-11-01"), periods=40, freq="W-SAT").date
    latest_week = weeks[-1]
    rows = []

    # --- general (non-trap) snapshots: sparse, healthy aging ---
    n_gen = min(400, len(nontrap))
    combos = [(p, l) for p in rng.choice(nontrap.product_id.values, n_gen, replace=False)
              for l in rng.choice(locs, 3, replace=False)]
    for (p, l) in combos:
        base_units = int(rng.integers(20, 300))
        for w in weeks[-12:]:  # keep general volume modest
            onh = max(0, base_units + int(rng.integers(-30, 30)))
            days_oh = int(rng.integers(5, 85))
            bucket = ("0-30" if days_oh <= 30 else "31-60" if days_oh <= 60 else "61-90")
            cost = round(onh * cost_map[p], 2)
            rows.append((f"INV-{len(rows):08d}", w, p, l, onh, cost,
                         w - dt.timedelta(days=days_oh), days_oh, bucket, False,
                         round(cost * 0.05, 2)))

    # --- planted trap rows at the latest snapshot: aged 90+ ---
    trap_rows_start = len(rows)
    trap_costs = []
    trap_meta = []
    for p in trap.product_id.values:
        for l in rng.choice(locs, size=int(rng.integers(3, 7)), replace=False):
            onh = int(rng.integers(80, 400))
            days_oh = int(rng.integers(95, 160))
            trap_costs.append(onh * cost_map[p])
            trap_meta.append((p, l, onh, days_oh))
    scaled = scale_to(trap_costs, T_INVENTORY)  # exact $3.5M at cost
    for (p, l, onh, days_oh), cost in zip(trap_meta, scaled):
        rows.append((f"INV-{len(rows):08d}", latest_week, p, l, onh, float(cost),
                     latest_week - dt.timedelta(days=days_oh), days_oh, "90+", True,
                     round(float(cost) * 0.45, 2)))  # heavy markdown liability

    return pd.DataFrame(rows, columns=[
        "snapshot_id", "week_ending_date", "product_id", "store_id",
        "on_hand_units", "on_hand_cost", "last_receipt_date", "days_on_hand",
        "aging_bucket", "is_aged", "markdown_liability_amt"])


def build_fact_po(dim_product, dim_supplier):
    """PO lines. Planted: oversized FW25 outerwear buy w/ OPEN inbound lines; expedite+tariff lines for AP."""
    trap = dim_product[(dim_product.department == "Women's") &
                       (dim_product.category == "Outerwear")]
    cost_map = dict(zip(dim_product.product_id, dim_product.unit_cost))
    sup_by_prod = dict(zip(dim_product.product_id, dim_product.primary_supplier_id))
    rows = []

    # historical received POs (normal)
    for k in range(2500):
        p = rng.choice(dim_product.product_id.values)
        od = START + dt.timedelta(days=int(rng.integers(0, 700)))
        units = int(rng.integers(50, 500))
        rows.append((f"PO-{k:05d}", f"{k:05d}-1", sup_by_prod[p], p, od,
                     od + dt.timedelta(days=45), "RECEIVED", units,
                     round(units * cost_map[p], 2), False, "OCEAN", 0.0))

    # planted: oversized outerwear buy, OPEN lines still inbound (receipt after ALERT)
    for j, p in enumerate(trap.product_id.values):
        units = int(rng.integers(300, 900))
        od = dt.date(2026, 6, int(rng.integers(1, 28)))
        exp = ALERT + dt.timedelta(days=int(rng.integers(5, 40)))  # future = still inbound
        rows.append((f"PO-OW{j:04d}", f"OW{j:04d}-1", "SUP-01", p, od, exp,
                     "OPEN", units, round(units * cost_map[p], 2),
                     bool(rng.random() < 0.4), rng.choice(["OCEAN", "AIR"]), 0.0))

    return pd.DataFrame(rows, columns=[
        "po_id", "po_line_id", "supplier_id", "product_id", "order_date",
        "expected_receipt_date", "status", "ordered_units", "committed_cost",
        "is_expedited", "freight_mode", "tariff_surcharge_amt"])


def build_fact_ap():
    """Supplier payments. Planted: cash_impact_vs_plan sums to exactly $2.0M on accelerated payments."""
    rows = []
    # historical normal payments
    for k in range(2000):
        sup = f"SUP-{int(rng.integers(1,26)):02d}"
        pay = START + dt.timedelta(days=int(rng.integers(30, 720)))
        amt = round(float(rng.uniform(5_000, 80_000)), 2)
        rows.append((f"AP-{k:06d}", sup, f"PO-{int(rng.integers(0,2500)):05d}", amt,
                     pay, pay, int(rng.choice([30, 45, 60])), 0, 0.0, 0.0, 0.0,
                     0.0, 0.0, 0.0, "NORMAL"))

    # planted accelerated payments to SUP-01 / SUP-02 in the quarter (impact scaled to $2.0M)
    n_acc = 40
    raw = rng.uniform(20_000, 120_000, size=n_acc)
    impacts = scale_to(raw, T_AP)
    for i, imp in enumerate(impacts):
        sup = "SUP-01" if i % 2 == 0 else "SUP-02"
        sched = dt.date(2026, 12, int(rng.integers(1, 28)))  # planned in Dec
        actual = dt.date(2026, 10, int(rng.integers(15, 31)))  # paid early in Oct
        days_early = (sched - actual).days
        # decompose the impact across the four AP mechanisms
        early = round(float(imp) * 0.5, 2)
        lost = round(float(imp) * 0.2, 2)
        surch = round(float(imp) * 0.2, 2)
        exped = round(float(imp) - early - lost - surch, 2)
        invoice = round(float(rng.uniform(80_000, 200_000)), 2)
        rows.append((f"AP-ACC{i:04d}", sup, f"PO-OW{i:04d}", invoice, sched, actual,
                     30, days_early, round(invoice * 0.02, 2), 0.0, lost, surch,
                     exped, round(float(imp), 2), "ACCELERATED"))
    return pd.DataFrame(rows, columns=[
        "payment_id", "supplier_id", "po_id", "invoice_amt", "scheduled_pay_date",
        "actual_pay_date", "terms_days", "days_early", "early_pay_discount_offered_amt",
        "early_pay_discount_captured_amt", "early_pay_discount_lost_amt",
        "surcharge_amt", "expedite_freight_amt", "cash_impact_vs_plan_amt",
        "payment_category"])


def build_fact_ar():
    """AR invoices. Planted: $1.7M wholesale disputes (3 custs) + $0.8M settlement slip = $2.5M."""
    rows = []
    # historical wholesale invoices (mostly paid)
    for k in range(2200):
        cid = f"CUST-{int(rng.integers(1,27)):02d}"
        inv = START + dt.timedelta(days=int(rng.integers(0, 700)))
        due = inv + dt.timedelta(days=60)
        amt = round(float(rng.uniform(8_000, 90_000)), 2)
        paid = due + dt.timedelta(days=int(rng.integers(-5, 12)))
        status = "PAID" if paid <= ALERT else "OPEN"
        pd_ = paid if status == "PAID" else None
        rows.append((f"INV-W{k:06d}", cid, "WHOLESALE", inv, due, amt, pd_, status,
                     False, None, (pd_ or due), 0.0, False))

    # planted wholesale disputes: 3 customers, impact scaled to $1.7M; 2 flip in last 2wk
    reasons = ["short-ship", "quality defect", "pricing discrepancy"]
    custs = ["CUST-01", "CUST-02", "CUST-03"]
    n_disp = 18
    raw = rng.uniform(40_000, 160_000, size=n_disp)
    impacts = scale_to(raw, T_WHOLESALE)
    for i, imp in enumerate(impacts):
        cid = custs[i % 3]
        inv = dt.date(2026, 9, int(rng.integers(1, 28)))
        due = inv + dt.timedelta(days=60)
        flipped = i < 2  # two big ones flip in the last 2 weeks
        # expected cash pushed past quarter-end
        exp_cash = QEND + dt.timedelta(days=int(rng.integers(15, 60)))
        rows.append((f"INV-D{i:04d}", cid, "WHOLESALE", inv, due,
                     round(float(imp), 2), None, "DISPUTED", True,
                     reasons[i % 3], exp_cash, round(float(imp), 2), bool(flipped)))

    # planted consumer settlement slippage: marketplace/BNPL, impact scaled to $0.8M
    n_slip = 10
    raw = rng.uniform(30_000, 120_000, size=n_slip)
    impacts = scale_to(raw, T_SETTLEMENT)
    part = ["CUST-27", "CUST-29", "CUST-28", "CUST-30"]
    for i, imp in enumerate(impacts):
        cid = part[i % 4]
        ch = "MARKETPLACE" if cid in ("CUST-27", "CUST-28") else "BNPL"
        inv = dt.date(2026, 10, int(rng.integers(1, 28)))
        due = inv + dt.timedelta(days=14 if ch == "MARKETPLACE" else 30)
        exp_cash = QEND + dt.timedelta(days=int(rng.integers(5, 30)))  # settles after quarter-end
        rows.append((f"INV-S{i:04d}", cid, ch, inv, due, round(float(imp), 2),
                     None, "SLIPPED", False, None, exp_cash, round(float(imp), 2), False))

    return pd.DataFrame(rows, columns=[
        "invoice_id", "customer_id", "channel", "invoice_date", "due_date",
        "amount", "paid_date", "status", "dispute_flag", "dispute_reason",
        "expected_cash_date", "cash_impact_vs_plan_amt", "flipped_last_2wk"])


CASH_CATS = ["SALES_RECEIPTS", "WHOLESALE_COLLECTIONS", "SUPPLIER_PAYMENTS",
             "PAYROLL", "RENT", "OTHER_OPEX", "TAX", "CAPEX"]


def build_fact_cash_ledger():
    """Daily actual cash in/out by category; running balance ends ~$28M at ALERT with a recent dip."""
    days = pd.date_range(START, ALERT, freq="D").date
    rows = []
    balance = 15_000_000.0
    for d in days:
        seasonal = 1.0 + 0.15 * np.sin((d.timetuple().tm_yday / 365) * 2 * np.pi)
        recent_dip = 0.85 if d >= dt.date(2026, 10, 22) else 1.0  # last 2 weeks softer
        day_net = 0.0
        for cat in CASH_CATS:
            if cat in ("SALES_RECEIPTS", "WHOLESALE_COLLECTIONS"):
                cin = round(float(rng.uniform(150_000, 350_000)) * seasonal * recent_dip, 2)
                cout = 0.0
            else:
                cin = 0.0
                cout = round(float(rng.uniform(80_000, 220_000)) * seasonal, 2)
            net = round(cin - cout, 2)
            day_net += net
            rows.append([d, cat, cin, cout, net, None])
        balance += day_net
        # write running balance onto the day's rows (last cat carries EoD balance)
        rows[-1][5] = round(balance, 2)
    df = pd.DataFrame(rows, columns=["ledger_date", "cash_category", "cash_in_amt",
                                     "cash_out_amt", "net_cash_amt", "running_cash_balance"])
    # forward-fill running balance to all category rows of the day
    df["running_cash_balance"] = df["running_cash_balance"].bfill().ffill()
    return df


def build_fact_cash_forecast():
    """Authored forecast: quarter-end plan $30M, current projected $22M, prior run $25M."""
    rows = []
    drivers_end = {  # current run 2026-11-05
        "BASELINE": (PLAN_QEND_CASH, PLAN_QEND_CASH),
        "INVENTORY_TRAP": (0.0, -T_INVENTORY),
        "COLLECTIONS_SLOWDOWN": (0.0, -T_COLLECTIONS),
        "AP_ACCELERATION": (0.0, -T_AP),
    }
    drivers_prior = {  # prior run 2026-10-22 (smaller problem -> $25M)
        "BASELINE": (PLAN_QEND_CASH, PLAN_QEND_CASH),
        "INVENTORY_TRAP": (0.0, -3_000_000.0),
        "COLLECTIONS_SLOWDOWN": (0.0, -1_000_000.0),
        "AP_ACCELERATION": (0.0, -1_000_000.0),
    }

    def emit(run_date, ddict):
        weeks = pd.date_range(pd.Timestamp(run_date) + pd.Timedelta(days=2),
                              pd.Timestamp("2027-01-02"), freq="W-SAT").date
        n = len(weeks)
        for wi, w in enumerate(weeks):
            frac = (wi + 1) / n
            for drv, (pl, pr) in ddict.items():
                planned = round(pl * (0.94 + 0.06 * frac) if drv == "BASELINE" else pl, 2)
                projected = round(pr * (0.94 + 0.06 * frac) if drv == "BASELINE" else pr * frac, 2)
                rows.append((run_date, w, drv, planned, projected, round(projected - planned, 2)))
        # explicit quarter-end checkpoint rows (canonical targets)
        for drv, (pl, pr) in ddict.items():
            rows.append((run_date, QEND, drv, round(pl, 2), round(pr, 2), round(pr - pl, 2)))

    emit(PRIOR_RUN, drivers_prior)
    emit(ALERT, drivers_end)
    return pd.DataFrame(rows, columns=[
        "forecast_run_date", "forecast_week_ending", "driver",
        "planned_cash_amt", "projected_cash_amt", "variance_amt"])


# =====================================================================
# WRITE + COMMENT + RECONCILE
# =====================================================================
TABLE_COMMENTS = {
    "dim_date": "Date dimension spanning history + forecast horizon (2024-11-01..2026-12-31).",
    "dim_product": "Product/SKU dimension. Planted trap: Women's Outerwear (FW25) over-bought with weak sell-through.",
    "dim_store": "Store/DC/channel dimension: 40 stores + 3 DCs + 1 ecom.",
    "dim_customer": "AR customers: wholesale accounts + consumer settlement partners. Planted: 3 wholesale disputes (CUST-01/02/03) + slipping settlement partners.",
    "dim_supplier": "Supplier dimension. Planted: SUP-01 Highland Mills / SUP-02 Pacific Textile Co drive AP acceleration.",
    "fact_sales": "Daily sell-through by SKU x location (sparse). Planted: Women's Outerwear sell-through collapses in recent months.",
    "fact_inventory_snapshot": "Weekly inventory snapshot. Planted: ~$3.5M Women's Outerwear at cost in the 90+ aged bucket at the latest snapshot.",
    "fact_purchase_orders": "Purchase orders. Planted: oversized FW25 outerwear buy with OPEN lines still inbound; expedite/air-freight + tariff lines.",
    "fact_ap_payments": "Supplier payments (AP). Planted: ~$2.0M cash_impact_vs_plan from accelerated payments, lost discounts, tariff surcharge, expedited freight.",
    "fact_ar_invoices": "AR invoices. Planted: $1.7M wholesale disputes + $0.8M consumer settlement slippage = $2.5M; 2 flips in last 2 weeks.",
    "fact_cash_ledger": "Daily actual cash in/out by category feeding ai_forecast(); recent weeks trend down.",
    "fact_cash_forecast": "Rolling cash forecast by driver. Quarter-end 2026-12-31: plan $30M, projected $22M, variance -$8M (-3.5/-2.5/-2.0). Prior run 2026-10-22 projected $25M (~$3M 2-week deterioration).",
}

COL_COMMENTS = {
    "fact_inventory_snapshot": {"aging_bucket": "0-30 / 31-60 / 61-90 / 90+",
                                "is_aged": "True when 90+ (cash trapped)",
                                "on_hand_cost": "On-hand inventory at cost (cash trapped)",
                                "markdown_liability_amt": "Estimated markdown exposure"},
    "fact_ar_invoices": {"cash_impact_vs_plan_amt": "Expected cash NOT arriving by 2026-12-31 vs plan",
                         "dispute_reason": "short-ship / quality defect / pricing discrepancy",
                         "flipped_last_2wk": "Flipped to disputed in last 2 weeks (velocity trigger)",
                         "status": "PAID / OPEN / DISPUTED / SLIPPED"},
    "fact_ap_payments": {"cash_impact_vs_plan_amt": "Extra near-term cash out vs plan (early + surcharge + expedite + lost discount)",
                         "days_early": "scheduled - actual (positive = paid early)",
                         "payment_category": "NORMAL / ACCELERATED"},
    "fact_purchase_orders": {"status": "OPEN / RECEIVED / CANCELLED",
                             "expected_receipt_date": "Future date = still inbound"},
    "fact_cash_forecast": {"driver": "BASELINE / INVENTORY_TRAP / COLLECTIONS_SLOWDOWN / AP_ACCELERATION",
                           "variance_amt": "projected - planned (negative = shortfall)"},
}


def main():
    from databricks.connect import DatabricksSession
    spark = DatabricksSession.builder.profile("fe-vm-genie-cfo").serverless(True).getOrCreate()
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CAT}.{SCH}")

    sup = build_dim_supplier()
    prod = build_dim_product(sup.supplier_id.values, "SUP-01")
    store = build_dim_store()
    cust = build_dim_customer()

    tables = {
        "dim_date": build_dim_date(),
        "dim_product": prod,
        "dim_store": store,
        "dim_customer": cust,
        "dim_supplier": sup,
        "fact_sales": build_fact_sales(prod, store),
        "fact_inventory_snapshot": build_fact_inventory(prod, store),
        "fact_purchase_orders": build_fact_po(prod, sup),
        "fact_ap_payments": build_fact_ap(),
        "fact_ar_invoices": build_fact_ar(),
        "fact_cash_ledger": build_fact_cash_ledger(),
        "fact_cash_forecast": build_fact_cash_forecast(),
    }

    for name, df in tables.items():
        sdf = spark.createDataFrame(df)
        sdf.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(f"{CAT}.{SCH}.{name}")
        tc = TABLE_COMMENTS[name].replace("'", "''")
        spark.sql(f"COMMENT ON TABLE {CAT}.{SCH}.{name} IS '{tc}'")
        for col, cm in COL_COMMENTS.get(name, {}).items():
            spark.sql(f"ALTER TABLE {CAT}.{SCH}.{name} ALTER COLUMN {col} COMMENT '{cm.replace(chr(39), chr(39)*2)}'")
        print(f"  wrote {name:26s} {len(df):>8,} rows")

    print("\n=== ROW COUNTS ===")
    for name in tables:
        print(f"  {name:26s} {spark.table(f'{CAT}.{SCH}.{name}').count():>8,}")

    print("\n=== RECONCILIATION ===")
    q = lambda s: spark.sql(s).collect()[0][0]
    inv = q(f"SELECT sum(on_hand_cost) FROM {CAT}.{SCH}.fact_inventory_snapshot WHERE is_aged")
    whole = q(f"SELECT sum(cash_impact_vs_plan_amt) FROM {CAT}.{SCH}.fact_ar_invoices WHERE status='DISPUTED'")
    settle = q(f"SELECT sum(cash_impact_vs_plan_amt) FROM {CAT}.{SCH}.fact_ar_invoices WHERE status='SLIPPED'")
    ap = q(f"SELECT sum(cash_impact_vs_plan_amt) FROM {CAT}.{SCH}.fact_ap_payments WHERE payment_category='ACCELERATED'")
    proj = q(f"SELECT sum(projected_cash_amt) FROM {CAT}.{SCH}.fact_cash_forecast WHERE forecast_run_date='2026-11-05' AND forecast_week_ending='2026-12-31'")
    plan = q(f"SELECT sum(planned_cash_amt) FROM {CAT}.{SCH}.fact_cash_forecast WHERE forecast_run_date='2026-11-05' AND forecast_week_ending='2026-12-31'")
    prior = q(f"SELECT sum(projected_cash_amt) FROM {CAT}.{SCH}.fact_cash_forecast WHERE forecast_run_date='2026-10-22' AND forecast_week_ending='2026-12-31'")
    print(f"  Inventory aged (90+) cost : ${inv:,.2f}  (target $3,500,000)")
    print(f"  Wholesale disputes        : ${whole:,.2f}  (target $1,700,000)")
    print(f"  Settlement slippage       : ${settle:,.2f}  (target $800,000)")
    print(f"  Collections total         : ${whole+settle:,.2f}  (target $2,500,000)")
    print(f"  AP acceleration           : ${ap:,.2f}  (target $2,000,000)")
    print(f"  SUM of drivers            : ${inv+whole+settle+ap:,.2f}  (target $8,000,000)")
    print(f"  Plan quarter-end cash     : ${plan:,.2f}  (target $30,000,000)")
    print(f"  Projected quarter-end cash: ${proj:,.2f}  (target $22,000,000)")
    print(f"  Prior-run projected       : ${prior:,.2f}  (target $25,000,000)")
    print(f"  2-week deterioration      : ${prior-proj:,.2f}  (target $3,000,000)")
    spark.stop()


if __name__ == "__main__":
    main()
