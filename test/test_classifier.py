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

    def save(item: transform.Issue, tagging: prompt.Tagging) -> None:
        assert threading.current_thread() is threading.main_thread()
        saved[item.number] = tagging.tag.value

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


def test_progress_counts_tagged_and_skipped_issues() -> None:
    model = FakeModel(
        replies=[reply("bug"), reply(None), reply(None)],
        default=reply("feature"),
    )
    seen: list[tuple[int, int]] = []

    classifier.classify(
        ISSUES, model, 1.0, lambda *_: None, 1, lambda *args: seen.append(args)
    )

    # #1 is tagged, #2 is skipped after two bad replies, #3 is tagged.
    assert seen == [(1, 3), (2, 3), (3, 3)]


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


def test_a_bug_needs_severity_and_priority() -> None:
    usage = provider.Usage(1, 1)
    bare = provider.Reply(
        {"tag": "bug", "summary": "It crashes.", "rationale": "Crash."}, usage
    )

    with pytest.raises(classifier.InvalidReplyError, match="severity"):
        classifier.validate(bare)

    tagging = classifier.validate(reply("bug", severity="high", priority="low"))
    assert (tagging.severity, tagging.priority) == (
        prompt.Level.HIGH,
        prompt.Level.LOW,
    )


def test_fields_for_another_tag_are_dropped() -> None:
    tagging = classifier.validate(reply("question", moscow="must-have"))

    assert tagging.tag is prompt.Tag.QUESTION
    assert tagging.moscow is None


def test_a_subtag_is_optional_for_any_tag() -> None:
    question = classifier.validate(reply("question", subtag="duplicate"))
    bug = classifier.validate(reply("bug", subtag="fix-failed"))

    assert question.subtag is prompt.Subtag.DUPLICATE
    assert bug.subtag is prompt.Subtag.FIX_FAILED
    assert classifier.validate(reply("bug")).subtag is None
    assert classifier.validate(reply("bug", subtag="none")).subtag is None
    with pytest.raises(classifier.InvalidReplyError, match="subtag"):
        classifier.validate(reply("bug", subtag="wontfix"))


def test_a_reply_needs_a_summary_and_a_long_one_is_cut() -> None:
    usage = provider.Usage(1, 1)
    bare = provider.Reply({"tag": "docs", "rationale": "Typo."}, usage)

    with pytest.raises(classifier.InvalidReplyError, match="summary"):
        classifier.validate(bare)

    slightly_long = ("word " * 22).strip()  # 109 characters: kept whole.
    kept = classifier.validate(reply("docs", summary=slightly_long)).summary
    assert kept == slightly_long

    cut = classifier.validate(reply("docs", summary="word " * 60)).summary
    assert len(cut) <= prompt.SUMMARY_HARD_MAX_CHARS
    assert cut.endswith("word…")


def test_a_change_note_is_optional_and_cut_at_a_word() -> None:
    noted = classifier.validate(reply("bug", change="Fix released in 3.4.3"))
    long = classifier.validate(reply("bug", change="word " * 60))

    assert noted.change_note == "Fix released in 3.4.3"
    assert classifier.validate(reply("bug")).change_note is None
    assert long.change_note is not None
    assert len(long.change_note) <= prompt.CHANGE_HARD_MAX_CHARS
