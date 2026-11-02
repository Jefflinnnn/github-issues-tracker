import pytest
from conftest import FakeModel
from conftest import issue
from conftest import reply

from triage.model import classifier
from triage.model import prompt
from triage.model import provider
from triage.pipeline import transform

ISSUES = [transform.to_issue(issue(n)) for n in (1, 2, 3)]


def _run(
    model: FakeModel, budget: float = 1.0
) -> tuple[classifier.Outcome, dict[int, str]]:
    saved: dict[int, str] = {}

    def save(item: transform.Issue, tag: prompt.Tag, rationale: str) -> None:
        saved[item.number] = tag.value

    return classifier.classify(ISSUES, model, budget, save), saved


def test_tags_every_issue_and_adds_up_spend() -> None:
    outcome, saved = _run(FakeModel(default=reply("feature")))

    assert saved == {1: "feature", 2: "feature", 3: "feature"}
    # 1,000 in at $1.10/M plus 50 out at $5.50/M, three times.
    assert outcome.spent_usd == pytest.approx(3 * 0.001375)


def test_an_unusable_reply_is_retried_with_the_reason() -> None:
    model = FakeModel(replies=[reply("chore")])

    outcome, saved = _run(model)

    assert saved[1] == "bug"
    assert "isn't one of" in model.requests[1].user
    assert outcome.skipped == []


def test_two_unusable_replies_skip_the_issue() -> None:
    outcome, saved = _run(FakeModel(replies=[reply(None), reply(None)]))

    assert outcome.skipped == [1]
    assert sorted(saved) == [2, 3]


def test_stops_before_a_request_could_cross_the_budget() -> None:
    # Each request costs $0.001375; after two, a third would pass $0.003.
    outcome, saved = _run(FakeModel(), budget=0.003)

    assert sorted(saved) == [1, 2]
    assert outcome.left_for_budget == 1
    assert outcome.spent_usd <= 0.003


def test_a_model_without_a_price_sends_nothing() -> None:
    model = FakeModel(price=None)

    with pytest.raises(provider.ProviderError, match="no price"):
        _run(model)
    assert model.requests == []
