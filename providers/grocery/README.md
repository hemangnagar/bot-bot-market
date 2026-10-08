# grocery provider
Wraps https://github.com/hemangnagar/grocery_optimizer (MIT), pinned to commit
`ef8fa67ae9c6e3f21c7977800b674f3a7c69edb0`. `scripts/setup_upstream.sh` shallow-fetches that commit into
`.upstream/grocery_optimizer` (gitignored), creates a Python 3.12 venv there, installs the package editable, and seeds the
demo dataset with the upstream `grocery-init-db` and `grocery-seed-demo` entry points. The sidecar runs under that venv
(`runner/services.py` maps the provider to it), so none of its dependencies enter the bench venv.

Invocation per request: insert a basket through the documented `baskets` / `basket_items` tables, resolve each item with
`grocery_optimizer.silver.basket.candidates_for_term`, call `grocery_optimizer.silver.verdict.build_verdict(con, basket_id)`,
filter the "flexible" store totals to the requested stores, delete the basket. Nothing from the upstream repo is copied here.
