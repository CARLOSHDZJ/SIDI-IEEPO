import os
import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.database import init_db, SessionLocal, FORMATOS_DIR, LOGOS_DIR
from app.auth import init_default_admin, SECRET_KEY
from app.config_manager import ConfigManager
from app.models import FormatoOficial, Solicitud
from app.timezone import ahora_mexico
from app.worker import sidi_queue_worker, stop_worker
from app.routers import public, admin
import datetime

# Configuración del sistema de registro de eventos (Logging)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("sidi.main")

templates = Jinja2Templates(directory=os.path.join(os.path.dirname(__file__), "app", "templates"))


def seed_formatos_iniciales(db):
    """Registra en la base de datos los formatos normativos iniciales si no existen."""
    formatos_base = [
        {
            "nombre": "Formato Oficial de Alta de Bienes Muebles",
            "codigo": "ALTA",
            "tipo": "EXCEL",
            "archivo": "Formato_Alta_Bienes.xlsx",
            "descripcion": "Para la incorporación de bienes muebles por compra o donación al inventario escolar."
        },
        {
            "nombre": "Formato Oficial de Baja de Bienes Muebles",
            "codigo": "BAJA",
            "tipo": "EXCEL",
            "archivo": "Formato_Baja_Bienes.xlsx",
            "descripcion": "Para desincorporar bienes por obsolescencia, daño irreparable o siniestro."
        },
        {
            "nombre": "Formato Oficial de Traslado de Bienes",
            "codigo": "TRASLADO",
            "tipo": "EXCEL",
            "archivo": "Formato_Traslado_Bienes.xlsx",
            "descripcion": "Para la reubicación o cambio de adscripción de mobiliario entre centros de trabajo."
        },
        {
            "nombre": "Guía Oficial de Procedimientos de Inventario",
            "codigo": "GUIA",
            "tipo": "PDF",
            "archivo": "Guia_Oficial_Procedimientos_Inventario.pdf",
            "descripcion": "Manual normativo para el cotejo físico de los activos y resguardo de la cédula censal."
        }
    ]

    for fb in formatos_base:
        ruta = os.path.join(FORMATOS_DIR, fb["archivo"])
        if os.path.exists(ruta):
            existente = db.query(FormatoOficial).filter(FormatoOficial.codigo == fb["codigo"]).first()
            if not existente:
                db.add(FormatoOficial(
                    nombre=fb["nombre"],
                    codigo=fb["codigo"],
                    tipo=fb["tipo"],
                    ruta_archivo=ruta,
                    descripcion=fb["descripcion"],
                    activo=True
                ))
    db.commit()

    # Si la tabla está vacía y hay formatos ya cargados en la carpeta, auto-registrarlos
    if db.query(FormatoOficial).count() == 0 and os.path.exists(FORMATOS_DIR):
        for arch in os.listdir(FORMATOS_DIR):
            if arch.startswith("."):
                continue
            ext = os.path.splitext(arch)[1].lower()
            if ext in [".xlsx", ".xls", ".pdf"]:
                tipo = "EXCEL" if ext in [".xlsx", ".xls"] else "PDF"
                codigo = arch.split("_")[0].strip().upper() if "_" in arch else os.path.splitext(arch)[0].strip().upper()
                db.add(FormatoOficial(
                    nombre=f"Formato Oficial {codigo}",
                    codigo=codigo,
                    tipo=tipo,
                    ruta_archivo=os.path.join(FORMATOS_DIR, arch),
                    descripcion=f"Formato normativo oficial ({codigo}) para inventarios escolares.",
                    activo=True
                ))
        db.commit()


