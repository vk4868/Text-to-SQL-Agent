# Dataset for testing the text-to-SQL agent

**Status: LIVE and verified.** BigQuery connector is authorized (billing project `sql-bigquery-502206`) and a test join query has been run successfully against real data.

**Source:** `bigquery-public-data.thelook_ecommerce` — Google's public BigQuery dataset (fictitious e-commerce clothing store "TheLook", built by the Looker team). Real relational structure, free to query, no download needed. Kaggle itself is blocked by this sandbox's network allowlist, so this was used instead of a Kaggle CSV — same use case, genuine multi-table sales data with clean foreign keys, and it fits your project's name directly.

## Tables (4, chosen for joins) — schema confirmed via `get_table_info`

The full dataset has 7 tables; these 4 are the standard subset used for sales/order analysis and join practice:

| Table | Rows | Grain | Columns |
|---|---|---|---|
| `users` | 100,000 | 1 row per customer | `id` (PK, INT), `first_name`, `last_name`, `email`, `age` (INT), `gender`, `state`, `street_address`, `postal_code`, `city`, `country`, `latitude`, `longitude` (FLOAT), `traffic_source`, `created_at` (TIMESTAMP), `user_geom` (GEOGRAPHY) |
| `orders` | 125,593 | 1 row per order | `order_id` (PK, INT), `user_id` (FK → users.id), `status`, `gender`, `created_at`, `returned_at`, `shipped_at`, `delivered_at` (TIMESTAMP), `num_of_item` (INT) |
| `order_items` | 182,333 | 1 row per line item | `id` (PK, INT), `order_id` (FK → orders.order_id), `user_id` (FK → users.id), `product_id` (FK → products.id), `inventory_item_id` (INT), `status`, `created_at`, `shipped_at`, `delivered_at`, `returned_at` (TIMESTAMP), `sale_price` (FLOAT) |
| `products` | 29,120 | 1 row per SKU | `id` (PK, INT), `cost` (FLOAT), `category`, `name`, `brand`, `retail_price` (FLOAT), `department`, `sku`, `distribution_center_id` (INT) |

## Relationships

```
users (1) ───< orders (1) ───< order_items (M) >─── products (1)
   id           user_id           order_id, product_id        id
```

`orders` ↔ `order_items` is 1-to-many (an order has multiple line items). `order_items` ↔ `products` and `order_items` ↔ `users` are many-to-one. This gives you real 2-way, 3-way, and 4-way join scenarios.

## Example queries to sanity-test the agent

```sql
-- Revenue by product category, last 90 days
SELECT p.category, SUM(oi.sale_price) AS revenue, COUNT(*) AS items_sold
FROM `bigquery-public-data.thelook_ecommerce.order_items` oi
JOIN `bigquery-public-data.thelook_ecommerce.products` p ON oi.product_id = p.id
WHERE oi.created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 90 DAY)
GROUP BY p.category
ORDER BY revenue DESC;

-- Top 10 customers by lifetime spend
SELECT u.id, u.first_name, u.last_name, u.email, SUM(oi.sale_price) AS lifetime_spend
FROM `bigquery-public-data.thelook_ecommerce.order_items` oi
JOIN `bigquery-public-data.thelook_ecommerce.orders` o ON oi.order_id = o.order_id
JOIN `bigquery-public-data.thelook_ecommerce.users` u ON o.user_id = u.id
WHERE oi.status != 'Cancelled'
GROUP BY u.id, u.first_name, u.last_name, u.email
ORDER BY lifetime_spend DESC
LIMIT 10;

-- Order fulfillment funnel by state
SELECT u.state, o.status, COUNT(*) AS order_count
FROM `bigquery-public-data.thelook_ecommerce.orders` o
JOIN `bigquery-public-data.thelook_ecommerce.users` u ON o.user_id = u.id
GROUP BY u.state, o.status
ORDER BY u.state, order_count DESC;
```

## Connecting the agent

The Google Cloud BigQuery connector is authorized and working (billing project `sql-bigquery-502206`). It exposes:

- `list_dataset_ids`, `get_dataset_info`, `list_table_ids`, `get_table_info` — schema discovery (the "get the schema" step of your agent)
- `execute_sql` / `execute_sql_readonly` — run the generated query and return results (prefer the readonly variant for a query agent — it blocks INSERT/UPDATE/DELETE)

No project setup or data loading needed — point queries at `bigquery-public-data.thelook_ecommerce.<table>`, billed against `sql-bigquery-502206`. Row counts (100K users, 125K orders, 182K order items, 29K products) are large enough for a realistic business-scenario test, not a toy dataset.

**Verified:** ran the category-revenue query above live — top category is Outerwear & Coats at ~$1.35M revenue / 9,128 items sold.
