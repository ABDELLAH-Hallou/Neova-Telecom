"""Offline issue #5 conversation-graph tests: French traces, faked chat model.

No network calls: the chat model and the embedder are injected fakes
(same pattern as tests/test_retrieval.py); customer reads, booking and
handoffs run through the real FastAPI app over the in-process tool
transport, against a temporary database and a frozen demo clock.
"""

import sqlite3
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from neova import chunking, conversation, db, graph, retrieval, tools
from neova.app import app
from neova.embeddings import vector_to_blob

CUSTOMER = "NEO-88213"
OTHER = "NEO-10467"
PRO_CUSTOMER = "NEO-71925"

INTERNAL_SOURCE_IDS = ("politique-geste-commercial", "procedure-escalade-n2")

# Deterministic fake embedding space (same pattern as tests/test_retrieval.py).
VOCAB = (
    "resiliation", "resilier", "abonnement", "frais", "rejet", "prelevement",
    "facture", "montant", "prix", "forfait", "roaming", "etranger", "donnee",
    "technicien", "box", "retour", "equipement", "demenagement", "adresse",
    "conseiller", "banque", "activation", "grille", "archive", "go",
)
SYNONYMS = {"arreter": "resiliation", "stopper": "resiliation", "tarif": "prix"}


class FakeEmbedder:
    """Deterministic bag-of-words embedder; optionally fails."""

    def __init__(self, fail: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, texts: list[str]) -> list[list[float]]:
        if self.fail:
            raise RuntimeError("simulated embeddings outage")
        self.calls.append(list(texts))
        vectors = []
        for text in texts:
            counts = dict.fromkeys(VOCAB, 0)
            for token in retrieval._normalize(text).split():
                key = SYNONYMS.get(token, token)
                if key in counts:
                    counts[key] += 1
            vectors.append([counts[word] for word in VOCAB])
        return vectors


class FakeChatModel:
    """Records the prompts it receives; returns one fixed French reply."""

    def __init__(self, reply: str = (
            "D'après [faq-resiliation p.1], vous pouvez résilier à tout moment, "
            "avec un préavis de 10 jours.")) -> None:
        self.prompts: list[str] = []
        self.reply = reply

    def invoke(self, prompt: str):
        self.prompts.append(prompt)
        return SimpleNamespace(content=self.reply)


CHUNKS = (
    ("faq-resiliation",
     "Résiliation du contrat : le client peut résilier son abonnement à tout "
     "moment. Le préavis est de 10 jours et la dernière facture est calculée "
     "au prorata."),
    ("faq-depannage",
     "En cas de panne internet, redémarrez votre box et vérifiez les câbles. "
     "Si le problème persiste, un technicien peut intervenir sur rendez-vous."),
    ("grille-tarifaire-2026",
     "Grille tarifaire 2026 : Fibre Néova 500 Mb/s à 29,99 euros par mois. "
     "Frais de mise en service : 49 euros."),
    ("promo-rentree-2024",
     "Offre Rentrée 2024 : Fibre 500 Mb/s à 19,99 euros la première année. "
     "Document conservé à titre d'archive."),
    ("faq-facturation",
     "Frais de rejet de prélèvement : 2,00 euros. Appliqués au moment du "
     "rejet ou sur la facture suivante à J+21."),
)


def insert_public_chunk(conn, source_id: str, text: str, embedder,
                        model: str = "fake-model", page: int = 1) -> None:
    chunk_hash = chunking.chunk_hash_for(source_id, page, text)
    conn.execute(
        "INSERT INTO chunks (chunk_hash, source_id, source_path, page_start, "
        "page_end, section, access, word_count, text) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (chunk_hash, source_id, f"{source_id}.pdf", page, page, source_id,
         "public", len(text.split()), text),
    )
    conn.execute(
        "INSERT INTO chunk_fts (text, source_id, chunk_hash) VALUES (?, ?, ?)",
        (text, source_id, chunk_hash),
    )
    conn.commit()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO chunk_vectors (chunk_hash, model, dim, "
            "vector, created_at) VALUES (?, ?, ?, ?, '2026-08-26T12:00:00+02:00')",
            (chunk_hash, model, len(VOCAB), vector_to_blob(embedder([text])[0])),
        )


