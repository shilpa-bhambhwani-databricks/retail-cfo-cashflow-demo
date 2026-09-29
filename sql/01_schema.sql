-- =====================================================================
-- Lakeview Retail — CFO Cash-Flow Shortfall demo
-- Step 1: Schema + data-model DDL (12 tables)
-- Target: serverless_stable_genie_cfo_catalog.lakeview_retail
--
-- Canonical DDL for reproducibility/publishing. In practice the live
-- tables are created + loaded by data/generate_data.py (Databricks
-- Connect), which then applies these COMMENTs. Grain noted per table.
-- Demo clock: quarter-end 2026-12-31; alert as-of 2026-11-05.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS serverless_stable_genie_cfo_catalog.lakeview_retail
  COMMENT 'Lakeview Retail (omnichannel apparel) — CFO cash-flow-shortfall Genie demo. Planted $8M quarter-end shortfall: inventory-markdown trap $3.5M + collections/settlement slowdown $2.5M + supplier/AP acceleration $2.0M.';

USE CATALOG serverless_stable_genie_cfo_catalog;
USE SCHEMA lakeview_retail;

-- ---------------------------------------------------------------------
-- DIMENSIONS
-- ---------------------------------------------------------------------

-- Grain: one row per calendar day (2024-11-01 .. 2026-12-31, incl. forecast horizon)
CREATE TABLE IF NOT EXISTS dim_date (
  date_key            DATE    COMMENT 'Calendar date (PK)',
  day_of_month        INT     COMMENT 'Day of month 1-31',
  month_num           INT     COMMENT 'Month number 1-12',
  month_name          STRING  COMMENT 'Month name',
  quarter_num         INT     COMMENT 'Calendar quarter 1-4',
  year_num            INT     COMMENT 'Calendar year',
  fiscal_quarter      STRING  COMMENT 'Fiscal quarter label e.g. FY2026-Q4',
  fiscal_year         INT     COMMENT 'Fiscal year (calendar-aligned here)',
  week_of_year        INT     COMMENT 'ISO week of year',
  week_ending_date    DATE    COMMENT 'Saturday week-ending date this day rolls up to',
  day_of_week         STRING  COMMENT 'Day name (Mon..Sun)',
  is_weekend          BOOLEAN COMMENT 'True for Sat/Sun',
  is_quarter_end      BOOLEAN COMMENT 'True on the last day of a calendar quarter'
) COMMENT 'Date dimension spanning history + forecast horizon.';

-- Grain: one row per SKU (~500). Women's Outerwear is the planted inventory-trap category.
CREATE TABLE IF NOT EXISTS dim_product (
  product_id          STRING  COMMENT 'SKU id e.g. SKU-00001 (PK)',
  sku                 STRING  COMMENT 'SKU code',
  product_name        STRING  COMMENT 'Product display name',
  department          STRING  COMMENT "Women's / Men's / Kids / Footwear / Accessories",
  category            STRING  COMMENT 'Merchandise category e.g. Outerwear, Tops, Denim',
  subcategory         STRING  COMMENT 'Finer category',
  season              STRING  COMMENT 'Seasonal collection e.g. FW25, SS26; NULL = core',
  is_seasonal         BOOLEAN COMMENT 'True for seasonal (markdown-risk) product',
  unit_cost           DECIMAL(10,2) COMMENT 'Landed cost per unit',
  unit_price          DECIMAL(10,2) COMMENT 'List retail price per unit',
  primary_supplier_id STRING  COMMENT 'FK -> dim_supplier.supplier_id'
) COMMENT 'Product / SKU dimension. Planted trap: Women'' Outerwear (season FW25) over-bought, weak sell-through.';

-- Grain: one row per selling location — 40 stores + 3 DCs + 1 ecom channel (44 total)
CREATE TABLE IF NOT EXISTS dim_store (
  store_id            STRING  COMMENT 'Location id e.g. ST-001, DC-01, ECOM (PK)',
  store_name          STRING  COMMENT 'Location name',
  location_type       STRING  COMMENT 'STORE / DC / ECOM',
  region              STRING  COMMENT 'West / Midwest / South / Northeast',
  state               STRING  COMMENT 'US state (NULL for ECOM)',
  city                STRING  COMMENT 'City (NULL for ECOM)',
  open_date           DATE    COMMENT 'Location open date'
) COMMENT 'Store / DC / channel dimension.';

-- Grain: one row per wholesale/B2B account or consumer settlement partner (~30)
CREATE TABLE IF NOT EXISTS dim_customer (
  customer_id         STRING  COMMENT 'Customer/account id e.g. CUST-01 (PK)',
  customer_name       STRING  COMMENT 'Account name',
  channel             STRING  COMMENT 'WHOLESALE / MARKETPLACE / BNPL',
  segment             STRING  COMMENT 'Department store / Boutique / Marketplace / BNPL partner',
  region              STRING  COMMENT 'Region',
  credit_terms_days   INT     COMMENT 'Standard payment terms in days',
  onboarded_date      DATE    COMMENT 'Account onboarding date'
) COMMENT 'AR customers: wholesale/B2B accounts + consumer settlement partners (marketplace/BNPL). Planted: 3 wholesale disputes + 1 slipping settlement partner.';

