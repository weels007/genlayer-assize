# Assize

**A court with two instances — and one of them never touches the web.**

Two people disagree about one sentence and one page. Each posts the same bond. The
first instance reads the page once, rules `SUSTAINED` or `REJECTED`, copies the
sentence it relied on, and writes down the whole record it read.

The loser may appeal **once**, and must say on what ground:

- **RECORD** — "the ruling misread its own evidence". The second instance is given
  the claim, the ruling, and **the record the first instance read**, and it never
  fetches anything. Its only question is whether the ruling follows from that
  record.
- **EVIDENCE** — "the page was read wrongly". The second instance reads the page
  again, this time with the first ruling in front of it.

The appeal costs twice the case bond, so the pot grows and a frivolous second
instance is expensive. A ruling nobody appeals settles after its appeal window; an
appeal settles when the second instance answers. The winner takes the pot either
way, and a judgement nobody could ground moves nothing and can always be voided.

## Why it is a different model

Four things here are not in the usual "an AI judges a page" contract:

- **The appeal is a different question, not a second opinion.** A `RECORD` appeal
  asks whether the decision follows from the record as it was read. No web access
  at all — so a page that changed since the ruling cannot reach it, and the second
  instance cannot be argued with by rewriting the page.
- **The record the appeal reads is agreed before it is written.** Validators
  compare the keccak digest of the record along with the ruling, so a committee
  that read two different pages writes nothing. A leader cannot hand the appeal
  evidence of its own choosing: the cited sentence is stored for a human reader,
  and no later call reads it.
- **Two instances, one appeal, then final.** The loser chooses which question the
  court answers next, and the bond says how much that choice is worth.
- **A first instance that never rules costs nobody anything.** If the committee
  cannot ground a ruling, it books an attempt, moves no money, and after three of
  those *anyone* may void the case — both stakes go back to their own parties.

## Live on GenLayer Studio

`deployment.json` is the single source of truth: the **Deploy Contract** workflow
deploys the court once, writes the file, and commits it back to `main`.

