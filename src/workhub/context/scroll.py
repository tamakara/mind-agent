from uuid import UUID

from workhub.context.repository import ScrollRepository
from workhub.domain import ActorContext
from workhub.domain.context import ScrollWindow, SessionTurn


class ScrollBuilder:
    def __init__(self, repository: ScrollRepository) -> None:
        self.repository = repository

    async def build(
        self, actor: ActorContext, session_id: UUID, *, token_budget: int
    ) -> ScrollWindow:
        turns = await self.repository.list_turns(actor, session_id)
        selected: list[SessionTurn] = []
        used = 0
        for turn in reversed(turns):
            cost = _turn_tokens(turn)
            if turn.status == "running" or used + cost <= token_budget:
                selected.append(turn)
                used += cost
            else:
                break
        selected.reverse()
        selected_ids = {turn.turn_id for turn in selected}
        evicted = [turn for turn in turns if turn.turn_id not in selected_ids]
        navigation = _navigation(evicted) if evicted else None
        used += _estimate_tokens(navigation or "")
        return ScrollWindow(
            turns=tuple(selected),
            compressed_navigation=navigation,
            estimated_tokens=used,
        )


def _turn_tokens(turn: SessionTurn) -> int:
    return sum(
        _estimate_tokens(event.text or "")
        + _estimate_tokens(str(event.payload) if event.payload else "")
        + 4
        for event in turn.events
    )


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, (len(text) + 3) // 4)


def _navigation(turns: list[SessionTurn]) -> str:
    visible = turns[-20:]
    lines = ["[context compressed]"]
    if len(turns) > len(visible):
        older = turns[: -len(visible)]
        lines.append(f"Earlier turns: seq {older[0].seq_lo}-{older[-1].seq_hi or older[-1].seq_lo}")
    lines.extend(
        f"seq {turn.seq_lo}-{turn.seq_hi or turn.seq_lo}: {turn.headline or '(no headline)'}"
        for turn in visible
    )
    return "\n".join(lines)