-- Grain: one row per supplier (~25). 2 suppliers drive the AP acceleration.
CREATE TABLE IF NOT EXISTS dim_supplier (
  supplier_id         STRING  COMMENT 'Supplier id e.g. SUP-01 (PK)',
  supplier_name       STRING  COMMENT 'Supplier name',
  category            STRING  COMMENT 'What they supply e.g. Outerwear, Fabric, Footwear',
  country             STRING  COMMENT 'Sourcing country',
  default_terms_days  INT     COMMENT 'Standard payment terms in days (e.g. 60)',
  early_pay_discount_pct DECIMAL(5,2) COMMENT 'Early-pay discount % if paid early',
  region              STRING  COMMENT 'Sourcing region'
) COMMENT 'Supplier dimension. Planted: 2 suppliers with shortened terms / tariff surcharge / expedited freight.';

-- ---------------------------------------------------------------------
-- FACTS
-- ---------------------------------------------------------------------

-- Grain: product x store x day (sparse — only combos with a sale)
CREATE TABLE IF NOT EXISTS fact_sales (
  sale_id             STRING  COMMENT 'Surrogate sale-line id',
  date_key            DATE    COMMENT 'FK -> dim_date.date_key',
  product_id          STRING  COMMENT 'FK -> dim_product.product_id',
  store_id            STRING  COMMENT 'FK -> dim_store.store_id',
  units_sold          INT     COMMENT 'Units sold',
  gross_sales_amt     DECIMAL(12,2) COMMENT 'Gross sales before discount',
  discount_amt        DECIMAL(12,2) COMMENT 'Discount / markdown amount',
  net_sales_amt       DECIMAL(12,2) COMMENT 'Net sales after discount',
  cogs_amt            DECIMAL(12,2) COMMENT 'Cost of goods sold'
) COMMENT 'Daily sell-through by SKU x location. Planted: Women'' Outerwear sell-through ~35% vs 70% plan.';

-- Grain: product x location x week (weekly on-hand snapshot)
CREATE TABLE IF NOT EXISTS fact_inventory_snapshot (
  snapshot_id         STRING  COMMENT 'Surrogate snapshot id',
  week_ending_date    DATE    COMMENT 'Saturday week-ending date of snapshot',
  product_id          STRING  COMMENT 'FK -> dim_product.product_id',
  store_id            STRING  COMMENT 'FK -> dim_store.store_id',
  on_hand_units       INT     COMMENT 'Units on hand',
  on_hand_cost        DECIMAL(12,2) COMMENT 'On-hand inventory at cost (cash trapped)',
  last_receipt_date   DATE    COMMENT 'Date of most recent receipt (drives aging)',
  days_on_hand        INT     COMMENT 'Days since last receipt',
  aging_bucket        STRING  COMMENT '0-30 / 31-60 / 61-90 / 90+',
  is_aged             BOOLEAN COMMENT 'True when aging_bucket = 90+',
  markdown_liability_amt DECIMAL(12,2) COMMENT 'Estimated markdown exposure on this stock'
) COMMENT 'Weekly inventory snapshot. Planted: ~$3.5M of Women'' Outerwear at cost in the 90+ aged bucket at the latest snapshot.';

-- Grain: one row per PO line
CREATE TABLE IF NOT EXISTS fact_purchase_orders (
  po_id               STRING  COMMENT 'Purchase order id',
  po_line_id          STRING  COMMENT 'PO line id (PK with po_id)',
  supplier_id         STRING  COMMENT 'FK -> dim_supplier.supplier_id',
  product_id          STRING  COMMENT 'FK -> dim_product.product_id',
  order_date          DATE    COMMENT 'PO order date',
  expected_receipt_date DATE  COMMENT 'Expected receipt date (future = still inbound)',
  status              STRING  COMMENT 'OPEN / RECEIVED / CANCELLED',
  ordered_units       INT     COMMENT 'Units ordered',
  committed_cost      DECIMAL(12,2) COMMENT 'Committed cash for this PO line',
  is_expedited        BOOLEAN COMMENT 'True if expedited replenishment',
  freight_mode        STRING  COMMENT 'OCEAN / AIR',
  tariff_surcharge_amt DECIMAL(12,2) COMMENT 'Tariff/freight surcharge added to this line'
) COMMENT 'Purchase orders. Planted: oversized FW25 outerwear buy with OPEN lines still inbound; expedited/air-freight + tariff-surcharge lines feeding AP.';

