---
description: Run the deal-evaluator subagent over recent logged searches in data/evals.db
---

Run the `deal-evaluator` subagent to review recent entries in
`data/evals.db`'s `eval_runs` table: $ARGUMENTS (default — the 20 most
recent rows where `subagent_reviewed = 0`, prioritizing any with
`passed = 0` first). It should check each against `evals/criteria.yaml`,
write its verdict back into `subagent_notes` and set `subagent_reviewed = 1`
for every row it covers, and report back here any pattern worth fixing
(e.g. a site consistently failing the same check).