@pytest.fixture
def local_agent(monkeypatch, tmp_path):
    path = tmp_path / "neova.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{path}")
    monkeypatch.setenv("CLOCK_MODE", "frozen")
    monkeypatch.setenv("DEMO_TIMESTAMP", "2026-08-26T12:00:00+02:00")
    conversation.reset()
    model = FakeChatModel()
    monkeypatch.setattr(graph, "answer_model", lambda: model)
    embedder = FakeEmbedder()
    monkeypatch.setattr(graph, "embedder_factory", lambda: embedder)
    conn = db.connect(str(path))
    try:
        db.init_retrieval_schema(conn)
        for source_id, text in CHUNKS:
            insert_public_chunk(conn, source_id, text, embedder)
        with conn:
            db.set_meta(conn, retrieval.INDEX_MODEL_KEY, "fake-model")
    finally:
        conn.close()
    with TestClient(app) as client:
        token = client.post(
            "/demo/sessions", json={"customer_id": CUSTOMER}
        ).json()["session_token"]
        yield client, {"X-Demo-Session": token}, model, str(path)
    db.close_session_connections()
    tools.reset_transport()
    conversation.reset()


def chat(client, headers, message, **extra) -> dict:
    response = client.post(
        "/agent/chat", headers=headers, json={"message": message, **extra})
    assert response.status_code == 200, response.text
    return response.json()


def count(path: str, table: str) -> int:
    with sqlite3.connect(path) as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# ---------------------------------------------------------------------------
# Core French traces: internet, billing, moving, booking, termination


@pytest.mark.parametrize("message,route,expected_tools", [
    ("Internet ne marche pas depuis ce matin, c'est quoi le problème ?",
     "internet", {"customer_summary.read", "incidents.read"}),
    ("Je veux le détail de ma facture de ce mois",
     "billing", {"customer_summary.read"}),
    ("Je veux résilier mon abonnement", "termination", set()),
    ("Je veux prendre rendez-vous avec un technicien",
     "booking", {"customer_summary.read"}),
    ("Je déménage, il me faut une installation",
     "moving", {"customer_summary.read"}),
])
def test_core_french_traces_route_and_bounded_tools(
        local_agent, message, route, expected_tools):
    client, headers, model, path = local_agent
    response = client.post("/agent/chat", headers=headers, json={"message": message})
    assert response.status_code == 200
    result = response.json()
    assert result["route"] == route
    assert expected_tools <= set(result["tools_called"])
    assert len(result["tools_called"]) <= 3  # bounded turn, no tool loop
    assert result["reply"]
    sources = {citation["source_id"] for citation in result["citations"]}
    assert not (sources & set(INTERNAL_SOURCE_IDS))
    assert "procedure-escalade" not in response.text
    assert "politique-geste-commercial" not in response.text


def test_termination_answers_then_hands_off(local_agent):
    client, headers, model, path = local_agent
    result = chat(client, headers, "Je veux résilier mon abonnement")
    assert result["route"] == "termination"
    assert model.prompts  # grounded info came from the answer node
    assert "référence de suivi" in result["reply"]
    assert result["handoff_id"] is not None
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT category_id, urgency FROM handoffs").fetchone()
    assert row == ("termination", "normal")
    assert count(path, "appointments") == 0  # safe final action: no mutation


def test_internet_incident_reaches_grounding_prompt(local_agent):
    client, headers, model, path = local_agent
    chat(client, headers, "Internet ne marche pas depuis ce matin")
    prompt = model.prompts[-1]
    assert "INC-4471" in prompt            # linked incident data flowed in
    assert "PASSAGES PUBLICS" in prompt    # corpus passages included
    assert "VALEURS API AUTORISÉES" in prompt
    assert "NEO-" not in prompt            # no private identifiers in prompt


# ---------------------------------------------------------------------------
# Booking: multi-turn confirmation with the deterministic gate


