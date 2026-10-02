# Databricks notebook source
# MAGIC %md
# MAGIC # 01 · Generate Lakeview Retail data (Step 1)
# MAGIC
# MAGIC **Office of the CFO — Retail Cash-Flow Shortfall** demo. Deterministically
# MAGIC (seed=42) builds 12 tables into `serverless_stable_genie_cfo_catalog.lakeview_retail`.
# MAGIC
# MAGIC **Planted problem (reconciles exactly):** quarter-end cash plan **$30M** →
# MAGIC projected **$22M** = **$8M shortfall**, split **inventory-markdown $3.5M +
# MAGIC collections/settlement $2.5M + supplier/AP $2.0M**, with ~$3M of the drop in
# MAGIC the last 2 weeks. Operational ratios (DSO/DPO/DIO/CCC, sell-through) are
# MAGIC velocity-controlled so only Women's Outerwear reads as the outlier.
# MAGIC
# MAGIC Run this notebook top-to-bottom on serverless. Then run `02_metric_views`.

# COMMAND ----------

import datetime as dt
from collections import defaultdict
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
INV_LATEST_WEEK = dt.date(2026, 10, 31)  # latest inventory snapshot (Saturday)

# Exact reconciliation targets (USD)
T_INVENTORY = 3_500_000.0
T_WHOLESALE = 1_700_000.0
T_SETTLEMENT = 800_000.0
T_COLLECTIONS = T_WHOLESALE + T_SETTLEMENT   # 2.5M
T_AP = 2_000_000.0
PLAN_QEND_CASH = 30_000_000.0
PROJ_QEND_CASH = 22_000_000.0                # 30 - 8
PRIOR_PROJ_QEND = 25_000_000.0               # -> 3M deterioration

# --- Operational-ratio tuning knobs -----------------------------------
# Baseline per-SKU weekly unit velocity (company-wide, across all stores).
# Higher velocity raises trailing COGS, which shrinks the fixed $3.5M trap's
# weight in DIO/CCC and lifts the trap's sell-through toward the ~35% intent.
BASE_V = 16.0
AVG_UNITS_PER_LINE = 4.0
MAX_UNITS_PER_LINE = 7
# Latest-week baseline on-hand units = BASELINE_R * trailing-8wk units.
#   sell_through = 1/(1+R); weeks_of_supply = 8*R.  R=0.78 -> ST 56% / WOS 6.2.
BASELINE_R = 0.78
# Trap (Women's Outerwear) recent-window sales softening.
TRAP_RECENT_FACTOR = 0.85
TRAP_COLLAPSE_START = pd.Timestamp("2026-07-01")
# Target company DSO (days); drives the size of the normal open-AR block.
DSO_TARGET = 45.0


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

TRAP_KEY = ("Women's", "Outerwear")


def build_dim_product(supplier_ids, trap_supplier_id):
    rows = []
    i = 0
    for dept, cats in DEPT_CATS.items():
        for cat, n in cats:
            for _ in range(n):
                i += 1
                pid = f"SKU-{i:05d}"
                is_trap = (dept, cat) == TRAP_KEY
                season = "FW25" if (cat == "Outerwear" or rng.random() < 0.3) else None
                seasonal = season is not None
                if is_trap:
                    # premium winter outerwear — higher landed cost so the
                    # $3.5M trap is a believable unit count (not absurd volume)
                    cost = round(float(rng.uniform(200, 320)), 2)
                    price = round(cost * float(rng.uniform(2.0, 2.6)), 2)
                else:
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
    """Velocity-controlled daily sales. Each category sells at ~BASE_V units/
    SKU/week (seasonal); the trap category softens in the recent window so its
    sell-through reads as the clear outlier against healthy baseline categories."""
    sell_locs = dim_store[dim_store.location_type.isin(["STORE", "ECOM"])].store_id.values
    cost_map = dict(zip(dim_product.product_id, dim_product.unit_cost))
    price_map = dict(zip(dim_product.product_id, dim_product.unit_price))
    cat_pids = defaultdict(list)
    for pid, dept, cat in zip(dim_product.product_id, dim_product.department, dim_product.category):
        cat_pids[(dept, cat)].append(pid)

    weeks = pd.date_range(START, ALERT, freq="W-SAT")
    days_all, prods_all, stores_all, units_all = [], [], [], []
    for (dept, cat), pids in cat_pids.items():
        pids_arr = np.array(pids)
        base_weekly = BASE_V * len(pids_arr)
        for w in weeks:
            seas = 1.0 + 0.15 * np.sin(2 * np.pi * w.dayofyear / 365.0)
            wk_units = base_weekly * seas
            if (dept, cat) == TRAP_KEY and w >= TRAP_COLLAPSE_START:
                wk_units *= TRAP_RECENT_FACTOR
            n_lines = int(round(wk_units / AVG_UNITS_PER_LINE))
            if n_lines <= 0:
                continue
            day_off = rng.integers(0, 7, n_lines)
            dd = np.array([(w - pd.Timedelta(days=int(o))).date() for o in day_off])
            prods_all.append(rng.choice(pids_arr, n_lines))
            stores_all.append(rng.choice(sell_locs, n_lines))
            units_all.append(rng.integers(1, MAX_UNITS_PER_LINE + 1, n_lines))
            days_all.append(dd)

    days = np.concatenate(days_all)
    prods = np.concatenate(prods_all)
    stores = np.concatenate(stores_all)
    units = np.concatenate(units_all)
    keep = (days >= START) & (days <= ALERT)
    days, prods, stores, units = days[keep], prods[keep], stores[keep], units[keep]
    n = len(days)

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


