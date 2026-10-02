-- Databricks notebook source
-- MAGIC %md
-- MAGIC # 02 · Metric views + KPI views (Step 2)
-- MAGIC
-- MAGIC Governed semantic layer over the Lakeview Retail tables: 5 Unity Catalog
-- MAGIC **metric views** (`mv_*`) + 3 cross-fact **SQL views** (`v_*`). This is what
-- MAGIC makes every Genie answer reconcile to the planted $8M story.
-- MAGIC
-- MAGIC Run after `01_generate_data`, on a serverless SQL warehouse / serverless compute.

-- COMMAND ----------

-- =====================================================================
-- Lakeview Retail — CFO Cash-Flow Shortfall demo
-- Step 2: Metric views (governed semantic layer) + cross-fact KPI views
-- Target: serverless_stable_genie_cfo_catalog.lakeview_retail
--
-- Unity Catalog metric views (YAML v1.1) where a single fact grain +
-- dimension joins fits. Cross-fact ratios that span multiple fact
-- grains (working-capital cycle, sell-through, 2-week deterioration)
-- are plain SQL views — a single metric view has one source grain.
--
-- Join keys are aliased in each source SELECT (e.g. customer_id AS
-- join_customer_id) so the join criteria is unambiguous when the fact
-- and the dimension share the key column name.
--
-- Statements below are separated by a sentinel comment line so the
-- loader can execute them one at a time (metric-view DDL cannot be
-- batched with other statements in a single API call).
-- =====================================================================

-- ---------------------------------------------------------------------
-- METRIC VIEW 1: Cash forecast (the alert + the "why")
-- Reconciles: projected_quarter_end_cash=22M, planned=30M, variance=-8M
-- (run 2026-11-05); -3.5/-2.5/-2.0M split by driver.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW serverless_stable_genie_cfo_catalog.lakeview_retail.mv_cash_forecast
WITH METRICS LANGUAGE YAML AS $$
version: 1.1
comment: "Rolling cash forecast by run / week / driver. Carries the planted $8M quarter-end shortfall and its 3-driver split."
source: SELECT * FROM serverless_stable_genie_cfo_catalog.lakeview_retail.fact_cash_forecast
filter: driver IS NOT NULL
fields:
  - name: forecast_run_date
    expr: forecast_run_date
    display_name: Forecast Run Date
    synonyms: ["run date", "as-of date"]
  - name: forecast_week_ending
    expr: forecast_week_ending
    display_name: Forecast Week Ending
  - name: driver
    expr: driver
    display_name: Cash Driver
    synonyms: ["shortfall driver", "cause", "root cause"]
measures:
  - name: projected_cash
    expr: SUM(projected_cash_amt)
    display_name: Projected Cash
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
  - name: planned_cash
    expr: SUM(planned_cash_amt)
    display_name: Planned Cash
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
  - name: cash_variance
    expr: SUM(variance_amt)
    display_name: Cash Variance vs Plan
    synonyms: ["variance", "shortfall"]
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
  - name: projected_quarter_end_cash
    expr: SUM(projected_cash_amt) FILTER (WHERE forecast_week_ending = DATE'2026-12-31')
    display_name: Projected Quarter-End Cash
    synonyms: ["projected cash at quarter end", "year-end cash"]
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
  - name: planned_quarter_end_cash
    expr: SUM(planned_cash_amt) FILTER (WHERE forecast_week_ending = DATE'2026-12-31')
    display_name: Planned Quarter-End Cash
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
  - name: cash_vs_plan_variance
    expr: SUM(variance_amt) FILTER (WHERE forecast_week_ending = DATE'2026-12-31')
    display_name: Quarter-End Cash vs Plan
    synonyms: ["quarter end shortfall", "gap to plan"]
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
$$;

-- COMMAND ----------

