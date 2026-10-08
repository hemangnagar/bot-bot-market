# model_evidence provider
Wraps https://github.com/hemangnagar/model-evidence, pinned to commit `a0f9025fe54b391ad42f5c908812ee848668a4ae`,
shallow-fetched into `.upstream/model-evidence` (gitignored) by `scripts/setup_upstream.sh`. The repo has no LICENSE
file and `package.json` is `private: true`; the owner of both repos approved running it as a subprocess. Nothing is copied.

Invocation: the adapter writes a contract-1.0 bundle (`{name, split, runs:[{id, seed, rows:[{sample_id, y_true, y_pred}]}]}`)
and a policy to a temp dir, runs `node dist/run-evidence.mjs bundle.json policy.json --out verdict.json --quiet` (Node 22),
and reads metrics from `verdict.views.default.runs[0].metrics` and gate statuses from `verdict.views.default.gates`.
