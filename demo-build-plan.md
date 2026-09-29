# Demo Build Plan — Office of the CFO: Retail Cash-Flow Shortfall (Genie)
### Company: Lakeview Retail (omnichannel apparel)

> **Status:** Building — synthetic data design LOCKED (2026-09-29); schema + DDL next
> **Workspace:** `serverless-stable-genie-cfo` — AWS Stable Serverless, us-west-2
> `https://fevm-serverless-stable-genie-cfo.cloud.databricks.com` (FEVM resource `01a0b63c-e377-7986-a7f5-96688553b7bb`, expires 2026-10-18)
> **Approach:** Build fresh (not adapted from the manufacturing golden demo).
> **Sync rule:** This `.md` is the source of truth. The "Demo Build Plan" tab in the blog doc mirrors it — when we edit one, we update the other.

## Scope decisions (locked)
- **Genie:** All 5 specialized agents + Genie One on top.
- **Capabilities:** `ai_forecast()` cash prediction · AI functions on unstructured docs · AI/BI dashboards · `/cash-shortfall-briefing` skill.

## Guiding principle
Every asset exists to make one moment of the story land (**Alert → Investigate → Evidence → Action → Scale**). If a table, metric, or agent doesn't feed the alert, the "why," the evidence, or the action, it's out. All numbers must reconcile across every asset (the CFO's "can I trust this?" test) — which is why the metric-views layer is central.

The story (retail): *quarter-end cash is projected below plan — not because the business is unprofitable, but because cash is trapped in unsold inventory and mistimed payments.* Decomposes into: inventory build-up + AP acceleration + collections slowdown.

---

## 0. Synthetic Data Design (LOCKED — 2026-09-29)
Aligned to the blog doc tab "Retail Cash-Flow Shortfall (Reframed UC)" — the three drivers below map 1:1 to the doc's Inventory & Markdown Cash Trap / Supplier Payment & Terms Variance (AP) / Collections & Settlement Slowdown.

**Company:** **Lakeview Retail** — omnichannel apparel (retail stores + ecommerce + wholesale).

**Scale:**
- ~24 months of **daily** history, anchored to a fixed demo clock (below)
- **40 stores + 3 DCs + 1 ecommerce channel** across 4 regions
- **~500 SKUs** across 5 departments (Women's, Men's, Kids, Footwear, Accessories)
- **~30 wholesale customers** (AR) · **~25 suppliers** (AP)

**Demo clock:** Fiscal **quarter-end = 2026-12-31**. **Alert as-of date = 2026-11-05** (~8 weeks out — the "still time to act" window). History = 2024-11 → 2026-11-05; `ai_forecast()` projects 2026-11-05 → 2026-12-31.

**The planted problem (what the alert dings):** Plan quarter-end cash **≈ $30M** → forecast **≈ $22M** = **$8M shortfall**, of which **~$3M deteriorated in the last 2 weeks** so the alert trips on *both* level and velocity. Business is profitable; cash is trapped + mistimed. Decomposes:

| Driver (doc name) | $ | Structured plant | Unstructured evidence |
|---|---|---|---|
| **Inventory & Markdown Cash Trap** | **$3.5M** | Women's Outerwear seasonal buy misses; sell-through ~35% vs. 70% plan; >90-day aged stock + markdown liability; **open POs still inbound** (future receipt dates, uncancelled) → "receipts don't slow down" | the oversized buy in `fact_purchase_orders` |
| **Collections & Settlement Slowdown** | **$2.5M** | **$1.7M wholesale disputes** (3 customers — short-ship, quality defect, pricing) + **$0.8M consumer settlement slippage** (marketplace/BNPL settlement window pushes cash *past* Dec 31 — booked, not yet cash); DSO spikes; 2 invoices flip to `disputed` in last 2 wks → velocity trigger | dispute emails + remittance notes |
| **Supplier Payment & Terms Variance (AP)** | **$2.0M** | lost early-pay discounts + expedited/air-freight replenishment + a tariff/freight surcharge; 2 suppliers' cash out ~30 days early | supplier contract PDFs + tariff/surcharge notice |

**Action levers (the `/cash-shortfall-briefing` maps to these — doc's named levers):** cancel open POs · reflow inventory · defer eligible payments · chase high-confidence collections.

**Reconciliation rule:** $3.5M + $2.5M + $2.0M = **$8.0M**; every metric view (`cash_vs_plan_variance`, `DSO`/`DPO`/`DIO`, `inventory_aged_pct`, `collections_at_risk`, `early_pay_discount_lost`) must trace to exactly these plants.

---

## 1. Structured data — raw tables

| Table | Grain | Feeds beat |
|---|---|---|
| `dim_date` | day | All |
| `dim_product` | dept / category / SKU | All |
| `dim_store` | store / DC / region / channel | All |
| `dim_customer` | wholesale / B2B account | AR |
| `dim_supplier` | supplier | AP |
| `fact_sales` | product × store × day | Sell-through (leading signal) |
| `fact_inventory_snapshot` | product × store × week (on-hand qty/cost, receipt date → aging) | Inventory cash trap (Evidence) |
| `fact_purchase_orders` | PO line (committed $, expected receipt, status) | Cash out (Investigate/Evidence) |
| `fact_ap_payments` | payment (supplier, terms, scheduled vs. actual, discount lost) | AP acceleration (Evidence) |
| `fact_ar_invoices` | invoice (customer, due/paid date, amount, dispute flag) | Collections slowdown (Evidence) |
| `fact_cash_ledger` | daily actual cash in/out by category | History feeding the forecast |
| `fact_cash_forecast` | week × driver (planned vs. projected) | The alert + Investigate |

## 2. Unstructured data + AI functions (the Evidence differentiator)
Retail equivalent of the golden demo's tariff-invoice PDFs. Generate a small doc set, parse live.

| Unstructured asset | AI function | What it proves |
|---|---|---|
| Customer dispute / collections emails & remittance notes | `ai_parse_document` + `ai_extract` | *Why* specific customers aren't paying (shipment/quality disputes) — the reason, not just the aging bucket |
| Supplier contracts / PO terms PDFs | `ai_parse_document` + `ai_extract` | Payment terms & early-pay discount clauses driving AP acceleration |
| Credit memos (optional) | `ai_extract` | Ties disputes to dollar impact |

*Verify current syntax/availability of `ai_parse_document`, `ai_extract`, `ai_query`, `ai_classify` against Databricks docs before building.*

## 3. ML / forecasting — "predict the cash flow"
- **Primary: `ai_forecast()`** (built-in time-series SQL function) on `fact_cash_ledger` → projected quarter-end cash + confidence interval. Low infra, high wow-factor. Powers the alert.
- **Wrapper: UC function `predict_cash_flow(segment)`** around `ai_forecast()` so Genie can call it by name.
- Heavier alternative (AutoML/MLflow) — likely overkill for a blog demo.

## 4. Metric views (governed semantic layer — Unity Catalog)
What makes every answer reconcile and earns CFO trust.
- **Cash:** `projected_quarter_end_cash`, `cash_vs_plan_variance`, `cash_forecast_deterioration_2wk`
- **Working-capital cycle:** `DSO`, `DPO`, `DIO`, `cash_conversion_cycle`
- **Inventory:** `sell_through_rate`, `weeks_of_supply`, `inventory_aged_pct`, `markdown_liability`
- **AR/AP:** `overdue_ar`, `collections_at_risk`, `ap_paid_vs_plan`, `early_pay_discount_lost`

## 5. Genie spaces / agents (All 5 + Genie One)
Genie One on top; each agent = a scoped Genie space over relevant tables + metric views.
- 🎯 **Cash Flow & Working Capital** (hero — the alert lands here)
- **Inventory & Markdown Intelligence**
- **Collections & AR (Receivables)**
- **Procurement & AP (Payables)**
- **Sales & Sell-Through Performance**

## 6. Genie skills
- **`/cash-shortfall-briefing`** — drafts the standardized CFO briefing + recommended actions (Action beat). Analogous to golden demo's `/cash-control-briefing`.

## 7. AI/BI dashboards
- **Executive Cash Control** — cash forecast vs. plan, CCC, driver waterfall. Primary Genie grounding / drill-down source.
- **Working Capital Detail** — AR aging, AP schedule, inventory aging by category/store.

## 8. Alert / scheduled task
- **Genie scheduled task** monitoring `projected_quarter_end_cash` vs. plan; fires "level + velocity" alert (`$X below plan, deteriorated $Y in 2 weeks`) with control limits (only pings when it matters). Timing target: ~8 weeks before quarter-end.

## 9. Action / integrations / documents
- Persisted **Genie Document** (the briefing) + optional **Excel/Drive export** to show the action loop leaving Databricks.

## 10. Publishable blog assets
- Synthetic data-gen script · Genie prompts · metric-view + AI-function SQL · `skills.md` · README/user guide · optional recorded demo video.

---

## Story-beat → asset map

| Beat | Assets |
|---|---|
| **Alert** | scheduled task → `projected_quarter_end_cash` metric → `ai_forecast()` → `fact_cash_forecast` |
| **Investigate** | Cash Flow & WC agent + metric views (variance decomposed into inventory / AP / AR) |
| **Evidence** | AI functions on dispute emails & supplier PDFs; drill to `fact_inventory_snapshot`, `fact_ar_invoices`, `fact_ap_payments`, `fact_purchase_orders` |
| **Action** | `/cash-shortfall-briefing` skill → Genie Document → export |
| **Scale** | scheduled task re-runs on 13-week forecast; store convo as skill/alert |

## Build order (dependencies)
1. Catalog/schema + data-model DDL
2. Metric views
3. `ai_forecast()` + `predict_cash_flow()` + `fact_cash_forecast`
4. Unstructured docs + AI-function pipeline
5. Genie agents (5) + Genie One
6. `/cash-shortfall-briefing` skill
7. AI/BI dashboards
8. Alert / scheduled task
9. Blog assets

## Open items / to decide next
- ~~Synthetic data design~~ — **LOCKED 2026-09-29** (see §0).
- ~~Retail sub-vertical~~ — **omnichannel apparel** (Lakeview Retail).
- Confirm current Databricks syntax/availability before building each layer: `ai_forecast`, `ai_parse_document`/`ai_extract`, metric views, Genie scheduled tasks.
- Catalog/schema naming: catalog `serverless_stable_genie_cfo_catalog` exists; propose schema `lakeview_retail` (confirm on build).
