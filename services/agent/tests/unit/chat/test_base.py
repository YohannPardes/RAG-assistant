"""The chat contract's guarantees: an engine bug fails here, not on the user's screen."""

import pytest
from pydantic import ValidationError

from siteops_agent.chat.base import TicketProposal

from ..api.conftest import TICKET, chat_turn


def test_a_well_formed_turn_is_accepted() -> None:
    assert chat_turn().answered


def test_every_marker_in_the_answer_must_be_a_citation() -> None:
    with pytest.raises(ValidationError, match=r"cites \[3\]"):
        chat_turn(answer="See [1] and [3].")


def test_citation_numbers_are_unique() -> None:
    turn = chat_turn()

    with pytest.raises(ValidationError, match="unique"):
        chat_turn(citations=[turn.citations[0], turn.citations[0]], proposal=None, answer="[1]")


def test_quote_offsets_must_lie_inside_the_chunk() -> None:
    runbook = chat_turn().citations[0]

    with pytest.raises(ValidationError, match="offsets"):
        runbook.model_validate(runbook.model_dump() | {"quote_end": 10_000})


@pytest.mark.parametrize(
    ("evidence", "runbook"),
    [([1], 1), ([2], 2), ([9], None), ([2], 9)],
    ids=["evidence-is-a-runbook", "runbook-is-a-log", "evidence-unknown", "runbook-unknown"],
)
def test_a_proposal_points_only_at_citations_of_its_kind(evidence, runbook) -> None:
    with pytest.raises(ValidationError, match="proposal"):
        chat_turn(proposal=TicketProposal(ticket=TICKET, evidence=evidence, runbook=runbook))
