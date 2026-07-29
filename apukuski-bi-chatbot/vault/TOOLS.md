# TOOLS — työkalujeni käyttö

## bi_orders_report — ensisijainen (Supabase)
"Myynti", "montako keikkaa", "hubivertailu" → tämä ensin. Kerro date_basis.
group_by: day | hub | order_type | none.

## db_read — vapaa SQL Supabaseen
Aggregoivia kyselyitä. Esimerkkejä:

Viikon tilausmäärät luontipäivittäin:
```sql
SELECT date_trunc('week', created_at)::date AS week,
       COUNT(*) AS orders,
       COUNT(*) FILTER (WHERE is_manual) AS manual,
       COUNT(*) FILTER (WHERE NOT is_manual) AS platform
FROM orders
WHERE created_at >= NOW() - INTERVAL '90 days'
GROUP BY 1 ORDER BY 1;
```

Sync-tilan tarkistus:
```sql
SELECT workflow, started_at, status, rows_upserted, error_message
FROM sync_log ORDER BY started_at DESC LIMIT 10;
```

Manual-order gross total (euro):
```sql
SELECT COUNT(*) AS n,
       ROUND(SUM((manual_data->>'total_incl_vat_cents')::bigint)/100.0, 2) AS eur
FROM orders
WHERE is_manual AND manual_data->>'total_incl_vat_cents' IS NOT NULL;
```

## bi_revenue_report — live Backoffice
Vain jos Supabase ei riitä (tuoreus, API-vertailu).

## backoffice_get_hubs
Live hub-lista. Supabasessa: `SELECT hub_id, hub_name FROM hubs`.

## search_knowledge
Määritelmät ja tavoitteet ennen "en tiedä".

## flag_uncertainty
Epäilyttävät luvut, puuttuva data, tulkintariski.
