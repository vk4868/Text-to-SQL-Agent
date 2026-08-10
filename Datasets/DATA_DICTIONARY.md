# Word-to-Insights synthetic retail dataset

## Purpose

A compact but professionally structured BigQuery dataset for demonstrating a
schema-aware SQL and business-insights agent. The dataset contains exactly
1,500 rows across three related tables, while using design decisions that can
be applied to much larger BigQuery tables.

## Table relationships

- `fact_sales.customer_id` → `dim_customers.customer_id`
- `fact_sales.product_id` → `dim_products.product_id`

## Row counts

- `fact_sales`: 1,260 rows
- `dim_customers`: 200 rows
- `dim_products`: 40 rows
- Total: 1,500 rows

## Table grains

### dim_customers

One row per customer.

Primary key: `customer_id`

### dim_products

One row per product.

Primary key: `product_id`

### fact_sales

One row per transaction line.

Primary key: `sale_id`

Foreign keys: `customer_id`, `product_id`

## Financial formulas

- `gross_revenue = unit_price × quantity`
- `discount_amount = gross_revenue × discount_pct`
- `net_revenue = gross_revenue - discount_amount`
- `tax_amount = net_revenue × branch tax rate`
- `total_price = net_revenue + tax_amount`
- `cost_amount = product unit_cost × quantity`
- `profit_amount = net_revenue - cost_amount`

## Embedded analytical patterns

The data is deterministic and includes deliberate patterns so that analysis is
more meaningful than random totals:

- Sales seasonality is stronger in November and December.
- Electronics receive an additional holiday-period uplift.
- Beverages and fruit are stronger during summer months.
- Stationery is stronger around August and September.
- `Classic Notebook` declines during 2025.
- `Premium Shampoo` grows during 2025.
- Branches have different category preferences.
- Members purchase more often, receive more discounts, and earn reward points.
- December uses double reward points for members.

## BigQuery scalability design

For a larger production-style version:

- Partition `fact_sales` by `sale_date`.
- Cluster `fact_sales` by `customer_id`, `product_id`, and `branch_id`.
- Keep project and dataset identifiers configurable.
- Use dry runs and maximum-bytes-billed controls before execution.
- Avoid returning unlimited raw rows to the LLM.
- Perform aggregations in BigQuery rather than loading full tables into Python.
- Discover schemas through BigQuery metadata instead of hard-coding columns.

The small row count is for low-cost demonstration. The agent workflow and SQL
architecture should not depend on the row count.
