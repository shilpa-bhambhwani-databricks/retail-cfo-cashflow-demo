# Office of the CFO — Retail Cash-Flow Shortfall (Genie demo)
This is a test-SMB
A Databricks **Genie** demo for the "AI coworker for the Office of the CFO" blog
storyline, retail-focused cut. **Company: Lakeview Retail** (omnichannel apparel).

**The story (Alert → Investigate → Evidence → Action → Scale):** quarter-end cash
is projected below plan — not because the business is unprofitable, but because
cash is trapped in unsold seasonal inventory and mistimed AP/AR. Genie watches the
rolling cash forecast, alerts the CFO ~8 weeks before quarter-end, explains *why*
across every silo, shows the evidence, and drafts the action.

The planted shortfall (**$8M**, plan ≈$30M → forecast ≈$22M) decomposes into three
drivers that map 1:1 to the blog use-case doc:
- **Inventory & Markdown Cash Trap** — $3.5M
- **Collections & Settlement Slowdown** — $2.5M
- **Supplier Payment & Terms Variance (AP)** — $2.0M

## What's here

| Path | Contents |
|---|---|
| `demo-build-plan.md` | Source-of-truth build plan + locked synthetic-data design (§0) |
| `sql/` | Schema DDL, metric views, `ai_forecast()`, AI-function pipeline |
| `data/` | Synthetic data generation scripts |
| `genie/` | Genie space configs + prompts (5 agents + Genie One) |
| `skills/` | `/cash-shortfall-briefing` skill |
| `docs/` | Unstructured evidence (dispute emails, supplier contracts) |
| `dashboards/` | AI/BI (Lakeview) dashboard definitions |

## Workspace

Built in FEVM AWS Stable Serverless: `fevm-serverless-stable-genie-cfo`
(us-west-2). CLI profile: `fe-vm-genie-cfo`. Catalog:
`serverless_stable_genie_cfo_catalog`, schema `lakeview_retail`.
