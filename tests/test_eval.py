"""Issue #7 eval-runner contract tests: schema, sanitization, visibility.

These tests run the committed runner end-to-end (offline, fakes) once
per module and check the runner contract — not the case outcomes
themselves: a failing eval case is evidence to report, not a suite
failure. All execution is offline (temp databases, fakes, no network).
"""

import json
from pathlib import Path

import pytest

import neova.eval as eval_runner


@pytest.fixture(scope="module")
def offline_results(tmp_path_factory):
    """Run the committed case set once; share the artifacts."""
    out_dir = tmp_path_factory.mktemp("eval-results")
    exit_code = eval_runner.main(["--out", str(out_dir)])
    assert exit_code == 0
    results = json.loads((out_dir / "results.json").read_text(encoding="utf-8"))
    markdown = (out_dir / "results.md").read_text(encoding="utf-8")
    return results, markdown


def test_cases_fixture_declares_a_small_fixed_set():
    spec = json.loads(eval_runner.CASES_PATH.read_text(encoding="utf-8"))
    cases = spec["cases"]
    assert 10 <= len(cases) <= 14  # the committed ~12-case target
    ids = [case["id"] for case in cases]
    assert len(ids) == len(set(ids)), "case ids must be unique"
    categories = {case["category"] for case in cases}
    assert categories == {"routing", "grounding", "booking", "handoff",
                          "resilience", "privacy"}
    for case in cases:
        assert case["expect"]["route"], case["id"]
        assert isinstance(case["expect"]["reply_must_not"], list), case["id"]
        assert case["expect"].get("booking_saved") is not None, case["id"]


def test_runner_emits_machine_checkable_results(offline_results):
    results, _ = offline_results
    assert results["run"]["mode"] == "offline"
    assert results["run"]["cases_total"] == len(results["cases"])
    assert results["summary"]["passed"] + results["summary"]["failed"] \
        == len(results["cases"])
    for case in results["cases"]:
        assert set(case) >= {"id", "category", "pass", "checks",
                             "observed", "analysis"}
        assert case["checks"], f"case {case['id']} must record its checks"
        for check in case["checks"]:
            assert set(check) == {"check", "pass", "expected", "observed"}


def test_results_are_sanitized(offline_results):
    """No customer-record values, no account fields, no tokens/keys."""
    results, markdown = offline_results
    blob = json.dumps([case["observed"] for case in results["cases"]])
    for forbidden in ("monthly_price", "balance_due", "open_incident_id",
                      "session_token", "sk-lf-", "pk-lf-", "X-Demo-Session",
                      "NEO-88213", "NEO-10467"):
        assert forbidden not in blob, forbidden
    assert "sk-lf-" not in markdown and "NEO-88213" not in markdown


def test_failing_cases_stay_visible(offline_results):
    """Whatever fails must appear in results.md with its analysis."""
    results, markdown = offline_results
    for case in results["cases"]:
        if case["pass"]:
            continue
        assert case["id"] in markdown
        assert case["analysis"], case["id"]
        for check in case["checks"]:
            if not check["pass"]:
                assert check["check"] in markdown
    if results["summary"]["failed"] == 0:
        assert "All cases passed" in markdown


def test_report_renders_a_failing_case_with_analysis(offline_results):
    results, _ = offline_results
    import copy
    synthetic = copy.deepcopy(results)
    failed = synthetic["cases"][0]
    failed["pass"] = False
    failed["checks"][0]["pass"] = False
    failed["checks"][0]["observed"] = "unsupported"
    failed["analysis"] = eval_runner._one_line_analysis(failed)
    synthetic["summary"]["failed"] = 1
    synthetic["summary"]["passed"] -= 1
    markdown = eval_runner._results_markdown(synthetic)
    assert "## Failed cases" in markdown
    assert failed["id"] in markdown
    assert "routed `unsupported` instead of `internet`" in markdown


def test_fault_cases_are_deterministic():
    """The injected 429 run yields the same outcome twice."""
    spec = json.loads(eval_runner.CASES_PATH.read_text(encoding="utf-8"))
    case = next(c for c in spec["cases"] if c["fault"] == "chat_429")
    first = eval_runner._run_case(case, "offline")
    second = eval_runner._run_case(case, "offline")
    assert first["pass"] == second["pass"]
    assert first["observed"]["route"] == second["observed"]["route"]
    assert first["checks"] == second["checks"]
    assert first["observed"]["degraded"] == second["observed"]["degraded"]


def test_exhausted_read_is_recorded_as_attempted_tool(offline_results):
    results, _ = offline_results
    case = next(item for item in results["cases"]
                if item["id"] == "api-500-read-recovery")
    assert "customer_summary.read" in case["observed"]["tools_called"]
    assert "api_read_failed" in case["observed"]["degraded"]


def test_provider_faults_exhaust_bounded_attempts(offline_results):
    results, _ = offline_results
    for code in ("chat_429", "chat_529"):
        case = next(item for item in results["cases"] if item["fault"] == code)
        assert case["pass"]
        assert case["observed"]["provider_attempts"] == 3