-- ---------------------------------------------------------------------
-- METRIC VIEW 2: Accounts receivable (collections & settlement)
-- Reconciles: collections_at_risk=2.5M (wholesale 1.7 + consumer 0.8).
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW serverless_stable_genie_cfo_catalog.lakeview_retail.mv_ar
WITH METRICS LANGUAGE YAML AS $$
version: 1.1
comment: "AR invoices with dispute / settlement detail. collections_at_risk = the $2.5M collections-slowdown driver."
source: SELECT *, channel AS ar_channel, customer_id AS join_customer_id FROM serverless_stable_genie_cfo_catalog.lakeview_retail.fact_ar_invoices
joins:
  - name: customer
    source: serverless_stable_genie_cfo_catalog.lakeview_retail.dim_customer
    'on': join_customer_id = customer.customer_id
fields:
  - name: channel
    expr: ar_channel
    display_name: Channel
    synonyms: ["ar channel"]
  - name: status
    expr: status
    display_name: Invoice Status
  - name: dispute_reason
    expr: dispute_reason
    display_name: Dispute Reason
  - name: flipped_last_2wk
    expr: flipped_last_2wk
    display_name: Flipped To Disputed Last 2 Weeks
  - name: customer_name
    expr: customer.customer_name
    display_name: Customer
    synonyms: ["account"]
  - name: segment
    expr: customer.segment
    display_name: Customer Segment
  - name: customer_region
    expr: customer.region
    display_name: Customer Region
  - name: invoice_date
    expr: invoice_date
    display_name: Invoice Date
  - name: due_date
    expr: due_date
    display_name: Due Date
  - name: expected_cash_date
    expr: expected_cash_date
    display_name: Expected Cash Date
measures:
  - name: total_ar
    expr: SUM(amount)
    display_name: Total AR
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
  - name: collections_at_risk
    expr: SUM(cash_impact_vs_plan_amt)
    display_name: Collections At Risk
    synonyms: ["cash at risk in AR", "receivables shortfall"]
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
  - name: disputed_ar
    expr: SUM(amount) FILTER (WHERE dispute_flag = true)
    display_name: Disputed AR
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
  - name: overdue_ar
    expr: SUM(amount) FILTER (WHERE status IN ('OPEN','DISPUTED','SLIPPED') AND due_date < DATE'2026-11-05')
    display_name: Overdue AR
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
  - name: invoice_count
    expr: COUNT(1)
    display_name: Invoice Count
$$;

-- COMMAND ----------

-- ---------------------------------------------------------------------
-- METRIC VIEW 3: Accounts payable (supplier payments & terms)
-- Reconciles: ap_paid_vs_plan=2.0M (accelerated payments driver).
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW serverless_stable_genie_cfo_catalog.lakeview_retail.mv_ap
WITH METRICS LANGUAGE YAML AS $$
version: 1.1
comment: "Supplier payments (AP). ap_paid_vs_plan = the $2.0M supplier/terms-variance driver (early pay + surcharge + expedite + lost discounts)."
source: SELECT *, supplier_id AS join_supplier_id FROM serverless_stable_genie_cfo_catalog.lakeview_retail.fact_ap_payments
joins:
  - name: supplier
    source: serverless_stable_genie_cfo_catalog.lakeview_retail.dim_supplier
    'on': join_supplier_id = supplier.supplier_id
fields:
  - name: payment_category
    expr: payment_category
    display_name: Payment Category
    synonyms: ["normal or accelerated"]
  - name: supplier_name
    expr: supplier.supplier_name
    display_name: Supplier
  - name: supplier_category
    expr: supplier.category
    display_name: Supplier Category
  - name: supplier_country
    expr: supplier.country
    display_name: Sourcing Country
  - name: scheduled_pay_date
    expr: scheduled_pay_date
    display_name: Scheduled Pay Date
  - name: actual_pay_date
    expr: actual_pay_date
    display_name: Actual Pay Date
measures:
  - name: total_ap_paid
    expr: SUM(invoice_amt)
    display_name: Total AP Paid
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
  - name: ap_paid_vs_plan
    expr: SUM(cash_impact_vs_plan_amt)
    display_name: AP Cash Out vs Plan
    synonyms: ["extra cash out in AP", "payables acceleration"]
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
  - name: early_pay_discount_lost
    expr: SUM(early_pay_discount_lost_amt)
    display_name: Early-Pay Discount Lost
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
  - name: surcharge_paid
    expr: SUM(surcharge_amt)
    display_name: Tariff/Freight Surcharge Paid
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
  - name: expedite_freight_paid
    expr: SUM(expedite_freight_amt)
    display_name: Expedited Freight Paid
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
  - name: avg_days_early
    expr: AVG(days_early)
    display_name: Avg Days Paid Early
