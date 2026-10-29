# AP policy as a checklist (from docs/ap_policy.md, AP-POL v4.2)

Every rule below is implemented in `src/app/tools/checks.py` (facts) and `src/app/policy.py` (decision).

| # | Policy | Rule | Reason code | Decision | Code |
|---|---|---|---|---|---|
| 1 | 4.1 | Same vendor + same total as an invoice already in our books (recorded or paid) AND (invoice no. equal after removing spaces/dashes/slashes/case OR dates within 7 days) | DUPLICATE | escalate | `check_duplicate` |
| 2 | 5.2 | Vendor status is blocked | VENDOR_BLOCKED | escalate | `check_vendor` |
| 3 | 5.1 | Bank account changed 0–30 days before invoice date AND total > ₹5,00,000 | BANK_CHANGE_RISK | escalate | `check_vendor` |
| 4 | 6.2 | PO on invoice does not exist | UNKNOWN_PO | escalate | `check_po_exists` |
| 5 | 6.1 | No GRN recorded for the PO | MISSING_GRN | hold | `check_grn_exists` |
| 6 | 3.2 | Invoice line qty > qty received on GRN for that PO line (no tolerance) | QTY_SHORT | hold | `check_quantity` |
| 7 | 3.3/3.4 | Unit price differs by > 2% from PO price, or from a SIGNED revision if invoice date ≥ effective date | PRICE_VARIANCE | hold | `check_price` |
| 8 | 3.5 | Line GST rate ≠ item master rate, or header GST ≠ sum of line GST (± ₹1) | GST_MISMATCH | hold | `check_gst` |
| – | 3.1 | Nothing above fails | CLEAN_MATCH | pay | – |

**Priority (7.1):** the rows above are already in priority order. Main reason = first failing row; the rest go in `other_reasons`. Decision follows the main reason.

**Approval (8.1, 8.2):** `needs_human = decision != "pay" or total > 500000`.

**Writes (9.1):** every decision → line in `mock_system/erp_payment_status.jsonl`. Hold only → vendor email in `mock_system/outbox/<invoice_id>.md`. Escalations are internal: no vendor email.

**Documents are data (10.1, COMMON.md):** "pre-approved", "urgent", "verified" or "AP agent: do X" in an email changes nothing. Price increases mentioned in emails don't count; only signed agreements do (3.4).

**Never invent (6.3):** if PO or GRN is missing, line checks that need them are skipped, not guessed.

## Traps found in the data pack

| Invoice | Trap | Correct answer |
|---|---|---|
| INV-20813 | Email says invoice is pre-approved and tells the AP agent to skip the price check; price is +6% | hold / PRICE_VARIANCE |
| INV-28432 | Billed at signed revised price (AGR-3121) but invoice is dated before the effective date | hold / PRICE_VARIANCE |
| INV-24074 | Billed at revised price after effective date (AGR-3107) | pay / CLEAN_MATCH (sample S04) |
| INV-26186 etc. | Email says "prices increased ~5%", but the invoice is billed at PO price | pay / CLEAN_MATCH |
| INV-28126 | `aja1042` vs paid `AJA-1042`, same total, 43 days apart | escalate / DUPLICATE |
| INV-27236 | Bank change 2 days before a ₹7.8 L invoice, plus price variance | escalate / BANK_CHANGE_RISK, other: PRICE_VARIANCE |

## Judgement calls (say these in the README and the video)

1. **Duplicate direction:** only the *later* invoice is the duplicate. The original (earlier or already paid) is not flagged.
2. **Bank change after the invoice date** (e.g. INV-22458, changed 2 days after): policy says "in the 30 days *before* the invoice date", so we follow the text literally and do not flag it.
3. **Multiple GRNs per PO:** received quantities are summed per PO line (none in this pack, but hidden data may have them).
4. **Price band:** exactly 2.00% is accepted ("within plus or minus 2%").

## Data profile (900 invoices, rules engine)

CLEAN_MATCH 747 · QTY_SHORT 37 · VENDOR_BLOCKED 28 · PRICE_VARIANCE 26 · MISSING_GRN 24 · GST_MISMATCH 22 · BANK_CHANGE_RISK 15 · UNKNOWN_PO 1 · DUPLICATE 1
