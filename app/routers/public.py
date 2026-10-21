import os
import random
import asyncio
import datetime
from typing import Optional
from app.timezone import ahora_mexico
from fastapi import APIRouter, Depends, Request, Form, status
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from pydantic import ValidationError

from app.database import get_db
from app.models import Solicitud, FormatoOficial
from app.schemas import SolicitudCreate
from app.config_manager import ConfigManager
from app.mailer import SidiMailer
from app.worker import notificar_nueva_solicitud

router = APIRouter()
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates"))


def enmascarar_correo(correo: str) -> str:
    """Enmascara la cuenta de correo para proteger la privacidad visual del directivo."""
    if not correo or "@" not in correo:
        return correo
    usuario, dominio = correo.split("@", 1)
    if len(usuario) <= 2:
        usuario_mask = usuario[0] + "*"
    elif len(usuario) <= 4:
        usuario_mask = usuario[0] + "*" * (len(usuario) - 1)
    else:
        usuario_mask = usuario[0] + usuario[1] + "*" * (len(usuario) - 4) + usuario[-2] + usuario[-1]
    return f"{usuario_mask}@{dominio}"


def generar_siguiente_folio(db: Session) -> str:
    """Genera un folio oficial consecutivo y único para la solicitud."""
    anio_actual = ahora_mexico().year
    total = db.query(Solicitud).count() + 1
    folio = f"SIDI-{anio_actual}-{total:04d}"

    # Prevenir colisiones
    while db.query(Solicitud).filter(Solicitud.folio == folio).first() is not None:
        total += 1
        folio = f"SIDI-{anio_actual}-{total:04d}"

    return folio


@router.get("/", response_class=HTMLResponse)
async def inicio(request: Request, db: Session = Depends(get_db)):
    """Portal público principal para la solicitud de inventarios."""
    config = ConfigManager.get_all(db)
    formatos_activos = db.query(FormatoOficial).filter(FormatoOficial.activo == True).all()

    return templates.TemplateResponse(
        request=request,
        name="public_index.html",
        context={
            "config": config,
            "formatos_activos": formatos_activos,
            "is_admin": False,
            "aviso_antisaturacion": None,
            "mensaje_exito": False,
            "error_msg": None,
            "form_data": None
        }
    )


