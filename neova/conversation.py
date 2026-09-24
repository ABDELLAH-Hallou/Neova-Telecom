"""Deterministic conversation logic for the bounded agent graph.

This module owns everything that must never depend on model output:

- the pending-booking store: process-local, keyed by the demo session
  token (same lifetime contract as ``neova.session.SessionStore``) — it
  keeps the customer reference, the exact Europe/Paris slot, the
  enumerated reason and the confirmation state across turns;
- the deterministic confirmation gate: a booking is armed only by an
  explicit French affirmative **in the user's own message** addressed to
  the exact slot+reason pair. A model-generated "yes" can never arm a
  booking, and changing slot or reason clears the confirmation;
- the code-side sensitive-topic table and named-route keywords, so the
  internal routing rules stay out of the customer-facing prompt.
"""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
import unicodedata
from dataclasses import dataclass, replace
from datetime import datetime

from . import clock

# A code is scoped to one offered pair and expires even with a frozen demo clock.
CONFIRMATION_TTL_SECONDS = 10 * 60
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _new_code(previous: str | None) -> str:
    code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(4))
    while code == previous:
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(4))
    return code


def is_confirmation(message: str, code: str | None) -> bool:
    """True only when the whole user message is the exact confirm phrase."""
    return bool(code) and normalize(message) == f"confirmer rdv {code}".lower()


def is_cancellation(message: str, code: str | None) -> bool:
    return bool(code) and normalize(message) == f"annuler rdv {code}".lower()


def is_code_attempt(message: str) -> bool:
    return bool(re.fullmatch(r"(?:confirmer|annuler) rdv [a-z2-9]{4}", normalize(message)))


BOOKING_CONTINUATIONS = frozenset({"oui", "non", "ok", "confirmer", "annuler"})


def is_booking_continuation(message: str) -> bool:
    """Only these short replies continue; none authorizes a booking."""
    return normalize(message) in BOOKING_CONTINUATIONS


# Code-side second layer of prompt-injection defence. The classifier flags
# injection too; these deterministic patterns enforce the boundary even if
# the classifier is degraded or misses the attempt.
_INJECTION_PATTERNS = (
    "ignore previous", "ignore all previous", "ignore les instructions",
    "ignore toutes les instructions", "oublie tes instructions",
    "reveal your prompt", "reveal your instructions",
    "montre ton prompt", "montre moi ton prompt", "system prompt",
    "tu es maintenant", "you are now", "act as", "comme si tu eta",
    "developer mode", "mode developpeur", "jailbreak",
    "contourne les restrictions", "bypass",
)


def looks_like_injection(message: str) -> bool:
    text = normalize(message)
    return any(pattern in text for pattern in _INJECTION_PATTERNS)