$$;

-- COMMAND ----------

-- ---------------------------------------------------------------------
-- METRIC VIEW 4: Inventory (markdown cash trap)
-- Reconciles: aged_inventory_cost=3.5M for Women's Outerwear at the
-- latest snapshot week (2026-10-31, the Saturday week-ending).
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW serverless_stable_genie_cfo_catalog.lakeview_retail.mv_inventory
WITH METRICS LANGUAGE YAML AS $$
version: 1.1
comment: "Weekly inventory snapshot. aged_inventory_cost / markdown_liability carry the $3.5M Women's Outerwear cash trap (filter to latest week_ending_date)."
source: SELECT *, product_id AS join_product_id, store_id AS join_store_id FROM serverless_stable_genie_cfo_catalog.lakeview_retail.fact_inventory_snapshot
joins:
  - name: product
    source: serverless_stable_genie_cfo_catalog.lakeview_retail.dim_product
    'on': join_product_id = product.product_id
  - name: store
    source: serverless_stable_genie_cfo_catalog.lakeview_retail.dim_store
    'on': join_store_id = store.store_id
fields:
  - name: department
    expr: product.department
    display_name: Department
  - name: category
    expr: product.category
    display_name: Category
  - name: subcategory
    expr: product.subcategory
    display_name: Subcategory
  - name: season
    expr: product.season
    display_name: Season
  - name: region
    expr: store.region
    display_name: Region
  - name: location_type
    expr: store.location_type
    display_name: Location Type
  - name: store_name
    expr: store.store_name
    display_name: Location
  - name: week_ending_date
    expr: week_ending_date
    display_name: Week Ending
  - name: aging_bucket
    expr: aging_bucket
    display_name: Aging Bucket
  - name: is_aged
    expr: is_aged
    display_name: Is Aged 90+
measures:
  - name: on_hand_cost
    expr: SUM(on_hand_cost)
    display_name: On-Hand Inventory At Cost
    synonyms: ["cash in inventory", "inventory value"]
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
  - name: aged_inventory_cost
    expr: SUM(on_hand_cost) FILTER (WHERE is_aged = true)
    display_name: Aged Inventory (90+) At Cost
    synonyms: ["trapped inventory cash", "old inventory value"]
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
  - name: markdown_liability
    expr: SUM(markdown_liability_amt)
    display_name: Markdown Liability
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
  - name: on_hand_units
    expr: SUM(on_hand_units)
    display_name: On-Hand Units
  - name: inventory_aged_pct
    expr: MEASURE(aged_inventory_cost) / NULLIF(MEASURE(on_hand_cost), 0)
    display_name: Inventory Aged %
    synonyms: ["percent aged", "share of inventory aged"]
    format: {type: percentage, decimal_places: {type: exact, places: 1}}
$$;

-- COMMAND ----------

-- ---------------------------------------------------------------------
-- METRIC VIEW 5: Sales / sell-through
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW serverless_stable_genie_cfo_catalog.lakeview_retail.mv_sales
WITH METRICS LANGUAGE YAML AS $$
version: 1.1
comment: "Daily sell-through by SKU x location. Planted: Women's Outerwear net sales run far below plan."
source: SELECT *, product_id AS join_product_id, store_id AS join_store_id FROM serverless_stable_genie_cfo_catalog.lakeview_retail.fact_sales
joins:
  - name: product
    source: serverless_stable_genie_cfo_catalog.lakeview_retail.dim_product
    'on': join_product_id = product.product_id
  - name: store
    source: serverless_stable_genie_cfo_catalog.lakeview_retail.dim_store
    'on': join_store_id = store.store_id
fields:
  - name: department
    expr: product.department
    display_name: Department
  - name: category
    expr: product.category
    display_name: Category
  - name: season
    expr: product.season
    display_name: Season
  - name: region
    expr: store.region
    display_name: Region
  - name: location_type
    expr: store.location_type
    display_name: Location Type
  - name: store_name
    expr: store.store_name
    display_name: Location
  - name: date_key
    expr: date_key
    display_name: Date
