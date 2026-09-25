# Fixed evaluation run (issue #7)

- Mode: `offline` — clock: {'mode': 'frozen', 'timestamp': '2026-08-26T12:00:00+02:00'}
- Chat: `FakeChatModel (offline double)` · classifier: `keyword fallback router` · embedder: `FakeEmbedder (offline double)`
- Cases: 12/12 passed (0 failed)

| Case | Category | Result |
| --- | --- | --- |
| `internet-incident` | routing | PASS |
| `invoice-without-line-items` | grounding | PASS |
| `price-current-vs-archive` | grounding | PASS |
| `fee-timing-contradiction` | grounding | PASS |
| `moving-booking-success` | booking | PASS |
| `booking-bad-slot` | booking | PASS |
| `termination-handoff` | handoff | PASS |
| `sensitive-immediate-handoff` | handoff | PASS |
| `api-500-read-recovery` | resilience | PASS |
| `openrouter-429-exhaustion` | resilience | PASS |
| `openrouter-529-overload` | resilience | PASS |
| `anonymous-session` | privacy | PASS |

All cases passed; no failed case to display.

## Retrieval-mode comparison (top-3 source ids)

- `Quel est le prix actuel et le tarif archive de la grille 2024 ?`
  - semantic: ['promo-rentree-2024', 'grille-tarifaire-2026', 'fiche-roaming-international-scan']
  - fts: ['promo-rentree-2024', 'grille-tarifaire-2026', 'fiche-roaming-international-scan']
  - hybrid: ['promo-rentree-2024', 'grille-tarifaire-2026', 'fiche-roaming-international-scan']
- `Des frais de rejet de prélèvement s'appliquent à quel moment ?`
  - semantic: ['faq-facturation', 'faq-espace-client', 'cgv-resiliation']
  - fts: ['faq-facturation', 'faq-espace-client', 'grille-tarifaire-2026']
  - hybrid: ['faq-facturation', 'faq-espace-client', 'grille-tarifaire-2026']
- `Roaming international : mes données depuis l'étranger sont-elles incluses ?`
  - semantic: []
  - fts: ['fiche-roaming-international-scan', 'faq-retour-equipement', 'grille-tarifaire-2026']
  - hybrid: ['fiche-roaming-international-scan', 'faq-retour-equipement', 'grille-tarifaire-2026']
- `frais de mise en service`
  - semantic: ['cgv-resiliation', 'procedure-demenagement', 'faq-facturation']
  - fts: ['faq-facturation', 'faq-facturation', 'faq-espace-client']
  - hybrid: ['faq-facturation', 'cgv-resiliation', 'procedure-demenagement']
