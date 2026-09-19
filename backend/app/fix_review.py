"""The review conversation about a proposed fix.

Three verbs, not two. `approve` and `reject` are decisions; `revise` is a
conversation -- the human says what they want done differently and the Fix
Council runs again with that in context, producing a new attempt rather than
a terminal no.

That split is taken from the sibling `opencode` deployment, whose plan
approval endpoint explicitly REFUSES `approved=false` and redirects to a
separate replan endpoint. Collapsing "no" and "no, do it this way instead"
into one boolean throws away the only part a human actually knows better than
the council: the approach.

Nothing here touches GitHub, the sandbox, or a worktree. This module decides
and records; the worker executes (plan.md §15).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select, update

from app.enums import FixReviewStatus, FixStatus, IssueStatus
from app.models import Fix, FixReview, Issue

logger = logging.getLogger("whipguard.fix_review")

# How many times a human may send it back before the thread stops accepting
# revisions. Not arbitrary: each revision is a full council run -- retrieval,
# patch generation, a sandbox verification and an adversarial score -- so an
# unbounded loop is real money spent on an issue nobody is going to merge.
# The sibling deployment bounds its own discovery loop the same way
# (PlanningSession.clarification_count).
MAX_REVISIONS = 5

# The transcript is replayed into the next attempt's prompt. Long threads are
# the normal case by the third revision, so it is budgeted rather than
# truncated at the model's limit by accident.
_TRANSCRIPT_BUDGET_TOKENS = 1200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def turn(role: str, text: str, *, kind: str = "", fix_id=None) -> dict:
    entry = {"role": role, "text": text, "at": _now()}
    if kind:
        entry["kind"] = kind
    if fix_id is not None:
        entry["fix_id"] = str(fix_id)
    return entry


def proposal_summary(fix: Fix, verdict: str, rubric: list[dict] | None) -> str:
    """What the council says when it puts an attempt on the table.

    Written as prose rather than a JSON dump because it is the first thing a
    human reads in the thread and the thing they reply to.
    """
    lines = [f"Attempt {fix.attempt}: resolution confidence {fix.resolution_score}/100."]
    if verdict:
        lines.append(f"Arbiter verdict: {verdict}")
    for factor in (rubric or [])[:4]:
        name = str(factor.get("factor") or factor.get("name") or "").strip()
        note = str(factor.get("note") or factor.get("reason") or "").strip()
        if name and note:
            lines.append(f"- {name}: {note}")
    lines.append("Review the diff below. Approve it, or tell me what to do differently.")
    return "\n".join(lines)


async def open_review(db, issue_id, fix: Fix, *, summary: str) -> FixReview:
    """Start (or continue) the thread for this issue and put `fix` on it.

    A second council run for an issue that already has a thread -- a retry
    after a verification failure, say -- appends rather than starting over.
    The conversation is about the issue, not about one attempt.
    """
    review = (
        await db.execute(select(FixReview).where(FixReview.issue_id == issue_id))
    ).scalars().first()

    if review is None:
        review = FixReview(issue_id=issue_id, transcript=[], attempts=0)
        db.add(review)
        await db.flush()

    review.current_fix_id = fix.id
    review.status = FixReviewStatus.AWAITING_DECISION.value
    review.attempts = (review.attempts or 0) + 1
    # Reassigned rather than appended in place: JSONB columns are mutable
    # Python lists, and SQLAlchemy does not see an in-place .append() as a
    # change, so the turn is silently never persisted.
    review.transcript = [*(review.transcript or []), turn("council", summary, kind="proposal", fix_id=fix.id)]
    await db.flush()
    return review


async def record_decision(db, review: FixReview, *, actor: str, surface: str, decision: str, note: str) -> None:
    """Append the human's decision to the thread, exactly as they wrote it."""
    text = note.strip() or f"{decision.capitalize()} (no reason given)."
    review.transcript = [
        *(review.transcript or []),
        turn("human", text, kind=f"decision:{decision}"),
    ]
    review.status = (
        FixReviewStatus.APPROVED.value if decision == "approved" else FixReviewStatus.REJECTED.value
    )
    logger.info("review %s %s by %s via %s", review.id, decision, actor, surface)


