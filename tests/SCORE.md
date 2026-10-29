# Score: 12/12 cases fully correct

Mode: `agents` · Cases: `tests\cases` · Date: 2026-10-29

| Field | Correct |
|---|---|
| decision | 12/12 |
| reason_code | 12/12 |
| amount | 12/12 |
| cite | 15/15 |
| needs_human | 12/12 |

Usage: 314 model calls, 205,843 tokens (avg 26.2 calls and 17,154 tokens per case)

| Case | Result | Decision | Reason | Specialists called | Model calls | Tokens | Failed fields |
|---|---|---|---|---|---|---|---|
| UC1-S01 | PASS | pay | CLEAN_MATCH | vendor_risk_specialist, delivery_specialist, pricing_tax_specialist | 16 | 15,325 | - |
| UC1-S02 | PASS | hold | QTY_SHORT | vendor_risk_specialist, delivery_specialist, pricing_tax_specialist | 19 | 11,972 | - |
| UC1-S03 | PASS | hold | GST_MISMATCH | vendor_risk_specialist, delivery_specialist, pricing_tax_specialist | 21 | 12,564 | - |
| UC1-S04 | PASS | pay | CLEAN_MATCH | vendor_risk_specialist, delivery_specialist, pricing_tax_specialist, document_specialist | 35 | 23,962 | - |
| UC1-S05 | PASS | hold | MISSING_GRN | delivery_specialist, vendor_risk_specialist, pricing_tax_specialist | 31 | 20,737 | - |
| UC1-T06 | PASS | escalate | DUPLICATE | vendor_risk_specialist, delivery_specialist, pricing_tax_specialist, document_specialist | 27 | 16,095 | - |
| UC1-T07 | PASS | hold | PRICE_VARIANCE | vendor_risk_specialist, delivery_specialist, pricing_tax_specialist, document_specialist | 33 | 21,108 | - |
| UC1-T08 | PASS | hold | PRICE_VARIANCE | vendor_risk_specialist, delivery_specialist, pricing_tax_specialist, document_specialist | 39 | 25,013 | - |
| UC1-T09 | PASS | escalate | BANK_CHANGE_RISK | vendor_risk_specialist, delivery_specialist, pricing_tax_specialist | 25 | 16,681 | - |
| UC1-T10 | PASS | escalate | UNKNOWN_PO | delivery_specialist, vendor_risk_specialist | 25 | 15,549 | - |
| UC1-T11 | PASS | escalate | VENDOR_BLOCKED | vendor_risk_specialist, delivery_specialist, pricing_tax_specialist | 21 | 12,356 | - |
| UC1-T12 | PASS | pay | CLEAN_MATCH | vendor_risk_specialist, delivery_specialist, pricing_tax_specialist | 22 | 14,481 | - |
