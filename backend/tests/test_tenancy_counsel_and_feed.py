"""The three surfaces the org-scoping pass missed.

`repos.org_id` is the one column carrying tenancy, and the pass that taught
`api.py` and `fix_review.py` to read it stopped there. Counsel, the activity
websocket and the clarification endpoints each kept a way across the boundary,
and two of them were worse than a read:

- the websocket had no authentication at all — `@app.middleware("http")` does
  not run for a websocket scope, and `/ws/activity` is not under `/api/`
- `POST /api/human-input/{id}/answer` enqueues a `fix_council` work item, so an
  answer is a way to steer another organisation's council run
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, text

from app.db import async_session
from app.enums import IssueStatus
from app.enums import UserRole, UserStatus
from app.models import HumanInputRequest, Issue, Repo, User
from app.routers import counsel as counsel_router
from app.routers import human_input as human_input_router
from app.routers import ws as ws_router


@pytest.fixture
async def two_orgs(tmp_path):
    """Two repos standing in for two tenants.

    `theirs/` sorts before `mine/` deliberately: the bug being fixed was a
    fallback of `select(Repo).first()`, and a fixture where the caller's own
    repo happens to come first would pass against the broken code.
    """
    async with async_session() as db:
        other = User(
            email=f"other-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="x", role=UserRole.MEMBER, status=UserStatus.ACTIVE,
        )
        db.add(other)
        await db.flush()
        mine = Repo(github_full_name=f"zzz-mine/{uuid.uuid4().hex[:8]}")
        theirs = Repo(github_full_name=f"aaa-theirs/{uuid.uuid4().hex[:8]}")
        db.add_all([mine, theirs])
        await db.flush()
        my_issue = Issue(
            repo_id=mine.id, category="ui", origin="detected",
            title="Mine", severity=2, status=IssueStatus.RAISED,
        )
        their_issue = Issue(
            repo_id=theirs.id, category="security", origin="detected",
            title="A leaked key in their source", severity=4, status=IssueStatus.RAISED,
        )
        db.add_all([my_issue, their_issue])
        await db.commit()
        ids = {
            "mine": mine.id, "theirs": theirs.id,
            "my_issue": my_issue.id, "their_issue": their_issue.id,
            "mine_name": mine.github_full_name, "theirs_name": theirs.github_full_name,
            "other_user": other.id,
        }

    yield ids

    async with async_session() as db:
        await db.execute(delete(HumanInputRequest).where(HumanInputRequest.issue_id.in_([ids["my_issue"], ids["their_issue"]])))
        await db.execute(delete(Issue).where(Issue.id.in_([ids["my_issue"], ids["their_issue"]])))
        await db.execute(delete(Repo).where(Repo.id.in_([ids["mine"], ids["theirs"]])))
        await db.execute(text("DELETE FROM work_items WHERE payload->>'job_id' LIKE 'test-%'"))
        await db.execute(delete(User).where(User.id == ids["other_user"]))
        await db.commit()


def _user(email="me@example.com"):
    class _U:
        id = uuid.uuid4()
        role = type("R", (), {"value": "member"})()

    user = _U()
    user.email = email
    return user


# --- Counsel: which repository it is allowed to read -------------------------


async def test_counsel_refuses_another_orgs_repo_as_missing(two_orgs):
    """404, not 403: a 403 confirms the repository id is real."""
    async with async_session() as db:
        with pytest.raises(HTTPException) as caught:
            await counsel_router._resolve_context(db, _user(), two_orgs["theirs"], [two_orgs["mine"]])
    assert caught.value.status_code == 404


async def test_counsel_with_no_repo_given_never_falls_back_outside_the_org(two_orgs, tmp_path):
    """The old fallback was `select(Repo).first()` — an arbitrary repository
    from the whole deployment, which is how Counsel could read another
    organisation's source with `read_file` and `git log`."""
    worktree_root = tmp_path / "root"
    (worktree_root / "counsel").mkdir(parents=True)

    async with async_session() as db:
        with patch("app.sandbox.worktree.repo_root", return_value=worktree_root):
            context = await counsel_router._resolve_context(db, _user(), None, [two_orgs["mine"]])

    assert context.repo_id == str(two_orgs["mine"])
    assert context.repo_full_name == two_orgs["mine_name"]


async def test_counsel_with_no_repos_at_all_says_so_rather_than_borrowing_one(two_orgs):
    async with async_session() as db:
        with pytest.raises(HTTPException) as caught:
            await counsel_router._resolve_context(db, _user(), None, [])
    assert caught.value.status_code == 400