def normalize(text: str) -> str:
    """Lowercase, strip accents and punctuation for deterministic matching."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", stripped.lower()).strip()


# ---------------------------------------------------------------------------
# Pending-booking state


@dataclass
class PendingBooking:
    """The exact booking offer kept across turns; `proposed` means the
    exact slot+reason pair was shown and its time-limited code is awaited."""

    customer_id: str
    slot_id: str | None = None
    slot_label: str | None = None
    reason_id: str | None = None
    proposed: bool = False
    code: str | None = None
    proposed_at: float | None = None
    previous_code: str | None = None
    expired: bool = False

    @property
    def complete(self) -> bool:
        return self.slot_id is not None and self.reason_id is not None

    @property
    def awaiting_confirmation(self) -> bool:
        return (self.proposed and self.complete and self.code is not None
                and self.proposed_at is not None
                and time.monotonic() - self.proposed_at < CONFIRMATION_TTL_SECONDS)


class ConversationStore:
    """Process-local pending state, keyed by the hashed session token."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._pending: dict[str, PendingBooking] = {}
        self._offered: dict[str, list[dict]] = {}
        self._last_code: dict[str, str] = {}

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def pending(self, token: str) -> PendingBooking | None:
        with self._lock:
            key = self._key(token)
            booking = self._pending.get(key)
            if (booking and booking.proposed and booking.proposed_at is not None
                    and time.monotonic() - booking.proposed_at >= CONFIRMATION_TTL_SECONDS):
                booking = replace(booking, proposed=False, code=None,
                                  proposed_at=None, expired=True,
                                  previous_code=booking.code)
                self._pending[key] = booking
            return booking

    def start(self, token: str, customer_id: str) -> PendingBooking:
        with self._lock:
            booking = self._pending.get(self._key(token))
            if booking is None:
                booking = PendingBooking(customer_id=customer_id)
                self._pending[self._key(token)] = booking
            return booking

    def update(
        self, token: str, *, slot_id: str | None = None,
        slot_label: str | None = None, reason_id: str | None = None,
    ) -> PendingBooking | None:
        """Apply a partial update; any slot or reason change clears the
        confirmation (``proposed`` resets to False)."""
        with self._lock:
            booking = self._pending.get(self._key(token))
            if booking is None:
                return None
            changed = (slot_id is not None and slot_id != booking.slot_id) or (
                reason_id is not None and reason_id != booking.reason_id
            )
            fields: dict = {}
            if slot_id is not None:
                fields["slot_id"] = slot_id
            if slot_label is not None:
                fields["slot_label"] = slot_label
            if reason_id is not None:
                fields["reason_id"] = reason_id
            if changed:
                fields.update(proposed=False, code=None, proposed_at=None,
                              expired=False, previous_code=booking.code or booking.previous_code)
            booking = replace(booking, **fields)
            self._pending[self._key(token)] = booking
            return booking

    def mark_proposed(self, token: str) -> PendingBooking | None:
        with self._lock:
            booking = self._pending.get(self._key(token))
            if booking is None or not booking.complete:
                return None
            key = self._key(token)
            code = _new_code(self._last_code.get(key) or booking.code or booking.previous_code)
            booking = replace(booking, proposed=True, proposed_at=time.monotonic(),
                              code=code, expired=False)
            self._last_code[key] = code
            self._pending[key] = booking
            return booking

    def set_offered(self, token: str, offered: list[dict]) -> None:
        with self._lock:
            self._offered[self._key(token)] = list(offered)

    def offered(self, token: str) -> list[dict]:
        with self._lock:
            return list(self._offered.get(self._key(token), []))

    def clear(self, token: str) -> None:
        with self._lock:
            self._pending.pop(self._key(token), None)
            self._offered.pop(self._key(token), None)

    def reset(self) -> None:
        """Test helper: drop all pending state."""
        with self._lock:
            self._pending.clear()
            self._offered.clear()
            self._last_code.clear()


_store = ConversationStore()


def pending(token: str) -> PendingBooking | None:
    return _store.pending(token)


def start(token: str, customer_id: str) -> PendingBooking:
    return _store.start(token, customer_id)


def update(token: str, *, slot_id: str | None = None,
           slot_label: str | None = None,
           reason_id: str | None = None) -> PendingBooking | None:
    return _store.update(token, slot_id=slot_id, slot_label=slot_label,
                         reason_id=reason_id)


def mark_proposed(token: str) -> PendingBooking | None:
    return _store.mark_proposed(token)


def set_offered(token: str, offered: list[dict]) -> None:
    _store.set_offered(token, offered)


def offered(token: str) -> list[dict]:
    return _store.offered(token)


def clear(token: str) -> None:
    _store.clear(token)


def reset() -> None:
    _store.reset()


def pending_view(token: str | None) -> dict | None:
    """Trace-safe view of the pending booking, or None."""
    if not token:
        return None
    booking = _store.pending(token)
    if booking is None:
        return None
    return {
        "customer_id": booking.customer_id,
        "slot_id": booking.slot_id,
        "slot_label": booking.slot_label,
        "reason_id": booking.reason_id,
        "confirmation_pending": booking.awaiting_confirmation,
    }


