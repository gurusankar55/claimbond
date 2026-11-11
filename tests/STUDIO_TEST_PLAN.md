# ClaimBond — GenLayer Studio test plan

Deploy `contracts/claim_bond.py` in GenLayer Studio with:

- `min_bond` = `0`
- `challenge_window_seconds` = `120`

## 1. Deterministic rejection (no AI)
`propose("Is it live?", "yes_no", "YES", "https://example.com", 0)`
Expected: error `question length must be 10-300 characters`.

## 2. Bad URL rejected
`propose("Is the Bitcoin whitepaper online?", "yes_no", "YES", "http://bitcoin.org/bitcoin.pdf", 0)`
Expected: error `evidence_url must be a valid https URL`.

## 3. Optimistic path
`propose("Does this page describe the Bitcoin whitepaper?", "yes_no", "YES", "https://bitcoin.org/en/bitcoin-paper", 0)`
Expected: returns `"0"`. `get_status("0")` → `PROPOSED`.
Call `finalize("0")` immediately → error `challenge window still open`.
Wait 2+ minutes, call `finalize("0")` → `ACCEPTED`. `get_final_answer("0")` → `YES`.

## 4. Dispute path (validators read both pages)
`propose("Does this page describe the Bitcoin whitepaper?", "yes_no", "NO", "https://bitcoin.org/en/bitcoin-paper", 0)`
Expected: returns `"1"`.
From a **different account**, inside 2 minutes:
`dispute("1", "https://bitcoin.org/en/bitcoin-paper")`
(The same page is used as counter evidence so both reads agree.)
Expected: `CORRECTED`, `get_final_answer("1")` → `YES`. `get_claim("1")` shows the values each page returned.

## 5. Views
`get_config()`, `get_claim_count()` → `2`, `is_final("1")` → `true`, `get_status("9")` → `NO_CLAIM`.