# --- Counsel: conversations and jobs -----------------------------------------


async def test_another_users_conversation_reads_as_missing(two_orgs):
    conversation_id = uuid.uuid4()
    async with async_session() as db:
        await db.execute(
            text(
                "INSERT INTO counsel_conversations (id, user_id, title, created_at) "
                "VALUES (:id, :uid, 't', now())"
            ),
            {"id": str(conversation_id), "uid": str(two_orgs["other_user"])},
        )
        await db.commit()
        try:
            with pytest.raises(HTTPException) as caught:
                await counsel_router.get_conversation(conversation_id, user=_user(), db=db)
            assert caught.value.status_code == 404, "someone else's conversation must not read as 403"
        finally:
            await db.execute(
                text("DELETE FROM counsel_conversations WHERE id = :id"), {"id": str(conversation_id)}
            )
            await db.commit()


async def test_asking_into_another_users_conversation_is_refused(two_orgs):
    """`get_conversation` checked ownership and `ask` did not, so another
    user's conversation id both replayed their history into the model and
    appended to their transcript."""
    conversation_id = uuid.uuid4()
    async with async_session() as db:
        await db.execute(
            text(
                "INSERT INTO counsel_conversations (id, user_id, title, created_at) "
                "VALUES (:id, :uid, 't', now())"
            ),
            {"id": str(conversation_id), "uid": str(two_orgs["other_user"])},
        )
        await db.commit()
        try:
            with patch.object(counsel_router, "_resolve_context", new=AsyncMock(return_value=object())):
                with pytest.raises(HTTPException) as caught:
                    await counsel_router.ask(
                        {"question": "what is in here?", "conversation_id": str(conversation_id)},
                        user=_user(), db=db, visible=[two_orgs["mine"]],
                    )
            assert caught.value.status_code == 404
        finally:
            await db.execute(
                text("DELETE FROM counsel_conversations WHERE id = :id"), {"id": str(conversation_id)}
            )
            await db.commit()


async def test_a_malformed_conversation_id_is_a_400_not_a_500(two_orgs):
    async with async_session() as db:
        with pytest.raises(HTTPException) as caught:
            await counsel_router.ask(
                {"question": "hi", "conversation_id": "not-a-uuid"},
                user=_user(), db=db, visible=[two_orgs["mine"]],
            )
    assert caught.value.status_code == 400


async def _insert_job(db, job_id: str, repo_id, result: dict):
    await db.execute(
        text(
            "INSERT INTO work_items (id, kind, payload, status, result, attempts, created_at) "
            "VALUES (gen_random_uuid(), 'counsel_prd', :payload, 'done', :result, 0, now())"
        ),
        {
            "payload": json.dumps({"job_id": job_id, "repo_id": str(repo_id)}),
            "result": json.dumps(result),
        },
    )
    await db.commit()


async def test_another_orgs_prd_job_does_not_return_its_result(two_orgs):
    """A PRD's result is a document full of file paths, source excerpts and
    citations. The job id is a UUID, but "hard to guess" is not access
    control — it also appears in that organisation's own activity events."""
    job_id = f"test-{uuid.uuid4()}"
    async with async_session() as db:
        await _insert_job(db, job_id, two_orgs["theirs"], {"markdown": "# their secrets"})
        row = await counsel_router.get_job(job_id, user=_user(), db=db, visible=[two_orgs["mine"]])

    assert row["result"] is None
    assert row["status"] == "queued", "an invisible job must look pending, not forbidden"


async def test_my_own_job_still_comes_back(two_orgs):
    job_id = f"test-{uuid.uuid4()}"
    async with async_session() as db:
        await _insert_job(db, job_id, two_orgs["mine"], {"markdown": "# mine"})
        row = await counsel_router.get_job(job_id, user=_user(), db=db, visible=[two_orgs["mine"]])

    assert row["status"] == "done"
    assert row["result"]["markdown"] == "# mine"


# --- Clarification requests --------------------------------------------------


async def _add_request(db, issue_id) -> uuid.UUID:
    request = HumanInputRequest(
        issue_id=issue_id,
        node_name="fix_council.patch_generation",
        kind="free_text",
        question="Should the retry cap be configurable?",
        options=[],
        context={"already_considered": "read their config.js"},
        status="pending",
        thread=[],
    )
    db.add(request)
    await db.commit()
    return request.id


