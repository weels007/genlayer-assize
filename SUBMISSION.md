# Assize — submission pack

**A court with two instances, where the second one sometimes reads only the record.**

## The idea

Two accounts disagree about one sentence and one public page. Each posts the same
bond. A committee reads the page once, rules `SUSTAINED` or `REJECTED`, copies the
sentence it relied on, and writes down the whole record it read.

The loser may appeal once, and must say on what ground:

- **`RECORD`** — "the ruling misread its own evidence". The second instance is
  handed the claim, the ruling and **the record the first instance read**, and
  **never fetches the page**. It answers one question: does that ruling follow from
  that record?
- **`EVIDENCE`** — "the page was read wrongly". The second instance reads the page
  again, with the first ruling in front of it.

The appeal costs twice the case bond, so the pot grows. The winner takes it; a case
nobody can decide moves no money and can always be voided by anyone.

## Why the appeal cannot be steered

A `RECORD` appeal argues over text the first instance wrote down, so that text is
bound by early consensus: the first instance returns the whole record together
with its keccak digest, validators compare both, and the contract recomputes the
digest over the record it is about to store before it will rule. A committee that
read two different pages writes nothing at all, and a node that sends a record and
a digest describing different bytes writes nothing either. The sentence the first
instance cites is stored for a human reader and is never handed to the appeal, so
a leader cannot pick the evidence that decides the payout.

## Deployed facts

| | |
|---|---|
| Network | GenLayer Studio — chainId `61999` (`0xf22f`) |
| Contract | [`0x77b6c082441cE4382C054A9e4773a9C4A30a4CDd`](https://explorer-studio.genlayer.com/address/0x77b6c082441cE4382C054A9e4773a9C4A30a4CDd) |
| Source revision | `235f1e515d3e` — the first twelve characters of the SHA-256 of `contracts/Assize.py` |
| Owner | none: no pause, no setter, no upgrade path |
| Money | test GEN only |

`python scripts/verify_deployment.py` recomputes that revision from the checkout and
fails if the contract has drifted from the recorded address.

## What is proven, and where

| Suite | Proves |
|---|---|
| **Direct**, in memory | filing, contesting, both instances, both grounds, the loser rule, the appeal window, every credit and refusal, the attempt cap, the ledger invariant, and the consensus that binds the record a `RECORD` appeal reads |
| **Studio**, live | a real filing; a contest by a second wallet; a ruling that keeps the record it read; an appeal argued on the record (`UPHELD`); an appeal argued on the page (`UPHELD`); a case filed against an ungroundable page that books three attempts and is then voided by the other account; the settlement |
| **Money paths**, live | a mismatched stake as a credit and the credit withdrawn; a self-contest refused; an uncontested filing withdrawn; a contested case that cannot be withdrawn; a void refused before it is earned; a stranger reaching for a filing that is not theirs |
| **Lint** | `genvm-lint check contracts/Assize.py` |

Every public method — nine writes and eleven views — is exercised against the deployed
address. The evidence is the
[Actions](https://github.com/weels007/genlayer-assize/actions) history.

The live suites sign with throwaway Studio accounts that live in Actions secrets —
one filer and one contestant — so no key material is committed.

## Limits, stated plainly

- **`RECORD` judges the record, not the page as it is now.** It asks whether the
  ruling follows from the text the first instance read. A page rewritten after the
  ruling is invisible to it — that is what a second instance with no web access is
  for, and the ground is recorded on-chain so a reader can see which record was
  argued over.
- **A page that changes while the committee reads it cannot be ruled.** Two
  validators that read different text produce different record digests, consensus
  writes nothing, and the case books an attempt rather than settle on a record
  nobody agreed on. A page with a clock or a rotating banner is not a page this
  court can rule: three attempts, then anyone may void it.
- **A page that cannot ground a twenty-letter quote makes everyone wait.** Three
  attempts, then anyone may void the case and both sides get their own money back.
  The contract will not guess, and `testdata/ungroundable.txt` exists to prove that
  exit works on demand.
- **Studio only.** No mainnet, no audited token, no upgrade path — by design.

## Try it

```bash
pip install -r requirements.txt

python scripts/verify_deployment.py     # the address is this source
pytest tests/direct -v                  # the whole court, in memory, milliseconds
pytest tests/integration -v -s          # the deployed court, live
pytest tests/e2e -v -s                  # the deployed court's money paths
```