measures:
  - name: net_sales
    expr: SUM(net_sales_amt)
    display_name: Net Sales
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
  - name: gross_sales
    expr: SUM(gross_sales_amt)
    display_name: Gross Sales
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
  - name: units_sold
    expr: SUM(units_sold)
    display_name: Units Sold
  - name: cogs
    expr: SUM(cogs_amt)
    display_name: COGS
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
  - name: discount
    expr: SUM(discount_amt)
    display_name: Discount / Markdown
    format: {type: currency, currency_code: USD, decimal_places: {type: exact, places: 0}}
$$;

-- COMMAND ----------

-- ---------------------------------------------------------------------
-- KPI VIEW 6 (plain SQL): 2-week cash-forecast deterioration
-- Cross-run (period-over-period) comparison — not a single-grain
-- measure, so implemented as a regular view. Expect -3,000,000.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW serverless_stable_genie_cfo_catalog.lakeview_retail.v_cash_forecast_deterioration
COMMENT 'Quarter-end projected-cash change between the two most recent forecast runs (velocity of the shortfall). Current run 2026-11-05 vs prior 2026-10-22 = -$3M.'
AS
WITH qe AS (
  SELECT forecast_run_date,
         SUM(projected_cash_amt) AS projected_quarter_end_cash
  FROM serverless_stable_genie_cfo_catalog.lakeview_retail.fact_cash_forecast
  WHERE forecast_week_ending = DATE'2026-12-31'
  GROUP BY forecast_run_date
),
ranked AS (
  SELECT forecast_run_date, projected_quarter_end_cash,
         LAG(projected_quarter_end_cash) OVER (ORDER BY forecast_run_date) AS prior_projected_quarter_end_cash
  FROM qe
)
SELECT forecast_run_date AS current_run_date,
       projected_quarter_end_cash,
       prior_projected_quarter_end_cash,
       projected_quarter_end_cash - prior_projected_quarter_end_cash AS deterioration_2wk
FROM ranked
WHERE prior_projected_quarter_end_cash IS NOT NULL;

-- COMMAND ----------

-- ---------------------------------------------------------------------
-- KPI VIEW 7 (plain SQL): Working-capital cycle (DSO / DPO / DIO / CCC)
-- Cross-fact ratios as of the alert date 2026-11-05, flows over the
-- trailing 90 days. Documented approximations (no single fact grain).
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW serverless_stable_genie_cfo_catalog.lakeview_retail.v_working_capital_cycle
COMMENT 'Cash-conversion-cycle KPIs as of 2026-11-05: DSO (AR vs trailing-90d net sales), DIO (ending inventory vs trailing-90d COGS), DPO (weighted actual terms), CCC = DSO + DIO - DPO.'
AS
WITH asof AS (SELECT DATE'2026-11-05' AS d),
flows AS (
  SELECT SUM(net_sales_amt) AS net_sales_90, SUM(cogs_amt) AS cogs_90
  FROM serverless_stable_genie_cfo_catalog.lakeview_retail.fact_sales, asof
  WHERE date_key > date_sub(asof.d, 90) AND date_key <= asof.d
),
inv AS (
  SELECT SUM(on_hand_cost) AS ending_inventory_cost
  FROM serverless_stable_genie_cfo_catalog.lakeview_retail.fact_inventory_snapshot
  WHERE week_ending_date = (SELECT MAX(week_ending_date)
                            FROM serverless_stable_genie_cfo_catalog.lakeview_retail.fact_inventory_snapshot)
),
ar AS (
  SELECT SUM(amount) AS outstanding_ar
  FROM serverless_stable_genie_cfo_catalog.lakeview_retail.fact_ar_invoices, asof
  WHERE invoice_date <= asof.d AND (paid_date IS NULL OR paid_date > asof.d)
),
ap AS (
  SELECT SUM(invoice_amt * (terms_days - days_early)) / NULLIF(SUM(invoice_amt), 0) AS dpo_days
  FROM serverless_stable_genie_cfo_catalog.lakeview_retail.fact_ap_payments, asof
  WHERE actual_pay_date > date_sub(asof.d, 90) AND actual_pay_date <= asof.d
)
SELECT
  round(ar.outstanding_ar / (flows.net_sales_90 / 90.0), 1) AS dso_days,
  round(ap.dpo_days, 1)                                     AS dpo_days,
  round(inv.ending_inventory_cost / (flows.cogs_90 / 90.0), 1) AS dio_days,
  round(ar.outstanding_ar / (flows.net_sales_90 / 90.0)
      + inv.ending_inventory_cost / (flows.cogs_90 / 90.0)
      - ap.dpo_days, 1)                                     AS ccc_days
