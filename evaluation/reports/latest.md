# Evaluation report

- Cases: **15** · Passed: **11** (73%)
- Model: `gemma4:latest`
- Run: 2026-08-25T02:20:44.615692+00:00
- Mean latency: 21.0s · p95: 25.2s
- Mean tokens per question: 2404
- Cases needing a repair: 0 of 15

## By metric

| Metric | Passed | Rate | What it proves |
|---|---|---|---|
| `guardrail_pass` | 15/15 | 100% | not refused by its own safety layer |
| `executed` | 15/15 | 100% | produced a result |
| `table_grounding` | 15/15 | 100% | used the expected tables |
| `execution_accuracy` | 14/15 | 93% | result set matches the reference query |
| `repair_efficiency` | 15/15 | 100% | stayed within the repair budget |
| `analysis_grounding` | 12/15 | 80% | every figure in the prose is derivable |

## By case

| Case | Category | guardrail pass | executed | table grounding | execution accuracy | repair efficiency | analysis grounding | Repairs | Tokens | Time |
|---|---|---|---|---|---|---|---|---|---|---|
| `net_sales_by_month_2025` | time_series_aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 3155 | 25.2s |
| `total_net_sales_2025` | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2316 | 18.1s |
| `total_profit_2025` | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2152 | 15.9s |
| `profit_by_category_2025` | join | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2382 | 17.4s |
| `top_5_products_by_revenue_2025` | ranking | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2755 | 20.6s |
| `revenue_by_region_2025` | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | 0 | 2599 | 20.1s |
| `electronics_november_december_2025` | join | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2482 | 19.3s |
| `sales_by_membership_status_2025` | join | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2375 | 17.7s |
| `transaction_count_2025` | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2253 | 56.5s |
| `average_discount_by_promotion_2025` | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2280 | 19.0s |
| `profit_margin_by_category_2025` | ratio | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | 0 | 2803 | 21.5s |
| `sales_by_channel_2025` | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | 0 | 2569 | 21.8s |
| `quantity_sold_by_category_2025` | join | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | 0 | 2751 | 22.4s |
| `best_month_for_profit_2025` | ranking | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2293 | 16.6s |
| `no_sales_in_1999` | empty_result | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 894 | 3.3s |

## Guardrails

**19/19** adversarial queries refused, every one before a byte was scanned.

| Refused at stage | Cases |
|---|---|
| `result_limit` | 1 |
| `table_access` | 7 |
| `validation` | 11 |

## Failures

### `revenue_by_region_2025`

> How did net revenue break down by branch region in 2025?

- **analysis_grounding** — ungrounded figures: ['58%']

Agent SQL:

```sql
SELECT
    t1.branch_region,
    SUM(t1.net_revenue) AS total_net_revenue
FROM
    `your-project-id.business_insights.fact_sales` AS t1
WHERE
    t1.sale_date BETWEEN DATE('2025-01-01') AND DATE('2025-12-31')
GROUP BY 1
```

Reference SQL:

```sql
SELECT
  branch_region,
  SUM(net_revenue) AS total_net_revenue
FROM `your-project-id.business_insights.fact_sales`
WHERE sale_date BETWEEN '2025-01-01' AND '2025-12-31'
GROUP BY branch_region
```

### `profit_margin_by_category_2025`

> What was the profit margin as a percentage of net revenue for each product category in 2025?

- **execution_accuracy** — row count 6 vs 6; missing e.g. (Personal Care, 54.2); unexpected e.g. (Personal Care, 0.5)

Agent SQL:

```sql
SELECT
    t2.category,
    CAST(SUM(t1.profit_amount) AS NUMERIC) / NULLIF(SUM(t1.net_revenue), 0) AS profit_margin_percentage
FROM
    `your-project-id.business_insights.fact_sales` AS t1
INNER JOIN
    `your-project-id.business_insights.dim_products` AS t2 ON t1.product_id = t2.product_id
WHERE
    t1.sale_date BETWEEN DATE('2025-01-01') AND DATE('2025-12-31')
GROUP BY
    t2.category
```

Reference SQL:

```sql
SELECT
  p.category,
  SAFE_DIVIDE(SUM(f.profit_amount), SUM(f.net_revenue)) * 100
    AS profit_margin_pct
FROM `your-project-id.business_insights.fact_sales` AS f
JOIN `your-project-id.business_insights.dim_products` AS p
  ON f.product_id = p.product_id
WHERE f.sale_date BETWEEN '2025-01-01' AND '2025-12-31'
GROUP BY p.category
```

### `sales_by_channel_2025`

> What was the split of net revenue by sales channel in 2025?

- **analysis_grounding** — ungrounded figures: ['48%', '$24,700.65']

Agent SQL:

```sql
SELECT
    t1.sales_channel,
    SUM(t1.net_revenue) AS total_net_revenue
FROM
    `your-project-id.business_insights.fact_sales` AS t1
WHERE
    t1.sale_date BETWEEN DATE('2025-01-01') AND DATE('2025-12-31')
GROUP BY 1
```

Reference SQL:

```sql
SELECT
  sales_channel,
  SUM(net_revenue) AS total_net_revenue
FROM `your-project-id.business_insights.fact_sales`
WHERE sale_date BETWEEN '2025-01-01' AND '2025-12-31'
GROUP BY sales_channel
```

### `quantity_sold_by_category_2025`

> How many units were sold in each product category in 2025?

- **analysis_grounding** — ungrounded figures: ['2,890', '2,209']

Agent SQL:

```sql
SELECT
    t2.category,
    SUM(t1.quantity) AS total_units_sold
FROM
    `your-project-id.business_insights.fact_sales` AS t1
INNER JOIN
    `your-project-id.business_insights.dim_products` AS t2 ON t1.product_id = t2.product_id
WHERE
    t1.sale_date BETWEEN DATE('2025-01-01') AND DATE('2025-12-31')
GROUP BY
    t2.category
```

Reference SQL:

```sql
SELECT
  p.category,
  SUM(f.quantity) AS total_quantity
FROM `your-project-id.business_insights.fact_sales` AS f
JOIN `your-project-id.business_insights.dim_products` AS p
  ON f.product_id = p.product_id
WHERE f.sale_date BETWEEN '2025-01-01' AND '2025-12-31'
GROUP BY p.category
```
