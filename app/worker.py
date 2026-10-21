import os
import sys
import asyncio
import logging
import datetime
import re
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models import Solicitud, FormatoOficial
from app.config_manager import ConfigManager
from app.playwright_bot import PlaywrightInventoryBot
from app.mailer import SidiMailer
from app.timezone import ahora_mexico

logger = logging.getLogger("sidi.worker")

# Evento para despertar al worker inmediatamente cuando entra una nueva solicitud
_worker_wake_event = asyncio.Event()
_worker_running = True


def notificar_nueva_solicitud():
    """Despierta al worker si estaba en reposo."""
    _worker_wake_event.set()


def ejecutar_bot_extraccion(config: dict, cct: str, folio: str):
    """
    Ejecuta el motor de extracción en un hilo de trabajo dedicado (ThreadPoolExecutor).
    Al usar Playwright Sync adaptado de Bot-Inventarios-IEEPO, queda 100% inmune
    a los problemas de bucle de eventos en Windows causados por Uvicorn.
    """
    bot = PlaywrightInventoryBot(config)
    return bot.extraer_constancia(cct, folio)


async def procesar_solicitud_individual(solicitud_id: int):
    """
    Procesa de manera atómica una solicitud individual:
    1. Extracción con motor Playwright adaptado de Bot-Inventarios-IEEPO
    2. Envío de correo electrónico con formatos normativos
    3. Purga inmediata de la constancia PDF en almacenamiento local
    4. Manejo de reintentos y actualización de estado
    """
    db: Session = SessionLocal()
    try:
        solicitud = db.query(Solicitud).filter(Solicitud.id == solicitud_id).first()
        if not solicitud:
            return

        # Marcar como en proceso
        solicitud.estado = "PROCESANDO"
        db.commit()
        db.refresh(solicitud)

        config = ConfigManager.get_all(db)
        logger.info(f"==> Iniciando procesamiento de Folio {solicitud.folio} (CCT: {solicitud.cct})")

        try:
            # 1. Extracción vía Playwright ejecutada en ThreadPool para no bloquear FastAPI ni fallar en Windows
            current_loop = asyncio.get_running_loop()
            exito, codigo_estado, pdf_temporal = await current_loop.run_in_executor(
                None, ejecutar_bot_extraccion, config, solicitud.cct, solicitud.folio
            )

            if exito and codigo_estado == "EXITO":
                logger.info(f"CCT {solicitud.cct} localizada. Preparando despacho de correo...")

                # Obtener formatos oficiales activos
                formatos = db.query(FormatoOficial).filter(FormatoOficial.activo == True).all()
                rutas_formatos = [f.ruta_valida for f in formatos if os.path.exists(f.ruta_valida)]

                # Detectar si pertenece a un paquete de Supervisión Escolar
                mensaje_info = solicitud.mensaje_error or ""
                es_supervision = "[SUPERVISION:" in mensaje_info

                zona_id = ""
                if es_supervision:
                    match_zona = re.search(r"\[SUPERVISION:\s*([^\]]+)\]", mensaje_info)
                    if match_zona:
                        zona_id = match_zona.group(1).strip()

                # REGLA CRÍTICA ANTI-DUPLICIDAD:
                # Comprobar si ya se envió previamente con éxito algún correo para esta misma supervisión
                ya_envio_formatos_supervision = False
                if es_supervision and zona_id:
                    ya_envio = db.query(Solicitud).filter(
                        Solicitud.id != solicitud.id,
                        Solicitud.mensaje_error.like(f"%[SUPERVISION: {zona_id}]%"),
                        Solicitud.estado == "ENVIADO"
                    ).first()
                    if ya_envio:
                        ya_envio_formatos_supervision = True

                # Detectar si hay correo en copia (CC) a la Supervisión
                correo_cc_supervision = None
                if "[CC:" in mensaje_info:
                    match_cc = re.search(r"\[CC:\s*([^\]]+)\]", mensaje_info)
                    if match_cc:
                        correo_cc_supervision = match_cc.group(1).strip().lower()

                # Despacho de Correo según la regla:
                # Caso A: Directores con copia a la Supervisión
                if correo_cc_supervision and correo_cc_supervision != solicitud.correo.strip().lower():
                    # 1. El Director SIEMPRE recibe sus formatos e instructivos completos + cédula
                    await SidiMailer.enviar_correo_exito(
                        solicitud=solicitud,
                        config=config,
                        pdf_temporal_path=pdf_temporal,
                        rutas_formatos=rutas_formatos,
                        purgar_pdf=False  # No purgar aún, falta enviar acuse a la supervisión
                    )

                    # 2. La Supervisión recibe los formatos ÚNICAMENTE en la primera escuela exitosa
                    formatos_para_supervision = rutas_formatos if not ya_envio_formatos_supervision else []
                    asunto_supervision = f"[Acuse Supervisión {zona_id}] Constancia de Inventario - CCT: {solicitud.cct}"
                    await SidiMailer.enviar_correo_exito(
                        solicitud=solicitud,
                        config=config,
                        pdf_temporal_path=pdf_temporal,
                        rutas_formatos=formatos_para_supervision,
                        destinatario_override=correo_cc_supervision,
                        asunto_override=asunto_supervision,
                        purgar_pdf=True
                    )

                # Caso B: Despacho concentrado en la Supervisión (Modo 'supervision')
                elif es_supervision:
                    # En la primera escuela exitosa se adjuntan los formatos; en las siguientes 49 SOLO la cédula
                    formatos_a_enviar = rutas_formatos if not ya_envio_formatos_supervision else []
                    await SidiMailer.enviar_correo_exito(
                        solicitud=solicitud,
                        config=config,
                        pdf_temporal_path=pdf_temporal,
                        rutas_formatos=formatos_a_enviar,
                        purgar_pdf=True
                    )

                # Caso C: Despacho estándar individual
                else:
                    await SidiMailer.enviar_correo_exito(
                        solicitud=solicitud,
                        config=config,
                        pdf_temporal_path=pdf_temporal,
                        rutas_formatos=rutas_formatos,
                        purgar_pdf=True
                    )

                solicitud.estado = "ENVIADO"
                solicitud.fecha_procesado = ahora_mexico()
                if es_supervision:
                    solicitud.mensaje_error = f"[SUPERVISION: {zona_id}] Despachado exitosamente."
                else:
                    solicitud.mensaje_error = None
                
                if not config.get("smtp_host") or not config.get("smtp_user"):
                    solicitud.archivo_generado = "Cédula PDF extraída con éxito. (Aviso: Servidor SMTP no configurado, envío simulado en log)"
                else:
                    solicitud.archivo_generado = "Cédula PDF generada y despachada por correo exitosamente."
                
                db.commit()
                logger.info(f"==> Folio {solicitud.folio} PROCESADO Y ENVIADO EXITOSAMENTE")

            elif codigo_estado == "CCT_NO_ENCONTRADA":
                logger.warning(f"CCT {solicitud.cct} no registrada en portal central. Notificando al solicitante...")
                await SidiMailer.enviar_correo_cct_no_encontrada(solicitud, config)

                solicitud.estado = "CCT_NO_ENCONTRADA"
                solicitud.fecha_procesado = ahora_mexico()
                solicitud.mensaje_error = "Centro de Trabajo no localizado en el catálogo de inventarios."
                db.commit()
                logger.info(f"==> Folio {solicitud.folio} MARCADO COMO CCT_NO_ENCONTRADA")

        except Exception as err:
            logger.error(f"Error al procesar CCT {solicitud.cct}: {err}")
            solicitud.reintentos += 1

            if solicitud.reintentos < 3:
                solicitud.estado = "PENDIENTE"
                solicitud.mensaje_error = f"Reintento {solicitud.reintentos}/3: {str(err)}"
                db.commit()
                logger.warning(f"Reintentando folio {solicitud.folio} en breve (Intento {solicitud.reintentos}/3)...")
                await asyncio.sleep(5 * solicitud.reintentos)
            else:
                solicitud.estado = "ERROR_PORTAL"
                solicitud.mensaje_error = f"Fallo tras 3 intentos: {str(err)}"
                solicitud.fecha_procesado = ahora_mexico()
                db.commit()
                logger.error(f"Folio {solicitud.folio} agotó los 3 reintentos. Estado: ERROR_PORTAL")

    except Exception as general_err:
        logger.error(f"Excepción general en worker para ID {solicitud_id}: {general_err}")
    finally:
        db.close()


