# Evaluation report

- Cases: **45** · Passed: **31** (69%)
- Model: `gemma4:latest`
- Run: 2026-10-06T12:06:58.911210+00:00
- Mean latency: 19.2s · p95: 26.5s
- Mean tokens per question: 2419
- Cases needing a repair: 0 of 45

## Provenance

- Git head: `a9514870f8c6104969af99d127d8fd8c2fad16c5`
- Branch: `feature/verified-numerical-answers`
- Dirty files: 4
- Uncommitted diff sha256: `ab616776669fc4d8abd5138bce2b026a58e11084b48642414975d78e3ce6e514`

### Model

| Field | Value |
|---|---|
| Tag | gemma4:latest |
| Digest | c6eb396dbd5992bbe3f5cdb947e8bbc0ee413d7c17e2beaae69f5d569cf982eb |
| Family | gemma4 |
| Parameter size | 8.0B |
| Quantisation | Q4_K_M |
| Ollama server | 0.34.3 |
| Temperature | 0.1 |

### Caps

| Setting | Value |
|---|---|
| `MAX_QUERY_BYTES` | 100000000 |
| `MAX_RESULT_ROWS` | 100 |
| `QUERY_TIMEOUT_SECONDS` | 30 |
| `MAX_SQL_REPAIR_ATTEMPTS` | 2 |
| `MAX_ANALYSIS_ROWS` | 50 |
| `SCHEMA_CACHE_TTL_SECONDS` | 300.0 |
| `OLLAMA_TIMEOUT_SECONDS` | 120.0 |

### Dataset reconciliation

| Table | Figure | CSV | BigQuery | Match |
|---|---|---|---|---|
| `fact_sales` | row_count | 1260 | 1260 | yes |
| `fact_sales` | distinct_sale_id | 1260 | 1260 | yes |
| `fact_sales` | sum_net_revenue | 47017.30 | 47017.30 | yes |
| `fact_sales` | sum_profit_amount | 26409.50 | 26409.50 | yes |
| `fact_sales` | sum_quantity | 6148 | 6148 | yes |
| `fact_sales` | min_sale_date | 2024-01-02 | 2024-01-02 | yes |
| `fact_sales` | max_sale_date | 2025-12-31 | 2025-12-31 | yes |
| `dim_products` | row_count | 40 | 40 | yes |
| `dim_products` | sum_list_price | 414.60 | 414.60 | yes |
| `dim_products` | sum_unit_cost | 170.50 | 170.50 | yes |
| `dim_customers` | row_count | 200 | 200 | yes |
| `dim_customers` | member_count | 142 | 142 | yes |

### Categorical column reconciliation

| Table | Column | Distinct (CSV/BQ) | Values match |
|---|---|---|---|
| `fact_sales` | `sale_id` | 1260/1260 | yes |
| `fact_sales` | `sale_date` | 593/593 | yes |
| `fact_sales` | `branch_id` | 4/4 | yes |
| `fact_sales` | `branch_city` | 4/4 | yes |
| `fact_sales` | `branch_state` | 4/4 | yes |
| `fact_sales` | `branch_region` | 4/4 | yes |
| `fact_sales` | `customer_id` | 199/199 | yes |
| `fact_sales` | `product_id` | 40/40 | yes |
| `fact_sales` | `payment_method` | 4/4 | yes |
| `fact_sales` | `sales_channel` | 3/3 | yes |
| `fact_sales` | `promotion_type` | 4/4 | yes |
| `dim_products` | `product_id` | 40/40 | yes |
| `dim_products` | `product_name` | 40/40 | yes |
| `dim_products` | `category` | 6/6 | yes |
| `dim_products` | `subcategory` | 28/28 | yes |
| `dim_products` | `brand` | 20/20 | yes |
| `dim_products` | `launch_date` | 39/39 | yes |
| `dim_customers` | `customer_id` | 200/200 | yes |
| `dim_customers` | `customer_name` | 200/200 | yes |
| `dim_customers` | `membership_status` | 2/2 | yes |
| `dim_customers` | `customer_segment` | 3/3 | yes |
| `dim_customers` | `gender` | 4/4 | yes |
| `dim_customers` | `age_group` | 5/5 | yes |
| `dim_customers` | `signup_date` | 183/183 | yes |
| `dim_customers` | `home_city` | 8/8 | yes |
| `dim_customers` | `home_state` | 8/8 | yes |
| `dim_customers` | `acquisition_channel` | 5/5 | yes |

All reconciliation figures match: yes

### CSV hashes

| File | Rows | sha256 |
|---|---|---|
| `dim_customers.csv` | 200 | `5b234bdff1ec07048d5c451d1af154605cdadb6768e9c95a9572ea9894b7e40a` |
| `dim_products.csv` | 40 | `34975f62dc1e9fce882130c9e88e2ce8ad901f175018abbea5b9ab19451ac300` |
| `fact_sales.csv` | 1260 | `f987ca7964253b5a360a7681705f5afb167641b1d77618dd85b2b6af39048226` |

