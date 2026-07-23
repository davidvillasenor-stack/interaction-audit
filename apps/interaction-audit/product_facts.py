from __future__ import annotations
"""
apps/interaction-audit/product_facts.py — the grounded source-of-truth fact base for the
accuracy checker. Replaces the 7 hard-coded "sample" facts.

Grounded from (in precedence order):
  1. HSA Hub  — PRIMARY  (~/github/superdojo/apps/hsa-hub/src: App.tsx, FOCallPlaybook.tsx)
  2. cx-knowledge / opendoor-master / hsa-journey (superdojo skills) — cross-reference + gap fill
  3. batting-cage economics — numeric definitions for the deal reconciliation

Design notes (why this isn't a flat lookup):
  • The sources CONFLICT on some facts (DV window, close window, CP2 cap). Each fact carries a
    `source` + `freshness` + `tier`; CONFLICTS lists the reconciliation rule (newest/most-specific wins).
  • Product names are synonyms (see SYNONYMS): CNML = Cash+ = SWU = Sell With Upside.
  • Some sanctioned talk tracks technically "violate" a hard rule (e.g. the service-fee-vs-6%-commission
    comparison). EXCEPTIONS whitelists those so we don't flag Opendoor's own approved scripts.
  • Numbers a rep quotes are reconciled against THIS deal's real pulled data — see NUMERIC_CHECKS.
"""

FACTS_SOURCE = "HSA Hub (primary) + cx-knowledge/opendoor-master (cross-ref)"