@router.post("/solicitar", response_class=HTMLResponse)
async def registrar_solicitud(
    request: Request,
    cct: str = Form(...),
    nombre_solicitante: str = Form(...),
    cargo: str = Form(...),
    telefono: str = Form(...),
    correo: str = Form(...),
    region: str = Form(...),
    nivel_educativo: str = Form(...),
    db: Session = Depends(get_db)
):
    """
    Procesa el formulario público de solicitud con:
    1. Validación estricta Pydantic y Regex oficial de CCT.
    2. Regla de Negocio Anti-Saturación.
    3. Encolamiento FIFO y notificación al Worker.
    """
    config = ConfigManager.get_all(db)
    formatos_activos = db.query(FormatoOficial).filter(FormatoOficial.activo == True).all()

    form_raw = {
        "cct": cct.strip().upper(),
        "nombre_solicitante": nombre_solicitante.strip(),
        "cargo": cargo.strip(),
        "telefono": telefono.strip(),
        "correo": correo.strip().lower(),
        "region": region.strip(),
        "nivel_educativo": nivel_educativo.strip()
    }

    # 1. Validación de esquema y Regex oficial de CCT
    try:
        validados = SolicitudCreate(**form_raw)
    except ValidationError as ve:
        error_mensajes = [e["msg"].replace("Value error, ", "") for e in ve.errors()]
        return templates.TemplateResponse(
            request=request,
            name="public_index.html",
            context={
                "config": config,
                "formatos_activos": formatos_activos,
                "is_admin": False,
                "error_msg": " / ".join(error_mensajes),
                "form_data": form_raw,
                "aviso_antisaturacion": None,
                "mensaje_exito": False
            }
        )

    # 2. Control Anti-Saturación: Verificar si la CCT ya tiene petición activa en curso
    solicitud_en_curso = db.query(Solicitud).filter(
        Solicitud.cct == validados.cct,
        Solicitud.estado.in_(["PENDIENTE", "PROCESANDO"])
    ).first()

    if solicitud_en_curso:
        aviso = {
            "cct": solicitud_en_curso.cct,
            "folio": solicitud_en_curso.folio,
            "fecha_solicitud": solicitud_en_curso.fecha_solicitud.strftime("%d/%m/%Y a las %H:%M hrs")
        }
        return templates.TemplateResponse(
            request=request,
            name="public_index.html",
            context={
                "config": config,
                "formatos_activos": formatos_activos,
                "is_admin": False,
                "aviso_antisaturacion": aviso,
                "mensaje_exito": False,
                "error_msg": None,
                "form_data": None
            }
        )

    requerir_otp = config.get("requerir_otp", "true").lower() in ["true", "1", "yes"]

    # Si ya tiene una solicitud pendiente de verificación con código aún vigente o expirado
    solicitud_otp_vigente = db.query(Solicitud).filter(
        Solicitud.cct == validados.cct,
        Solicitud.estado == "PENDIENTE_VERIFICACION"
    ).first()
    if solicitud_otp_vigente:
        if not requerir_otp:
            solicitud_otp_vigente.estado = "PENDIENTE"
            solicitud_otp_vigente.mensaje_error = "Activado automáticamente al desactivarse validación OTP."
            db.commit()
            notificar_nueva_solicitud()
            return RedirectResponse(
                url=f"/consulta?folio={solicitud_otp_vigente.folio}",
                status_code=status.HTTP_303_SEE_OTHER
            )
        elif solicitud_otp_vigente.otp_expira and ahora_mexico() < solicitud_otp_vigente.otp_expira:
            return RedirectResponse(
                url=f"/verificar-otp/{solicitud_otp_vigente.folio}",
                status_code=status.HTTP_303_SEE_OTHER
            )
        else:
            # OTP expirado: renovar automáticamente y enviar código nuevo
            nuevo_codigo = f"{random.randint(100000, 999999)}"
            solicitud_otp_vigente.codigo_otp = nuevo_codigo
            solicitud_otp_vigente.otp_expira = ahora_mexico() + datetime.timedelta(minutes=10)
            solicitud_otp_vigente.otp_intentos = 0
            db.commit()
            asyncio.create_task(SidiMailer.enviar_codigo_otp(solicitud_otp_vigente, nuevo_codigo, config))
            return RedirectResponse(
                url=f"/verificar-otp/{solicitud_otp_vigente.folio}?reenviado=1",
                status_code=status.HTTP_303_SEE_OTHER
            )

    # 2.1 Control de Seguridad Anti-Acaparamiento: Límite de CCTs por solicitante (correo / teléfono)
    max_ccts = int(config.get("max_cct_por_solicitante", "2"))
    solicitudes_previas = db.query(Solicitud).filter(
        (Solicitud.correo == validados.correo) | (Solicitud.telefono == validados.telefono)
    ).all()
    ccts_solicitadas = set(s.cct for s in solicitudes_previas)

    # Si intenta registrar una CCT distinta y ya llegó al límite permitido por directivo
    if validados.cct not in ccts_solicitadas and len(ccts_solicitadas) >= max_ccts:
        return templates.TemplateResponse(
            request=request,
            name="public_index.html",
            context={
                "config": config,
                "formatos_activos": formatos_activos,
                "is_admin": False,
                "error_msg": (
                    f"Control de Seguridad: El correo ({validados.correo}) o teléfono ({validados.telefono}) "
                    f"ya cuenta con el límite máximo de {max_ccts} escuelas registradas. "
                    "Para solicitudes extraordinarias de supervisión, comuníquese con el Departamento de Inventarios."
                ),
                "form_data": form_raw,
                "aviso_antisaturacion": None,
                "mensaje_exito": False
            }
        )

    nuevo_folio = generar_siguiente_folio(db)

    # Si la validación OTP está desactivada en configuración, pasar directamente a cola PENDIENTE
    if not requerir_otp:
        nueva_solicitud = Solicitud(
            folio=nuevo_folio,
            cct=validados.cct,
            nombre_solicitante=validados.nombre_solicitante,
            cargo=validados.cargo,
            telefono=validados.telefono,
            correo=validados.correo,
            region=validados.region,
            nivel_educativo=validados.nivel_educativo,
            estado="PENDIENTE",
            reintentos=0,
            codigo_otp=None,
            otp_expira=None,
            otp_intentos=0,
            fecha_solicitud=ahora_mexico()
        )
        db.add(nueva_solicitud)
        db.commit()
        db.refresh(nueva_solicitud)

        notificar_nueva_solicitud()

        return RedirectResponse(
            url=f"/consulta?folio={nuevo_folio}&nuevo=1",
            status_code=status.HTTP_303_SEE_OTHER
        )

    # 3. Flujo con Verificación OTP (6 dígitos, vigencia 10 minutos)
    codigo_otp = f"{random.randint(100000, 999999)}"
    otp_expira = ahora_mexico() + datetime.timedelta(minutes=10)

    nueva_solicitud = Solicitud(
        folio=nuevo_folio,
        cct=validados.cct,
        nombre_solicitante=validados.nombre_solicitante,
        cargo=validados.cargo,
        telefono=validados.telefono,
        correo=validados.correo,
        region=validados.region,
        nivel_educativo=validados.nivel_educativo,
        estado="PENDIENTE_VERIFICACION",
        reintentos=0,
        codigo_otp=codigo_otp,
        otp_expira=otp_expira,
        otp_intentos=0,
        fecha_solicitud=ahora_mexico()
    )

    db.add(nueva_solicitud)
    db.commit()
    db.refresh(nueva_solicitud)

    # 4. Enviar código OTP por correo electrónico al solicitante
    asyncio.create_task(SidiMailer.enviar_codigo_otp(nueva_solicitud, codigo_otp, config))

    # 5. Redirigir a la pantalla de verificación OTP
    return RedirectResponse(
        url=f"/verificar-otp/{nuevo_folio}",
        status_code=status.HTTP_303_SEE_OTHER
    )


