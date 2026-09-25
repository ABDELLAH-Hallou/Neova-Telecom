"""Fixed evaluation runner (issue #7): declared outcomes, measured results.

Reads the committed fixture set ``eval/cases.json`` and runs each case
against the real bounded graph through the FastAPI app, then applies
the rubric checks (see ``eval/rubric.md``) and writes two artifacts:

- ``eval/results.json`` — machine-checkable per-case outcomes;
- ``eval/results.md``  — human-readable run report; failing cases stay
  visible with a one-line error analysis.

Modes:

- **offline (default):** deterministic and free — fake chat model and
  fake embedder (the same doubles the test suite uses, imported at run
  time only so production imports stay clean), keyword fallback router,
  temp database seeded from the real fixture, frozen demo clock, and
  fault injection for the API-500 / OpenRouter-429 / 529 cases. No
  network is touched and Langfuse tracing is forced off.
- **--live:** real models behind the budget gate (``ensure_budget``),
  same cases and checks, ``USAGE_LOG`` honored; requires the OpenRouter
  key and model configuration. User-invoked.

Artifacts are sanitized by construction: no customer-record values, no
API/session tokens — only routes, tool names, flag codes, degradation
markers, booking state and truncated French replies.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
from pathlib import Path

from neova import db, retrieval

REPO_ROOT = Path(__file__).resolve().parents[1]
EVAL_DIR = REPO_ROOT / "eval"
CASES_PATH = EVAL_DIR / "cases.json"

CONFIRM_RE = re.compile(r"CONFIRMER RDV ([A-Z2-9]{4})")
CODE_PLACEHOLDER = "{code}"

_OFFLINE_ENV = {
    "CLOCK_MODE": "frozen",
    "DEMO_TIMESTAMP": "2026-08-26T12:00:00+02:00",
    "CHAT_MODEL": "offline-fake-chat",
}
_OFFLINE_UNSET = ("OPENROUTER_API_KEY", "LANGFUSE_PUBLIC_KEY",
                  "LANGFUSE_SECRET_KEY", "CHAT_FALLBACK_MODEL", "USAGE_LOG")


# ---------------------------------------------------------------------------
# Environment and fault injection


def _swap_env(values: dict[str, str], unset: tuple[str, ...]) -> dict:
    saved = {key: os.environ.get(key) for key in list(values) + list(unset)}
    for key, value in values.items():
        os.environ[key] = value
    for key in unset:
        os.environ.pop(key, None)
    return saved


def _restore_env(saved: dict) -> None:
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


class _FaultyApi:
    """Exhaust both retries of the essential customer-summary read."""

    def __init__(self, real) -> None:
        self._real = real

    def request(self, method: str, path: str, json=None, headers=None):
        if method == "GET" and path.endswith("/summary"):
            from types import SimpleNamespace
            return SimpleNamespace(
                status_code=500, json=lambda: {"detail": "Simulated API failure"})
        return self._real.request(method, path, json=json, headers=headers)


def _install_fault(fault: str | None) -> list:
    """Install the case's injected fault; return the undo callables."""
    undos: list = []
    if fault == "api_500":
        from neova import tools
        original = tools._transport_client
        real_transport = original()
        tools._transport_client = lambda: _FaultyApi(real_transport)
        undos.append(lambda: setattr(tools, "_transport_client", original))
    elif fault in ("chat_429", "chat_529"):
        from neova import provider
        status = 429 if fault == "chat_429" else 529

        def _overloaded_transport():
            def call(request: dict) -> dict:
                raise provider.RouteError(status, None, "injected overload")
            return call

        original = provider.openrouter_transport
        real_sleep = time.sleep
        provider.openrouter_transport = _overloaded_transport
        time.sleep = lambda seconds: None  # bounded waits stay deterministic
        undos.append(lambda: setattr(provider, "openrouter_transport", original))
        undos.append(lambda: setattr(time, "sleep", real_sleep))
    return undos


def _install_offline_fakes(model, embedder) -> list:
    """Patch the node seams with the offline doubles; return undo callables."""
    from neova.nodes import classify, french_answer, gather

    originals = (
        (french_answer, "answer_model", french_answer.answer_model),
        (gather, "embedder_factory", gather.embedder_factory),
        (classify, "classifier_factory", classify.classifier_factory),
    )
    french_answer.answer_model = lambda: model
    gather.embedder_factory = lambda: embedder
    classify.classifier_factory = lambda: None

    def _undo() -> None:
        for module, name, value in originals:
            setattr(module, name, value)

    return [_undo]


# ---------------------------------------------------------------------------
# Case execution


