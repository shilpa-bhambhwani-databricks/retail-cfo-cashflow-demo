# Databricks notebook source
# MAGIC %md
# MAGIC # Lakeview Retail — CFO Cash-Flow Shortfall — Data Exploration Worksheet
# MAGIC
# MAGIC A guided, **read-only** tour of the demo dataset. Run it top to bottom.
# MAGIC
# MAGIC **The story (Alert → Investigate → Evidence):** quarter-end cash (2026-12-31) is projected at
# MAGIC **$22M against a $30M plan — an $8M shortfall** — not because the business is unprofitable, but because
# MAGIC cash is trapped in unsold inventory and mistimed payments. It decomposes into three drivers:
# MAGIC
# MAGIC | Driver | Impact |
# MAGIC |---|---|
# MAGIC | Inventory & markdown cash trap | **−$3.5M** |
# MAGIC | Collections & settlement slowdown | **−$2.5M** |
# MAGIC | Supplier / AP acceleration | **−$2.0M** |
# MAGIC
# MAGIC The alert also trips on **velocity**: the projection fell from $25M (run 2026-10-22) to $22M
# MAGIC (run 2026-11-05) — a **$3M drop in two weeks**.
# MAGIC
# MAGIC _Catalog:_ `serverless_stable_genie_cfo_catalog` · _Schema:_ `lakeview_retail`

# COMMAND ----------

# MAGIC %sql
# MAGIC USE CATALOG serverless_stable_genie_cfo_catalog;
# MAGIC USE SCHEMA lakeview_retail;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. What's in the dataset
# MAGIC 12 tables (5 dimensions + 7 facts). Row counts:

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT 'dim_date' AS table_name, count(*) AS rows FROM dim_date
# MAGIC UNION ALL SELECT 'dim_product', count(*) FROM dim_product
# MAGIC UNION ALL SELECT 'dim_store', count(*) FROM dim_store
# MAGIC UNION ALL SELECT 'dim_customer', count(*) FROM dim_customer
# MAGIC UNION ALL SELECT 'dim_supplier', count(*) FROM dim_supplier
# MAGIC UNION ALL SELECT 'fact_sales', count(*) FROM fact_sales
# MAGIC UNION ALL SELECT 'fact_inventory_snapshot', count(*) FROM fact_inventory_snapshot
# MAGIC UNION ALL SELECT 'fact_purchase_orders', count(*) FROM fact_purchase_orders
# MAGIC UNION ALL SELECT 'fact_ap_payments', count(*) FROM fact_ap_payments
# MAGIC UNION ALL SELECT 'fact_ar_invoices', count(*) FROM fact_ar_invoices
# MAGIC UNION ALL SELECT 'fact_cash_ledger', count(*) FROM fact_cash_ledger
# MAGIC UNION ALL SELECT 'fact_cash_forecast', count(*) FROM fact_cash_forecast
# MAGIC ORDER BY table_name;

# COMMAND ----------

# MAGIC %md
# MAGIC **Views** (the governed semantic layer): 5 UC metric views — `mv_cash_forecast`, `mv_ar`, `mv_ap`,
# MAGIC `mv_inventory`, `mv_sales` — plus 3 SQL KPI views — `v_cash_forecast_deterioration`,
# MAGIC `v_working_capital_cycle`, `v_inventory_sell_through`. Fact date ranges:

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT 'fact_sales' AS fact, min(date_key) AS from_date, max(date_key) AS to_date FROM fact_sales
# MAGIC UNION ALL SELECT 'fact_cash_ledger', min(ledger_date), max(ledger_date) FROM fact_cash_ledger
# MAGIC UNION ALL SELECT 'fact_cash_forecast', min(forecast_week_ending), max(forecast_week_ending) FROM fact_cash_forecast;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. The alert — projected quarter-end cash vs plan
# MAGIC Both forecast runs at quarter-end (2026-12-31). The current run (2026-11-05) projects
# MAGIC **$22M vs a $30M plan = −$8M**; the prior run (2026-10-22) was still at $25M.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT forecast_run_date,
# MAGIC        round(sum(planned_cash_amt))   AS plan_cash,
# MAGIC        round(sum(projected_cash_amt)) AS projected_cash,
# MAGIC        round(sum(variance_amt))       AS variance
# MAGIC FROM fact_cash_forecast
# MAGIC WHERE forecast_week_ending = DATE'2026-12-31'
# MAGIC GROUP BY forecast_run_date ORDER BY forecast_run_date;

# COMMAND ----------

# MAGIC %md
# MAGIC **Velocity** — the 2-week deterioration via `v_cash_forecast_deterioration`. Expect **−$3M**.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT * FROM v_cash_forecast_deterioration;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. Why — the −$8M split by driver
# MAGIC At quarter-end the shortfall decomposes cleanly into the three drivers.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT driver, round(sum(variance_amt)) AS variance
# MAGIC FROM fact_cash_forecast
# MAGIC WHERE forecast_run_date = DATE'2026-11-05' AND forecast_week_ending = DATE'2026-12-31'
# MAGIC GROUP BY driver ORDER BY variance;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Driver 1 — Inventory & markdown cash trap (−$3.5M)
# MAGIC Sell-through by category at the latest snapshot. **Women's Outerwear is the clear outlier** —
# MAGIC lowest sell-through, highest weeks of supply — while every other category looks healthy.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT * FROM v_inventory_sell_through ORDER BY sell_through_rate ASC;

# COMMAND ----------

