RELATIONSHIPS : list[str] = [
    (
        "fact_sales.customer_id = "
        "dim_customers.customer_id" 
    ),
    (
        "fact_sales.product_id = "
        "dim_products.product_id"
    ),
]