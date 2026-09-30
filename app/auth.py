import hashlib, hmac, os, secrets
from fastapi import Depends, Header, HTTPException
from sqlalchemy.orm import Session
from .db import SessionLocal, Token, User

def get_db():
    db = SessionLocal()
    try: yield db
    finally: db.close()

def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt, 200_000)
    return salt.hex() + "$" + dk.hex()

def verify_password(pw: str, stored: str) -> bool:
    salt_hex, dk_hex = stored.split("$")
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), bytes.fromhex(salt_hex), 200_000)
    return hmac.compare_digest(dk.hex(), dk_hex)

def new_token(db: Session, user: User) -> str:
    t = secrets.token_hex(24)
    db.add(Token(token=t, user_id=user.id)); db.commit()
    return t

def current_user(authorization: str = Header(default=""), db: Session = Depends(get_db)) -> User:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "Missing 'Authorization: Bearer <token>' header")
    tok = db.get(Token, authorization[7:])
    if not tok: raise HTTPException(401, "Invalid token")
    return db.get(User, tok.user_id)