- Warm-up: question `How many products are in the catalogue?` · status success · 19.3s
- Run log: `/private/tmp/claude-501/-Users-vineetkumar-Documents-Work-Projects-Konkrd-Feature/044ea6fb-824a-4970-a187-65009ea2732e/scratchpad/baseline/runs.jsonl`

### Models loaded before each trial

| Trial | Model tag | Loaded |
|---|---|---|
| 1 | `gemma4:latest` | yes |
| 2 | `gemma4:latest` | yes |
| 3 | `gemma4:latest` | yes |

## Latency

Pooled: mean 19.2s · median 20.0s · p95 26.5s

| Trial | Passed | Mean | Median | p95 | Tokens mean |
|---|---|---|---|---|---|
| 1 | 9/15 | 18.9s | 20.4s | 21.5s | 2386 |
| 2 | 11/15 | 19.8s | 20.0s | 28.8s | 2445 |
| 3 | 11/15 | 18.7s | 19.5s | 22.5s | 2427 |

| Node | Calls | Mean | p95 |
|---|---|---|---|
| `get_schema` | 45 | 0.14s | 1.93s |
| `generate_sql` | 45 | 2.60s | 3.07s |
| `execute_sql` | 45 | 1.61s | 2.83s |
| `analyze_result` | 45 | 14.80s | 19.24s |

| LLM role | Calls | Mean | p95 | Mean tokens |
|---|---|---|---|---|
| `sql_generation` | 45 | 2.60s | 3.07s | 921 |
| `result_analysis` | 42 | 15.85s | 19.24s | 1605 |

Outcomes: success: 45

Failures: rejected: 0, error: 0, unavailable: 0

Warehouse (final execution per run): billed 325058560 B · processed 410689 B · cache hits 21 · SQL executions (all pipeline runs, incl. repairs) 45

## By metric

Downstream metrics are scored only on successful runs; their denominators exclude failed/rejected runs. Overall pass rate uses all 45 case-runs.

| Metric | Passed | Rate | What it proves |
|---|---|---|---|
| `guardrail_pass` | 45/45 | 100% | not refused by its own safety layer |
| `executed` | 45/45 | 100% | produced a result |
| `table_grounding` | 45/45 | 100% | used the expected tables |
| `execution_accuracy` | 42/45 | 93% | result set matches the reference query |
| `repair_efficiency` | 45/45 | 100% | stayed within the repair budget |
| `analysis_grounding` | 33/45 | 73% | every figure in the prose is derivable |

### Supplementary (not part of the pass rate)

| Metric | Passed | Rate |
|---|---|---|
| `execution_coverage` | 45/45 | 100% |
| `sql_accuracy_any_outcome` | 42/45 | 93% |

`sql_accuracy_any_outcome` scores the final SQL on every run whose execution succeeded, whatever happened to the analysis.

## By case