class RevisionRejected(Exception):
    """Why a revision could not be accepted, phrased for the person asking."""


async def request_revision(db, fix_id, *, instruction: str, actor: str, surface: str) -> dict:
    """Record a human's "do it differently" and queue the rework.

    The status transition is an atomic conditional UPDATE, not a read-then-
    write. A double-clicked button or a retried request would otherwise both
    read AWAITING_APPROVAL, both pass a plain equality check, and both queue a
    council run against the same fix -- two concurrent attempts appending to
    one transcript. The sibling deployment hit exactly this with its planning
    sessions (four replies landed with no assistant turn between them and the
    thread's state came back unreadable) and fixed it the same way: let one
    caller through at the database, and tell everyone else plainly.
    """
    instruction = (instruction or "").strip()
    if not instruction:
        raise RevisionRejected("Say what you want done differently -- an empty revision has nothing to act on.")

    fix = await db.get(Fix, fix_id)
    if fix is None:
        raise RevisionRejected("That fix no longer exists.")

    issue = await db.get(Issue, fix.issue_id)
    if issue is None:
        raise RevisionRejected("That fix's issue no longer exists.")

    review = (
        await db.execute(select(FixReview).where(FixReview.issue_id == fix.issue_id))
    ).scalars().first()
    if review is not None and (review.attempts or 0) >= MAX_REVISIONS:
        raise RevisionRejected(
            f"This fix has already been reworked {review.attempts} times (limit {MAX_REVISIONS}). "
            "Reject it and reopen the issue if the approach still is not right."
        )

    claimed = await db.execute(
        update(Fix)
        .where(Fix.id == fix_id, Fix.status == FixStatus.AWAITING_APPROVAL)
        .values(status=FixStatus.REVISING, decision_note=instruction)
    )
    if claimed.rowcount != 1:
        await db.refresh(fix)
        raise RevisionRejected(
            f"This fix is no longer awaiting a decision (it is {fix.status.value}) -- nothing was queued."
        )

    if review is not None:
        review.transcript = [
            *(review.transcript or []),
            turn("human", instruction, kind="revision-request", fix_id=fix_id),
        ]
        review.status = FixReviewStatus.REVISING.value

    await db.commit()

    from app.work_queue import enqueue

    await enqueue(
        "revise_fix",
        {"fix_id": str(fix_id), "issue_id": str(fix.issue_id), "actor": actor, "surface": surface},
    )
    logger.info("revision queued for fix %s by %s via %s", fix_id, actor, surface)
    return {"ok": True, "status": FixStatus.REVISING.value, "queued": True}


def compile_feedback(transcript: list[dict] | None) -> str:
    """Turn the whole conversation into the `prior_rejection` the Fix Council
    already knows how to read.

    Every human turn, not just the latest one. "Use the existing helper" said
    on attempt 1 is still true on attempt 3, and a council prompted with only
    the most recent sentence re-breaks whatever the earlier ones asked for --
    which reads to the human as the system ignoring them.
    """
    from app.prompt_compiler import fit_text

    lines: list[str] = []
    for entry in transcript or []:
        if entry.get("role") != "human":
            continue
        text = str(entry.get("text") or "").strip()
        if text:
            lines.append(f"- {text}")

    if not lines:
        return ""
    body = "\n".join(lines)
    return fit_text(
        "A human reviewed earlier attempts and asked for these changes. "
        "Every one of them still applies:\n" + body,
        _TRANSCRIPT_BUDGET_TOKENS,
    )


async def reopen_issue(issue: Issue | None) -> None:
    """A rejected fix must leave its issue actionable again.

    The dashboard's retry control renders only for `raised`; without this a
    rejection parks the issue at `fix-proposed` forever with no way back --
    the same dead end the approval flow's own failure paths already call
    `_reopen_issue_for_retry` to avoid.
    """
    if issue is not None and issue.status != IssueStatus.CLOSED:
        issue.status = IssueStatus.RAISED
