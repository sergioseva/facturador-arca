"""Autenticación: hashing de passwords, carga del usuario y decoradores."""

from functools import wraps

from flask import abort, g, redirect, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

import db


def hash_password(password):
    return generate_password_hash(password)


def verify_password(password_hash, password):
    return check_password_hash(password_hash, password)


def load_user():
    """Carga el usuario de la sesión en `g.user` (o None). Idempotente por request."""
    if "user" in g:
        return g.user
    uid = session.get("user_id")
    g.user = db.get_user_by_id(uid) if uid else None
    if g.user and not g.user["activo"]:
        g.user = None
    return g.user


def login_user(user):
    session["user_id"] = user["id"]
    session["role"] = user["role"]
    session.permanent = True


def logout_user():
    session.clear()


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = load_user()
        if not user:
            session.clear()
            return redirect(url_for("login"))
        # defensa: nunca operar sin un user_id válido
        assert user.get("id"), "usuario sin id en sesión"
        return f(*args, **kwargs)

    return wrapper


def admin_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = load_user()
        if not user:
            return redirect(url_for("login"))
        if user["role"] != "admin":
            abort(403)
        return f(*args, **kwargs)

    return wrapper
