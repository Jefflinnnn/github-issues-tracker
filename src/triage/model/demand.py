"""Sets a floor under each feature's priority from how many people want it.

The model judges a feature request from what it says; code makes sure
demand isn't ignored. Demand is reactions plus the number of other people
commenting (see ``transform.demand``), ranked against the repository's
other open feature requests:

- the top ``MUST_SHARE`` of them, with at least ``MUST_MIN`` demand, can't
  be below must-have;
- the top ``SHOULD_SHARE``, with at least ``SHOULD_MIN``, can't be below
  should-have.

The floor only raises the model's answer, never lowers it. It is worked
out on every run from current engagement, so a feature that heats up
rises without being sent to the model again.
"""

import math
from collections.abc import Sequence

from triage.model import prompt
from triage.pipeline import transform

MUST_SHARE = 0.05
MUST_MIN = 10
SHOULD_SHARE = 0.20
SHOULD_MIN = 3

_ORDER = list(prompt.Moscow)


def floors(features: Sequence[transform.Issue]) -> dict[str, prompt.Moscow]:
    """Returns the lowest priority each feature request may have.

    Args:
        features: Every open issue tagged as a feature. Each is ranked
            against the others, so pass the full set.

    Returns:
        ``{issue ID: floor}``; could-have means no floor.
    """
    scores = {issue.id: transform.demand(issue) for issue in features}
    ranked = sorted(scores.values(), reverse=True)
    must_cut = max(_cutoff(ranked, MUST_SHARE), MUST_MIN)
    should_cut = max(_cutoff(ranked, SHOULD_SHARE), SHOULD_MIN)
    result = {}
    for issue_id, score in scores.items():
        if score >= must_cut:
            result[issue_id] = prompt.Moscow.MUST
        elif score >= should_cut:
            result[issue_id] = prompt.Moscow.SHOULD
        else:
            result[issue_id] = prompt.Moscow.COULD
    return result


def raise_to(picked: prompt.Moscow, floor: prompt.Moscow) -> prompt.Moscow:
    """Returns the higher of the model's pick and the floor.

    Args:
        picked: What the model chose.
        floor: The lowest the demand allows.

    Returns:
        The final priority.
    """
    return max(picked, floor, key=_ORDER.index)


def _cutoff(ranked: Sequence[int], share: float) -> int:
    """Returns the score at the edge of the top ``share`` of ``ranked``.

    Ties at the edge are all in, so the share can come out larger.
    """
    if not ranked:
        return 0
    return ranked[math.ceil(len(ranked) * share) - 1]