-- Grain: one row per supplier payment
CREATE TABLE IF NOT EXISTS fact_ap_payments (
  payment_id          STRING  COMMENT 'Payment id (PK)',
  supplier_id         STRING  COMMENT 'FK -> dim_supplier.supplier_id',
  po_id               STRING  COMMENT 'FK -> fact_purchase_orders.po_id',
  invoice_amt         DECIMAL(12,2) COMMENT 'Invoice amount',
  scheduled_pay_date  DATE    COMMENT 'Planned payment date per standard terms',
  actual_pay_date     DATE    COMMENT 'Actual payment date',
  terms_days          INT     COMMENT 'Terms applied on this payment (days)',
  days_early          INT     COMMENT 'scheduled_pay_date - actual_pay_date (positive = paid early)',
  early_pay_discount_offered_amt DECIMAL(12,2) COMMENT 'Early-pay discount available',
  early_pay_discount_captured_amt DECIMAL(12,2) COMMENT 'Early-pay discount actually captured',
  early_pay_discount_lost_amt DECIMAL(12,2) COMMENT 'Early-pay discount forfeited',
  surcharge_amt       DECIMAL(12,2) COMMENT 'Tariff/freight surcharge paid',
  expedite_freight_amt DECIMAL(12,2) COMMENT 'Expedited freight paid',
  cash_impact_vs_plan_amt DECIMAL(12,2) COMMENT 'Extra near-term cash out vs plan (early payment + surcharge + expedite + lost discount)',
  payment_category    STRING  COMMENT 'NORMAL / ACCELERATED'
) COMMENT 'Supplier payments (AP). Planted: ~$2.0M cash_impact_vs_plan from accelerated payments, lost discounts, tariff surcharge, expedited freight.';

-- Grain: one row per AR invoice
CREATE TABLE IF NOT EXISTS fact_ar_invoices (
  invoice_id          STRING  COMMENT 'Invoice id (PK)',
  customer_id         STRING  COMMENT 'FK -> dim_customer.customer_id',
  channel             STRING  COMMENT 'WHOLESALE / MARKETPLACE / BNPL',
  invoice_date        DATE    COMMENT 'Invoice issue date',
  due_date            DATE    COMMENT 'Payment due date',
  amount              DECIMAL(12,2) COMMENT 'Invoice amount',
  paid_date           DATE    COMMENT 'Date paid (NULL if unpaid)',
  status              STRING  COMMENT 'PAID / OPEN / DISPUTED / SLIPPED',
  dispute_flag        BOOLEAN COMMENT 'True if in dispute',
  dispute_reason      STRING  COMMENT 'short-ship / quality defect / pricing discrepancy (NULL if none)',
  expected_cash_date  DATE    COMMENT 'Currently expected cash date (may be after quarter-end)',
  cash_impact_vs_plan_amt DECIMAL(12,2) COMMENT 'Expected cash NOT arriving by 2026-12-31 vs plan',
  flipped_last_2wk    BOOLEAN COMMENT 'True if this invoice flipped to disputed in the last 2 weeks (velocity trigger)'
) COMMENT 'AR invoices. Planted: $1.7M wholesale disputes (3 customers) + $0.8M consumer settlement slippage = $2.5M collections shortfall; 2 flips in last 2 weeks.';

-- Grain: one row per day x cash category
CREATE TABLE IF NOT EXISTS fact_cash_ledger (
  ledger_date         DATE    COMMENT 'FK -> dim_date.date_key',
  cash_category       STRING  COMMENT 'SALES_RECEIPTS / WHOLESALE_COLLECTIONS / SUPPLIER_PAYMENTS / PAYROLL / RENT / OTHER_OPEX / TAX / CAPEX',
  cash_in_amt         DECIMAL(14,2) COMMENT 'Cash inflow for the category that day',
  cash_out_amt        DECIMAL(14,2) COMMENT 'Cash outflow for the category that day',
  net_cash_amt        DECIMAL(14,2) COMMENT 'cash_in_amt - cash_out_amt',
  running_cash_balance DECIMAL(14,2) COMMENT 'Cumulative cash balance to date'
) COMMENT 'Daily actual cash in/out by category — history that feeds ai_forecast(). Recent weeks trend down.';

-- Grain: one row per forecast_run x forecast_week x driver
CREATE TABLE IF NOT EXISTS fact_cash_forecast (
  forecast_run_date   DATE    COMMENT 'Date the forecast was produced (2026-10-22 prior run, 2026-11-05 current run)',
  forecast_week_ending DATE   COMMENT 'Week-ending date being forecast',
  driver              STRING  COMMENT 'BASELINE / INVENTORY_TRAP / COLLECTIONS_SLOWDOWN / AP_ACCELERATION',
  planned_cash_amt    DECIMAL(14,2) COMMENT 'Plan cash for the week (driver contribution)',
  projected_cash_amt  DECIMAL(14,2) COMMENT 'Projected cash for the week (driver contribution)',
  variance_amt        DECIMAL(14,2) COMMENT 'projected - planned (negative = shortfall)'
) COMMENT 'Rolling cash forecast by driver. At quarter-end 2026-12-31: plan $30M, projected $22M, variance -$8M split -3.5M/-2.5M/-2.0M. Prior run (2026-10-22) projected $25M so current run shows a ~$3M 2-week deterioration.';
