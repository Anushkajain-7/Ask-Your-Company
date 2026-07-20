from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.core.security import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.models import User, Workspace
from app.schemas import LoginRequest, MeResponse, SignupRequest, TokenResponse

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/signup", response_model=TokenResponse)
def signup(payload: SignupRequest, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == payload.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="An account with this email already exists")

    workspace = Workspace(name=payload.workspace_name)
    db.add(workspace)
    db.flush()  # get workspace.id without committing yet

    user = User(
        email=payload.email,
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        role="admin",  # first user in a new workspace is its admin
        workspace_id=workspace.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(access_token=token, user_email=user.email, workspace_name=workspace.name)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account is disabled")

    token = create_access_token({"sub": str(user.id)})
    return TokenResponse(
        access_token=token, user_email=user.email, workspace_name=user.workspace.name
    )


@router.post("/logout")
def logout(user: User = Depends(get_current_user)):
    """
    JWTs are stateless, so 'logout' is enforced client-side (the frontend
    discards the token). We still expose this endpoint so the client has a
    single clear call to make, and so a future server-side denylist (e.g.
    Redis-backed) can be dropped in here without changing the frontend.
    """
    return {"detail": "Logged out. Discard the access token on the client."}


@router.get("/me", response_model=MeResponse)
def me(user: User = Depends(get_current_user)):
    return MeResponse(
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        workspace_name=user.workspace.name,
    )