def test_booking_requires_explicit_user_yes(local_agent):
    client, headers, model, path = local_agent
    first = chat(client, headers, "Je veux prendre rendez-vous avec un technicien")
    assert first["route"] == "booking"
    assert "motif" in first["reply"].lower()
    assert first["pending_booking"]["confirmation_pending"] is False
    assert count(path, "appointments") == 0

    second = chat(client, headers, "Le motif : absence d'internet")
    assert second["route"] == "booking"
    assert "créneaux" in second["reply"].lower()
    assert "slots.read" in second["tools_called"]
    assert count(path, "appointments") == 0

    third = chat(client, headers, "Créneau 1")
    assert "Confirmez-vous" in third["reply"]
    assert "Europe/Paris" in third["reply"]
    assert third["pending_booking"] == {
        "customer_id": CUSTOMER, "slot_id": "SLOT-7A31",
        "slot_label": "2026-08-27 09:00–11:00 (Europe/Paris)",
        "reason_id": "no_internet", "confirmation_pending": True,
    }
    assert "appointments.book" not in third["tools_called"]
    assert count(path, "appointments") == 0

    fourth = chat(client, headers, "Oui")
    assert "rendez-vous confirmé" in fourth["reply"].lower()
    assert "Numéro de dossier" in fourth["reply"]
    assert "appointments.book" in fourth["tools_called"]
    assert fourth["pending_booking"] is None
    assert count(path, "appointments") == 1


def test_ambiguous_or_model_generated_yes_never_books(local_agent):
    client, headers, model, path = local_agent
    chat(client, headers, "Je veux prendre rendez-vous avec un technicien")
    chat(client, headers, "Le motif : remplacement d'équipement")
    proposed = chat(client, headers, "Créneau 2")
    assert proposed["pending_booking"]["confirmation_pending"] is True

    # Neither an ambiguous user turn nor anything the model says books.
    hesitating = chat(client, headers, "Je crois que oui")
    assert hesitating["pending_booking"]["confirmation_pending"] is True
    assert "appointments.book" not in hesitating["tools_called"]
    assert count(path, "appointments") == 0

    confirmed = chat(client, headers, "Oui")
    assert "rendez-vous confirmé" in confirmed["reply"].lower()
    assert count(path, "appointments") == 1


def test_change_of_slot_or_reason_requires_fresh_confirmation(local_agent):
    client, headers, model, path = local_agent
    chat(client, headers, "Je veux prendre rendez-vous avec un technicien")
    chat(client, headers, "Le motif : absence d'internet")
    chat(client, headers, "Créneau 1")

    changed = chat(client, headers, "En fait plutôt le créneau 2")
    assert changed["pending_booking"]["slot_id"] == "SLOT-7A33"
    # The old confirmation is void: the new pair must be re-proposed and the
    # user must say yes again before any POST.
    assert changed["pending_booking"]["confirmation_pending"] is True
    assert "Confirmez-vous" in changed["reply"]
    assert "appointments.book" not in changed["tools_called"]
    assert count(path, "appointments") == 0

    refused = chat(client, headers, "Non")
    assert refused["pending_booking"] is None  # refusal clears the offer

    # Reason change also resets confirmation on a fresh proposal.
    chat(client, headers, "Je veux prendre rendez-vous avec un technicien")
    chat(client, headers, "Le motif : installation")
    chat(client, headers, "Créneau 1")
    reason_changed = chat(client, headers, "En fait plutôt internet lent")
    assert reason_changed["pending_booking"]["reason_id"] == "slow_internet"
    # Fresh proposal: the earlier yes (never given) is void either way.
    assert reason_changed["pending_booking"]["confirmation_pending"] is True
    assert count(path, "appointments") == 0

    booked = chat(client, headers, "Oui")
    assert "rendez-vous confirmé" in booked["reply"].lower()
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT slot_id, reason_id FROM appointments").fetchone()
    assert row == ("SLOT-7A31", "slow_internet")