| | |
|---|---|
| **Contract** | [`0xC6B56fE9584bB88f3879545A78D903c297288806`](https://explorer-studio.genlayer.com/address/0xC6B56fE9584bB88f3879545A78D903c297288806) |
| **Network** | GenLayer Studio — chainId `61999` (`0xf22f`), test GEN only |
| **Evidence** | [Actions](https://github.com/weels007/genlayer-assize/actions) — direct, Studio, money paths, every deployment |

The address above is checked against this repository: `scripts/verify_deployment.py`
compares the recorded `revision` — the first twelve characters of the SHA-256 of
`contracts/Assize.py`, written by the deploy script — with the checkout, and both
live suites refuse to run when the two disagree. A court with no owner cannot be
upgraded, so the address and the source have to be provably the same thing.

## Lifecycle

```mermaid
flowchart LR
  F["file_case(claim, url, window) · bond"] --> C1[FILED]
  C1 -- "contest · matching bond" --> C2[CONTESTED]
  C2 -- "rule" --> R[RULED<br/>record + digest stored]
  R -- "no appeal + window" --> S[SETTLED<br/>pot to winner]
  R -- "loser appeals · 2× bond" --> A[APPEALED]
  A -- "RECORD: no web" --> S
  A -- "EVIDENCE: page again" --> S
  C2 -- "undecided ×3 → void" --> V[VOID<br/>both refunded]
  A -- "undecided ×3 → void" --> V
  C1 -- "withdraw_case" --> V
```

## How a ruling is produced

| Step | What happens |
|---|---|
| **File** | A claim (10–400 chars), an `https` page, a bond of at least `MIN_BOND`, and a caller-scoped reference so a replay cannot mint a second case. |
| **Contest** | A different account posts the same bond. No model is involved. |
| **Rule** | Permissionless. The page is fetched once inside a non-deterministic block, capped at `MAX_PAGE_CHARS`; the model answers `SUSTAINED`, `REJECTED` or `SILENT` with a verbatim quote. That capped text is the **record**: it is stored with the ruling. |
| **Ground** | The quote must appear in the page after folding to letters and digits. It is a pointer for a reader — no later call reads it. |
| **Consensus** | Validators re-run the same instance and must produce the same `ruling` **and the same record digest**, so the text a `RECORD` appeal will argue over is text every validator read. An appeal is compared on its `decision`. |
| **Settle** | The winner takes `bond × 2` plus `bond × 2` more if there was an appeal. |

### Error taxonomy

| Tag | Meaning | Consensus behaviour |
|---|---|---|
| `[EXPECTED]` | bad input, wrong caller, wrong stake, a case in the wrong state | deterministic, must match |
| `[EXTERNAL]` | the page is empty | deterministic, recorded |
| `[TRANSIENT]` | network / 5xx, page unreachable | both transient means agreement, retry |
| `[LLM_ERROR]` | malformed choice, ungrounded quote | always disagrees → new leader |

## Money rules

- **`file_case` and `contest` never revert once value is attached.** A refused
  call — bond below the floor, a bad URL, a duplicate reference, a stake that does
  not match, a self-contest, a closed case, a bad ground, an appeal after the
  window, an appeal by the winner — becomes a **credit** owned by the sender,
  withdrawable with `withdraw_credit`.
- **The loser is the only one who may appeal**, and only inside the window.
- **`void` pays every party its own money back** — the bond, the contestant's
  stake, and the appeal stake if there was one.
- **`withdraw_case`** returns the bond of a filing nobody contested.
- **The ledger is updated first and the transfer last**, so a failed transfer
  rolls both back together.

Invariant: `total_in == total_out + locked_total`, credits included.

## Contract API

**Write**

| Method | Who | Purpose |
|---|---|---|
| `file_case(claim, source_url, appeal_window, case_ref)` · payable | anyone | open a case with a bond |
| `contest(case_id)` · payable | anyone but the filer | take the other side |
| `rule(case_id)` | anyone | first instance: read the page and rule |
| `appeal(case_id, ground)` · payable | the loser only | ask the second instance, `RECORD` or `EVIDENCE` |
| `judge_appeal(case_id)` | anyone | second instance: record only, or the page again |
| `settle(case_id)` | anyone | pay the pot to the winner |
| `void(case_id)` | anyone | refund everybody when nothing could be decided |
| `withdraw_case(case_id)` | the filer | take back an uncontested filing |
| `withdraw_credit()` | anyone with a credit | take back money from a refused call |

**View**

| Method | Returns |
|---|---|
| `get_case(id)` · `get_cases()` · `get_case_ids()` | a case, all cases, the id list |
| `get_record(id)` | the text the court read, which a `RECORD` appeal argues over |
| `is_contested(id)` · `can_appeal(id)` | whether a case has two sides, whether the window is still open |
| `get_credit(address)` · `get_ledger()` · `get_balance()` | credits, `total_in`, `total_out`, `locked_total`, real balance |
| `get_summary()` · `get_rules()` | counters, and the bounds the contract enforces |

## Security review

| Risk | How Assize answers it |
|---|---|
| A second instance that just asks the model again | `RECORD` runs on the claim, the ruling and the stored record only, with no web access, so it cannot be moved by rewriting the page. The direct suite proves it runs with no page handler at all. |
| A ruling whose citation the committee never agreed on | The first instance returns the whole record and its keccak digest, and both are compared. The stored record is therefore the text every validator read, and the `RECORD` appeal is never shown the cited sentence — so a leader cannot pick the evidence the appeal argues over and flip the payout. |
| A leader that sends a record and a digest describing different bytes | The contract recomputes the keccak over the record it is about to store and refuses to rule when it does not match, in deterministic code after consensus, so text that was never agreed on cannot reach the appeal. |
| Stored text steering a later decision without consensus | The compared fields are `ruling` with the record digest, and `decision`. The record is consensus-bound; the quote, the reasons and the appeal's own quote are descriptive and no later call reads them as a decision. |
| An ungrounded citation deciding a case | A ruling without a quote taken from the page becomes undecided, which moves nothing. |
| The loser could stall forever | `settle` is permissionless after the window, and `void` is permissionless once the attempts are spent. |
| One side taking the other's money | Every payout goes to the stored party: the filer, the contestant, or the appellant. A stranger's `withdraw_case` is refused as `not_filer`. |
| The pot growing unbounded | One appeal only; the second instance is final. |
| Caller text manipulating the judge | Claim and page are framed as untrusted data, and instructions inside either are explicitly ignored. |
| Replay minting a second case | The reference is caller-scoped and recorded once. |
| SSRF through the URL | `https` only, no IP literal, no single-label host, no metadata aliases, alphabetic TLD. |
| Unbounded work | 500 cases, three attempts per instance, capped strings, O(1) summary. |

Residual risks, stated plainly:

- **`RECORD` judges the record, not the page as it is now.** It asks whether the
  ruling follows from the text the first instance read; a page rewritten after the
  ruling is invisible to it, which is the point of a second instance with no web
  access.
- **A page that changes while the committee reads it cannot be ruled.** Two
  validators that read different text produce different record digests, consensus
  writes nothing, and the case books an attempt instead of settling on a record
  nobody agreed on. A page with a clock or a rotating banner on it is not a page
  this court can rule; three attempts and anyone may void it. The digest stored
  with the ruling is the one every validator agreed on, so a reader can see exactly
  what was judged.
- A `SILENT` page makes both parties wait: three attempts, then a void.
- **Studio has been quoting less than it charges.** A send that carries only the
  quoted fee comes back reverted at the EVM step, before any contract code runs.
  `scripts/deploy.py` and both live suites therefore retry such a send with a
  larger deposit. Nothing is half-done by it: a reverted transaction never landed,
  so the retry is the same call, not a second one.

## Testing

| Suite | What it covers | Run |
|---|---|---|
| **Direct** | filing, contesting, both instances, all grounds, the loser rule, the window, credits, voids, the record a `RECORD` appeal reads and the consensus that binds it, and the ledger invariant | `pytest tests/direct -v` |
| **Studio** | the deployed court: a real filing, a contest, a live ruling that keeps the record it read, an appeal argued on the record, an appeal argued on the page, a case the committee cannot rule that is voided after three attempts, and the settlement | `pytest tests/integration -v -s` |
| **End to end** | the money paths without a model: mismatched stake, self-contest, withdrawal of a filing, a contested case, an unearned void, and a stranger | `pytest tests/e2e -v -s` |

Every assertion in the live suites is a delta, and every claim reference carries
the run's start time, so they can be re-run against the same court.

## Quick start

```bash
pip install -r requirements.txt

genvm-lint check contracts/Assize.py   # forbidden imports, storage, nondet misuse
pytest tests/direct -v                 # the whole court, in memory, milliseconds
pytest tests/integration -v -s         # the deployed court, live
pytest tests/e2e -v -s                 # the deployed court's money paths

python scripts/deploy.py               # deploy once, record deployment.json
```

The direct suite mocks the page and the model; both instances, both grounds, the
window, every refund, the loser rule and the record binding are exercised in
memory.

The live suites sign with throwaway Studio accounts that the repository keeps in
Actions secrets — one filer and one contestant — so no key material is committed.

## Layout

```text
contracts/Assize.py              the contract
tests/direct/                    filing, ruling, appeal, views
tests/integration/               the deployed court, live
tests/e2e/                       the deployed court's money paths
testdata/ungroundable.txt        a page no quote can be grounded in
scripts/deploy.py                deploys once, records deployment.json
scripts/verify_deployment.py     the address on file is this contract
scripts/wallet.py                keystore from tests/ or a repository secret
deployment.json                  the deployed address (written by the workflow)
.github/workflows/               ci, live, deploy-contract
SUBMISSION.md                    the short pack for a reviewer
```

## Submission

[`SUBMISSION.md`](SUBMISSION.md) is the short pack for a reviewer: what the
contract is, the deployed facts, what each suite proves, and the limits that are
still true. Every public method — nine writes and eleven views — is exercised against
the deployed address; the Studio suite and the money paths are the evidence.

`python scripts/verify_deployment.py` proves the address and the source are the
same thing, which is the one claim a reviewer cannot check from the contract
alone.

## License

[MIT](LICENSE).
