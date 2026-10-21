import os
import pytest
from starlette.testclient import TestClient
from main import app
from app.database import init_db, SessionLocal
from app.models import Solicitud, UsuarioAdmin
from app.config_manager import ConfigManager
from app.routers.admin import generar_folios_en_lote


@pytest.fixture(scope="module")
def client():
    os.environ["SIDI_TESTING"] = "1"
    init_db()
    db = SessionLocal()
    ConfigManager.init_defaults(db)
    orig_val = ConfigManager.get(db, "portal_simulacion", "false")
    ConfigManager.set(db, "portal_simulacion", "true")
    db.close()

    with TestClient(app) as c:
        yield c

    os.environ.pop("SIDI_TESTING", None)
    db = SessionLocal()
    ConfigManager.set(db, "portal_simulacion", orig_val)
    db.close()


def test_generar_folios_en_lote():
    """Prueba la generación atómica y secuencial de folios masivos."""
    db = SessionLocal()
    folios = generar_folios_en_lote(db, 5)
    assert len(folios) == 5
    # Todos deben ser únicos
    assert len(set(folios)) == 5
    for f in folios:
        assert f.startswith("SIDI-")
    db.close()


def test_despacho_masivo_supervision(client):
    """Prueba el registro masivo de escuelas para una supervisión escolar."""
    # 1. Login como admin
    resp_login = client.post("/admin/login", data={"username": "admin", "password": "admin1234"}, follow_redirects=True)
    assert resp_login.status_code == 200

    # 2. Enviar lote de CCTs con duplicadas y claves de prueba
    ccts_input = """
    20DPR0001A
    20DPR0002B
    20DPR0001A # Duplicada, debe omitirse
    20DTV0003C
    CLAVE_INVALIDA # Inválida, debe ignorarse
    """

    data_form = {
        "nombre_supervisor": "Mtro. Juan Manuel Morales",
        "zona_escolar": "Zona Escolar 047",
        "cargo_solicitante": "Supervisor(a) Escolar",
        "telefono_contacto": "9510000001",
        "correo_supervision": "supervision.demo@ejemplo.gob.mx",
        "region": "Istmo",
        "nivel_educativo": "Primaria General",
        "modo_envio": "supervision",
        "ccts_texto": ccts_input
    }

    resp_masivo = client.post("/admin/solicitudes/masivo", data=data_form, follow_redirects=True)
    assert resp_masivo.status_code == 200
    assert "Carga Masiva Exitosa" in resp_masivo.text
    assert "3 escuelas" in resp_masivo.text

    # 3. Verificar en la base de datos que se crearon en PENDIENTE y sin OTP
    db = SessionLocal()
    solicitudes_zona = db.query(Solicitud).filter(Solicitud.correo == "supervision.demo@ejemplo.gob.mx").all()
    assert len(solicitudes_zona) >= 3

    for sol in solicitudes_zona:
        assert sol.estado in ["PENDIENTE", "PROCESANDO", "ENVIADO"]
        assert sol.codigo_otp is None
        assert "[SUPERVISION:" in sol.mensaje_error

    db.close()

    # 4. Probar descarga de Acuse de Entrega Oficial en Excel
    resp_reporte = client.get("/admin/supervision/reporte?zona=Zona Escolar 047")
    assert resp_reporte.status_code == 200
    assert "spreadsheetml" in resp_reporte.headers.get("content-type", "")
    assert len(resp_reporte.content) > 1000


@pytest.mark.asyncio
async def test_antiduplicidad_formatos_supervision():
    """Verifica que para una supervisión escolar los formatos normativos se envíen una sola vez."""
    from app.worker import procesar_solicitud_individual
    db = SessionLocal()
    db.query(Solicitud).filter(Solicitud.cct.in_(["20DPR9991A", "20DPR9992B"])).delete()
    db.commit()

    sol1 = Solicitud(
        folio="SIDI-2026-9991",
        cct="20DPR9991A",
        nombre_solicitante="Supervisor Prueba",
        cargo="Supervisor(a) Escolar",
        telefono="9510000000",
        correo="supervision.prueba@ejemplo.gob.mx",
        region="Valles Centrales",
        nivel_educativo="Primaria General",
        estado="PENDIENTE",
        mensaje_error="[SUPERVISION: Zona 999] Despacho Masivo en Cola"
    )
    sol2 = Solicitud(
        folio="SIDI-2026-9992",
        cct="20DPR9992B",
        nombre_solicitante="Supervisor Prueba",
        cargo="Supervisor(a) Escolar",
        telefono="9510000000",
        correo="supervision.prueba@ejemplo.gob.mx",
        region="Valles Centrales",
        nivel_educativo="Primaria General",
        estado="PENDIENTE",
        mensaje_error="[SUPERVISION: Zona 999] Despacho Masivo en Cola"
    )
    db.add(sol1)
    db.add(sol2)
    db.commit()
    db.refresh(sol1)
    db.refresh(sol2)
    id1 = sol1.id
    id2 = sol2.id
    db.close()

    # Procesar ambas solicitudes en simulación
    await procesar_solicitud_individual(id1)
    await procesar_solicitud_individual(id2)

    db = SessionLocal()
    res1 = db.query(Solicitud).filter(Solicitud.id == id1).first()
    res2 = db.query(Solicitud).filter(Solicitud.id == id2).first()
    assert res1.estado == "ENVIADO"
    assert res2.estado == "ENVIADO"
    assert "[SUPERVISION: Zona 999]" in res1.mensaje_error
    assert "[SUPERVISION: Zona 999]" in res2.mensaje_error
    db.close()


def test_descargar_plantilla_excel(client):
    """Verifica la generación y descarga de la plantilla Excel para carga masiva."""
    # 1. Login
    client.post("/admin/login", data={"username": "admin", "password": "admin1234"}, follow_redirects=True)

    # 2. Descargar plantilla en blanco
    resp = client.get("/admin/solicitudes/plantilla-excel")
    assert resp.status_code == 200
    assert "spreadsheetml" in resp.headers.get("content-type", "")
    assert "plantilla_carga_masiva_sidi.xlsx" in resp.headers.get("content-disposition", "")
    assert len(resp.content) > 1000

    # 3. Descargar plantilla pre-llenada con CCTs
    resp_ccts = client.get("/admin/solicitudes/plantilla-excel?ccts=20DJN0001A,20DJN0002B")
    assert resp_ccts.status_code == 200
    assert len(resp_ccts.content) > 1000

