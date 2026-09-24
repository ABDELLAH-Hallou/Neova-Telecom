# Source register — independent corpus review (issue #4 / PR 3)

**Status:** review record for the ten original PDFs and the PNG in `corpus/`, produced 2026-09-23 on branch `4-pr-3-retrieval-verified-public-corpus-hybrid-search-and-evidence-gate`.

**Method.** Each original PDF was opened independently with `pypdf` 6.19.0 (script `.cache/pdf-review/extract.py`, run locally) and read page by page; the PNG was inspected as an image. Per-page character counts confirm no empty pages. The git-ignored derived extracts in `.cache/pdf-text/` were used **only as a cross-check** of wording and metadata lines — they are not the evidence for this register. `docs/material_inventory.md` (earlier, extract-based) is superseded on matters of verification status by this register.

**Reviewer:** coding agent (OpenCode), human review pending before merge.

## PDF sources

All ten PDFs parse with `pypdf`; every page yields non-empty text (no scanned/blank pages). The originals are rendered pages carrying the same `CORPUS` banner, `corpus/*.md` source label and `1/2 · 2/2` footers as the cached extracts; banners/footers/metadata lines are extraction artifacts, stripped before indexing, not content.

| Source (path) | Pages / coverage | Section map (original pages) | Update date (`maj`) | Status | Access | Verification |
| --- | --- | --- | --- | --- | --- | --- |
| `corpus/cgv-resiliation.pdf` | 2; pp. 1–2 non-empty (3.2k chars) | pp. 1–2: arts. 11–14 — notice, early termination, exemptions, returns/indemnities | 2026-04-02 | current | public | pypdf extraction + read; cross-checked vs `.cache/pdf-text/cgv-resiliation.txt` |
| `corpus/faq-box-internet.pdf` | 2; pp. 1–2 non-empty | p. 1: indicators, network check, restart; p. 2: technician criteria, conditional 69 € charge, mobile caveat | 2026-05-18 | current | public | same method |
| `corpus/faq-espace-client.pdf` | 2; pp. 1–2 non-empty | p. 1: login/recovery, self-service table; p. 2: banking details, closure | 2026-04-29 | current | public | same method; self-declared uncleaned help-site export (nav/cookie text present) |
| `corpus/faq-facturation.pdf` | 2; pp. 1–2 non-empty (3.0k chars) | p. 1: billing cycle, amount-differs causes, nonpayment stages; p. 2: payment plan, dispute, refund | 2026-06-20 | current | public | same method; fee-timing tension observed on p. 1 (see conflicts below) |
| `corpus/faq-retour-equipement.pdf` | 2; pp. 1–2 non-empty | p. 1: return items, 15-day deadline; p. 2: shipping/collection, tracking, losses | 2026-01-15 | current | public | same method |
| `corpus/grille-tarifaire-2026.pdf` | 2; pp. 1–2 non-empty (2.0k chars) | p. 1: fibre/mobile offers, options; p. 2: one-off fees, multi-offer discount, Pro exclusion | 2026-06-01 | current | public | same method; states it replaces all earlier grids/promotions |
| `corpus/politique-geste-commercial.pdf` | 2; pp. 1–2 non-empty | pp. 1–2: internal eligibility thresholds, caps, exclusions | 2026-05-30 | current | **internal** | same method; text says not to disclose thresholds/conditions to customers → **routing-only, never indexed/quoted** |
| `corpus/procedure-demenagement.pdf` | 2; pp. 1–2 non-empty | p. 1: transfer/eligibility, timing; p. 2: fees, uncovered areas, mobile move | 2026-03-11 | current | public | same method |
| `corpus/procedure-escalade-n2.pdf` | 2; pp. 1–2 non-empty | pp. 1–2: immediate/conditional handoffs, contents, callback timing | 2026-06-14 | current | **internal** | same method; expressly not for customer disclosure → **routing-only, never indexed/quoted** |
| `corpus/promo-rentree-2024.pdf` | 1; p. 1 non-empty (1.1k chars) | p. 1: promo prices, included advantages, archive conditions | 2024-09-01 | deprecated (archive) | public | same method; page states its tariffs are no longer marketed and must not be given as current prices |

## PNG source

`corpus/fiche-roaming-international-scan.png` — SHA-256 `032f1ac426617c4cf6d71b82cc6f045ce06e8f281c2be4a14d545eca0c9369d3`, inspected as an image on 2026-09-23 (no OCR dependency; transcription stored as reviewed data in `neova/corpus.py`).

Observed content: header `NÉOVA TÉLÉCOM — Fiche d'information client — Réf. FIC-ROAM-2026-02 — Mise à jour : 27 février 2026 — Version en vigueur. Remplace et annule la fiche FIC-ROAM-2025-04. Diffusion : clients et conseillers.`; title `Utilisation à l'étranger — offres mobiles`; sections: `Union européenne, DOM inclus` (usable data per offer: Mobile 5 Go → 5 Go; 80 Go → 25 Go; 200 Go → 35 Go; beyond envelope 3 € per started GB; alerts at 80 % then 100 %; majority-foreign usage over four consecutive months may bill out-of-UE rates after notification), `Hors Union européenne` (call emitted 0,50 €/min; call received 0,25 €/min; SMS 0,20 €; data 5,00 € per started 100 Mo; billed as « hors forfait international » with up to two billing-cycle lag), `Blocage préventif` (free reversible data block via customer area), `Contestation des consommations hors forfait` (waivers go to an advisor). Footer: `document non contractuel, établi sur la base des conditions générales en vigueur au 1er janvier 2026.` — hence **usable citations carry the non-contractual flag**.

## Conflicts and limits recorded at review time

1. **Fee timing (faq-facturation, p. 1):** “Frais de rejet de prélèvement : 2,00 €” is stated as applied when the bank rejects the debit, while the nonpayment stage list applies the 2 € fee at J+21 on the next bill. Unresolved from the documents; retrieval must flag, not resolve.
2. **Current vs archived prices (grille-tarifaire-2026 p. 1 vs promo-rentree-2024 p. 1):** different figures for the same offer names; the 2026 grid replaces prior promotions and the 2024 page itself forbids presenting its prices as current. General price answers must prefer the 2026 grid.
3. **Roaming authority:** the scan is current-marked but footer-non-contractual and dated to conditions of 1 Jan 2026; the 2026 grid covers domestic allowances only. No numeric contradiction established.
4. **Missing evidence:** no invoice line items, coverage lookup, promotion attribution or contract dates exist in `data/neova_data.json`; individual-fee questions stay flagged, not inferred.

## Register-driven indexing decisions

- **Indexed (customer-public):** cgv-resiliation, faq-box-internet, faq-espace-client, faq-facturation, faq-retour-equipement, grille-tarifaire-2026, procedure-demenagement, promo-rentree-2024 (archived → flagged at gate time), the roaming PNG transcription (flagged non-contractual).
- **Excluded from chunks, FTS5, vectors and answer context (routing-only):** politique-geste-commercial, procedure-escalade-n2.