import os
import re
import pytest
import asyncio

os.environ["SIDI_TESTING"] = "1"
from pydantic import ValidationError
from app.database import init_db, SessionLocal, TEMP_DIR
from app.models import Solicitud, FormatoOficial, UsuarioAdmin
from app.schemas import SolicitudCreate, CCT_REGEX
from app.config_manager import ConfigManager
from app.auth import hash_password, verify_password, init_default_admin
from app.playwright_bot import PlaywrightInventoryBot
from app.mailer import SidiMailer
from main import seed_formatos_iniciales


def test_cct_regex_validation():
    """Verifica que el validador oficial de CCT acepte y rechace según las reglas."""
    # Válidas
    validas = ["20DPR0005E", "20des0006f", "20DTV0007G", "20EJN0008H", "09DPR9999X"]
    for cct in validas:
        sol = SolicitudCreate(
            cct=cct,
            nombre_solicitante="Juan Pérez Hernández",
            cargo="Director(a) Técnico(a)",
            telefono="9510000001",
            correo="director@ejemplo.gob.mx",
            region="Valles Centrales",
            nivel_educativo="Primaria General"
        )
        assert sol.cct == cct.upper()

    # Inválidas
    invalidas = ["12345", "20DPR123", "20DPR012345", "INVALIDCCT", "20-DPR-0123-A"]
    for cct in invalidas:
        with pytest.raises(ValidationError):
            SolicitudCreate(
                cct=cct,
                nombre_solicitante="Juan Pérez Hernández",
                cargo="Director(a) Técnico(a)",
                telefono="9510000001",
                correo="director@ejemplo.gob.mx",
                region="Valles Centrales",
                nivel_educativo="Primaria General"
            )


def test_auth_hashing():
    """Verifica el cifrado y validación de contraseñas de administrador."""
    pwd = "MiContraseñaSegura2026"
    hashed = hash_password(pwd)
    assert verify_password(pwd, hashed) is True
    assert verify_password("Erronea", hashed) is False


def test_database_and_config_manager():
    """Prueba la inicialización de la base de datos y la persistencia de configuraciones."""
    init_db()
    db = SessionLocal()
    try:
        init_default_admin(db)
        ConfigManager.init_defaults(db)
        seed_formatos_iniciales(db)

        # Verificar admin
        admin = db.query(UsuarioAdmin).filter(UsuarioAdmin.username == "admin").first()
        assert admin is not None
        assert verify_password("admin1234", admin.password_hash)

        # Verificar formatos (soporta los formatos subidos por el usuario)
        formatos = db.query(FormatoOficial).all()
        assert len(formatos) >= 1
        codigos = [f.codigo for f in formatos]
        assert len(codigos) > 0

        # Probar actualización de branding dinámico
        ConfigManager.set(db, "color_primario", "#990000")
        assert ConfigManager.get(db, "color_primario") == "#990000"

        # Revertir
        ConfigManager.set(db, "color_primario", "#8B1D42")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_playwright_simulacion_y_purga_pdf():
    """
    Verifica que en modo simulación:
    1. Se genere el PDF temporal.
    2. El mailer procese el envío y elimine INMEDIATAMENTE el PDF de almacenamiento local.
    """
    init_db()
    db = SessionLocal()
    try:
        config = ConfigManager.get_all(db)
        config["portal_simulacion"] = "true"  # Habilitar simulación para test

        bot = PlaywrightInventoryBot(config)
        cct_test = "20DPR9999A"
        folio_test = "SIDI-TEST-0001"

        exito, estado, pdf_path = await asyncio.to_thread(bot.extraer_constancia, cct_test, folio_test)
        assert exito is True
        assert estado == "EXITO"
        assert pdf_path is not None
        assert os.path.exists(pdf_path)

        # Simular solicitud
        solicitud_dummy = Solicitud(
            folio=folio_test,
            cct=cct_test,
            nombre_solicitante="Profesor de Prueba",
            cargo="Director",
            telefono="9510000000",
            correo="prueba@test.com",
            region="Valles Centrales",
            nivel_educativo="Primaria General"
        )

        formatos_activos = [f.ruta_archivo for f in db.query(FormatoOficial).filter(FormatoOficial.activo == True).all()]

        # Ejecutar envío de correo (el mailer simulará el SMTP si no hay credenciales)
        await SidiMailer.enviar_correo_exito(
            solicitud=solicitud_dummy,
            config=config,
            pdf_temporal_path=pdf_path,
            rutas_formatos=formatos_activos
        )

        # REGLA DE ORO: El PDF temporal DEBE haberse borrado
        assert not os.path.exists(pdf_path), "El PDF temporal NO fue eliminado tras el envío."

    finally:
        db.close()


@pytest.mark.asyncio
async def test_cct_no_encontrada_simulacion():
    """Verifica que el bot detecte CCT no registrada."""
    config = {"portal_simulacion": "true"}
    bot = PlaywrightInventoryBot(config)

    # CCT de prueba configurada para simular no encontrada en thread
    exito, estado, pdf_path = await asyncio.to_thread(bot.extraer_constancia, "20DPR0000Z", "SIDI-TEST-0002")
    assert exito is False
    assert estado == "CCT_NO_ENCONTRADA"
    assert pdf_path is None
