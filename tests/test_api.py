import os
import pytest
from starlette.testclient import TestClient
from main import app
from app.database import init_db, SessionLocal
from app.models import Solicitud, Configuracion, UsuarioAdmin
from app.config_manager import ConfigManager


@pytest.fixture(scope="module")
def client():
    # Inicializar BD y activar modo simulación temporal para la prueba
    os.environ["SIDI_TESTING"] = "1"
    init_db()
    db = SessionLocal()
    ConfigManager.init_defaults(db)
    orig_val = ConfigManager.get(db, "portal_simulacion", "false")
    ConfigManager.set(db, "portal_simulacion", "true")
    db.close()

    # Inicializar cliente con context manager para disparar el lifespan (startup y worker)
    with TestClient(app) as c:
        yield c

    # Restaurar valor original
    os.environ.pop("SIDI_TESTING", None)
    db = SessionLocal()
    ConfigManager.set(db, "portal_simulacion", orig_val)
    db.close()


def test_public_routes(client):
    """Prueba de navegación del portal público y consulta."""
    # 1. Página de inicio
    resp = client.get("/")
    assert resp.status_code == 200
    assert "VENTANILLA DIGITAL" in resp.text
    assert "DEPARTAMENTO DE INVENTARIOS" in resp.text
    assert "Formulario Oficial de Solicitud" in resp.text

    # 2. Consulta de estatus
    resp_cons = client.get("/consulta")
    assert resp_cons.status_code == 200
    assert "Rastreo de Solicitud de Inventario" in resp_cons.text


def test_public_solicitud_y_antisaturacion(client):
    """Prueba el registro de solicitud, verificación OTP y el bloqueo anti-saturación."""
    db = SessionLocal()
    cct_test = "20DPR0004D"
    # Limpiar previas de esta CCT y correo
    db.query(Solicitud).filter((Solicitud.cct == cct_test) | (Solicitud.correo == "directora.maria@ejemplo.gob.mx")).delete()
    db.commit()
    db.close()

    # 1. Primera solicitud: Debe redirigir a verificación OTP
    data = {
        "cct": cct_test,
        "nombre_solicitante": "Directora María Elena Morales",
        "cargo": "Director(a) Técnico(a)",
        "telefono": "9510000004",
        "correo": "directora.maria@ejemplo.gob.mx",
        "region": "Valles Centrales",
        "nivel_educativo": "Primaria General"
    }
    resp1 = client.post("/solicitar", data=data, follow_redirects=True)
    assert resp1.status_code == 200
    assert "Verificación de Identidad" in resp1.text
    assert "Código de Verificación (6 dígitos)" in resp1.text

    # 2. Consultar BD para obtener el folio y código OTP generado
    db = SessionLocal()
    sol = db.query(Solicitud).filter(Solicitud.cct == cct_test).first()
    assert sol is not None
    assert sol.estado == "PENDIENTE_VERIFICACION"
    assert len(sol.codigo_otp) == 6
    otp_generado = sol.codigo_otp
    folio_generado = sol.folio
    db.close()

    # 3. Probar código OTP incorrecto
    resp_bad = client.post(f"/verificar-otp/{folio_generado}", data={"codigo_otp": "000000"})
    assert resp_bad.status_code == 200
    assert "Código de verificación incorrecto" in resp_bad.text

    # 4. Probar código OTP correcto (debe activar solicitud a PENDIENTE y mostrar éxito)
    resp_ok = client.post(f"/verificar-otp/{folio_generado}", data={"codigo_otp": otp_generado})
    assert resp_ok.status_code == 200
    assert "¡Solicitud Registrada con Éxito!" in resp_ok.text
    assert folio_generado in resp_ok.text

    # Verificar que el estado cambió a PENDIENTE y el OTP fue borrado
    db = SessionLocal()
    sol_actualizada = db.query(Solicitud).filter(Solicitud.folio == folio_generado).first()
    assert sol_actualizada.estado in ["PENDIENTE", "PROCESANDO", "ENVIADO"]
    assert sol_actualizada.codigo_otp is None
    db.close()

    # 5. Segunda solicitud idéntica en curso (Debe disparar Anti-Saturación si está activa)
    # Forzar temporalmente a PENDIENTE para asegurar la prueba de concurrencia
    db = SessionLocal()
    sol_actualizada = db.query(Solicitud).filter(Solicitud.folio == folio_generado).first()
    sol_actualizada.estado = "PENDIENTE"
    db.commit()
    db.close()

    resp2 = client.post("/solicitar", data=data)
    assert resp2.status_code == 200
    assert "Control Anti-Saturación" in resp2.text
    assert "ya cuenta con una solicitud activa" in resp2.text


def test_admin_flow_and_backoffice(client):
    """Prueba el flujo completo de autenticación y paneles del Backoffice."""
    # 1. Intento de acceso sin login a /admin (debe redirigir a /admin/login)
    resp_no_auth = client.get("/admin", follow_redirects=False)
    assert resp_no_auth.status_code in [302, 303]
    assert "/admin/login" in resp_no_auth.headers.get("Location", "")

    # 2. Login con credenciales correctas
    resp_login = client.post("/admin/login", data={"username": "admin", "password": "admin1234"}, follow_redirects=True)
    assert resp_login.status_code == 200
    assert "Dashboard & Analítica Avanzada" in resp_login.text

    # 3. Acceso a Directorio y Solicitudes
    resp_sol = client.get("/admin/solicitudes")
    assert resp_sol.status_code == 200
    assert "Directorio Vivo y Bitácora Oficial" in resp_sol.text

    # 4. Acceso a Formatos Oficiales
    resp_fmt = client.get("/admin/formatos")
    assert resp_fmt.status_code == 200
    assert "Gestión de Formatos e Instructivos" in resp_fmt.text

    # 5. Acceso a Configuración / Branding
    resp_cfg = client.get("/admin/configuracion")
    assert resp_cfg.status_code == 200
    assert "Identidad, Mensajes y Parámetros del Sistema" in resp_cfg.text

    # 7. Gestión de usuarios (Crear, Editar, Eliminar)
    # Crear
    resp_crear = client.post("/admin/usuarios/crear", data={
        "username": "operador_prueba",
        "nombre": "Operador de Pruebas",
        "password": "pass_seguro_123"
    }, follow_redirects=True)
    assert resp_crear.status_code == 200
    assert "operador_prueba" in resp_crear.text

    # Editar
    db = SessionLocal()
    op_user = db.query(UsuarioAdmin).filter(UsuarioAdmin.username == "operador_prueba").first()
    assert op_user is not None
    op_id = op_user.id
    db.close()

    resp_edit = client.post(f"/admin/usuarios/{op_id}/editar", data={
        "nombre": "Operador Modificado",
        "activo": "true",
        "password": ""
    }, follow_redirects=True)
    assert resp_edit.status_code == 200
    assert "Operador Modificado" in resp_edit.text

    # Eliminar
    resp_del = client.post(f"/admin/usuarios/{op_id}/eliminar", follow_redirects=True)
    assert resp_del.status_code == 200
    assert "Usuario eliminado" in resp_del.text