async def test_the_clarification_list_shows_only_my_organizations_questions(two_orgs):
    async with async_session() as db:
        mine = await _add_request(db, two_orgs["my_issue"])
        theirs = await _add_request(db, two_orgs["their_issue"])
        rows = await human_input_router.list_requests(db=db, visible=[two_orgs["mine"]])

    ids = {row["id"] for row in rows}
    assert str(mine) in ids
    assert str(theirs) not in ids


async def test_a_member_of_no_org_sees_no_clarification_requests(two_orgs):
    async with async_session() as db:
        await _add_request(db, two_orgs["my_issue"])
        rows = await human_input_router.list_requests(db=db, visible=[])
    assert rows == []


async def test_answering_another_orgs_question_is_refused_and_queues_nothing(two_orgs):
    """The dangerous half. Answering enqueues a `fix_council` work item, so an
    unscoped answer endpoint is a way to steer another organisation's council."""
    async with async_session() as db:
        theirs = await _add_request(db, two_orgs["their_issue"])
        with patch("app.work_queue.enqueue", new_callable=AsyncMock) as enqueue:
            with pytest.raises(HTTPException) as caught:
                await human_input_router.answer_request(
                    theirs, {"answer": "do it my way"},
                    db=db, user=_user(), visible=[two_orgs["mine"]],
                )
        assert caught.value.status_code == 404
        enqueue.assert_not_called()

        still_pending = await db.get(HumanInputRequest, theirs)
        await db.refresh(still_pending)
        assert still_pending.status == "pending"


async def test_answering_my_own_question_works_and_records_the_real_actor(two_orgs):
    """`actor` came from the request body, so a caller could sign someone
    else's name to the answer that resumes a council run."""
    async with async_session() as db:
        mine = await _add_request(db, two_orgs["my_issue"])
        with patch("app.work_queue.enqueue", new_callable=AsyncMock) as enqueue:
            result = await human_input_router.answer_request(
                mine, {"answer": "make it configurable", "actor": "someone-else@evil.test"},
                db=db, user=_user("real@example.com"), visible=[two_orgs["mine"]],
            )
        assert result["ok"] is True
        enqueue.assert_awaited_once()

        answered = await db.get(HumanInputRequest, mine)
        await db.refresh(answered)
        assert answered.answered_by == "real@example.com"


# --- The activity feed -------------------------------------------------------


class _FakeSocket:
    def __init__(self):
        self.sent: list[dict] = []
        self.closed_with: int | None = None

    async def accept(self):
        pass

    async def send_text(self, payload: str):
        self.sent.append(json.loads(payload))

    async def close(self, code: int = 1000):
        self.closed_with = code


@pytest.fixture
def clean_broadcaster():
    broadcaster = ws_router.ActivityBroadcaster()
    original, ws_router.broadcaster = ws_router.broadcaster, broadcaster
    yield broadcaster
    ws_router.broadcaster = original


async def test_an_event_reaches_only_the_connections_that_may_see_that_repo(clean_broadcaster):
    mine, theirs = str(uuid.uuid4()), str(uuid.uuid4())
    mine_socket, their_socket = _FakeSocket(), _FakeSocket()
    await clean_broadcaster.connect(mine_socket, [mine])
    await clean_broadcaster.connect(their_socket, [theirs])

    await clean_broadcaster.broadcast({"type": "node", "message": "Scanning…", "repo_id": mine})

    assert len(mine_socket.sent) == 1
    assert their_socket.sent == [], "the feed was a broadcast channel across every org"


async def test_an_unattributed_event_reaches_nobody(clean_broadcaster, caplog):
    """The only two failure modes are "invisible in the feed" and "visible to
    everyone". The first is a bug someone reports; the second is the leak."""
    socket = _FakeSocket()
    await clean_broadcaster.connect(socket, [str(uuid.uuid4())])

    await clean_broadcaster.broadcast({"type": "node", "message": "who am I for?"})

    assert socket.sent == []
    assert "unattributed activity event" in caplog.text


async def test_a_disconnected_socket_is_dropped_rather_than_retried(clean_broadcaster):
    repo_id = str(uuid.uuid4())

    class _Broken(_FakeSocket):
        async def send_text(self, payload):
            raise RuntimeError("socket is gone")

    await clean_broadcaster.connect(_Broken(), [repo_id])
    await clean_broadcaster.broadcast({"type": "node", "repo_id": repo_id})
    assert clean_broadcaster.connections == []


