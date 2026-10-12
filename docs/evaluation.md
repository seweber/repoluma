# Evaluation notes

## Authoring loop on an unseen repository (2026-10-12)

**Setup.** A fresh coding agent with no knowledge of repoluma's internals was given the
`repoluma init` prompt and pointed at a clone of `psf/requests` (commit 611c616, ~6,400
lines of library code). It was allowed to use only `repoluma guide`, `schema` and
`validate`, and not to execute the repository.

**Result.** 45 nodes, 41 relationships, 2 flows (with failure branches), 86 references,
all resolving; 5 validate runs. The top level was organized by purpose (making
requests, session state, preparing the wire request, sending over the network,
redirects, authentication, reading responses, error model, outside world), not by file.
The agent marked unverified claims as `inferred`/`needs_review` and listed omissions in
`scope.not_covered`.

**Diagnostics it hit:** `yaml-syntax` twice (commas inside inline mappings; `: ` in
plain text), `ref-ambiguous` (`@overload` stubs), `ref-missing-symbol` (the
"did you mean" suggestion was exactly right), `contract-without-terms` (info).

**Changes made from this run:**
- `@overload` stubs no longer make a Python symbol ambiguous (tested).
- The YAML hint and the guide now mention commas in inline mappings, prefer block
  style, and say only the first syntax error is reported.
- The guide now documents severities and `--verbose`, the `contract-without-terms`
  and heuristic notes, how unlabeled branch steps chain, where failure steps should
  point, how to model external libraries, when to set `reviewed_revision`, and a size
  budget for a first map.
- Viewing that map exposed a rendering crash on very flat external-system boxes
  (negative corner radius), fixed in `roundRect`.

**Not done yet (Stage C of the brief):** a session with a person unfamiliar with a
repository, comparing time-to-answer and wrong conclusions against normal browsing.
The task script in the brief (locate a rule, explain a flow, inspect a contract,
identify review candidates) is what we would run with the `requests` map.