def test_booking_conflict_failure_is_never_a_success(local_agent):
    client, headers, model, path = local_agent
    chat(client, headers, "Je veux prendre rendez-vous avec un technicien")
    chat(client, headers, "Le motif : absence d'internet")
    chat(client, headers, "Créneau 1")
    # The slot is taken directly between the proposal and the user yes.
    intruder = client.post("/appointments", headers=headers, json={
        "customer_id": CUSTOMER, "slot_id": "SLOT-7A31",
        "reason_id": "no_internet", "confirmation_key": "intruder"})
    assert intruder.status_code == 201

    failed = chat(client, headers, "Oui")
    assert "rendez-vous confirmé" not in failed["reply"].lower()
    assert "appointments.book" in failed["tools_called"]
    assert failed["pending_booking"] is None
    assert count(path, "appointments") == 1  # only the direct booking


# ---------------------------------------------------------------------------
# Handoffs: sensitive topics, urgency, no-session generic route


def test_sensitive_topics_immediate_handoff(local_agent):
    client, headers, model, path = local_agent
    fraud = chat(client, headers, "C'est de la fraude, on m'a usurpé mon compte")
    assert fraud["route"] == "sensitive"
    assert isinstance(fraud["handoff_id"], int)
    assert "transmise" in fraud["reply"]
    assert "accepté le dossier" not in fraud["reply"]
    assert count(path, "appointments") == 0
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT urgency, category_id FROM handoffs WHERE id = ?",
            (fraud["handoff_id"],)).fetchone()
    assert row == ("urgent", "other")

    privacy = chat(client, headers, "Je veux supprimer mes données personnelles (RGPD)")
    assert privacy["route"] == "sensitive"
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT urgency FROM handoffs WHERE id = ?",
            (privacy["handoff_id"],)).fetchone()
    assert row == ("normal",)

    distress = chat(client, headers, "Je subis du harcèlement, je n'en peux plus")
    assert distress["route"] == "sensitive"
    assert count(path, "handoffs") == 3


def test_no_session_generic_human_route(local_agent):
    client, headers, model, path = local_agent
    anonymous = client.post(
        "/agent/chat", json={"message": "C'est de la fraude, on m'a usurpé mon compte"})
    assert anonymous.status_code == 200
    result = anonymous.json()
    assert result["handoff_id"] is None
    assert "conseiller" in result["reply"].lower()
    assert "référence de suivi" not in result["reply"]
    assert count(path, "handoffs") == 0

    booking = client.post(
        "/agent/chat", json={"message": "Je veux prendre rendez-vous avec un technicien"})
    result = booking.json()
    assert result["tools_called"] == []
    assert "session" in result["reply"].lower()
    assert count(path, "appointments") == 0

    question = client.post(
        "/agent/chat", json={"message": "Ma connexion internet ne marche pas"})
    result = question.json()
    assert result["route"] == "internet"
    assert result["citations"]  # public corpus still answers anonymously
    assert "NEO-" not in question.text

    termination = client.post(
        "/agent/chat", json={"message": "Je veux résilier mon abonnement"})
    result = termination.json()
    assert result["handoff_id"] is None
    assert "canaux officiels" in result["reply"]
    assert count(path, "handoffs") == 0


def test_pro_contract_immediate_handoff(local_agent):
    client, headers, model, path = local_agent
    token = client.post(
        "/demo/sessions", json={"customer_id": PRO_CUSTOMER}
    ).json()["session_token"]
    pro_headers = {"X-Demo-Session": token}
    result = chat(client, pro_headers, "Je veux prendre rendez-vous avec un technicien")
    assert result["route"] == "sensitive_pro"
    assert result["handoff_id"] is not None
    assert "customer_summary.read" in result["tools_called"]
    assert "slots.read" not in result["tools_called"]
    assert "appointments.book" not in result["tools_called"]
    assert count(path, "appointments") == 0
    assert count(path, "handoffs") == 1


# ---------------------------------------------------------------------------
# Evidence gate uncertainty and scope wording