def _index_template(path: str, mode: str) -> None:
    """Build one public-only index from the supplied corpus, not canned text."""
    if mode == "offline":
        from tests.test_conversation import FakeEmbedder
        embedder, model = FakeEmbedder(), "fake-model"
    else:
        from neova.config import get_embedding_model
        from neova.embeddings import openrouter_embedder
        embedder, model = openrouter_embedder(), get_embedding_model()
    conn = db.connect(path)
    try:
        report = retrieval.index_corpus(conn, embedder, model)
        if report["state"] != "complete":
            raise RuntimeError("Evaluation index incomplete")
    finally:
        conn.close()


def _appointments_count(path: str, customer_id: str | None) -> int:
    if not customer_id:
        return 0
    conn = sqlite3.connect(path)
    try:
        return conn.execute(
            "SELECT COUNT(*) FROM appointments WHERE customer_id = ?",
            (customer_id,)).fetchone()[0]
    finally:
        conn.close()


def _run_case(case: dict, mode: str, template: str | None = None) -> dict:
    """Execute one case in its own isolated environment; return its record."""
    from fastapi.testclient import TestClient

    from neova import conversation, db, tools, usage
    from neova.app import app

    fault = case.get("fault")
    db_env_saved = os.environ.get("DATABASE_URL")
    undos: list = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "neova.db")
            if template is not None:
                shutil.copyfile(template, db_path)
            os.environ["DATABASE_URL"] = f"sqlite:///{db_path}"
            conversation.reset()
            tools.reset_transport()
            usage.reset()
            if template is None:
                _index_template(db_path, mode)
            if mode == "offline":
                from tests.test_conversation import FakeChatModel, FakeEmbedder
                embedder = FakeEmbedder()
                undos += _install_offline_fakes(FakeChatModel(), embedder)
                if fault in ("chat_429", "chat_529"):
                    from neova.nodes import french_answer
                    from neova.provider import PolicyChatModel
                    old = french_answer.answer_model
                    french_answer.answer_model = lambda: PolicyChatModel()
                    undos.append(lambda: setattr(french_answer, "answer_model", old))
            undos += _install_fault(fault)
            with TestClient(app) as client:
                headers: dict = {}
                if case.get("customer_id"):
                    headers = {"X-Demo-Session": client.post(
                        "/demo/sessions",
                        json={"customer_id": case["customer_id"]}
                    ).json()["session_token"]}
                result: dict = {}
                code = ""
                all_tools: list[str] = []
                routes: list[str] = []
                for turn in case["turns"]:
                    message = turn["message"].replace(CODE_PLACEHOLDER, code)
                    response = client.post(
                        "/agent/chat", headers=headers,
                        json={"message": message})
                    assert response.status_code == 200, "agent request failed"
                    result = response.json()
                    all_tools.extend(result.get("tools_called", []))
                    routes.append(result.get("route", "unknown"))
                    match = CONFIRM_RE.search(result.get("reply", ""))
                    if match:
                        code = match.group(1)
                observed = {
                    "route": result.get("route", "unknown"),
                    "routes": routes,
                    "tools_called": all_tools,
                    "gate_flag_codes": [flag.get("code")
                                        for flag in result.get("gate_flags", [])],
                    "degraded": list(result.get("degraded", [])),
                    "citations_count": len(result.get("citations", [])),
                    "handoff_stored": result.get("handoff_id") is not None,
                    "_reply": result.get("reply", ""),
                    "booking_saved": _appointments_count(
                        db_path, case.get("customer_id")) > 0,
                    "provider_attempts": sum(
                        1 for record in usage.records()
                        if record.kind == usage.KIND_CHAT),
                }
            return _apply_checks(case, observed)
    finally:
        for undo in reversed(undos):
            undo()
        conversation.reset()
        db.close_session_connections()
        if db_env_saved is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = db_env_saved


def _check(name: str, passed: bool, expected, observed) -> dict:
    return {"check": name, "pass": bool(passed),
            "expected": expected, "observed": observed}


