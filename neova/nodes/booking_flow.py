"""Node: booking_flow — deterministic pending state and confirmation gate.

The confirmation gate is ``neova.conversation``: only an explicit French
affirmative in the *user's* message to the exact slot+reason pair arms
the booking; a model-generated "yes" can never do so. Changing slot or
reason voids the confirmation; ``rendez-vous confirmé`` is emitted only
after a successful API result with a saved appointment ID.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .. import conversation, tools
from ..tools import ToolError

if TYPE_CHECKING:
    from ..graph import GraphState

_ASK_REASON = (
    "Quel est le motif du rendez-vous technicien ? Choisissez parmi : "
    + ", ".join(f"« {label} »" for label in conversation.REASON_LABELS.values()) + "."
)

_ANONYMOUS_BOOKING_REPLY = (
    "Pour programmer un rendez-vous technicien, une session de démonstration "
    "liée à un client est nécessaire. Je vous propose de contacter un "
    "conseiller via nos canaux officiels.")

_ASK_SLOTS = "Voici les créneaux technicien disponibles (Europe/Paris) :"
_ASK_SLOT_CHOICE = ("Indiquez le numéro du créneau souhaité "
                    "(par exemple « créneau 1 ») ou un identifiant SLOT.")
_NO_SLOTS = ("Aucun créneau technicien n'est disponible pour le moment. "
             "Souhaitez-vous être mis en relation avec un conseiller ?")
_RE_ASK = "Pour confirmer, répondez « oui » ; pour annuler, répondez « non »."
_REFUSED = ("D'accord, je n'enregistre aucun rendez-vous : la proposition "
            "est annulée.")
_CONFLICT_REPLY = ("Ce créneau vient d'être pris ou n'est plus disponible. "
                   "Le rendez-vous n'a pas été enregistré. Souhaitez-vous "
                   "choisir un autre créneau ?")


def _reason_label(reason_id: str | None) -> str:
    return conversation.REASON_LABELS.get(reason_id or "", reason_id or "")


def _offer_slots(state: GraphState, token: str, customer: str,
                 tools_called: list) -> GraphState:
    try:
        slots = tools.get_slots(token, customer)
        tools_called.append("slots.read")
    except ToolError as error:
        return {"tools_called": tools_called,
                "reply": f"Je n'ai pas pu consulter les créneaux ({error.detail}). "
                         "Merci de réessayer."}
    offered = [
        {"slot_id": slot["slot_id"], "start": slot["start"], "end": slot["end"],
         "label": conversation.slot_label(slot["start"], slot["end"])}
        for slot in slots
    ]
    if not offered:
        return {"tools_called": tools_called, "reply": _NO_SLOTS}
    conversation.set_offered(state["session_token"], offered)
    lines = [_ASK_SLOTS]
    for index, item in enumerate(offered, start=1):
        lines.append(f"{index}) {item['label']}")
    lines.append(_ASK_SLOT_CHOICE)
    return {"tools_called": tools_called, "reply": "\n".join(lines)}


def _execute_booking(state: GraphState, pending: conversation.PendingBooking,
                     tools_called: list) -> GraphState:
    token = state["session_token"]
    customer = state["customer_id"]
    key = conversation.confirmation_key(
        pending.customer_id, pending.slot_id, pending.reason_id)
    tools_called.append("appointments.book")  # recorded even on failure
    try:
        result = tools.book_appointment(
            token, customer, pending.slot_id, pending.reason_id, key)
    except ToolError as error:
        if error.status_code == 409:
            conversation.clear(token)
            return {"tools_called": tools_called, "reply": _CONFLICT_REPLY}
        return {"tools_called": tools_called,
                "reply": f"Le rendez-vous n'a pas pu être enregistré "
                         f"({error.detail}). Aucun rendez-vous n'a été créé."}
    conversation.clear(token)
    if result.get("replayed"):
        reply = (f"Un rendez-vous existe déjà pour ce créneau et ce motif "
                 f"({pending.slot_label}). Numéro de dossier : "
                 f"{result['appointment_id']}.")
    else:
        reply = (f"Rendez-vous confirmé : créneau {pending.slot_label}, "
                 f"motif « {_reason_label(pending.reason_id)} ». "
                 f"Numéro de dossier : {result['appointment_id']}.")
    return {"tools_called": tools_called, "reply": reply,
            "handoff_id": state.get("handoff_id")}


def booking_flow_node(state: GraphState) -> GraphState:
    steps = state.get("steps", 0) + 1
    token = state.get("session_token")
    customer = state.get("customer_id")
    message = state["input"]
    tools_called = list(state.get("tools_called", []))
    updates: dict[str, Any] = {"steps": steps, "tools_called": tools_called}

    if not (token and customer):
        updates["reply"] = _ANONYMOUS_BOOKING_REPLY
        return updates

    pending = conversation.pending(token)

    # 1. Explicit user yes (deterministic gate) on an already-proposed pair.
    if pending and pending.awaiting_confirmation and conversation.is_affirmation(message):
        updates.update(_execute_booking(state, pending, tools_called))
        return updates
    if pending and pending.awaiting_confirmation and conversation.is_refusal(message):
        conversation.clear(token)
        updates["reply"] = _REFUSED
        return updates

    # 2. First booking turn on the direct booking route: mandatory
    #    Pro-contract context read, then start the pending state.
    if pending is None and state.get("route") == "booking":
        try:
            summary = tools.get_summary(token, customer)
            summary.pop("customer_id", None)  # no identifiers in the prompt
            tools_called.append("customer_summary.read")
            if "pro" in str(summary.get("plan", "")).lower():
                updates["route"] = "sensitive_pro"
                updates["handoff"] = {
                    "topic": "client Pro",
                    "topic_label": "contrat professionnel",
                    "urgency": "normal", "category": "other",
                }
                return updates
        except ToolError as error:
            updates["reply"] = (f"Je n'ai pas pu consulter votre dossier "
                                f"({error.detail}). Merci de réessayer.")
            return updates
        pending = conversation.start(token, customer)

    # 3. Deterministic updates from the user message (change clears confirmation).
    offered = conversation.offered(token)
    slot_id = conversation.extract_slot(message, offered)
    reason_id = conversation.extract_reason(message)
    if pending is None:
        pending = conversation.start(token, customer)

    # A named slot that is not in the offered list is validated against a
    # fresh read; an unknown slot is never applied.
    if slot_id is not None and not any(
            item["slot_id"] == slot_id for item in offered):
        updates.update(_offer_slots(state, token, customer, tools_called))
        offered = conversation.offered(token)
        if not any(item["slot_id"] == slot_id for item in offered):
            updates["reply"] = ("Ce créneau n'est pas disponible.\n"
                                + (updates.get("reply") or ""))
            return updates

    label = next((item["label"] for item in offered
                  if item["slot_id"] == slot_id), None)
    if slot_id is not None or reason_id is not None:
        pending = conversation.update(
            token, slot_id=slot_id, slot_label=label,
            reason_id=reason_id) or pending

    # 4. Ask at most one missing detail per turn (reason → slot → propose).
    if pending.reason_id is None:
        updates["reply"] = _ASK_REASON
        return updates
    if pending.slot_id is None:
        updates.update(_offer_slots(state, token, customer, tools_called))
        return updates
    if not pending.proposed:
        conversation.mark_proposed(token)
        updates["reply"] = (
            f"Je vous propose un rendez-vous technicien : créneau "
            f"{pending.slot_label}, motif « {_reason_label(pending.reason_id)} ». "
            f"Confirmez-vous exactement ce créneau et ce motif ? "
            f"Répondez « oui » pour confirmer ou « non » pour annuler.")
        return updates
    updates["reply"] = _RE_ASK
    return updates