FROM flows, inv, ar, ap;

-- COMMAND ----------

-- ---------------------------------------------------------------------
-- KPI VIEW 8 (plain SQL): Inventory sell-through & weeks of supply
-- Cross-fact (sales + inventory) by department/category at latest week.
-- Planted: Women's Outerwear sell-through ~35%.
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW serverless_stable_genie_cfo_catalog.lakeview_retail.v_inventory_sell_through
COMMENT 'Sell-through rate and weeks-of-supply by department/category as of the latest inventory snapshot, using trailing-8-week sales. Planted: Women''s Outerwear sell-through well below plan.'
AS
WITH latest AS (
  SELECT MAX(week_ending_date) AS wk
  FROM serverless_stable_genie_cfo_catalog.lakeview_retail.fact_inventory_snapshot
),
onhand AS (
  SELECT p.department, p.category,
         SUM(i.on_hand_units) AS on_hand_units
  FROM serverless_stable_genie_cfo_catalog.lakeview_retail.fact_inventory_snapshot i
  JOIN serverless_stable_genie_cfo_catalog.lakeview_retail.dim_product p ON i.product_id = p.product_id
  JOIN latest ON i.week_ending_date = latest.wk
  GROUP BY p.department, p.category
),
sales8 AS (
  SELECT p.department, p.category,
         SUM(s.units_sold) AS units_8wk,
         SUM(s.units_sold) / 8.0 AS units_per_week
  FROM serverless_stable_genie_cfo_catalog.lakeview_retail.fact_sales s
  JOIN serverless_stable_genie_cfo_catalog.lakeview_retail.dim_product p ON s.product_id = p.product_id
  JOIN latest ON s.date_key > date_sub(latest.wk, 56) AND s.date_key <= latest.wk
  GROUP BY p.department, p.category
)
SELECT
  o.department, o.category,
  o.on_hand_units,
  COALESCE(sl.units_8wk, 0) AS units_sold_8wk,
  round(COALESCE(sl.units_8wk, 0) / NULLIF(COALESCE(sl.units_8wk, 0) + o.on_hand_units, 0), 3) AS sell_through_rate,
  round(o.on_hand_units / NULLIF(sl.units_per_week, 0), 1) AS weeks_of_supply
FROM onhand o
LEFT JOIN sales8 sl ON o.department = sl.department AND o.category = sl.category
ORDER BY sell_through_rate ASC;

-- COMMAND ----------
-- MAGIC %md ## Validation — the numbers that must tie out

-- COMMAND ----------
-- Quarter-end cash: plan 30M, projected 22M, variance -8M (run 2026-11-05)
SELECT forecast_run_date,
       round(sum(planned_cash_amt))   AS plan_cash,
       round(sum(projected_cash_amt)) AS projected_cash,
       round(sum(variance_amt))       AS variance
FROM serverless_stable_genie_cfo_catalog.lakeview_retail.fact_cash_forecast
WHERE forecast_week_ending = DATE'2026-12-31'
GROUP BY forecast_run_date ORDER BY forecast_run_date;

-- COMMAND ----------
-- Working-capital cycle: DSO~45, DPO~47, DIO~75, CCC~74
SELECT * FROM serverless_stable_genie_cfo_catalog.lakeview_retail.v_working_capital_cycle;

-- COMMAND ----------
-- Sell-through: Women's Outerwear is the clear outlier
SELECT department, category, round(sell_through_rate,3) AS sell_through, round(weeks_of_supply,1) AS weeks_supply
FROM serverless_stable_genie_cfo_catalog.lakeview_retail.v_inventory_sell_through
ORDER BY sell_through_rate ASC;