def _apply_checks(case: dict, observed: dict) -> dict:
    expect = case["expect"]
    checks: list[dict] = []

    tools_called = set(observed["tools_called"])
    if expect.get("route") is not None:
        first_route = observed["routes"][0]
        checks.append(_check("route", first_route == expect["route"],
                             expect["route"], first_route))
    if expect.get("tools_include"):
        missing = sorted(set(expect["tools_include"]) - tools_called)
        checks.append(_check("tools_include", not missing,
                             expect["tools_include"], observed["tools_called"]))
    if expect.get("tools_exclude"):
        leaked = sorted(set(expect["tools_exclude"]) & tools_called)
        checks.append(_check("tools_exclude", not leaked,
                             expect["tools_exclude"], leaked))
    if expect.get("gate_flags"):
        missing = sorted(set(expect["gate_flags"])
                         - set(observed["gate_flag_codes"]))
        checks.append(_check("gate_flags", not missing,
                             expect["gate_flags"], observed["gate_flag_codes"]))
    if expect.get("citations_present") is not None:
        wanted = expect["citations_present"]
        checks.append(_check(
            "citations",
            (observed["citations_count"] > 0) if wanted
            else (observed["citations_count"] == 0),
            wanted, observed["citations_count"]))
    if expect.get("reply_must"):
        reply = observed["_reply"]
        missing = [needle for needle in expect["reply_must"]
                   if needle.casefold() not in reply.casefold()]
        checks.append(_check("reply_must", not missing,
                             expect["reply_must"], missing))
    if expect.get("reply_must_not"):
        reply = observed["_reply"]
        leaked = [needle for needle in expect["reply_must_not"]
                  if needle.casefold() in reply.casefold()]
        checks.append(_check("reply_must_not", not leaked,
                             expect["reply_must_not"], leaked))
    if expect.get("degraded_include"):
        missing = sorted(set(expect["degraded_include"])
                         - set(observed["degraded"]))
        checks.append(_check("degraded_include", not missing,
                             expect["degraded_include"], observed["degraded"]))
    if expect.get("handoff_stored") is not None:
        checks.append(_check("handoff_stored",
                             observed["handoff_stored"] == expect["handoff_stored"],
                             expect["handoff_stored"], observed["handoff_stored"]))
    if expect.get("booking_saved") is not None:
        checks.append(_check("booking_saved",
                             observed["booking_saved"] == expect["booking_saved"],
                             expect["booking_saved"], observed["booking_saved"]))
    passed = all(check["pass"] for check in checks)
    observed.pop("_reply", None)  # no raw customer messages or replies in artifacts
    return {"id": case["id"], "category": case["category"],
            "fault": case.get("fault"), "pass": passed, "checks": checks,
            "observed": observed}


# ---------------------------------------------------------------------------
# Retrieval-mode comparison (measurement, not a retrieval change)


def _retrieval_comparison(questions: list[str]) -> dict:
    """Top-three source ids per mode (semantic/FTS5/hybrid) per question."""
    from tests.test_conversation import FakeEmbedder

    embedder = FakeEmbedder()
    rows: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as tmp:
        conn = db.connect(str(Path(tmp) / "cmp.db"))
        try:
            retrieval.index_corpus(conn, embedder, "fake-model")
            for question in questions:
                per_mode: dict[str, list[str]] = {}
                for mode in ("semantic", "fts", "hybrid"):
                    outcome = retrieval.search(
                        conn, question, embed_fn=embedder,
                        model="fake-model", mode=mode)
                    per_mode[mode] = [passage.source_id
                                      for passage in outcome.results]
                rows[question] = per_mode
                rows[question]["agreement_with_hybrid_top3"] = {
                    mode: len(set(per_mode[mode]) & set(per_mode["hybrid"]))
                    for mode in ("semantic", "fts")}
        finally:
            conn.close()
    return rows


# ---------------------------------------------------------------------------
# Artifacts


def _one_line_analysis(case: dict) -> str:
    failing = [check for check in case["checks"] if not check["pass"]]
    if not failing:
        return ""
    parts: list[str] = []
    for check in failing:
        if check["check"] == "route":
            parts.append(f"routed `{check['observed']}` instead of "
                         f"`{check['expected']}`")
        elif check["check"] in ("tools_include", "gate_flags",
                                "degraded_include", "reply_must"):
            parts.append(f"missing {check['observed']}")
        elif check["check"] in ("tools_exclude", "reply_must_not"):
            parts.append(f"unexpected {check['observed']}")
        else:
            parts.append(f"{check['check']} expected {check['expected']}, "
                         f"observed {check['observed']}")
    return "; ".join(parts)


