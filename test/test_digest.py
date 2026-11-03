import pytest
from conftest import FakeModel

from triage.model import digest
from triage.model import provider

CHANGED = [
    digest.Item(
        number=7,
        label="bug: urgent",
        summary="Crash on start",
        activity=12,
        note="The fix failed",
        transition="fix-released → fix-failed",
    )
]


def test_the_request_fences_the_material_and_names_the_tool() -> None:
    built = digest.request(6, CHANGED, [], [])

    assert built.tool_name == digest.TOOL_NAME
    assert built.user.startswith("<changes>\nSince the last run (6 days ago)")
    assert (
        "- #7; bug: urgent; fix-released → fix-failed; 12 reactions+comments; "
        "The fix failed" in built.user
    )
    assert built.user.endswith("</changes>")


def test_write_returns_the_digest_and_its_cost() -> None:
    result = digest.write(
        FakeModel(digest="Fix for #7 failed."),
        digest.request(1, CHANGED, [], []),
        1.0,
    )

    assert result.text == "Fix for #7 failed."
    # 1,000 in at $1.10/M plus 50 out at $5.50/M.
    assert result.cost_usd == pytest.approx(0.001375)


def test_write_skips_when_the_budget_cant_cover_it() -> None:
    model = FakeModel()

    result = digest.write(model, digest.request(1, CHANGED, [], []), 0.0001)

    assert result == digest.Result(None, skipped="the budget ran out")
    assert model.digest_requests == []


def test_an_unusable_reply_or_a_failure_costs_only_the_digest() -> None:
    unusable = digest.write(
        FakeModel(digest=None), digest.request(1, CHANGED, [], []), 1.0
    )

    class Down(FakeModel):
        def classify(self, request: provider.Request) -> provider.Reply:
            raise provider.ProviderError("down")

    failed = digest.write(Down(), digest.request(1, CHANGED, [], []), 1.0)

    assert unusable.text is None
    assert unusable.skipped == "the model's reply was unusable"
    assert failed == digest.Result(None, skipped="the request failed")


def test_ago_reads_naturally() -> None:
    assert [digest.ago(days) for days in (0, 1, 6)] == [
        "earlier today",
        "yesterday",
        "6 days ago",
    ]
