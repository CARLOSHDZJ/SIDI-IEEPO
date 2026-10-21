import hashlib
import hmac
import os
from typing import Optional
from fastapi import Request, HTTPException, status
from sqlalchemy.orm import Session
from app.models import UsuarioAdmin

SECRET_KEY = os.getenv("SIDI_SECRET_KEY", "sidi-demo-secret-key-change-in-production")


def hash_password(password: str) -> str:
    """Genera hash seguro de contraseña con salt."""
    salt = "sidi_salt_2026"
    return hashlib.sha256(f"{salt}{password}".encode("utf-8")).hexdigest()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verifica si la contraseña coincide con el hash."""
    return hmac.compare_digest(hash_password(plain_password), hashed_password)


def get_current_admin(request: Request, db: Session) -> Optional[UsuarioAdmin]:
    """Obtiene el usuario administrador desde la sesión de cookie."""
    username = request.session.get("admin_user")
    if not username:
        return None
    user = db.query(UsuarioAdmin).filter(UsuarioAdmin.username == username, UsuarioAdmin.activo == True).first()
    return user


def require_admin(request: Request) -> str:
    """Validador de dependencia rápida para rutas de administración."""
    admin_user = request.session.get("admin_user")
    if not admin_user:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/admin/login"},
            detail="No autenticado"
        )
    return admin_user


def init_default_admin(db: Session):
    """Crea el usuario administrador por defecto si no existe ninguno."""
    existing = db.query(UsuarioAdmin).first()
    if not existing:
        default_admin = UsuarioAdmin(
            username="admin",
            password_hash=hash_password("admin1234"),
            nombre="Administrador SIDI",
            activo=True
        )
        db.add(default_admin)
        db.commit()
