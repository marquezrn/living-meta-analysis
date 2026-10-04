"""Server-side sessions and project-level authorization."""

import hashlib
import secrets
from datetime import timedelta, timezone

from fastapi import HTTPException, Request, Response
from sqlalchemy import select

from .db import AuditEvent, AuthSession, Membership, Project, User, utcnow

COOKIE = "livingmeta_session"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def session_user(request: Request, db):
    token = request.cookies.get(COOKIE)
    if not token:
        return None
    session = db.get(AuthSession, token_hash(token))
    if not session or aware(session.expires_at) <= utcnow():
        return None
    return db.get(User, session.user_id)


def require_user(request: Request, db):
    user = session_user(request, db)
    if user is None:
        raise HTTPException(401, "Sign in to access private research projects")
    return user


def require_project(request: Request, db, project_id: str, roles=None):
    user = require_user(request, db)
    project = db.get(Project, project_id)
    membership = db.scalar(select(Membership).where(Membership.project_id == project_id,
                                                    Membership.user_id == user.id))
    if not project or not membership:
        raise HTTPException(404, "Project not found")
    if roles and membership.role not in roles:
        raise HTTPException(403, "Your project role does not permit this action")
    return user, project, membership


def issue_session(response: Response, user: User, db, settings):
    token = secrets.token_urlsafe(48)
    db.add(AuthSession(token_hash=token_hash(token), user_id=user.id,
                       expires_at=utcnow() + timedelta(hours=settings.session_hours)))
    db.commit()
    response.set_cookie(COOKIE, token, httponly=True, secure=settings.production,
                        samesite="lax", max_age=settings.session_hours * 3600, path="/")


def audit(db, action, project_id=None, user_id=None, **details):
    db.add(AuditEvent(action=action, project_id=project_id, user_id=user_id, details=details))
