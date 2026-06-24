"""
Comandos de administración (uso manual, no es una ruta web).

  python manage.py crear-admin <email>
      Crea un usuario admin con una clave temporal aleatoria (que imprime).

  python manage.py bootstrap-owner <email> <cuit> <pv> <entorno> <concepto> [actividad]
      Crea el admin (dueño) + su tenant_config con delegacion_ok=1 (porque su
      cert es el de la plataforma y representa su propio CUIT). Imprime la clave
      temporal. Pensado para la migración inicial del single-tenant a SaaS.

  python manage.py list-users
"""

import secrets
import sys

import auth
import db


def _nueva_clave():
    return secrets.token_urlsafe(9)


def crear_admin(email):
    db.init_db()
    if db.get_user_by_email(email):
        print(f"Ya existe un usuario con email {email}")
        return
    pw = _nueva_clave()
    uid = db.create_user(email, auth.hash_password(pw), role="admin")
    print(f"✅ Admin creado: id={uid} email={email}")
    print(f"   CLAVE TEMPORAL: {pw}")
    print("   Cambiala desde 'Mi cuenta' después de entrar.")


def bootstrap_owner(email, cuit, pv, entorno, concepto, actividad=None):
    db.init_db()
    user = db.get_user_by_email(email)
    if user:
        uid = user["id"]
        print(f"Usuario {email} ya existía (id={uid}); actualizo su tenant_config.")
    else:
        pw = _nueva_clave()
        uid = db.create_user(email, auth.hash_password(pw), role="admin")
        print(f"✅ Admin creado: id={uid} email={email}")
        print(f"   CLAVE TEMPORAL: {pw}")
        print("   Cambiala desde 'Mi cuenta' después de entrar.")
    db.upsert_tenant_config(
        uid,
        cuit="".join(c for c in cuit if c.isdigit()),
        punto_venta=int(pv),
        entorno=entorno,
        concepto=int(concepto),
        actividad=("".join(c for c in actividad if c.isdigit()) or None) if actividad else None,
        delegacion_ok=1,
    )
    print(f"✅ tenant_config del dueño listo (CUIT {cuit}, PV {pv}, {entorno}, delegacion_ok=1).")


def list_users():
    for u in db.list_users():
        print(u["id"], u["email"], u["role"], "activo" if u["activo"] else "inactivo", u["created_at"][:10])


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd, args = sys.argv[1], sys.argv[2:]
    if cmd == "crear-admin":
        crear_admin(*args)
    elif cmd == "bootstrap-owner":
        bootstrap_owner(*args)
    elif cmd == "list-users":
        list_users()
    else:
        print(__doc__)
        sys.exit(1)
