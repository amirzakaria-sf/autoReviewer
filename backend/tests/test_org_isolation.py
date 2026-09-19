"""One organization must not be able to read another's findings.

`repos.org_id` is the one column carrying tenancy -- issues, fixes, traces and
chunks all reach their org THROUGH their repo -- but until now nothing read it
on the way out. Every product endpoint listed every repository and every issue
in the deployment.

Invisible while there was one organization. Found the moment a second existed:
a developer invited into a brand-new org with no repositories at all opened the
dashboard and saw twenty findings belonging to somebody else, complete with
file paths and leaked-credential detail.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import delete, select

from app.db import async_session
from app.enums import IssueStatus
from app.models import Issue, Repo
from app.routers import api as api_router


@pytest.fixture
async def two_orgs():
    """Two repos standing in for two tenants, and the issue one of them owns."""
    async with async_session() as db:
        mine = Repo(github_full_name=f"mine/{uuid.uuid4().hex[:8]}")
        theirs = Repo(github_full_name=f"theirs/{uuid.uuid4().hex[:8]}")
        db.add_all([mine, theirs])
        await db.flush()
        their_issue = Issue(
            repo_id=theirs.id, category="security", origin="detected",
            title="A leaked key in their source", severity=4, status=IssueStatus.RAISED,
        )
        db.add(their_issue)
        await db.commit()
        ids = (mine.id, theirs.id, their_issue.id)

    yield ids

    async with async_session() as db:
        await db.execute(delete(Issue).where(Issue.id == ids[2]))
        await db.execute(delete(Repo).where(Repo.id.in_([ids[0], ids[1]])))
        await db.commit()


async def test_the_repo_list_shows_only_my_organizations_repos(two_orgs):
    mine, theirs, _ = two_orgs
    async with async_session() as db:
        rows = await api_router.list_repos(db=db, repo_ids=[mine])

    names = {row["id"] for row in rows}
    assert str(mine) in names
    assert str(theirs) not in names


async def test_the_issue_list_shows_only_my_organizations_issues(two_orgs):
    mine, _theirs, their_issue = two_orgs
    async with async_session() as db:
        rows = await api_router.list_issues(db=db, repo_ids=[mine])

    assert str(their_issue) not in {row["id"] for row in rows}


async def test_another_orgs_issue_reads_as_missing_not_forbidden(two_orgs):
    """404, never 403. A 403 confirms the id names something real, which turns
    the tenancy boundary into a lookup service for the other side of it."""
    mine, _theirs, their_issue = two_orgs
    async with async_session() as db:
        with pytest.raises(HTTPException) as error:
            await api_router.get_issue(their_issue, db=db, repo_ids=[mine])

    assert error.value.status_code == 404


async def test_another_orgs_repo_settings_cannot_be_read_or_changed(two_orgs):
    mine, theirs, _ = two_orgs
    async with async_session() as db:
        with pytest.raises(HTTPException) as read_error:
            await api_router.get_repo_settings(theirs, db=db, repo_ids=[mine])
        with pytest.raises(HTTPException) as write_error:
            await api_router.update_repo_settings(
                theirs, {"detection_paused": True}, db=db, repo_ids=[mine]
            )

    assert read_error.value.status_code == 404
    assert write_error.value.status_code == 404

    async with async_session() as db:
        untouched = await db.get(Repo, theirs)
    assert untouched.detection_paused is False, "a refused write must change nothing"


async def test_scanning_another_orgs_repo_is_refused(two_orgs):
    """Not merely a read: a scan clones and executes repository code, on
    somebody else's bill."""
    mine, theirs, _ = two_orgs
    async with async_session() as db:
        with pytest.raises(HTTPException) as error:
            await api_router.scan_repo(theirs, db=db, repo_ids=[mine])
    assert error.value.status_code == 404


async def test_triggering_a_fix_on_another_orgs_issue_is_refused(two_orgs):
    mine, _theirs, their_issue = two_orgs
    async with async_session() as db:
        with pytest.raises(HTTPException) as error:
            await api_router.trigger_fix(their_issue, db=db, repo_ids=[mine])
    assert error.value.status_code == 404


async def test_the_overview_counts_only_my_organizations_work(two_orgs):
    mine, _theirs, _their_issue = two_orgs
    async with async_session() as db:
        counts = await api_router.overview(db=db, repo_ids=[mine])

    assert counts["raised_by_ai"] == 0, "their raised issue must not be counted here"


async def test_a_user_with_no_organization_sees_nothing_rather_than_everything():
    """The state right after an account is created. Empty is the correct
    answer -- the bug was that it returned the entire deployment."""
    async with async_session() as db:
        assert await api_router.list_repos(db=db, repo_ids=[]) == []
        assert await api_router.list_issues(db=db, repo_ids=[]) == []
        assert await api_router.list_fixes(db=db, repo_ids=[]) == []
        assert (await api_router.overview(db=db, repo_ids=[]))["raised_by_ai"] == 0