async def test_emit_event_addresses_an_event_from_the_ambient_scope(monkeypatch, clean_broadcaster):
    """There are ~47 `emit_event` call sites; threading a repo id through all
    of them is how one gets missed. The ambient scope is set once per run at
    each entry point, and this is the part that has to work for the feed to
    show anything at all."""
    import asyncio

    def no_database():
        raise RuntimeError("no database in this test")

    monkeypatch.setattr("app.sync_db.connection", no_database)
    monkeypatch.setattr(ws_router, "_main_loop", asyncio.get_running_loop())

    repo_id = uuid.uuid4()
    socket = _FakeSocket()
    await clean_broadcaster.connect(socket, [str(repo_id)])

    with ws_router.activity_scope(repo_id):
        ws_router.emit_event({"type": "node", "node": "detect", "message": "Scanning…"})
    await asyncio.sleep(0.05)

    assert socket.sent, "an event emitted inside a scope must reach that repo's viewers"
    assert socket.sent[0]["repo_id"] == str(repo_id)


async def test_an_event_emitted_outside_any_scope_is_not_addressed(monkeypatch, clean_broadcaster):
    import asyncio

    monkeypatch.setattr("app.sync_db.connection", lambda: (_ for _ in ()).throw(RuntimeError("no db")))
    monkeypatch.setattr(ws_router, "_main_loop", asyncio.get_running_loop())

    socket = _FakeSocket()
    await clean_broadcaster.connect(socket, [str(uuid.uuid4())])
    ws_router.emit_event({"type": "node", "message": "who am I for?"})
    await asyncio.sleep(0.05)

    assert socket.sent == []


def test_a_scope_does_not_leak_past_its_block():
    repo_id = uuid.uuid4()
    with ws_router.activity_scope(repo_id):
        assert ws_router._event_repo_id.get() == str(repo_id)
    assert ws_router._event_repo_id.get() == ""


async def test_the_websocket_refuses_a_request_with_no_session():
    socket = _FakeSocket()
    socket.cookies = {}
    await ws_router.activity_stream(socket)
    assert socket.closed_with == 4401


async def test_the_websocket_refuses_a_forged_token():
    socket = _FakeSocket()
    socket.cookies = {"access_token": "not.a.real.token"}
    await ws_router.activity_stream(socket)
    assert socket.closed_with == 4401


# --- The handshake, through a real server ------------------------------------


def _feed_app():
    """Only the websocket router.

    The whole point is to exercise Starlette's own cookie parsing on a real
    handshake. A hand-built fake socket proves the handler's branches and
    proves nothing about whether a browser's cookie actually arrives — which
    is the assumption this change rests on, and the one that would take the
    activity feed down for everyone if it were wrong.
    """
    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(ws_router.router)
    return app


def test_a_real_handshake_with_no_cookie_is_closed(clean_broadcaster):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect as ClientDisconnect

    client = TestClient(_feed_app())
    with pytest.raises(ClientDisconnect) as caught:
        with client.websocket_connect("/ws/activity"):
            pass
    assert caught.value.code == 4401
    assert clean_broadcaster.connections == []


def test_a_real_handshake_with_a_valid_cookie_connects_and_is_scoped(clean_broadcaster):
    from fastapi.testclient import TestClient

    from app.security import issue_access_token

    user_id = uuid.uuid4()
    repo_id = uuid.uuid4()
    token = issue_access_token(user_id, "member")

    client = TestClient(_feed_app())
    client.cookies.set("access_token", token)

    with patch("app.orgs.repo_ids_for_user", return_value=[repo_id]):
        with client.websocket_connect("/ws/activity"):
            assert len(clean_broadcaster.connections) == 1
            _, visible = clean_broadcaster.connections[0]
            assert visible == frozenset({str(repo_id)})


def test_an_expired_or_tampered_cookie_is_closed_not_accepted(clean_broadcaster):
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect as ClientDisconnect

    from app.security import issue_access_token

    token = issue_access_token(uuid.uuid4(), "member")
    payload, signature = token.split(".", 1)
    tampered = f"{payload}.{'0' * len(signature)}"

    client = TestClient(_feed_app())
    client.cookies.set("access_token", tampered)
    with pytest.raises(ClientDisconnect) as caught:
        with client.websocket_connect("/ws/activity"):
            pass
    assert caught.value.code == 4401


async def test_a_member_of_no_org_cannot_answer_anything(two_orgs):
    async with async_session() as db:
        mine = await _add_request(db, two_orgs["my_issue"])
        with patch("app.work_queue.enqueue", new_callable=AsyncMock) as enqueue:
            with pytest.raises(HTTPException) as caught:
                await human_input_router.answer_request(
                    mine, {"answer": "x"}, db=db, user=_user(), visible=[],
                )
    assert caught.value.status_code == 404
    enqueue.assert_not_called()
