"""Session-local bounded conversation and explicit-submit idempotency."""
from __future__ import annotations

from src.analyst import provider

MAX_TURNS = 8


def sync(state: dict, snapshot: str) -> bool:
    changed = bool(state.get("snapshot") and state["snapshot"] != snapshot)
    if state.get("snapshot") != snapshot:
        state.clear()
        state.update(snapshot=snapshot, turns=[], last_submission=None)
    return changed


def submit(state: dict, submission: str, config: provider.Config, context: dict,
           question: str) -> bool:
    """Consume an event before network I/O, so reruns cannot repeat even a failed call."""
    if state.get("last_submission") == submission:
        return False
    state["last_submission"] = submission
    state.pop("error", None)
    scope = context["analysis_filter"]
    history = [t for t in state["turns"] if t["context"]["analysis_filter"] == scope]
    try:
        answer = provider.complete(config, context, question, history)
    except provider.AnalystError as exc:
        state["error"] = str(exc)
        return False
    state["turns"].append({"question": question, "answer": answer, "context": context})
    state["turns"] = state["turns"][-MAX_TURNS:]
    return True