| Case | Trial | Category | guardrail pass | executed | table grounding | execution accuracy | repair efficiency | analysis grounding | Repairs | Tokens | Time |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `net_sales_by_month_2025` | 1 | time_series_aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | 0 | 2927 | 20.4s |
| `total_net_sales_2025` | 1 | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2304 | 18.4s |
| `total_profit_2025` | 1 | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2113 | 15.6s |
| `profit_by_category_2025` | 1 | join | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2369 | 18.8s |
| `top_5_products_by_revenue_2025` | 1 | ranking | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2930 | 25.7s |
| `revenue_by_region_2025` | 1 | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | 0 | 2583 | 21.2s |
| `electronics_november_december_2025` | 1 | join | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2460 | 20.7s |
| `sales_by_membership_status_2025` | 1 | join | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | 0 | 2420 | 18.8s |
| `transaction_count_2025` | 1 | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2162 | 18.0s |
| `average_discount_by_promotion_2025` | 1 | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2335 | 18.7s |
| `profit_margin_by_category_2025` | 1 | ratio | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | 0 | 2693 | 21.4s |
| `sales_by_channel_2025` | 1 | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | 0 | 2579 | 20.7s |
| `quantity_sold_by_category_2025` | 1 | join | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | 0 | 2682 | 21.5s |
| `best_month_for_profit_2025` | 1 | ranking | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2337 | 20.6s |
| `no_sales_in_1999` | 1 | empty_result | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 892 | 3.4s |
| `net_sales_by_month_2025` | 2 | time_series_aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | 0 | 3279 | 28.8s |
| `total_net_sales_2025` | 2 | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2173 | 16.7s |
| `total_profit_2025` | 2 | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2291 | 19.4s |
| `profit_by_category_2025` | 2 | join | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2916 | 29.1s |
| `top_5_products_by_revenue_2025` | 2 | ranking | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2867 | 23.4s |
| `revenue_by_region_2025` | 2 | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | 0 | 2491 | 18.7s |
| `electronics_november_december_2025` | 2 | join | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2316 | 17.6s |
| `sales_by_membership_status_2025` | 2 | join | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2571 | 20.7s |
| `transaction_count_2025` | 2 | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2161 | 16.0s |
| `average_discount_by_promotion_2025` | 2 | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2515 | 20.1s |
| `profit_margin_by_category_2025` | 2 | ratio | ✅ | ✅ | ✅ | ❌ | ✅ | ✅ | 0 | 2765 | 25.1s |
| `sales_by_channel_2025` | 2 | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | 0 | 2560 | 20.0s |
| `quantity_sold_by_category_2025` | 2 | join | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2552 | 20.4s |
| `best_month_for_profit_2025` | 2 | ranking | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2339 | 18.2s |
| `no_sales_in_1999` | 2 | empty_result | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 878 | 3.4s |
| `net_sales_by_month_2025` | 3 | time_series_aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | 0 | 3085 | 22.5s |
| `total_net_sales_2025` | 3 | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2143 | 15.4s |
| `total_profit_2025` | 3 | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2131 | 14.9s |
| `profit_by_category_2025` | 3 | join | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2981 | 26.5s |
| `top_5_products_by_revenue_2025` | 3 | ranking | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2766 | 21.2s |
| `revenue_by_region_2025` | 3 | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | 0 | 2460 | 18.0s |
| `electronics_november_december_2025` | 3 | join | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2328 | 18.0s |
| `sales_by_membership_status_2025` | 3 | join | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2427 | 19.0s |
| `transaction_count_2025` | 3 | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2227 | 17.1s |
| `average_discount_by_promotion_2025` | 3 | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2491 | 20.2s |
| `profit_margin_by_category_2025` | 3 | ratio | ✅ | ✅ | ✅ | ❌ | ✅ | ❌ | 0 | 2838 | 21.9s |
| `sales_by_channel_2025` | 3 | aggregation | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2571 | 20.7s |
| `quantity_sold_by_category_2025` | 3 | join | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | 0 | 2775 | 22.0s |
| `best_month_for_profit_2025` | 3 | ranking | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 2309 | 19.5s |
| `no_sales_in_1999` | 3 | empty_result | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | 0 | 878 | 3.5s |

## Guardrails

**19/19** adversarial queries refused, every one before a byte was scanned.

| Refused at stage | Cases |
|---|---|
| `result_limit` | 1 |
| `table_access` | 7 |
| `validation` | 11 |

## Failures

### `net_sales_by_month_2025` (trial 1)

> What were total net sales by month in 2025?

- **analysis_grounding** — ungrounded figures: ['6,261.05', '8,977.44']

Agent SQL:

```sql
SELECT
    FORMAT_DATE('%Y-%m', sale_date) AS sales_month,
    SUM(net_revenue) AS total_net_sales
FROM
    `your-project-id.business_insights.fact_sales`
WHERE
    sale_date BETWEEN DATE('2025-01-01') AND DATE('2025-12-31')
GROUP BY
    sales_month
ORDER BY
    sales_month
```

Reference SQL:

```sql
SELECT
  FORMAT_DATE('%Y-%m', sale_date) AS month,
  SUM(net_revenue) AS total_net_sales
FROM `your-project-id.business_insights.fact_sales`
WHERE sale_date BETWEEN '2025-01-01' AND '2025-12-31'
GROUP BY month
ORDER BY month
```

### `revenue_by_region_2025` (trial 1)

> How did net revenue break down by branch region in 2025?

- **analysis_grounding** — ungrounded figures: ['14,544.01', '10,256.64']

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

### `sales_by_membership_status_2025` (trial 1)

> How did net revenue compare between members and non-members in 2025?

- **analysis_grounding** — ungrounded figures: ['4.5']

Agent SQL:

```sql
SELECT
    t1.membership_status,
    SUM(t2.net_revenue) AS total_net_revenue
FROM
    `your-project-id.business_insights.dim_customers` AS t1
INNER JOIN
    `your-project-id.business_insights.fact_sales` AS t2
ON
    t1.customer_id = t2.customer_id
WHERE
    t2.sale_date BETWEEN DATE('2025-01-01') AND DATE('2025-12-31')
GROUP BY
    t1.membership_status
```

Reference SQL:

```sql
SELECT
  c.membership_status,
  SUM(f.net_revenue) AS total_net_revenue
FROM `your-project-id.business_insights.fact_sales` AS f
JOIN `your-project-id.business_insights.dim_customers` AS c
  ON f.customer_id = c.customer_id
WHERE f.sale_date BETWEEN '2025-01-01' AND '2025-12-31'
GROUP BY c.membership_status
```