# MAGIC %md
# MAGIC The trapped cash: aged 90+ on-hand at cost by department/category (latest week 2026-10-31).
# MAGIC Women's Outerwear = **$3.5M**, plus its looming markdown liability.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT p.department, p.category,
# MAGIC        round(sum(i.on_hand_cost))           AS aged_on_hand_cost,
# MAGIC        round(sum(i.markdown_liability_amt)) AS markdown_liability
# MAGIC FROM fact_inventory_snapshot i
# MAGIC JOIN dim_product p ON i.product_id = p.product_id
# MAGIC WHERE i.is_aged
# MAGIC   AND i.week_ending_date = (SELECT max(week_ending_date) FROM fact_inventory_snapshot)
# MAGIC GROUP BY p.department, p.category
# MAGIC ORDER BY aged_on_hand_cost DESC;

# COMMAND ----------

# MAGIC %md
# MAGIC "Receipts don't slow down" — open POs still inbound (future receipt dates) keep cash committed.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT status,
# MAGIC        count(*)                   AS po_lines,
# MAGIC        round(sum(committed_cost)) AS committed_cost
# MAGIC FROM fact_purchase_orders
# MAGIC GROUP BY status ORDER BY committed_cost DESC;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. Driver 2 — Collections & settlement slowdown (−$2.5M)
# MAGIC By channel: wholesale disputes **$1.7M** + consumer settlement slippage (marketplace + BNPL) **$0.8M**.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT channel,
# MAGIC        round(sum(cash_impact_vs_plan_amt))              AS collections_at_risk,
# MAGIC        count(*)                                         AS invoices,
# MAGIC        sum(CASE WHEN flipped_last_2wk THEN 1 ELSE 0 END) AS flipped_last_2wk
# MAGIC FROM fact_ar_invoices
# MAGIC WHERE cash_impact_vs_plan_amt > 0
# MAGIC GROUP BY channel ORDER BY collections_at_risk DESC;

# COMMAND ----------

# MAGIC %md
# MAGIC The 3 wholesale disputes with reasons — the two that flipped in the last 2 weeks drive the velocity trigger.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT c.customer_name, i.dispute_reason,
# MAGIC        round(sum(i.amount))    AS disputed_amount,
# MAGIC        max(i.flipped_last_2wk) AS flipped_last_2wk
# MAGIC FROM fact_ar_invoices i
# MAGIC JOIN dim_customer c ON i.customer_id = c.customer_id
# MAGIC WHERE i.dispute_flag
# MAGIC GROUP BY c.customer_name, i.dispute_reason
# MAGIC ORDER BY disputed_amount DESC;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Driver 3 — Supplier / AP acceleration (−$2.0M)
# MAGIC Extra near-term cash out vs plan and its components: lost early-pay discounts, tariff surcharge, expedited freight.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT payment_category,
# MAGIC        count(*)                                AS payments,
# MAGIC        round(sum(cash_impact_vs_plan_amt))     AS cash_out_vs_plan,
# MAGIC        round(sum(early_pay_discount_lost_amt)) AS early_pay_discount_lost,
# MAGIC        round(sum(surcharge_amt))               AS tariff_surcharge,
# MAGIC        round(sum(expedite_freight_amt))        AS expedited_freight
# MAGIC FROM fact_ap_payments
# MAGIC GROUP BY payment_category ORDER BY cash_out_vs_plan DESC;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Working-capital cycle (DSO / DPO / DIO / CCC)
# MAGIC As of the alert date (2026-11-05). Healthy for apparel — reinforcing that this is a **timing** story, not a broken business.

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT * FROM v_working_capital_cycle;

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Using the metric views (governed semantic layer)
# MAGIC The `mv_*` metric views expose reusable **measures** you query with `MEASURE()` and slice by dimensions —
# MAGIC the same numbers, governed and reconciled. This is the layer Genie will sit on.
# MAGIC
# MAGIC **Cash — the −$8M split** (`cash_vs_plan_variance` is pre-filtered to quarter-end):

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT driver, MEASURE(cash_vs_plan_variance) AS quarter_end_variance
# MAGIC FROM mv_cash_forecast
# MAGIC WHERE forecast_run_date = DATE'2026-11-05'
# MAGIC GROUP BY driver ORDER BY quarter_end_variance;

# COMMAND ----------

# MAGIC %md
# MAGIC **Inventory — trapped cash by category** (`aged_inventory_cost`, latest week):

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT department, category, MEASURE(aged_inventory_cost) AS aged_cost
# MAGIC FROM mv_inventory
# MAGIC WHERE week_ending_date = DATE'2026-10-31' AND is_aged = true
# MAGIC GROUP BY department, category ORDER BY aged_cost DESC;

# COMMAND ----------

# MAGIC %md
# MAGIC **AR — collections at risk by channel** and **AP — cash out vs plan** (the other two drivers, via metric views):

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT channel, MEASURE(collections_at_risk) AS collections_at_risk
# MAGIC FROM mv_ar GROUP BY channel ORDER BY collections_at_risk DESC;

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT payment_category, MEASURE(ap_paid_vs_plan) AS ap_cash_out_vs_plan
# MAGIC FROM mv_ap GROUP BY payment_category ORDER BY ap_cash_out_vs_plan DESC;

# COMMAND ----------

# MAGIC %md
# MAGIC ---
# MAGIC _Read-only worksheet. Everything reconciles to the planted **$8M** shortfall:
# MAGIC **$3.5M** inventory + **$2.5M** collections + **$2.0M** AP._