# ---------------------------------------------------------------------------
# Deterministic confirmation gate (user turns only, never model output)
#
# Stricter flow: a booking is armed ONLY by the exact code phrase shown in
# the proposal (e.g. "CONFIRMER RDV 7K3P"), tied to the exact slot+reason
# pair. Bare "oui" and every model-generated affirmative book nothing.
# The phrases are matched case-insensitively after accent/punctuation
# normalization; anything else re-asks.


def confirmation_phrase(code: str) -> str:
    return f"CONFIRMER RDV {code}"


def cancellation_phrase(code: str) -> str:
    return f"ANNULER RDV {code}"


def confirmation_key(customer_id: str, slot_id: str, reason_id: str) -> str:
    """Deterministic idempotency key for the exact confirmed pair."""
    return f"conv:{customer_id}:{slot_id}:{reason_id}"[:128]


# ---------------------------------------------------------------------------
# Slot / reason extraction (deterministic)


REASON_LABELS: dict[str, str] = {
    "no_internet": "absence d'internet",
    "slow_internet": "internet lent",
    "installation": "installation",
    "equipment_swap": "remplacement d'équipement",
}

_REASONS = (
    ("no_internet", ("pas d internet", "sans internet", "internet ne marche",
                     "plus d internet", "absence d internet", "coupure internet",
                     "no_internet")),
    ("slow_internet", ("internet lent", "lenteur", "debit faible",
                       "connexion ralenti", "slow_internet")),
    ("installation", ("installation", "installer", "nouvelle ligne",
                      "raccorder", "premiere installation", "installation")),
    ("equipment_swap", ("remplacer", "echanger", "nouvelle box",
                        "changer de box", "remplacement", "equipment_swap")),
)

_MONTHS = {"janvier": 1, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
           "juin": 6, "juillet": 7, "aout": 8, "septembre": 9, "octobre": 10,
           "novembre": 11, "decembre": 12}


def extract_reason(message: str) -> str | None:
    text = normalize(message)
    for reason_id, keywords in _REASONS:
        if any(keyword in text for keyword in keywords):
            return reason_id
    return None


def extract_slot(message: str, offered: list[dict] | None) -> str | None:
    """Match a slot by explicit id, offered-list index, or calendar date."""
    text = normalize(message)
    match = re.search(r"slot\s*([a-z0-9]+)", text)
    if match:
        return f"SLOT-{match.group(1).upper()}"
    if offered:
        match = re.search(
            r"(?:creneau|option|rendez vous)\s*(?:numero|no|n)?\s*([1-9])\b", text)
        if match:
            index = int(match.group(1)) - 1
            return offered[index]["slot_id"] if index < len(offered) else None
        match = re.search(r"\b(\d{1,2})[/ ](\d{1,2})(?:[/ ]\d{2,4})?\b", text)
        if match and _date_in_offered(int(match.group(1)), int(match.group(2)), offered):
            return _date_in_offered(int(match.group(1)), int(match.group(2)), offered)
        match = re.search(
            r"\b(\d{1,2}) (" + "|".join(_MONTHS) + r")\b", text)
        if match and _date_in_offered(int(match.group(1)), _MONTHS[match.group(2)], offered):
            return _date_in_offered(int(match.group(1)), _MONTHS[match.group(2)], offered)
    return None


def _date_in_offered(day: int, month: int, offered: list[dict]) -> str | None:
    for item in offered:
        start = datetime.fromisoformat(item["start"])
        if start.day == day and start.month == month:
            return item["slot_id"]
    return None


def slot_label(start_iso: str, end_iso: str) -> str:
    """Exact Europe/Paris display of a slot (issue requirement)."""
    start = datetime.fromisoformat(start_iso)
    end = datetime.fromisoformat(end_iso)
    return (f"{clock.format_slot_time(start)}–"
            f"{end.astimezone(clock.PARIS).strftime('%H:%M')} (Europe/Paris)")


# ---------------------------------------------------------------------------
# Sensitive topics and named routes (code-side; never pasted into prompts)