# tier: 1 = HSA Hub primary · 2 = cx-knowledge/skills cross-ref · 3 = batting-cage economics
FACTS = [
    # ── Products ────────────────────────────────────────────────────────────
    {"id": "cash", "cat": "Products", "tier": 1,
     "fact": "Cash offer = 100% paid at close, no upside/second payment.",
     "source": "HSA Hub App.tsx:477"},
    {"id": "cnml", "cat": "Products", "tier": 1,
     "fact": "CNML (Cash Now, More Later) = upfront cash (CP1) at closing PLUS a second payment (CP2) after Opendoor resells. Also called Cash+ / SWU.",
     "source": "HSA Hub App.tsx:68,451; cx-knowledge §2"},
    {"id": "cp1", "cat": "Products", "tier": 1,
     "fact": "CP1 (First Payment) = ~75% of estimated sale price, paid at closing. Must cover the seller's mortgage payoff or CNML may not fit.",
     "source": "HSA Hub App.tsx:70,452,487"},
    {"id": "cp2", "cat": "Products", "tier": 1,
     "fact": "CP2 (Second Payment) = additional proceeds paid AFTER the home resells, up to 1 year from acquisition close (extended from 120 days). Not paid at closing.",
     "source": "HSA Hub App.tsx:71,460; cx-knowledge §2"},
    {"id": "cnml-buyback", "cat": "Products", "tier": 1,
     "fact": "After CP1 closes, the seller's only buy-back option = the upfront cash amount + a 3% processing fee.",
     "source": "HSA Hub App.tsx:492; legal.md"},
    {"id": "cnml-cp2-cap", "cat": "Products", "tier": 2, "freshness": "2026-06-25",
     "fact": "CNML resale list price is capped at 120% of Opendoor's estimate (reduced from 150% on 2026-06-25, OFFER-2158).",
     "source": "cx-knowledge DATA_MODEL §2 (only source)"},
    {"id": "alo", "cat": "Products", "tier": 1,
     "fact": "ALO = Agent-Led Offer — an offer initiated through a real estate agent on the seller's behalf. (NOT 'Agent Listing Option'.) Void if the seller lists instead.",
     "source": "HSA Hub App.tsx:63; opendoor-master systems.md"},

    # ── Fees ────────────────────────────────────────────────────────────────
    {"id": "fee-cash", "cat": "Fees", "tier": 1,
     "fact": "Cash service fee: quote the DOLLAR amount, never a fixed %. Each offer's fee is unique; the old '5%' figure is deprecated. Covers transaction/operational/market-risk costs.",
     "source": "HSA Hub FOCallPlaybook.tsx:78; opendoor-master products.md/pricing.md"},
    {"id": "fee-cnml", "cat": "Fees", "tier": 1,
     "fact": "CNML service fee is variable, 0.1%–3.1%.",
     "source": "HSA Hub App.tsx:461; cx-knowledge §2"},
    {"id": "fee-noblanket", "cat": "Fees", "tier": 3,
     "fact": "There is NO blanket fee %. Always verify against THIS offer's actual fee (WEB.OFFERS.FEE1_PERCENTAGE; for Cash+ use the program fee). Opendoor's internal spread (internal_fee_usd) is NOT the customer's fee.",
     "source": "batting-cage pull.py; SUBMISSION.md"},

    # ── Process: FO, DV, repairs ──────────────────────────────────────────────
    {"id": "fo", "cat": "Process", "tier": 1,
     "fact": "Final Offer = the official offer after underwriting; non-negotiable (disputes are a pricing review, not a negotiation). Generated 24–48h post-underwriting; DTC has a 48h hold.",
     "source": "HSA Hub App.tsx:79,1569; cx-knowledge §4"},
    {"id": "dv", "cat": "Process", "tier": 1,
     "fact": "Diligence Visit (DV) happens AFTER the contract is signed, via Inspectify (all DVs since Jan 6 2026); ~30–45 min. Findings typically 3–5 business days.",
     "source": "HSA Hub App.tsx:74,1642; cx-knowledge §5"},
    {"id": "dv-window", "cat": "Process", "tier": 2, "freshness": "2026-03-02",
     "fact": "DV timing — Cash (V21 Addendum, Mar 2 2026): schedule within 5 days, complete within a 10-day window, auto-cancel at day 10. CNML: 15-day diligence period. (See CONFLICTS — older docs still say a flat 15-day for Cash.)",
     "source": "cx-knowledge §5; hsa-journey Stage 3"},
    {"id": "repair-credit", "cat": "Process", "tier": 1,
     "fact": "There is NO 'roof replaced within N years + permit → charge auto-removed' rule. Repair credits come from the DV scope; to challenge one, the seller provides certified/insured proof and the HSA files a DV Results Review via #pricing-subsidy-ask. The ask itself is a review, not automatic.",
     "source": "HSA Hub App.tsx:1704,1758 (no roof/permit rule exists)"},
    {"id": "cnml-repair", "cat": "Process", "tier": 1,
     "fact": "CNML post-DV repairs come out of CP2 upside first (CP1 unchanged); only if the ask exceeds upside is CP1 reduced. New-FO-Prez CNML deals get NO addendum (adjust CP2 + Zendesk email).",
     "source": "HSA Hub App.tsx:1696,1721; cx-knowledge §5"},
    {"id": "appliances", "cat": "Process", "tier": 2,
     "fact": "Appliances: refrigerator, oven/range, dishwasher MUST convey. Washer/dryer are negotiable (seller may keep).",
     "source": "cx-knowledge §5; legal.md; hsa-journey"},

    # ── Money: EMD, cancellation, walk ────────────────────────────────────────
    {"id": "emd", "cat": "Money", "tier": 1,
     "fact": "EMD (Earnest Money Deposit) = $1,250 held with the escrow agent (stated for CNML; Cash EMD 'varies' by market). Refunded in 3–5 business days on cancel.",
     "source": "HSA Hub App.tsx:75,464; legal.md"},
    {"id": "cancel", "cat": "Money", "tier": 1,
     "fact": "Seller can cancel anytime before CP1/closing at no cost/penalty. Walk disposition: opendoor_walk + opendoor_addendum → EMD stays with Opendoor; opendoor_walk + anything else, or seller-initiated → per the rule set (see legal.md).",
     "source": "HSA Hub App.tsx:465,754; cx-knowledge §6"},

    # ── Timeline / closing ────────────────────────────────────────────────────
    {"id": "close-window", "cat": "Timeline", "tier": 1,
     "fact": "Standard close window: seller picks, typically 14–60 days (some sources say 7–60; CNML as early as 14). COE cannot be changed by the HSA directly — goes through the pricing/subsidy channel.",
     "source": "HSA Hub App.tsx:1551,1824; products.md"},
    {"id": "hoa", "cat": "Timeline", "tier": 2,
     "fact": "HOA estoppel/ buyer-approval adds time, especially in FL (estoppel ~10–15 business days). HSA Hub has no specific HOA buyer-approval timeline — do not assert a hard number.",
     "source": "HSA Hub App.tsx:83; cx-knowledge STATES.md"},

    # ── Late checkout ─────────────────────────────────────────────────────────
    {"id": "late-checkout", "cat": "Late checkout", "tier": 1,
     "fact": "Late checkout: up to 17 days post-close, day 1 free, $4,000 refundable deposit. ⚠ The DAILY RATE IS PER-DEAL — stored in DWH.WEB.TASKS_LATE_CHECKOUTS.COST_PER_DAY_CENTS by offer (median ~$132, ranges ~$40–$330, up to ~$750 overstay). HSA Hub's '$318/day' is an ILLUSTRATIVE example, NOT a fixed rate. Reconcile any quoted daily rate against THIS deal's actual COST_PER_DAY (economics.late_checkout_per_day), never against $318.",
     "source": "DWH.WEB.TASKS_LATE_CHECKOUTS (real rate) + HSA Hub App.tsx:1820 (example only)"},

    # ── Hero / military ───────────────────────────────────────────────────────
    {"id": "hero", "cat": "Credits", "tier": 2,
     "fact": "Hero's Home Credit: up to $8,000 ($4,000 sell + $4,000 buy), active-duty + veterans; applied via #cx-subsidy-ask.",
     "source": "HSA Hub App.tsx:559"},
]

