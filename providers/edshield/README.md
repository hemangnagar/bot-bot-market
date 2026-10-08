# edshield provider
Wraps https://github.com/hemangnagar/edshield (Apache-2.0), pinned to commit
`a568e73a6c34d40335802235db81950e84004ab1`, installed from its git URL by `pip install -e ".[providers]"`
(see pyproject.toml). The adapter calls the package's public `edshield.deidentify(text, policy=..., model_name="rules")`
and returns `deidentified_text` plus the per-label counts from the audit record. Rules-only: no model download,
no torch, no network. Nothing from the upstream repo is copied into this one.