def build_fact_inventory(dim_product, dim_store, fact_sales):
    """Weekly snapshots (recent 12 weeks). Baseline on-hand is pegged to each
    category's trailing-8wk sales (BASELINE_R) for healthy sell-through / WOS.
    Trap: Women's Outerwear only at the latest week, aged 90+, $3.5M @ cost."""
    locs = dim_store[dim_store.location_type.isin(["STORE", "DC"])].store_id.values
    cost_map = dict(zip(dim_product.product_id, dim_product.unit_cost))
    cat_pids = defaultdict(list)
    for pid, dept, cat in zip(dim_product.product_id, dim_product.department, dim_product.category):
        cat_pids[(dept, cat)].append(pid)

    weeks = [w.date() for w in pd.date_range(end=pd.Timestamp(INV_LATEST_WEEK), periods=12, freq="W-SAT")]
    latest = weeks[-1]

    # trailing-8wk units by category as of the latest snapshot week
    fs = fact_sales.merge(dim_product[["product_id", "department", "category"]], on="product_id")
    lo = latest - dt.timedelta(days=56)
    rec = fs[(fs.date_key > lo) & (fs.date_key <= latest)]
    u8 = rec.groupby(["department", "category"]).units_sold.sum().to_dict()

    rows = []

    def bucket_of(days_oh):
        return ("90+" if days_oh > 90 else "61-90" if days_oh > 60
                else "31-60" if days_oh > 30 else "0-30")

    # baseline categories (everything except the trap)
    for (dept, cat), pids in cat_pids.items():
        if (dept, cat) == TRAP_KEY:
            continue
        pids_arr = np.array(pids)
        u = u8.get((dept, cat), 0)
        target_latest = max(len(pids_arr), int(round(BASELINE_R * u)))
        for wd in weeks:
            tot = max(len(pids_arr), int(round(target_latest * (0.9 + 0.2 * rng.random()))))
            per = np.full(len(pids_arr), tot // len(pids_arr))
            per[:tot % len(pids_arr)] += 1
            for pid, up in zip(pids_arr, per):
                if up <= 0:
                    continue
                loc = rng.choice(locs)
                days_oh = int(rng.integers(5, 86))  # healthy: never 90+
                cost = up * cost_map[pid]
                rows.append((f"INV-{len(rows):08d}", wd, pid, str(loc), int(up),
                             round(float(cost), 2), wd - dt.timedelta(days=days_oh),
                             days_oh, bucket_of(days_oh), False, round(float(cost) * 0.05, 2)))

    # planted trap: latest week only, aged 90+, cost scaled to exactly $3.5M
    trap_meta, raw_costs = [], []
    for pid in cat_pids[TRAP_KEY]:
        for loc in rng.choice(locs, size=int(rng.integers(3, 7)), replace=False):
            onh = int(rng.integers(25, 76))
            days_oh = int(rng.integers(95, 160))
            trap_meta.append((pid, str(loc), onh, days_oh))
            raw_costs.append(onh * cost_map[pid])
    scaled = scale_to(raw_costs, T_INVENTORY)
    for (pid, loc, onh, days_oh), cost in zip(trap_meta, scaled):
        rows.append((f"INV-{len(rows):08d}", latest, pid, loc, onh, float(cost),
                     latest - dt.timedelta(days=days_oh), days_oh, "90+", True,
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
    """Supplier payments. Planted: cash_impact_vs_plan sums to exactly $2.0M on
    accelerated payments. Normal payments carry realistic net-45/60 terms so the
    company DPO (weighted, trailing-90d) reads believably."""
    rows = []
    span = (ALERT - START).days
    # historical normal payments spread across the whole window
    for k in range(2200):
        sup = f"SUP-{int(rng.integers(1,26)):02d}"
        pay = START + dt.timedelta(days=int(rng.integers(0, span)))
        amt = round(float(rng.uniform(5_000, 80_000)), 2)
        terms = int(rng.choice([45, 60, 60]))
        rows.append((f"AP-{k:06d}", sup, f"PO-{int(rng.integers(0,2500)):05d}", amt,
                     pay, pay, terms, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, "NORMAL"))

    # planted accelerated payments to SUP-01 / SUP-02 (impact scaled to $2.0M);
    # paid ~30 days early on net-60 terms so they don't crater company DPO
    n_acc = 40
    raw = rng.uniform(20_000, 120_000, size=n_acc)
    impacts = scale_to(raw, T_AP)
    for i, imp in enumerate(impacts):
        sup = "SUP-01" if i % 2 == 0 else "SUP-02"
        actual = dt.date(2026, 10, int(rng.integers(15, 31)))  # paid early, in-window
        sched = actual + dt.timedelta(days=30)                 # planned ~30 days later
        days_early = (sched - actual).days                     # = 30
        early = round(float(imp) * 0.5, 2)
        lost = round(float(imp) * 0.2, 2)
        surch = round(float(imp) * 0.2, 2)
        exped = round(float(imp) - early - lost - surch, 2)
        invoice = round(float(rng.uniform(80_000, 200_000)), 2)
        rows.append((f"AP-ACC{i:04d}", sup, f"PO-OW{i:04d}", invoice, sched, actual,
                     60, days_early, round(invoice * 0.02, 2), 0.0, lost, surch,
                     exped, round(float(imp), 2), "ACCELERATED"))
    return pd.DataFrame(rows, columns=[
        "payment_id", "supplier_id", "po_id", "invoice_amt", "scheduled_pay_date",
        "actual_pay_date", "terms_days", "days_early", "early_pay_discount_offered_amt",
        "early_pay_discount_captured_amt", "early_pay_discount_lost_amt",
        "surcharge_amt", "expedite_freight_amt", "cash_impact_vs_plan_amt",
        "payment_category"])


def build_fact_ar(fact_sales):
    """AR invoices. Planted: $1.7M wholesale disputes (3 custs) + $0.8M settlement
    slip = $2.5M. A normal open-AR block is sized off trailing-90d net sales so the
    company DSO lands near DSO_TARGET."""
    rows = []
    # historical wholesale invoices, all settled before the alert date
    for k in range(1800):
        cid = f"CUST-{int(rng.integers(1,27)):02d}"
        inv = START + dt.timedelta(days=int(rng.integers(0, 610)))
        due = inv + dt.timedelta(days=60)
        paid = due + dt.timedelta(days=int(rng.integers(-5, 10)))
        if paid > ALERT:  # force settled before alert so it's not counted outstanding
            paid = ALERT - dt.timedelta(days=int(rng.integers(1, 30)))
        amt = round(float(rng.uniform(8_000, 90_000)), 2)
        rows.append((f"INV-W{k:06d}", cid, "WHOLESALE", inv, due, amt, paid, "PAID",
                     False, None, paid, 0.0, False))

    # normal OPEN AR block, sized so DSO ~ DSO_TARGET (net_sales_90 driven)
    lo = ALERT - dt.timedelta(days=90)
    ns90 = float(fact_sales[(fact_sales.date_key > lo) &
                            (fact_sales.date_key <= ALERT)].net_sales_amt.sum())
    outstanding_target = DSO_TARGET * ns90 / 90.0
    normal_open_target = max(250_000.0, outstanding_target - T_COLLECTIONS)
    n_open = 45
    amts = scale_to(rng.uniform(20_000, 120_000, size=n_open), normal_open_target)
    for i, amt in enumerate(amts):
        cid = f"CUST-{int(rng.integers(4,27)):02d}"  # non-disputing wholesale
        inv = dt.date(2026, 9, 1) + dt.timedelta(days=int(rng.integers(0, 60)))
        due = inv + dt.timedelta(days=60)  # due after the alert date
        rows.append((f"INV-O{i:04d}", cid, "WHOLESALE", inv, due, round(float(amt), 2),
                     None, "OPEN", False, None, due, 0.0, False))

    # planted wholesale disputes: 3 customers, impact scaled to $1.7M; 2 flip in last 2wk
    reasons = ["short-ship", "quality defect", "pricing discrepancy"]
    custs = ["CUST-01", "CUST-02", "CUST-03"]
    n_disp = 18
    impacts = scale_to(rng.uniform(40_000, 160_000, size=n_disp), T_WHOLESALE)
    for i, imp in enumerate(impacts):
        cid = custs[i % 3]
        inv = dt.date(2026, 9, int(rng.integers(1, 28)))
        due = inv + dt.timedelta(days=60)
        flipped = i < 2  # two big ones flip in the last 2 weeks
        exp_cash = QEND + dt.timedelta(days=int(rng.integers(15, 60)))
        rows.append((f"INV-D{i:04d}", cid, "WHOLESALE", inv, due,
                     round(float(imp), 2), None, "DISPUTED", True,
                     reasons[i % 3], exp_cash, round(float(imp), 2), bool(flipped)))

    # planted consumer settlement slippage: marketplace/BNPL, impact scaled to $0.8M
    n_slip = 10
    impacts = scale_to(rng.uniform(30_000, 120_000, size=n_slip), T_SETTLEMENT)
    part = ["CUST-27", "CUST-29", "CUST-28", "CUST-30"]
    for i, imp in enumerate(impacts):
        cid = part[i % 4]
        ch = "MARKETPLACE" if cid in ("CUST-27", "CUST-28") else "BNPL"
        inv = dt.date(2026, 10, int(rng.integers(1, 28)))
        due = inv + dt.timedelta(days=14 if ch == "MARKETPLACE" else 30)
        exp_cash = QEND + dt.timedelta(days=int(rng.integers(5, 30)))
        rows.append((f"INV-S{i:04d}", cid, ch, inv, due, round(float(imp), 2),
                     None, "SLIPPED", False, None, exp_cash, round(float(imp), 2), False))

    return pd.DataFrame(rows, columns=[
        "invoice_id", "customer_id", "channel", "invoice_date", "due_date",
        "amount", "paid_date", "status", "dispute_flag", "dispute_reason",
        "expected_cash_date", "cash_impact_vs_plan_amt", "flipped_last_2wk"])


CASH_CATS = ["SALES_RECEIPTS", "WHOLESALE_COLLECTIONS", "SUPPLIER_PAYMENTS",
             "PAYROLL", "RENT", "OTHER_OPEX", "TAX", "CAPEX"]


def build_fact_cash_ledger():
    """Daily actual cash in/out by category. The end-of-day running balance follows a
    realistic path: it builds to ~$30M by mid-2026, then drifts down to ~$25M by the
    2026-11-05 alert as the shortfall takes hold. ai_forecast(version=>'1') on this recent
    decline extrapolates to ~$22M at quarter-end, independently corroborating the authored
    fact_cash_forecast. running_cash_balance is written as the end-of-day balance on every
    category row, so a clean daily series (GROUP BY ledger_date) is trivial to forecast."""
    def target_balance(d):
        # piecewise: build 22M->30M to mid-2026, hold, then decline 30M->25M into the alert
        if d <= dt.date(2026, 6, 30):
            frac = (d - START).days / (dt.date(2026, 6, 30) - START).days
            return 22_000_000.0 + frac * 8_000_000.0
        if d <= dt.date(2026, 8, 31):
            return 30_000_000.0
        frac = (d - dt.date(2026, 8, 31)).days / (ALERT - dt.date(2026, 8, 31)).days
        return 30_000_000.0 - frac * 5_000_000.0

    IN_SPLIT = {"SALES_RECEIPTS": 0.74, "WHOLESALE_COLLECTIONS": 0.26}
    OUT_SPLIT = {"SUPPLIER_PAYMENTS": 0.47, "PAYROLL": 0.20, "RENT": 0.07,
                 "OTHER_OPEX": 0.16, "TAX": 0.06, "CAPEX": 0.04}
    days = list(pd.date_range(START, ALERT, freq="D").date)
    rows = []
    prev_bal = target_balance(START)
    for d in days:
        seasonal = 1.0 + 0.06 * np.sin((d.timetuple().tm_yday / 365) * 2 * np.pi)
        eod = target_balance(d) + float(rng.uniform(-120_000, 120_000))
        net = eod - prev_bal
        prev_bal = eod
        recent_dip = 0.90 if d >= PRIOR_RUN else 1.0  # collections soften in the last 2 weeks
        gross_in = float(rng.uniform(850_000, 1_050_000)) * seasonal * recent_dip
        gross_out = gross_in - net
        if gross_out < 0:                       # keep both sides non-negative
            gross_in += -gross_out
            gross_out = 0.0
        bal = round(eod, 2)
        for cat in CASH_CATS:
            if cat in IN_SPLIT:
                cin = round(gross_in * IN_SPLIT[cat], 2); cout = 0.0
            else:
                cin = 0.0; cout = round(gross_out * OUT_SPLIT[cat], 2)
            rows.append([d, cat, cin, cout, round(cin - cout, 2), bal])
    return pd.DataFrame(rows, columns=["ledger_date", "cash_category", "cash_in_amt",
                                       "cash_out_amt", "net_cash_amt", "running_cash_balance"])


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
    "fact_sales": "Daily sell-through by SKU x location. Planted: Women's Outerwear sell-through collapses in recent months.",
    "fact_inventory_snapshot": "Weekly inventory snapshot (latest week 2026-10-31). Planted: ~$3.5M Women's Outerwear at cost in the 90+ aged bucket at the latest snapshot.",
    "fact_purchase_orders": "Purchase orders. Planted: oversized FW25 outerwear buy with OPEN lines still inbound; expedite/air-freight + tariff lines.",
    "fact_ap_payments": "Supplier payments (AP). Planted: ~$2.0M cash_impact_vs_plan from accelerated payments, lost discounts, tariff surcharge, expedited freight.",
    "fact_ar_invoices": "AR invoices. Planted: $1.7M wholesale disputes + $0.8M consumer settlement slippage = $2.5M; 2 flips in last 2 weeks.",
    "fact_cash_ledger": "Daily actual cash in/out by category feeding ai_forecast(); end-of-day balance builds to ~$30M then declines to ~$25M by the 2026-11-05 alert (ai_forecast v1 projects ~$22M at quarter-end).",
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

# COMMAND ----------

def main():
    # `spark` is the ambient session provided by the Databricks notebook.
    spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CAT}.{SCH}")

    sup = build_dim_supplier()
    prod = build_dim_product(sup.supplier_id.values, "SUP-01")
    store = build_dim_store()
    cust = build_dim_customer()
    sales = build_fact_sales(prod, store)

    tables = {
        "dim_date": build_dim_date(),
        "dim_product": prod,
        "dim_store": store,
        "dim_customer": cust,
        "dim_supplier": sup,
        "fact_sales": sales,
        "fact_inventory_snapshot": build_fact_inventory(prod, store, sales),
        "fact_purchase_orders": build_fact_po(prod, sup),
        "fact_ap_payments": build_fact_ap(),
        "fact_ar_invoices": build_fact_ar(sales),
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

    print("\n=== RECONCILIATION (dollars) ===")
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

    print("\n=== OPERATIONAL RATIOS (requires the 02_metric_views notebook) ===")
    try:
        for r in spark.sql(f"SELECT * FROM {CAT}.{SCH}.v_working_capital_cycle").collect():
            print(f"  DSO={r.dso_days}  DPO={r.dpo_days}  DIO={r.dio_days}  CCC={r.ccc_days}")
        for r in spark.sql(f"SELECT * FROM {CAT}.{SCH}.v_inventory_sell_through ORDER BY sell_through_rate").collect():
            print(f"    {r.department:12s} {r.category:12s} ST={r.sell_through_rate}  WOS={r.weeks_of_supply}")
    except Exception:
        print("  (views not created yet — run the 02_metric_views notebook, then re-run this cell)")


main()