def test_uncertainty_preserved_for_gate_flags(local_agent):
    client, headers, model, path = local_agent
    pricing = chat(client, headers, "Combien coûte l'abonnement 500 Mb ?")
    assert pricing["route"] == "billing"
    codes = {flag["code"] for flag in pricing["gate_flags"]}
    assert "archived_pricing" in codes
    assert "archivée" in pricing["reply"]
    prompt = model.prompts[-1]
    assert "FLAGS" in prompt and "archived_pricing" in prompt
    assert "ne tranche pas" in prompt  # grounded, uncertainty-preserving prompt

    fees = chat(client, headers, "Quels sont les frais de rejet de prélèvement ?")
    codes = {flag["code"] for flag in fees["gate_flags"]}
    assert "fee_timing_conflict" in codes
    assert "se contredisent" in fees["reply"]


def test_area_only_incident_uses_sector_wording(local_agent):
    client, headers, model, path = local_agent
    token = client.post(
        "/demo/sessions", json={"customer_id": OTHER}
    ).json()["session_token"]
    result = chat(client, {"X-Demo-Session": token},
                  "Ma connexion internet rame, c'est quoi le problème ?")
    assert result["route"] == "internet"
    prompt = model.prompts[-1]
    assert "area_only" in prompt
    assert "secteur" in prompt
    assert "INC-4502" in prompt
    assert "NEO-" not in prompt          # no customer identifiers
    assert "0612840193" not in prompt    # no phone numbers


def test_unsupported_question_and_mutation(local_agent):
    client, headers, model, path = local_agent
    before = count(path, "handoffs")
    question = chat(client, headers, "Quelle est la capitale du Japon ?")
    assert question["route"] == "unsupported"
    assert question["handoff_id"] is None
    assert count(path, "handoffs") == before
    assert count(path, "appointments") == 0

    mutation = chat(client, headers, "Changez mon forfait tout de suite")
    assert mutation["route"] == "unsupported_mutation"
    assert mutation["handoff_id"] is not None
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT category_id FROM handoffs WHERE id = ?",
            (mutation["handoff_id"],)).fetchone()
    assert row == ("other",)


def test_history_reaches_the_grounded_prompt(local_agent):
    client, headers, model, path = local_agent
    history = [{"role": "assistant",
                "content": "Bonjour, comment puis-je vous aider ?"}]
    chat(client, headers, "Ma connexion internet ne marche pas", history=history)
    assert "assistant: Bonjour" in model.prompts[-1]


# ---------------------------------------------------------------------------
# HTTP contract


def test_agent_chat_request_limits_and_auth(local_agent):
    client, headers, model, path = local_agent
    assert client.post("/agent/chat", json={"message": "x" * 5001}).status_code == 422
    assert client.post("/agent/chat", json={"message": "   "}).status_code == 422
    assert client.post("/agent/chat", json={"message": "ok", "extra": 1}).status_code == 422
    long_history = [{"role": "user", "content": "tour"} for _ in range(21)]
    assert client.post(
        "/agent/chat", json={"message": "ok", "history": long_history}
    ).status_code == 422
    assert client.post(
        "/agent/chat", headers={"X-Demo-Session": "bogus-token"},
        json={"message": "ok"}).status_code == 401


def test_confirmation_gate_is_deterministic(local_agent):
    client, headers, model, path = local_agent
    for message in ("Oui", "OUI !", "d'accord", "je confirme", "c'est bon"):
        conversation.reset()
        with sqlite3.connect(path) as conn:
            conn.execute("DELETE FROM appointments")   # restore fixture slots
            conn.execute("UPDATE slots SET available = 1")
        chat(client, headers, "Je veux prendre rendez-vous avec un technicien")
        chat(client, headers, "Le motif : absence d'internet")
        chat(client, headers, "Créneau 1")
        result = chat(client, headers, message)
        assert "rendez-vous confirmé" in result["reply"].lower(), message
    for message in ("peut-être", "je crois que oui", "pourquoi pas"):
        conversation.reset()
        with sqlite3.connect(path) as conn:
            conn.execute("DELETE FROM appointments")
            conn.execute("UPDATE slots SET available = 1")
        chat(client, headers, "Je veux prendre rendez-vous avec un technicien")
        chat(client, headers, "Le motif : absence d'internet")
        chat(client, headers, "Créneau 1")
        result = chat(client, headers, message)
        assert "rendez-vous confirmé" not in result["reply"].lower(), message
        assert count(path, "appointments") == 0
