# ClaimBond — Optimistic Claim Oracle for GenLayer

**A reusable primitive for getting trustworthy off-chain facts into contracts, paying for AI consensus only when someone actually disagrees.**

Contracts often need a fact from the web: *Is the mainnet live? What is the token's max supply? Did the team ship the feature?* Asking validators to check every fact with an LLM is slow and costly. ClaimBond uses the **optimistic oracle** pattern:

1. Someone **proposes** an answer with a bond and an evidence URL.
2. A **challenge window** opens.
3. If nobody disputes, the answer becomes final **without any web or LLM call**.
4. If someone disputes (equal or bigger bond + a counter-evidence URL), **GenLayer validators read both pages** and must agree exactly on what each page says. Deterministic code then decides who was right, and the loser's bond goes to the winner.

Consumers only need two view calls:

```python
is_final(claim_id) -> bool
get_final_answer(claim_id) -> str   # "YES" / "NO" or a canonical number string
```

---

## Lifecycle

```
propose (bond, evidence_url)  ──► PROPOSED  (window open, no AI used)
        │
        ├── nobody disputes ──► finalize() after deadline ──► ACCEPTED
        │                         proposer gets bond back
        │
        └── dispute (bond ≥ proposer bond, counter_url) inside window
                 validators read BOTH pages (one consensus round)
                 ├── truth matches proposal ──► CONFIRMED  (proposer wins both bonds)
                 ├── truth differs         ──► CORRECTED  (disputer wins both bonds,
                 │                                          final answer = truth)
                 └── pages contradict /
                     nothing readable      ──► UNRESOLVABLE (both bonds refunded)
```

`ACCEPTED`, `CONFIRMED` and `CORRECTED` are final. `UNRESOLVABLE` is never final, so consumers never act on an unproven fact.

---

## How consensus is used

| Step | Deterministic or validator work? |
|---|---|
| Input checks: answer type, answer format, https URL, question length, bonds, window | Deterministic |
| Undisputed finalization | Deterministic (no web, no LLM) |
| Reading the two evidence pages | **Validators**, inside one `gl.eq_principle.strict_eq` block |
| Deciding the winner and paying bonds | Deterministic, from the agreed values |

Inside the validator block, each validator:

1. Fetches each page with `gl.nondet.web.render(url, mode="text")` (capped at 12k characters).
2. Asks the LLM **one narrow extraction question**: return `{"value": ...}`, which must be `YES`/`NO`, or a plain number. The page text is fenced and marked as untrusted data, to resist prompt injection.
3. Normalizes the reply **in code**: `yes`, `True`, `Y.` → `YES`; `1,000,000`, `1e6`, `$1000000.0` → `1000000`.
4. Returns canonical JSON `{"a": <evidence value>, "b": <counter value>}`.

Because the LLM output is reduced to a tiny normalized value, honest validators produce byte-identical results and `strict_eq` is practical. **The LLM never decides who wins.**

### Why tolerance is outside consensus

For `number` questions the proposer sets `tolerance_bps` (max 2000 = 20%). Tolerance is applied **after** consensus in `_resolve()`, not inside the validator check. Validators agree on the exact value they read. Pure code then compares it with the proposal. This keeps the equivalence check strict and the business rule auditable.

### Resolution table (`_resolve`)

| Evidence page (a) | Counter page (b) | Result |
|---|---|---|
| value | value, agrees with a | truth = a |
| value | value, differs from a | `UNRESOLVABLE` |
| value | unreadable | truth = a |
| unreadable | value | truth = b |
| unreadable | unreadable | `UNRESOLVABLE` |

Then `truth` within tolerance of the proposal → `CONFIRMED`, otherwise `CORRECTED`.

---

## Methods

### Write
| Method | Description |
|---|---|
| `propose(question, answer_type, answer, evidence_url, tolerance_bps)` *payable* | Propose an answer with a bond. `answer_type` is `yes_no` or `number`. Returns `claim_id` |
| `dispute(claim_id, counter_url)` *payable* | Dispute inside the window. Bond must be ≥ proposer bond. Resolves in the same transaction |
| `finalize(claim_id)` | Anyone can finalize an undisputed claim after the deadline |
| `withdraw()` | Pull your credited bonds |

### View
| Method | Description |
|---|---|
| `is_final(claim_id)` | `true` for `ACCEPTED` / `CONFIRMED` / `CORRECTED` |
| `get_final_answer(claim_id)` | Final answer, or `""` if not final |
| `get_status(claim_id)` | Current status, or `NO_CLAIM` |
| `get_claim(claim_id)` | Full claim JSON (bonds, deadline, values each page returned) |
| `get_credit(address_hex)` | Withdrawable balance |
| `get_claim_count()` | Total claims |
| `get_config()` | `min_bond`, window, limits |

Constructor: `ClaimBond(min_bond, challenge_window_seconds)` (window max 30 days).

---

## Using it from another contract

```python
oracle = gl.get_contract_at(Address(CLAIMBOND_ADDRESS))
if oracle.view().is_final(claim_id):
    answer = oracle.view().get_final_answer(claim_id)
    if answer == "YES":
        release_milestone_payment()
```

Examples of consumers: milestone escrows ("did the team ship X?"), prediction markets, launchpads checking token facts, insurance triggers, DAO proposals that depend on a public fact.

---

## Design decisions

- **Optimistic by default.** Most claims are honest and never disputed, so they cost no validator AI work.
- **Disputer bond ≥ proposer bond.** Cheap griefing of honest proposers costs the griefer.
- **Both sides bring evidence.** Validators read the proposer's page *and* the disputer's page, so the result does not depend on a single source.
- **Contradiction refunds both.** If the two sources disagree, nobody can be proven right, so nobody is slashed and the fact is not finalized.
- **One dispute per claim.** The game stays bounded.
- **Pull payments.** Bonds are credited and withdrawn with `withdraw()`.
- **Time comes from the transaction** (`gl.message_raw["datetime"]`), so window checks are deterministic.

## Limitations

- Only `yes_no` and `number` answers. Free-text answers would need a comparative equivalence principle and are out of scope on purpose.
- If the proposer's evidence page is unreadable and the disputer's page is readable, the disputer's page decides. Proposers should use stable, official sources.
- Pages that render content only through heavy JavaScript may come back unreadable.
- A finalized answer is the answer *at that time*; it is not re-checked later.

---

## Tests

`tests/test_claim_bond.py` runs **offline with plain Python** (no Docker, no node). It installs a tiny stub of the `genlayer` module, imports the real contract file unchanged, and mocks pages and LLM replies.

```bash
python -m unittest tests/test_claim_bond.py -v
```

19 tests cover normalization, URL checks, the full resolution table, number tolerance, window timing, bond rules, payouts and withdraw.

`tests/STUDIO_TEST_PLAN.md` lists manual checks in GenLayer Studio.

## Files

```
contracts/claim_bond.py      the Intelligent Contract
tests/test_claim_bond.py     offline unit tests (19)
tests/STUDIO_TEST_PLAN.md    manual Studio test steps
```