SENSITIVE_TOPICS = (
    ("privacy", "protection des données", "normal", (
        "rgpd", "donnees personnelles", "mes donnees", "supprimer mes donnees",
        "droit a l oubli", "export de mes donnees")),
    ("fraud", "fraude signalée", "urgent", (
        "fraude", "arnaque", "escroquerie", "usurpation", "piratage", "pirate")),
    ("legal", "litige juridique", "urgent", (
        "avocat", "huissier", "poursuite", "tribunal", "plainte",
        "assignation", "justice")),
    ("bereavement", "décès", "urgent", ("deces", "succession")),
    ("protected_customer", "client protégé", "normal", (
        "mineur", "tutelle", "curatelle", "protection judiciaire")),
    ("distress", "détresse", "urgent", (
        "suicide", "menace", "harcèlement", "violence", "agression", "victime")),
)

ROUTE_KEYWORDS = (
    ("billing_dispute", ("contester", "litige", "erreur sur ma facture",
                         "remboursez", "remboursement abusif",
                         "je refuse de payer")),
    ("booking", ("rendez vous", "rendez-vous", "rdv", "creneau",
                 "technicien", "prendre rendez")),
    ("termination", ("resilier", "resiliation", "resilie",
                     "annuler mon abonnement", "annuler mon forfait",
                     "arreter mon abonnement", "arreter mon forfait")),
    ("moving", ("demenagement", "demenage", "nouvelle adresse",
                "changer d adresse", "transporte ma ligne")),
    ("billing", ("facture", "paiement", "prelevement", "frais", "montant",
                 "tarif", "prix", "solde", "mensualite", "facturation",
                 "remboursement", "combien", "coute", "cout")),
    ("internet", ("internet", "connexion", "wifi", "panne", "coupure",
                  "debit", "lent", "pas d acces", "impossible de me connecter",
                  "ma box")),
    ("unsupported_mutation", ("changez mon forfait", "changer mon forfait",
                              "changer mon adresse", "modifiez mon compte",
                              "change mon mot de passe", "activez l option",
                              "resiliez pour moi", "faites la resiliation")),
)


def _sensitive_params(message: str) -> dict | None:
    """Sensitive-topic parameters from the code-side table, or None."""
    text = normalize(message)
    for topic_id, label, urgency, keywords in SENSITIVE_TOPICS:
        if any(normalize(keyword) in text for keyword in keywords):
            return {"topic": topic_id, "topic_label": label,
                    "urgency": urgency, "category": "other"}
    return None


_STATIC_HANDOFF = {
    "billing_dispute": {"topic": "litige facturation",
                        "topic_label": "litige de facturation",
                        "urgency": "normal", "category": "billing_dispute"},
    "termination": {"topic": "résiliation", "topic_label": "résiliation",
                    "urgency": "normal", "category": "termination"},
    "unsupported_mutation": {"topic": "modification de compte",
                             "topic_label": "modification de compte",
                             "urgency": "normal", "category": "other"},
}


def params_for_route(route: str, message: str) -> dict | None:
    """Handoff parameters for a classifier-driven route (policy layer)."""
    if route == "sensitive":
        return _sensitive_params(message) or {
            "topic": "autre", "topic_label": "demande sensible",
            "urgency": "normal", "category": "other"}
    return _STATIC_HANDOFF.get(route)


def classify_request(message: str) -> tuple[str, dict | None]:
    """Deterministic keyword fallback routing (used when the semantic
    classifier is unconfigured or degraded).

    Sensitive topics win over everything: privacy rights, fraud, legal
    threats, death, protected/minor customers and distress go to an
    immediate minimal handoff. Billing disputes and unsupported account
    mutations are handed off as well.
    """
    text = normalize(message)
    params = _sensitive_params(message)
    if params is not None:
        return "sensitive", params
    for route, keywords in ROUTE_KEYWORDS:
        if any(normalize(keyword) in text for keyword in keywords):
            if route in _STATIC_HANDOFF:
                return route, dict(_STATIC_HANDOFF[route])
            return route, None
    return "unsupported", None


def handoff_summary(topic_label: str, message: str) -> str:
    """Factual, bounded internal summary for the advisor record."""
    excerpt = " ".join(message.split())[:200]
    return f"Transfert conseiller ({topic_label}). Demande client : {excerpt}"[:500]