def seed_solicitudes_demo(db):
    """Inicializa registros sintéticos de demostración si la tabla de solicitudes está vacía."""
    if db.query(Solicitud).count() > 0:
        return
    base_time = ahora_mexico() - datetime.timedelta(hours=18)
    demos = [
        ("SIDI-2026-0001", "20DPR0001A", "Mtra. Elena Morales Ruiz (Demo)", "Director(a) Efectivo(a)", "9510000001", "plantel1.demo@ejemplo.gob.mx", "Valles Centrales", "Primaria General", "ENVIADO", None, "Cédula PDF despachada con éxito (Modo Demo)"),
        ("SIDI-2026-0002", "20DTV0002B", "Prof. Carlos Jiménez Ortiz (Demo)", "Director(a) Comisionado(a)", "9510000002", "plantel2.demo@ejemplo.gob.mx", "Sierra Norte", "Telesecundaria", "ENVIADO", None, "Cédula PDF despachada con éxito (Modo Demo)"),
        ("SIDI-2026-0003", "20DJN0003C", "Lic. Sofía Hernández Cruz (Demo)", "Director(a) Efectivo(a)", "9510000003", "plantel3.demo@ejemplo.gob.mx", "Costa", "Preescolar General", "ENVIADO", None, "Cédula PDF despachada con éxito (Modo Demo)"),
        ("SIDI-2026-0004", "20DES0004D", "Mtro. Roberto Vázquez Luna (Demo)", "Director(a) Técnico(a)", "9510000004", "plantel4.demo@ejemplo.gob.mx", "Istmo", "Secundaria General", "ENVIADO", None, "Cédula PDF despachada con éxito (Modo Demo)"),
        ("SIDI-2026-0005", "20DST0005E", "Ing. Patricia Gómez Méndez (Demo)", "Director(a) Efectivo(a)", "9510000005", "plantel5.demo@ejemplo.gob.mx", "Mixteca", "Secundaria Técnica", "ENVIADO", None, "Cédula PDF despachada con éxito (Modo Demo)"),
        ("SIDI-2026-0006", "20DPB0006F", "Prof. Miguel Ángel Reyes (Demo)", "Supervisor(a) Escolar", "9510000006", "plantel6.demo@ejemplo.gob.mx", "Papaloapan", "Primaria Indígena", "ENVIADO", None, "Cédula PDF despachada con éxito (Modo Demo)"),
        ("SIDI-2026-0007", "20DTV0007G", "Mtra. Lucía Sánchez Pérez (Demo)", "Director(a) Efectivo(a)", "9510000007", "plantel7.demo@ejemplo.gob.mx", "Sierra Sur", "Telesecundaria", "ENVIADO", None, "Cédula PDF despachada con éxito (Modo Demo)"),
        ("SIDI-2026-0008", "20DJN0008H", "Lic. Fernando Díaz Castro (Demo)", "Responsable de Inventarios", "9510000008", "plantel8.demo@ejemplo.gob.mx", "Cañada", "Preescolar Indígena", "ENVIADO", None, "Cédula PDF despachada con éxito (Modo Demo)"),
        ("SIDI-2026-0009", "20DPR0000Z", "Prof. Alejandro Torres Vega (Demo)", "Director(a) Comisionado(a)", "9510000009", "plantel9.demo@ejemplo.gob.mx", "Valles Centrales", "Primaria General", "CCT_NO_ENCONTRADA", "Centro de Trabajo no localizado en el catálogo de inventarios.", None),
    ]
    for i, (folio, cct, nom, cargo, tel, mail, reg, niv, est, err, arch) in enumerate(demos):
        t_sol = base_time + datetime.timedelta(hours=i * 2)
        t_proc = t_sol + datetime.timedelta(seconds=8)
        db.add(Solicitud(
            folio=folio,
            cct=cct,
            nombre_solicitante=nom,
            cargo=cargo,
            telefono=tel,
            correo=mail,
            region=reg,
            nivel_educativo=niv,
            estado=est,
            reintentos=0,
            mensaje_error=err,
            archivo_generado=arch,
            fecha_solicitud=t_sol,
            fecha_procesado=t_proc
        ))
    db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Gestión del ciclo de vida de la aplicación:
    - Inicializa esquemas de BD, usuario administrador y parámetros de identidad.
    - Arranca el Worker asíncrono FIFO en segundo plano.
    - Garantiza el apagado seguro al detener el servidor.
    """
    logger.info("Iniciando Sistema de Despacho de Inventarios (SIDI)...")
    init_db()

    db = SessionLocal()
    try:
        init_default_admin(db)
        ConfigManager.init_defaults(db)
        seed_formatos_iniciales(db)
        seed_solicitudes_demo(db)
    finally:
        db.close()

    # Arrancar Worker FIFO asíncrono
    worker_task = asyncio.create_task(sidi_queue_worker())
    logger.info("Worker FIFO de extracción y despacho iniciado exitosamente.")

    yield

    # Cierre ordenado
    logger.info("Deteniendo Worker FIFO de SIDI...")
    stop_worker()
    await worker_task
    logger.info("SIDI detenido de forma limpia.")


# Instancia de la aplicación FastAPI
app = FastAPI(
    title="SIDI - Sistema de Despacho de Inventarios IEEPO",
    description="Sistema automatizado de despacho de inventarios escolares y formatos oficiales para el IEEPO",
    version="1.0.0",
    lifespan=lifespan
)

# Middleware de sesión para autenticación en backoffice
app.add_middleware(
    SessionMiddleware,
    secret_key=SECRET_KEY,
    session_cookie="sidi_session",
    max_age=86400  # 24 horas
)

# Montar archivos estáticos
static_dir = os.path.join(os.path.dirname(__file__), "app", "static")
os.makedirs(static_dir, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Registrar Routers
app.include_router(public.router)
app.include_router(admin.router)


@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    db = SessionLocal()
    try:
        config = ConfigManager.get_all(db)
    finally:
        db.close()
    return templates.TemplateResponse(
        request=request,
        name="public_status.html",
        context={
            "config": config,
            "solicitud": None,
            "query": "Página no encontrada (404)",
            "is_admin": False
        },
        status_code=404
    )


if __name__ == "__main__":
    import uvicorn
    reload_opt = os.getenv("SIDI_RELOAD", "false").lower() == "true"
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=reload_opt)
