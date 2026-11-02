from conftest import comment
from conftest import issue

from triage.model import demand
from triage.model import prompt
from triage.pipeline import transform


def _feature(
    number: int, reactions: int, commenters: int = 0
) -> transform.Issue:
    comments = [comment(f"user{n}", 1) for n in range(commenters)]
    return transform.to_issue(
        issue(number, reactions=reactions, comments=comments)
    )


def test_demand_counts_people_not_comments() -> None:
    chatty = transform.to_issue(
        issue(1, reactions=2, comments=[comment("a", 2), comment("a", 1)])
    )

    assert transform.demand(chatty) == 3


def test_the_busiest_features_get_a_floor() -> None:
    features = [_feature(n, reactions=0) for n in range(1, 19)]
    features += [
        _feature(19, reactions=4),
        _feature(20, reactions=30, commenters=5),
    ]

    floors = demand.floors(features)

    assert floors[features[-1].id] is prompt.Moscow.MUST
    assert floors[features[-2].id] is prompt.Moscow.SHOULD
    assert floors[features[0].id] is prompt.Moscow.COULD


def test_a_quiet_repository_sets_no_floor() -> None:
    features = [_feature(1, reactions=2), _feature(2, reactions=1)]

    assert set(demand.floors(features).values()) == {prompt.Moscow.COULD}


def test_the_floor_only_raises() -> None:
    must, could = prompt.Moscow.MUST, prompt.Moscow.COULD

    assert demand.raise_to(could, must) is must
    assert demand.raise_to(must, could) is must
