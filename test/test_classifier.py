import threading
import time

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
    model: FakeModel,
    budget: float = 1.0,
    workers: int = 1,
    issues: list[transform.Issue] = ISSUES,
) -> tuple[classifier.Outcome, dict[int, str]]:
    saved: dict[int, str] = {}

    def save(item: transform.Issue, tag: prompt.Tag, rationale: str) -> None:
        assert threading.current_thread() is threading.main_thread()
        saved[item.number] = tag.value

    outcome = classifier.classify(issues, model, budget, save, workers)
    return outcome, saved


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


class SlowModel(FakeModel):
    """Takes a moment per request and records how many ran at once."""

    def __init__(self) -> None:
        super().__init__()
        self.lock = threading.Lock()
        self.running = 0
        self.most_at_once = 0

    def classify(self, request: provider.Request) -> provider.Reply:
        with self.lock:
            self.running += 1
            self.most_at_once = max(self.most_at_once, self.running)
        time.sleep(0.02)
        with self.lock:
            self.running -= 1
        return super().classify(request)


def test_workers_run_in_parallel_after_the_first_request() -> None:
    model = SlowModel()
    many = [transform.to_issue(issue(n)) for n in range(1, 21)]

    outcome, saved = _run(model, workers=8, issues=many)

    assert len(saved) == 20
    assert outcome.tagged == 20
    assert 1 < model.most_at_once <= 8


def test_parallel_requests_stay_within_the_budget() -> None:
    model = SlowModel()
    many = [transform.to_issue(issue(n)) for n in range(1, 41)]

    # Each request costs $0.001375, so $0.01 covers 7 of them.
    outcome, saved = _run(model, budget=0.01, workers=16, issues=many)

    assert outcome.spent_usd <= 0.01
    assert len(saved) == 7
    assert outcome.left_for_budget == 33


def test_a_provider_error_stops_the_pass() -> None:
    class Broken(FakeModel):
        def classify(self, request: provider.Request) -> provider.Reply:
            raise provider.ProviderError("key rejected")

    with pytest.raises(provider.ProviderError, match="key rejected"):
        _run(Broken(), workers=4)


def test_workers_out_of_range_are_refused() -> None:
    with pytest.raises(ValueError, match="1 to 64"):
        _run(FakeModel(), workers=65)