def _results_markdown(results: dict) -> str:
    lines = ["# Fixed evaluation run (issue #7)", ""]
    run = results["run"]
    lines += [f"- Mode: `{run['mode']}` — clock: {run['clock']}",
              f"- Chat: `{run['chat_model']}` · classifier: "
              f"`{run['classifier']}` · embedder: `{run['embedder']}`",
              f"- Cases: {results['summary']['passed']}/"
              f"{results['summary']['total']} passed "
              f"({results['summary']['failed']} failed)", ""]
    lines.append("| Case | Category | Result |")
    lines.append("| --- | --- | --- |")
    for case in results["cases"]:
        lines.append(f"| `{case['id']}` | {case['category']} | "
                     f"{'PASS' if case['pass'] else 'FAIL'} |")
    failures = [case for case in results["cases"] if not case["pass"]]
    if failures:
        lines += ["", "## Failed cases (visible on purpose)", ""]
        for case in failures:
            lines.append(f"### `{case['id']}` ({case['category']})")
            for check in case["checks"]:
                if not check["pass"]:
                    lines.append(
                        f"- **{check['check']}**: expected "
                        f"`{json.dumps(check['expected'], ensure_ascii=False)}`, "
                        f"observed "
                        f"`{json.dumps(check['observed'], ensure_ascii=False)}`")
            lines.append(f"- Analysis: {case['analysis']}")
            lines.append("")
    else:
        lines += ["", "All cases passed; no failed case to display.", ""]
    lines += ["## Retrieval-mode comparison (top-3 source ids)", ""]
    comparison = results["retrieval_comparison"]
    if "note" in comparison:
        lines.append(comparison["note"])
    else:
        for question, per_mode in comparison.items():
            lines.append(f"- `{question}`")
            for mode in ("semantic", "fts", "hybrid"):
                lines.append(f"  - {mode}: {per_mode[mode]}")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m neova.eval",
        description="Fixed evaluation runner (issue #7): offline by default.")
    parser.add_argument("--live", action="store_true",
                        help="Run with real models behind the budget gate "
                             "(requires .env credentials; user-invoked).")
    parser.add_argument("--out", default=None,
                        help="Output directory (default eval/).")
    arguments = parser.parse_args(argv)
    mode = "live" if arguments.live else "offline"

    spec = json.loads(CASES_PATH.read_text(encoding="utf-8"))
    out_dir = Path(arguments.out) if arguments.out else EVAL_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    if mode == "offline":
        saved = _swap_env(_OFFLINE_ENV, _OFFLINE_UNSET)
    else:
        from neova.embeddings import BudgetExceeded, ensure_budget
        try:
            ensure_budget(1.0)
        except BudgetExceeded as error:
            print(json.dumps({"error": f"budget gate: {error}"},
                             ensure_ascii=False))
            return 1
        saved = _swap_env({
            "CLOCK_MODE": str(spec["fixture_clock"]["mode"]),
            "DEMO_TIMESTAMP": str(spec["fixture_clock"]["timestamp"]),
        }, ())

    case_records: list[dict] = []
    template_dir = tempfile.TemporaryDirectory()
    try:
        template = str(Path(template_dir.name) / "index.db")
        _index_template(template, mode)
        for case in spec["cases"]:
            try:
                record = _run_case(case, mode, template=template)
            except AssertionError:  # a broken run is a visible failure
                record = {"id": case["id"], "category": case["category"],
                          "fault": case.get("fault"), "pass": False,
                          "checks": [], "observed": {},
                          "analysis": "runner error: request or fixture assertion failed"}
            record.setdefault("analysis", "")
            if not record["pass"] and not record["analysis"]:
                record["analysis"] = _one_line_analysis(record)
            case_records.append(record)
        by_category: dict[str, dict] = {}
        for record in case_records:
            bucket = by_category.setdefault(
                record["category"], {"passed": 0, "total": 0})
            bucket["total"] += 1
            bucket["passed"] += 1 if record["pass"] else 0
        results = {
            "run": {
                "mode": mode,
                "clock": spec["fixture_clock"],
                "chat_model": "real CHAT_MODEL via provider policy"
                if mode == "live" else "FakeChatModel (offline double)",
                "embedder": "real EMBEDDING_MODEL" if mode == "live"
                else "FakeEmbedder (offline double)",
                "classifier": "real CLASSIFIER_MODEL" if mode == "live"
                else "keyword fallback router",
                "faults": "deterministic injection (api_500, chat_429, chat_529)",
                "cases_total": len(case_records),
            },
            "summary": {
                "passed": sum(1 for r in case_records if r["pass"]),
                "failed": sum(1 for r in case_records if not r["pass"]),
                "total": len(case_records),
                "by_category": by_category,
            },
            "cases": case_records,
        }
        if mode == "offline":
            results["retrieval_comparison"] = _retrieval_comparison(
                spec["retrieval_comparison_questions"])
        else:
            results["retrieval_comparison"] = {
                "note": "not part of the live run (embedding budget); "
                        "see the offline run for the mode comparison"}
        (out_dir / "results.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2),
            encoding="utf-8")
        (out_dir / "results.md").write_text(
            _results_markdown(results), encoding="utf-8")
        print(json.dumps(results["summary"], ensure_ascii=False))
        return 0
    finally:
        template_dir.cleanup()
        _restore_env(saved)


if __name__ == "__main__":
    raise SystemExit(main())