async def sidi_queue_worker():
    """
    Bucle principal del Worker Asíncrono FIFO.
    Toma estrictamente 1 solicitud a la vez para no saturar el portal central.
    """
    global _worker_running
    logger.info("Iniciando Worker FIFO de SIDI...")

    while _worker_running:
        db = SessionLocal()
        siguiente_solicitud = None
        try:
            # Seleccionar la solicitud pendiente más antigua (FIFO)
            siguiente_solicitud = db.query(Solicitud).filter(
                Solicitud.estado == "PENDIENTE"
            ).order_by(Solicitud.fecha_solicitud.asc()).first()
        except Exception as e:
            logger.error(f"Error al consultar cola de solicitudes: {e}")
        finally:
            db.close()

        if siguiente_solicitud:
            await procesar_solicitud_individual(siguiente_solicitud.id)
            # Pausa de cortesía entre peticiones para estabilidad SMTP y del portal central
            await asyncio.sleep(3.0)
        else:
            try:
                await asyncio.wait_for(_worker_wake_event.wait(), timeout=3.0)
                _worker_wake_event.clear()
            except asyncio.TimeoutError:
                pass


def stop_worker():
    """Detiene ordenadamente el worker al apagar la aplicación."""
    global _worker_running
    _worker_running = False
    _worker_wake_event.set()
