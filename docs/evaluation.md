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

## Schema 3: exchanges and cited sites (2026-10-13)

**Reader tasks, old and new shop map.** Scripted in headless Chromium against the
previous commit (b5da601) and this one; what the inspector shows after the stated
interaction. This is a walkthrough by the implementer, not a study with readers: it shows
whether the answer is *on screen*, not whether a person finds or understands it.

| task | before | after |
| --- | --- | --- |
| Where does the total handed to payment authorization come from? | Click the line: label only, "No source reference". Then open and read `place_order` (33 lines). | Click the label: the call (`self.payments.authorize(payment_token, total)`) and its origin (`total = self.price(lines)`) highlighted; the pricing exchange is one click away. |
| What does authorization return, and what on failure? | Click the line: "authorization", no code. The decline exists only in the contract text. | Click the label: `return Authorization(...)` and the caller's assignment; the exchange lists the error leg "decline reason" with `raise PaymentDeclined(...)`. |
| Where is the 50-line limit? | Rule node with `MAX_LINES_PER_ORDER` | Unchanged |
| What happens when stock is missing or payment is declined? | 9 steps; the out-of-stock failure was drawn on the *request* line (order lines), and the decline had no step of its own | 13 steps; out-of-stock and decline are drawn on their own error legs, then the compensation |
| Is new code covered? (a method added inside a class the map cites whole) | Nothing reported | With `reviewed_revision`: a warning naming `Inventory.write_off`; without: listed as broad coverage |

**Agent authoring trials.** A fresh agent with only `repoluma guide` and `validate` wrote
a map for the fixture packages plus the shop's code (without its map): 42 nodes, 42
relationships, 6 exchanges, all 37 data lines grounded, 66/66 focus texts found, no
warnings, 2 validate runs. It correctly kept the event bus one-way, summarized the
setters as one transfer with origins, modeled `set_currency` as its own operation, and
drew the shared dictionary as a write and a read. Its main critique was fair: the guide's
worked examples are taken from that same code, so the trial partly tests copying. Its
other points (nested-function symbols, writes without a receiving site, where an external
system's adapter code belongs, side effects inside flows, when broad coverage is too broad,
`certainty` when only one end is in the repository) were added to the guide.
A second trial on code the examples do not touch is recorded below.

**Second trial, unseen code.** The same setup on the source of `pluggy` 1.6.0 (~1,700
lines, no README or tests in the checkout), which none of the guide's examples resemble:
46 nodes, 71 relationships, 13 exchanges, 2 flows; 65 of 66 data lines cite a site with
its focus text found (the exception is marked `inferred`), 107/107 focus texts found, no
warnings, 4 validate runs. It modeled the hook-call loop as an exchange with arguments,
collected results and the caught exception, kept ignored results one-way, and used a
`response` leg for historic hooks' callbacks. Its problems, and what changed:

- an exchange id equal to a relationship id gave a message that pointed at the line
  itself; the message now says that the id belongs to another record and suggests one;
- `focus-ambiguous` said "2 times (lines 139)" when YAML had cut an unquoted focus at
  ` #`; it now says "on line 139" and mentions that cause;
- imports inside `if TYPE_CHECKING:` were reported as unexplained code; they are glue now;
- the guide did not say how to model an error that only passes through a layer (it drew
  an error leg per hop), how to treat generator `send`/`throw`, or that a flow may revisit
  a node; it does now.

It also marked as "needs review" the places where it had to cite a call statement as the
transfer of an exception raised further down; the new guidance on pass-through errors
addresses that. Not verified with these agents: whether the maps read well in the viewer
(neither opened it), and how people unfamiliar with a repository do with the new
inspector. Both trials used agents from the same model family as the implementer.
