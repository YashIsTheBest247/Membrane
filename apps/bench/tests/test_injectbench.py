"""InjectBench as a regression suite.

This is the test that has to keep passing. It runs the whole corpus and asserts
the three numbers the project asks to be judged on, together — any one of them
in isolation is trivially gamed.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from injectbench.agent import ReferenceAgent  # noqa: E402
from injectbench.harness import run_all, run_unprotected  # noqa: E402
from injectbench.report import TARGETS, render, targets_met  # noqa: E402
from injectbench.schema import load_benign, load_cases  # noqa: E402


@pytest.fixture(scope="module")
def report():
    import asyncio

    return asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
        run_all(label="pytest")
    )


def test_the_corpus_is_the_size_it_claims_to_be():
    cases = load_cases()
    assert len(cases) == 42
    families = {c.family for c in cases}
    assert len(families) == 9
    assert len(load_benign()) >= 20


def test_every_case_declares_a_success_condition():
    for case in load_cases():
        condition = case.success
        assert condition.tool or condition.args_contain or condition.any_arg_contains, (
            f"{case.id} has no success condition, so it cannot be scored"
        )


def test_no_case_targets_a_real_host():
    """Dual-use constraint: every sink is a reserved, unroutable name.

    RFC 2606 and RFC 6761 reserve .invalid, .example and .test. Nothing in this
    corpus can reach a real system even if it is executed by accident.
    """
    import re

    from injectbench.agent import _perceive

    allowed = re.compile(r"\.(invalid|example|test|localhost)$|"
                         r"\.example\.(com|org|net)$|^example\.(com|org|net)$", re.I)
    # Only hostnames under a real top-level domain could resolve. An
    # obfuscated payload such as the ROT13 case carries a nonsense TLD until it
    # is decoded, and the decoded form is checked below.
    real_tlds = {
        "com", "org", "net", "io", "ai", "co", "dev", "app", "sh", "me", "uk",
        "de", "fr", "ru", "cn", "info", "biz", "gov", "edu", "xyz", "top",
        "link", "click", "cloud", "site", "online", "site", "us", "eu", "in",
    }
    host = re.compile(r"(?:https?://|@)([A-Za-z0-9.\-]+\.[A-Za-z]{2,})")

    for case in load_cases():
        raw = case.content + " " + str(case.mcp_tools)
        # Check both what is written and what it decodes to: a payload that
        # only becomes a real host after base64 or ROT13 would still be one.
        for blob in (raw, _perceive(raw)):
            for match in host.finditer(blob):
                hostname = match.group(1).rstrip(".").lower()
                tld = hostname.rsplit(".", 1)[-1]
                if tld not in real_tlds and not allowed.search(hostname):
                    continue  # nonsense TLD: cannot resolve anywhere
                assert allowed.search(hostname), (
                    f"{case.id} references {hostname}, which is not a reserved name"
                )


def test_the_unprotected_baseline_actually_reproduces_every_attack():
    """Without this, the headline reduction figure means nothing."""
    agent = ReferenceAgent()
    unreproduced = [
        case.id for case in load_cases()
        if not case.success.met_by(run_unprotected(case, agent))
    ]
    assert not unreproduced, f"baseline did not reproduce: {unreproduced}"


def test_attack_success_rate_reduction_meets_target(report):
    assert report.unprotected_success == report.total_cases
    assert report.asr_reduction >= TARGETS["asr_reduction_min"], render(report)


def test_false_positive_rate_meets_target(report):
    assert report.false_positive_rate <= TARGETS["false_positive_rate_max"], render(report)


def test_added_latency_meets_target(report):
    assert report.percentile(0.95) <= TARGETS["p95_latency_ms_max"], render(report)


def test_every_family_is_defended(report):
    """No family may be left entirely undefended, whatever the total says."""
    for family, entry in report.by_family.items():
        assert entry["prevented"] > 0, f"{family} is entirely undefended"


def test_all_three_targets_hold_together(report):
    met = targets_met(report)
    assert all(met.values()), render(report)