# Product-name synonyms — normalize before checking.
SYNONYMS = {
    "cash now more later": "cnml", "cash+": "cnml", "cash plus": "cnml",
    "sell with upside": "cnml", "swu": "cnml", "cnml": "cnml",
}

# Sanctioned talk tracks that would otherwise trip a hard rule — DO NOT flag these.
EXCEPTIONS = [
    "Comparing Opendoor's service fee to a traditional 5–6% agent commission IS an approved "
    "objection script (cx-knowledge §8 obj #3), even though the hard rule says 'don't lead with the "
    "6% comparison.' Only flag if the rep misstates the commission number or implies the OD fee is a %.",
]

# Documented cross-source conflicts + how the checker should resolve them.
CONFLICTS = [
    "DV window (Cash): cx-knowledge/hsa-journey say 5-day-schedule/10-day-window (V21, Mar 2 2026); "
    "opendoor-master/legal still say flat 15-day. RESOLVE: newest wins → use 5/10-day for Cash, 15-day for CNML.",
    "Close window: '7–60 days' (most) vs '14–60' (hsa-journey). RESOLVE: treat 14–60 as safe; don't flag 7-day unless clearly wrong for the market.",
    "Service fee %: old '5%' deprecated in favor of dollar-amount framing. RESOLVE: flag any spoken fee % that "
    "doesn't match THIS offer's actual fee (Cash+ program fee, or FEE1_PERCENTAGE) by >0.5pt.",
    "CNML CP2 cap (120%, changed from 150% on 2026-06-25) exists only in cx-knowledge — treat as current.",
]

# Numeric claims to reconcile against the deal's real pulled data (economics/experience dicts).
# Each: what the rep might quote → which real field to compare → tolerance / rule.
NUMERIC_CHECKS = [
    {"claim": "service/program fee %", "field": "experience.fee (this offer's actual %)",
     "rule": "flag if a quoted fee % differs from the offer's actual fee by >0.5pt (Cash+ → program fee)."},
    {"claim": "fee $ / amount charged", "field": "experience.fee_usd (fee% × recorded price)",
     "rule": "flag if a quoted fee dollar amount differs materially from computed fee $."},
    {"claim": "headline / offer price", "field": "economics.offer_price (Cash+ → economics.cnml_headline)",
     "rule": "flag a quoted price that doesn't match the deal's headline (mind Cash vs Cash+ base)."},
    {"claim": "CP1 / upfront cash", "field": "economics.cp1",
     "rule": "flag if quoted upfront ≠ CP1_AMT; also confirm it's framed as ~75%/advance-rate, not full price."},
    {"claim": "CP2 / upside", "field": "economics.cp2",
     "rule": "flag if quoted upside ≠ projected resale upside, or framed as paid at closing."},
    {"claim": "earnest money", "field": "constant $1,250 (CNML)",
     "rule": "flag any earnest amount ≠ $1,250 for CNML."},
    {"claim": "closing date", "field": "milestones.expected_close / acq_close",
     "rule": "flag a promised closing date inconsistent with the deal's expected/actual close, or 'cleared to close' while approval is pending."},
    {"claim": "repair / DV credit", "field": "economics.dv_repairs",
     "rule": "flag a quoted repair credit that doesn't match the DV charge, or an unsupported policy basis (e.g. roof/permit rule)."},
    {"claim": "late-checkout daily rate", "field": "economics.late_checkout_per_day (this deal's actual COST_PER_DAY)",
     "rule": "reconcile a quoted daily rate against THIS deal's real per-day rate — NOT HSA Hub's $318 example. Only flag if it differs materially from economics.late_checkout_per_day (or is wildly outside the ~$40–$330 norm)."},
]
