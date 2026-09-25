"""Node: classify — deterministic guards first, then one semantic call.

Exact order (each earlier step wins and skips the later ones):

1. Deterministic injection check — runs before any model call, on the
   raw user message; matched override attempts never reach a provider.
2. Pending booking + exact known continuation — the short continuations
   (``oui``/``non``/``ok``/``confirmer``/``annuler``) and exact code
   phrases (``CONFIRMER RDV <code>``/``ANNULER RDV <code>``, including
   wrong or expired codes) route straight to the booking flow with no
   classifier call. They keep the flow active; only the exact live code
   phrase ever confirms a booking.
3. Otherwise: one cheap OpenRouter classification call (intent,
   out_of_scope, ambiguity, injection risk, reason candidate), validated
   by Pydantic; any failure degrades to the deterministic keyword router.
4. Protected policy routes: a pending booking never overrides injection,
   sensitive, out_of_scope or ambiguous outcomes. For the remaining
   routes, explicit slot/reason details keep the booking flow active.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import conversation, observability
from ..classifier import Classification, classify_message, trace

if TYPE_CHECKING:
    from ..graph import GraphState


def classifier_factory():
    """Configured OpenRouter classifier, or None (fail honest, offline).

    Patch point for tests: return an ``invoke(message) -> str`` double.
    """
    try:
        from ..classifier import openrouter_classifier
        return openrouter_classifier()
    except Exception:
        return None


def _classify_with_fallback(
    message: str,
) -> tuple[Classification | None, dict, tuple[str, dict | None] | None]:
    """Run the semantic classifier; degrade to the keyword router.

    Returns (classification-or-None, classifier-trace, fallback-pair).
    ``fallback-pair`` is the deterministic keyword routing, set only when
    a classifier call was attempted and failed (visible degradation).
    """
    invoke = classifier_factory()
    if invoke is None:
        return None, trace(None, source="keywords", degraded=False), None
    try:
        return (classify_message(message, invoke),
                trace(None, source="model", degraded=False), None)
    except Exception:
        # One bounded call; failures degrade visibly, never guess.
        return (None, trace(None, source="keywords", degraded=True),
                conversation.classify_request(message))


def classify_node(state: GraphState) -> GraphState:
    """Thin wrapper: one ``classify-intent`` observation (no-op when off)."""
    with observability.step("classify-intent", input=state["input"]) as obs:
        result = _classify_node(state)
        obs.update(
            output=str(result.get("route", "")),
            metadata={"classification_source": str(
                (result.get("classifier") or {}).get("source", ""))})
        return result


def _classify_node(state: GraphState) -> GraphState:
    message = state["input"]
    steps = state.get("steps", 0) + 1
    token = state.get("session_token")

    # 1. Deterministic injection check, before any model call and
    # regardless of the classifier result.
    if conversation.looks_like_injection(message):
        classifier_trace = trace(None, source="skipped", degraded=False)
        classifier_trace["prompt_injection"] = True
        return {"route": "injection", "handoff": None,
                "classifier": classifier_trace, "reason_candidate": None,
                "steps": steps}

    # 2. Exact known continuations and code phrases: deterministic booking
    # routing, no classifier call. Continuations never confirm by
    # themselves — booking_flow re-asks for the exact code.
    pending = conversation.pending(token) if token else None
    if pending is not None and (
            conversation.is_booking_continuation(message)
            or conversation.is_code_attempt(message)):
        return {"route": "booking", "handoff": None,
                "classifier": trace(None, source="skipped", degraded=False),
                "reason_candidate": None, "steps": steps}

    # 3. One cheap classification call (or the keyword fallback).
    classification, classifier_trace, fallback = _classify_with_fallback(message)

    if classification is not None:
        route, handoff = _policy_route(classification, message)
        reason_candidate = (classification.reason_candidate
                            if classification.reason_candidate != "unknown"
                            else None)
        classifier_trace.update({
            "intent": classification.intent,
            "out_of_scope": classification.out_of_scope,
            "ambiguous": classification.ambiguous,
            "prompt_injection": classification.prompt_injection,
            "reason_candidate": reason_candidate,
        })
    else:
        route, handoff = fallback if fallback is not None else (
            conversation.classify_request(message))
        reason_candidate = None

    # 4. Protected policy routes: a pending booking never overrides
    # injection, sensitive, out_of_scope or ambiguous outcomes. For the
    # remaining routes, explicit slot/reason details stay in the flow.
    if (route not in ("injection", "sensitive", "out_of_scope", "clarify")
            and pending is not None):
        if (conversation.extract_slot(
                message, conversation.offered(token)) is not None
                or conversation.extract_reason(message) is not None):
            route, handoff = "booking", None

    return {
        "route": route,
        "handoff": handoff,
        "classifier": classifier_trace,
        "reason_candidate": reason_candidate,
        "steps": steps,
    }


def _policy_route(classification: Classification, message: str) -> tuple[str, dict | None]:
    """Deterministic policy layer over the validated classification."""
    # Model flag is an additional boundary layer; the code-side guard has
    # already run before this call ever happened.
    if classification.prompt_injection:
        return "injection", None
    if classification.out_of_scope:
        return "out_of_scope", None
    if classification.ambiguous:
        return "clarify", None
    intent = classification.intent
    if intent in ("sensitive", "billing_dispute", "termination", "unsupported_mutation"):
        return intent, conversation.params_for_route(intent, message)
    if intent == "unsupported":
        return "unsupported", None
    return intent, None  # internet, billing, moving, booking