# ================= VERIFICACIÓN OTP =================

@router.get("/verificar-otp/{folio}", response_class=HTMLResponse)
async def pantalla_verificar_otp(
    folio: str,
    request: Request,
    reenviado: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Muestra la interfaz para ingresar el código OTP de 6 dígitos."""
    config = ConfigManager.get_all(db)
    solicitud = db.query(Solicitud).filter(Solicitud.folio == folio.strip().upper()).first()
    if not solicitud:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    # Si ya fue verificado o ya está en proceso/enviado, redirigir a rastreo
    if solicitud.estado != "PENDIENTE_VERIFICACION":
        return RedirectResponse(url=f"/consulta?folio={solicitud.folio}", status_code=status.HTTP_303_SEE_OTHER)

    correo_enmascarado = enmascarar_correo(solicitud.correo)
    ahora = ahora_mexico()
    segundos_restantes = max(0, int((solicitud.otp_expira - ahora).total_seconds())) if solicitud.otp_expira else 0

    return templates.TemplateResponse(
        request=request,
        name="public_otp.html",
        context={
            "config": config,
            "solicitud": solicitud,
            "correo_enmascarado": correo_enmascarado,
            "segundos_restantes": segundos_restantes,
            "error_otp": None,
            "exito_reenvio": bool(reenviado),
            "is_admin": False
        }
    )


@router.post("/verificar-otp/{folio}", response_class=HTMLResponse)
async def procesar_verificar_otp(
    folio: str,
    request: Request,
    codigo_otp: str = Form(...),
    db: Session = Depends(get_db)
):
    """Valida el código OTP de 6 dígitos. Si es correcto, activa la solicitud a PENDIENTE e inicia el worker."""
    config = ConfigManager.get_all(db)
    solicitud = db.query(Solicitud).filter(Solicitud.folio == folio.strip().upper()).first()
    if not solicitud:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    if solicitud.estado != "PENDIENTE_VERIFICACION":
        return RedirectResponse(url=f"/consulta?folio={solicitud.folio}", status_code=status.HTTP_303_SEE_OTHER)

    codigo_limpio = "".join(filter(str.isdigit, codigo_otp.strip()))
    correo_enmascarado = enmascarar_correo(solicitud.correo)
    ahora = ahora_mexico()
    segundos_restantes = max(0, int((solicitud.otp_expira - ahora).total_seconds())) if solicitud.otp_expira else 0

    # Validar número de intentos
    if solicitud.otp_intentos >= 5:
        return templates.TemplateResponse(
            request=request,
            name="public_otp.html",
            context={
                "config": config,
                "solicitud": solicitud,
                "correo_enmascarado": correo_enmascarado,
                "segundos_restantes": 0,
                "error_otp": "Ha superado el límite de 5 intentos fallidos. Por favor solicite el reenvío de un nuevo código.",
                "exito_reenvio": False,
                "is_admin": False
            }
        )

    # Validar vigencia
    if not solicitud.otp_expira or ahora > solicitud.otp_expira:
        return templates.TemplateResponse(
            request=request,
            name="public_otp.html",
            context={
                "config": config,
                "solicitud": solicitud,
                "correo_enmascarado": correo_enmascarado,
                "segundos_restantes": 0,
                "error_otp": "El código de verificación ha expirado. Haga clic en 'Reenviar Código' para recibir uno nuevo.",
                "exito_reenvio": False,
                "is_admin": False
            }
        )

    # Validar coincidencia de código
    if codigo_limpio != (solicitud.codigo_otp or "").strip():
        solicitud.otp_intentos += 1
        db.commit()
        db.refresh(solicitud)
        intentos_restantes = max(0, 5 - solicitud.otp_intentos)
        return templates.TemplateResponse(
            request=request,
            name="public_otp.html",
            context={
                "config": config,
                "solicitud": solicitud,
                "correo_enmascarado": correo_enmascarado,
                "segundos_restantes": segundos_restantes,
                "error_otp": f"Código de verificación incorrecto. Le quedan {intentos_restantes} intento(s).",
                "exito_reenvio": False,
                "is_admin": False
            }
        )

    # Código correcto: Activar solicitud a PENDIENTE
    solicitud.estado = "PENDIENTE"
    solicitud.codigo_otp = None  # Invalida código consumido por seguridad
    db.commit()

    # Despertar worker FIFO
    notificar_nueva_solicitud()

    # Mostrar confirmación exitosa en pantalla principal
    formatos_activos = db.query(FormatoOficial).filter(FormatoOficial.activo == True).all()
    return templates.TemplateResponse(
        request=request,
        name="public_index.html",
        context={
            "config": config,
            "formatos_activos": formatos_activos,
            "is_admin": False,
            "mensaje_exito": True,
            "nuevo_folio": solicitud.folio,
            "nuevo_folio_cct": solicitud.cct,
            "nuevo_correo": solicitud.correo,
            "aviso_antisaturacion": None,
            "error_msg": None,
            "form_data": None
        }
    )


@router.post("/verificar-otp/{folio}/reenviar")
async def reenviar_codigo_otp(
    folio: str,
    db: Session = Depends(get_db)
):
    """Genera un nuevo código OTP, reinicia intentos y vigencia, y lo envía por correo."""
    config = ConfigManager.get_all(db)
    solicitud = db.query(Solicitud).filter(Solicitud.folio == folio.strip().upper()).first()
    if not solicitud or solicitud.estado != "PENDIENTE_VERIFICACION":
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    nuevo_codigo = f"{random.randint(100000, 999999)}"
    solicitud.codigo_otp = nuevo_codigo
    solicitud.otp_expira = ahora_mexico() + datetime.timedelta(minutes=10)
    solicitud.otp_intentos = 0
    db.commit()

    asyncio.create_task(SidiMailer.enviar_codigo_otp(solicitud, nuevo_codigo, config))

    return RedirectResponse(
        url=f"/verificar-otp/{solicitud.folio}?reenviado=1",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/consulta", response_class=HTMLResponse)
async def consultar_solicitud(
    request: Request,
    folio: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Permite al solicitante rastrear el estado de su inventario mediante Folio o CCT."""
    config = ConfigManager.get_all(db)
    solicitud = None
    query_clean = folio.strip().upper() if folio else None

    if query_clean:
        solicitud = db.query(Solicitud).filter(
            (Solicitud.folio == query_clean) | (Solicitud.cct == query_clean)
        ).order_by(Solicitud.fecha_solicitud.desc()).first()

    return templates.TemplateResponse(
        request=request,
        name="public_status.html",
        context={
            "config": config,
            "solicitud": solicitud,
            "query": query_clean,
            "is_admin": False
        }
    )


@router.get("/descargar/formato/{formato_id}")
async def descargar_formato_publico(
    formato_id: int,
    request: Request,
    folio: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Descarga de formatos oficiales vigentes (Excel / PDF) - Requiere folio registrado o sesión admin."""
    es_admin = bool(request.session.get("admin_user"))
    tiene_folio = False
    if folio:
        tiene_folio = db.query(Solicitud).filter(Solicitud.folio == folio.strip().upper()).first() is not None

    if not es_admin and not tiene_folio:
        return RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)

    formato = db.query(FormatoOficial).filter(FormatoOficial.id == formato_id).first()
    if not formato:
        return HTMLResponse("El formato oficial solicitado no fue encontrado.", status_code=404)

    ruta_archivo = formato.ruta_valida
    if not os.path.exists(ruta_archivo):
        return HTMLResponse("El archivo del formato no se encuentra disponible en el servidor.", status_code=404)

    nombre_archivo = os.path.basename(ruta_archivo)
    media_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if formato.tipo == "EXCEL" else "application/pdf"

    return FileResponse(
        path=ruta_archivo,
        filename=nombre_archivo,
        media_type=media_type
    )