### `profit_margin_by_category_2025` (trial 1)

> What was the profit margin as a percentage of net revenue for each product category in 2025?

- **execution_accuracy** — row count 6 vs 6; missing e.g. (Electronics, 58.2); unexpected e.g. (Beverages, 0.5)

Agent SQL:

```sql
SELECT
    t2.category,
    CAST(SUM(t1.profit_amount) AS NUMERIC) / NULLIF(SUM(t1.net_revenue), 0) AS profit_margin_percentage
FROM
    your-project-id.business_insights.fact_sales AS t1
INNER JOIN
    your-project-id.business_insights.dim_products AS t2 ON t1.product_id = t2.product_id
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

### `sales_by_channel_2025` (trial 1)

> What was the split of net revenue by sales channel in 2025?

- **analysis_grounding** — ungrounded figures: ['24,700.65']

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

### `quantity_sold_by_category_2025` (trial 1)

> How many units were sold in each product category in 2025?

- **analysis_grounding** — ungrounded figures: ['3,648', '2,199']

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

### `net_sales_by_month_2025` (trial 2)

> What were total net sales by month in 2025?

- **analysis_grounding** — ungrounded figures: ['7,647.64', '5,361.05']

Agent SQL:

```sql
SELECT
    FORMAT_DATE('%Y-%m', sale_date) AS sale_month,
    SUM(net_revenue) AS total_net_sales
FROM
    `your-project-id.business_insights.fact_sales`
WHERE
    sale_date BETWEEN DATE('2025-01-01') AND DATE('2025-12-31')
GROUP BY
    sale_month
ORDER BY
    sale_month
```

Reference SQL:

```sql
SELECT
  FORMAT_DATE('%Y-%m', sale_date) AS month,
  SUM(net_revenue) AS total_net_sales
FROM `your-project-id.business_insights.fact_sales`
WHERE sale_date BETWEEN '2025-01-01' AND '2025-12-31'
GROUP BY month
ORDER BY month
```

### `revenue_by_region_2025` (trial 2)

> How did net revenue break down by branch region in 2025?

- **analysis_grounding** — ungrounded figures: ['169.23', '14,544.01', '60%']

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

### `profit_margin_by_category_2025` (trial 2)

> What was the profit margin as a percentage of net revenue for each product category in 2025?

- **execution_accuracy** — row count 6 vs 6; missing e.g. (Electronics, 58.2); unexpected e.g. (Personal Care, 0.5)

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

### `sales_by_channel_2025` (trial 2)

> What was the split of net revenue by sales channel in 2025?

- **analysis_grounding** — ungrounded figures: ['24,700.65']

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

### `net_sales_by_month_2025` (trial 3)

> What were total net sales by month in 2025?

- **analysis_grounding** — ungrounded figures: ['$24,792.65']

Agent SQL:

```sql
SELECT
    FORMAT_DATE('%Y-%m', sale_date) AS sales_month,
    SUM(net_revenue) AS total_net_sales
FROM
    `your-project-id.business_insights.fact_sales`
WHERE
    sale_date BETWEEN DATE('2025-01-01') AND DATE('2025-12-31')
GROUP BY
    sales_month
ORDER BY
    sales_month
```

Reference SQL:

```sql
SELECT
  FORMAT_DATE('%Y-%m', sale_date) AS month,
  SUM(net_revenue) AS total_net_sales
FROM `your-project-id.business_insights.fact_sales`
WHERE sale_date BETWEEN '2025-01-01' AND '2025-12-31'
GROUP BY month
ORDER BY month
```

### `revenue_by_region_2025` (trial 3)

> How did net revenue break down by branch region in 2025?

- **analysis_grounding** — ungrounded figures: ['158.83', '14,544.01']

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

### `profit_margin_by_category_2025` (trial 3)

> What was the profit margin as a percentage of net revenue for each product category in 2025?

- **execution_accuracy** — row count 6 vs 6; missing e.g. (Electronics, 58.2); unexpected e.g. (Beverages, 0.5)
- **analysis_grounding** — ungrounded figures: ['5.4']

Agent SQL:

```sql
SELECT
    t2.category,
    CAST(SUM(t1.profit_amount) AS NUMERIC) / NULLIF(SUM(t1.net_revenue), 0) AS profit_margin_percentage
FROM
    your-project-id.business_insights.fact_sales AS t1
INNER JOIN
    your-project-id.business_insights.dim_products AS t2 ON t1.product_id = t2.product_id
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

### `quantity_sold_by_category_2025` (trial 3)

> How many units were sold in each product category in 2025?

- **analysis_grounding** — ungrounded figures: ['2,199']

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
