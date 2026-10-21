import os
import logging
import mimetypes
from email.message import EmailMessage
from typing import List, Dict, Any, Tuple, Optional
import aiosmtplib
from app.config_manager import ConfigManager

logger = logging.getLogger("sidi.mailer")


class SidiMailer:
    """
    Servicio de correo electrónico asíncrono para el despacho de inventarios
    y notificaciones de CCT no encontrada.
    """

    @staticmethod
    def _construir_contexto(solicitud, config: Dict[str, str]) -> Dict[str, Any]:
        """Prepara las variables para las plantillas de correo."""
        return {
            "nombre": solicitud.nombre_solicitante,
            "cargo": solicitud.cargo,
            "cct": solicitud.cct,
            "folio": solicitud.folio,
            "telefono": solicitud.telefono,
            "correo": solicitud.correo,
            "region": solicitud.region,
            "nivel_educativo": solicitud.nivel_educativo,
            "fecha": solicitud.fecha_solicitud.strftime("%d/%m/%Y %H:%M") if solicitud.fecha_solicitud else "",
            "oficina_departamento": config.get("oficina_departamento", "Control de Inventarios IEEPO"),
            "oficina_telefono": config.get("oficina_telefono", "(000) 000-0000"),
            "oficina_correo": config.get("oficina_correo", "inventarios@ejemplo.gob.mx"),
            "oficina_direccion": config.get("oficina_direccion", "Oaxaca, México (Demo)"),
            "oficina_horario": config.get("oficina_horario", "Lunes a Viernes de 8:00 a 16:00 hrs."),
            "nombre_institucion": config.get("nombre_institucion", "IEEPO"),
            "url_sistema": config.get("url_sistema", "http://localhost:8000").rstrip("/"),
            "url_ayuda": f"{config.get('url_sistema', 'http://localhost:8000').rstrip('/')}/consulta?folio={solicitud.folio}",
        }

    @classmethod
    async def enviar_correo_exito(
        cls,
        solicitud,
        config: Dict[str, str],
        pdf_temporal_path: Optional[str] = None,
        rutas_formatos: Optional[List[str]] = None,
        destinatario_override: Optional[str] = None,
        asunto_override: Optional[str] = None,
        purgar_pdf: bool = True
    ) -> bool:
        """
        Envía la constancia PDF descargada junto con los formatos oficiales vigentes.
        Garantiza el borrado del PDF temporal al finalizar (si purgar_pdf es True).
        """
        try:
            rutas_formatos = rutas_formatos or []
            contexto = cls._construir_contexto(solicitud, config)

            # Renderizar plantilla dinámica
            asunto = asunto_override if asunto_override else ConfigManager.render_text(config.get("template_exito_asunto", ""), contexto)
            cuerpo_html = ConfigManager.render_text(config.get("template_exito_cuerpo", ""), contexto)

            msg = EmailMessage()
            msg["Subject"] = asunto
            from_name = config.get("smtp_from_name", "SIDI - Departamento de Inventarios")
            from_email = config.get("smtp_from_email", "").strip() or config.get("smtp_user", "no-reply@ejemplo.gob.mx")
            if "gmail.com" in config.get("smtp_host", "").lower() and "@gmail.com" not in from_email.lower():
                from_email = config.get("smtp_user", from_email)

            dest_email = destinatario_override.strip() if destinatario_override else solicitud.correo
            msg["From"] = f"{from_name} <{from_email}>"
            msg["To"] = dest_email
            msg["Reply-To"] = config.get("smtp_user") or config.get("oficina_correo", "")

            # Envolver cuerpo en plantilla HTML moderna
            color_primario = config.get("color_primario", "#8B1D42")
            color_secundario = config.get("color_secundario", "#B28834")

            html_final = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <style>
                    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 20px; }}
                    .container {{ max-width: 650px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }}
                    .header {{ background-color: {color_primario}; color: #ffffff; padding: 25px 30px; border-bottom: 4px solid {color_secundario}; }}
                    .header h1 {{ margin: 0; font-size: 20px; font-weight: 700; letter-spacing: 0.5px; }}
                    .header p {{ margin: 5px 0 0 0; font-size: 13px; opacity: 0.9; }}
                    .content {{ padding: 30px; font-size: 14px; line-height: 1.65; color: #334155; }}
                    .badge {{ display: inline-block; background-color: #f1f5f9; padding: 4px 10px; border-radius: 4px; font-weight: 600; color: {color_primario}; }}
                    .footer {{ background-color: #f1f5f9; padding: 20px 30px; font-size: 11px; color: #64748b; text-align: center; border-top: 1px solid #e2e8f0; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>SIDI | {config.get('nombre_institucion', 'Departamento de Inventarios')}</h1>
                        <p>Constancia de Inventario y Formatos Oficiales</p>
                    </div>
                    <div class="content">
                        {cuerpo_html}

                        <div style="margin: 25px 0 10px 0; padding: 18px 20px; background-color: #f8fafc; border-radius: 8px; border: 1px solid #e2e8f0; text-align: center;">
                            <p style="margin: 0 0 8px 0; font-size: 13px; font-weight: 700; color: #1e293b;">
                                💬 ¿Tiene alguna duda o aclaración sobre su inventario?
                            </p>
                            <p style="margin: 0 0 14px 0; font-size: 12px; color: #64748b; line-height: 1.5;">
                                Puede responder directamente a este correo electrónico o consultar su estatus oficial en nuestra ventanilla en línea:
                            </p>
                            <a href="{contexto['url_ayuda']}" target="_blank" style="display: inline-block; background-color: {color_primario}; color: #ffffff; text-decoration: none; padding: 10px 22px; border-radius: 6px; font-size: 12px; font-weight: 700; letter-spacing: 0.3px;">
                                🌐 Ir a la Ventanilla Web de Ayuda y Rastreo
                            </a>
                        </div>
                    </div>
                    <div class="footer">
                        Este es un mensaje institucional generado por el Sistema de Despacho de Inventarios (SIDI).<br>
                        {config.get('oficina_departamento', '')} - {config.get('oficina_direccion', '')}
                    </div>
                </div>
            </body>
            </html>
            """
            msg.set_content(cuerpo_html, subtype="html")
            msg.add_alternative(html_final, subtype="html")

            # 1. Adjuntar el PDF temporal de la CCT si existe
            if pdf_temporal_path and os.path.exists(pdf_temporal_path):
                with open(pdf_temporal_path, "rb") as f:
                    pdf_data = f.read()
                msg.add_attachment(
                    pdf_data,
                    maintype="application",
                    subtype="pdf",
                    filename=f"Constancia_Inventario_{solicitud.cct}_{solicitud.folio}.pdf"
                )

            # 2. Adjuntar los formatos oficiales vigentes (Excels de Alta, Baja, Traslado y Guías PDF)
            for ruta in rutas_formatos:
                if os.path.exists(ruta):
                    nombre_archivo = os.path.basename(ruta)
                    ctype, encoding = mimetypes.guess_type(ruta)
                    if ctype is None or encoding is not None:
                        ctype = "application/octet-stream"
                    maintype, subtype = ctype.split("/", 1)
                    with open(ruta, "rb") as f:
                        file_data = f.read()
                    msg.add_attachment(
                        file_data,
                        maintype=maintype,
                        subtype=subtype,
                        filename=nombre_archivo
                    )

            # 3. Disparo SMTP asíncrono
            await cls._ejecutar_envio_smtp(msg, config)
            logger.info(f"Correo de éxito despachado satisfactoriamente a {solicitud.correo} [Folio: {solicitud.folio}]")
            return True

        except Exception as e:
            logger.error(f"Fallo al despachar correo de éxito para {solicitud.folio}: {e}")
            raise e

        finally:
            # Regla de Negocio Crítica: Borra el PDF temporal solo si purgar_pdf es True
            if purgar_pdf and pdf_temporal_path and os.path.exists(pdf_temporal_path):
                try:
                    os.remove(pdf_temporal_path)
                    logger.info(f"PDF temporal borrado de forma segura: {pdf_temporal_path}")
                except Exception as err:
                    logger.warning(f"No se pudo eliminar el archivo temporal {pdf_temporal_path}: {err}")

    @classmethod
    async def enviar_correo_cct_no_encontrada(cls, solicitud, config: Dict[str, str]) -> bool:
        """
        Envía notificación al solicitante cuando la CCT no está registrada en el portal central.
        """
        try:
            contexto = cls._construir_contexto(solicitud, config)

            asunto = ConfigManager.render_text(config.get("template_no_encontrada_asunto", ""), contexto)
            cuerpo_html = ConfigManager.render_text(config.get("template_no_encontrada_cuerpo", ""), contexto)

            msg = EmailMessage()
            msg["Subject"] = asunto
            from_name = config.get("smtp_from_name", "SIDI - Departamento de Inventarios")
            from_email = config.get("smtp_from_email", "").strip() or config.get("smtp_user", "no-reply@ejemplo.gob.mx")
            if "gmail.com" in config.get("smtp_host", "").lower() and "@gmail.com" not in from_email.lower():
                from_email = config.get("smtp_user", from_email)

            msg["From"] = f"{from_name} <{from_email}>"
            msg["To"] = solicitud.correo
            msg["Reply-To"] = config.get("smtp_user") or config.get("oficina_correo", "")

            color_primario = config.get("color_primario", "#8B1D42")

            html_final = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <style>
                    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 20px; }}
                    .container {{ max-width: 650px; margin: 0 auto; background: #ffffff; border-radius: 8px; border: 1px solid #e2e8f0; overflow: hidden; }}
                    .header {{ background-color: #c2410c; color: #ffffff; padding: 25px 30px; }}
                    .header h1 {{ margin: 0; font-size: 20px; font-weight: 700; }}
                    .header p {{ margin: 5px 0 0 0; font-size: 13px; opacity: 0.9; }}
                    .content {{ padding: 30px; font-size: 14px; line-height: 1.65; color: #334155; }}
                    .footer {{ background-color: #f1f5f9; padding: 20px 30px; font-size: 11px; color: #64748b; text-align: center; border-top: 1px solid #e2e8f0; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>SIDI | AVISO DE ATENCIÓN DE INVENTARIO</h1>
                        <p>Notificación de Centro de Trabajo No Localizado</p>
                    </div>
                    <div class="content">
                        {cuerpo_html}

                        <div style="margin: 25px 0 10px 0; padding: 18px 20px; background-color: #fff7ed; border-radius: 8px; border: 1px solid #ffedd5; text-align: center;">
                            <p style="margin: 0 0 8px 0; font-size: 13px; font-weight: 700; color: #9a3412;">
                                💬 Mesa de Ayuda y Regularización
                            </p>
                            <p style="margin: 0 0 14px 0; font-size: 12px; color: #c2410c; line-height: 1.5;">
                                Puede responder directamente a este correo o ingresar a nuestra ventanilla web de ayuda para dar seguimiento a su caso:
                            </p>
                            <a href="{contexto['url_ayuda']}" target="_blank" style="display: inline-block; background-color: #ea580c; color: #ffffff; text-decoration: none; padding: 10px 22px; border-radius: 6px; font-size: 12px; font-weight: 700;">
                                🌐 Consultar Estatus en Línea
                            </a>
                        </div>
                    </div>
                    <div class="footer">
                        {config.get('oficina_departamento', '')} - {config.get('oficina_direccion', '')}<br>
                        Tel: {config.get('oficina_telefono', '')}
                    </div>
                </div>
            </body>
            </html>
            """
            msg.set_content(cuerpo_html, subtype="html")
            msg.add_alternative(html_final, subtype="html")

            await cls._ejecutar_envio_smtp(msg, config)
            logger.info(f"Notificación de CCT No Encontrada enviada a {solicitud.correo} [Folio: {solicitud.folio}]")
            return True

        except Exception as e:
            logger.error(f"Fallo al enviar notificación de CCT no encontrada para {solicitud.folio}: {e}")
            raise e

    @classmethod
    async def enviar_codigo_otp(cls, solicitud, codigo_otp: str, config: Dict[str, str]) -> bool:
        """
        Envía un correo con el código OTP de 6 dígitos para validar la identidad y correo del solicitante.
        """
        try:
            from_name = config.get("smtp_from_name", "SIDI - Departamento de Inventarios")
            from_email = config.get("smtp_from_email", "").strip() or config.get("smtp_user", "no-reply@ejemplo.gob.mx")
            if "gmail.com" in config.get("smtp_host", "").lower() and "@gmail.com" not in from_email.lower():
                from_email = config.get("smtp_user", from_email)

            color_primario = config.get("color_primario", "#8B1D42")
            color_secundario = config.get("color_secundario", "#B28834")

            msg = EmailMessage()
            msg["Subject"] = f"Código de Verificación SIDI: {codigo_otp} - CCT {solicitud.cct}"
            msg["From"] = f"{from_name} <{from_email}>"
            msg["To"] = solicitud.correo
            msg["Reply-To"] = config.get("smtp_user") or config.get("oficina_correo", "")

            url_sistema = config.get("url_sistema", "http://localhost:8000").rstrip("/")
            url_verificar = f"{url_sistema}/verificar-otp/{solicitud.folio}"

            html_final = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="utf-8">
                <style>
                    body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif; background-color: #f8fafc; margin: 0; padding: 20px; }}
                    .container {{ max-width: 580px; margin: 0 auto; background: #ffffff; border-radius: 12px; border: 1px solid #e2e8f0; overflow: hidden; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); }}
                    .header {{ background-color: {color_primario}; color: #ffffff; padding: 25px 30px; border-bottom: 4px solid {color_secundario}; text-align: center; }}
                    .header h1 {{ margin: 0; font-size: 20px; font-weight: 800; }}
                    .content {{ padding: 30px; font-size: 14px; line-height: 1.6; color: #334155; }}
                    .otp-box {{ background-color: #f8fafc; border: 2px dashed {color_primario}; border-radius: 10px; padding: 18px; text-align: center; margin: 25px 0 15px 0; }}
                    .otp-code {{ font-family: 'Courier New', Courier, monospace; font-size: 38px; font-weight: 900; letter-spacing: 8px; color: {color_primario}; }}
                    .otp-timer {{ font-size: 12px; color: #64748b; margin-top: 6px; font-weight: 600; }}
                    .footer {{ background-color: #f8fafc; padding: 18px 30px; font-size: 11px; color: #94a3b8; text-align: center; border-top: 1px solid #e2e8f0; }}
                </style>
            </head>
            <body>
                <div class="container">
                    <div class="header">
                        <h1>SIDI | {config.get('nombre_institucion', 'Departamento de Inventarios')}</h1>
                        <p style="margin: 4px 0 0 0; font-size: 12px; opacity: 0.9;">Confirmación de Solicitud Oficial de Inventario</p>
                    </div>
                    <div class="content">
                        <p>Estimado(a) <strong>{solicitud.nombre_solicitante}</strong> ({solicitud.cargo}),</p>
                        <p>Hemos recibido su petición para la emisión de la Cédula Censal de Inventario correspondiente al Centro de Trabajo <strong>{solicitud.cct}</strong> con folio <strong>{solicitud.folio}</strong>.</p>
                        <p>Para verificar su correo electrónico y autorizar la extracción oficial en el portal, ingrese el siguiente código en la ventanilla:</p>
                        
                        <div class="otp-box">
                            <div class="otp-code">{codigo_otp}</div>
                            <div class="otp-timer">⏱️ Válido durante los próximos 10 minutos</div>
                        </div>

                        <div style="text-align: center; margin: 20px 0 10px 0;">
                            <a href="{url_verificar}" style="background-color: {color_primario}; color: #ffffff; padding: 12px 24px; border-radius: 8px; text-decoration: none; font-weight: bold; font-size: 14px; display: inline-block;">
                                🔗 Ingresar Código en la Ventanilla
                            </a>
                        </div>
                        <p style="font-size: 11px; text-align: center; color: #94a3b8; margin-top: 4px;">
                            (Si cerró su navegador, haga clic en el botón para volver a la pantalla de verificación).
                        </p>

                        <p style="font-size: 12px; color: #64748b; margin-top: 25px;">
                            Si usted no solicitó este trámite o desconoce la CCT <strong>{solicitud.cct}</strong>, puede hacer caso omiso de este correo; la solicitud se cancelará automáticamente.
                        </p>
                    </div>
                    <div class="footer">
                        {config.get('oficina_departamento', 'Departamento de Inventarios')}<br>
                        {config.get('oficina_direccion', '')}
                    </div>
                </div>
            </body>
            </html>
            """
            msg.set_content(f"Su código de verificación SIDI para la CCT {solicitud.cct} es: {codigo_otp}. Para ingresarlo en línea, visite: {url_verificar}")
            msg.add_alternative(html_final, subtype="html")

            await cls._ejecutar_envio_smtp(msg, config)
            logger.info(f"Código OTP ({codigo_otp}) enviado con éxito a {solicitud.correo} [Folio: {solicitud.folio}]")
            return True
        except Exception as e:
            logger.error(f"Error al enviar código OTP a {solicitud.correo}: {e}")
            raise e

    @classmethod
    async def _ejecutar_envio_smtp(cls, msg: EmailMessage, config: Dict[str, str]):
        """Ejecuta el envío mediante aiosmtplib respetando TLS/SSL o simulándolo si no hay credenciales o está en pruebas."""
        # Si el sistema está en modo prueba o simulación, NUNCA enviar correos reales por SMTP
        en_simulacion = config.get("portal_simulacion", "false").lower() in ["true", "1", "yes"]
        en_testing = os.environ.get("SIDI_TESTING", "0") == "1"
        if en_simulacion or en_testing:
            logger.info(
                f"[MODO SIMULACIÓN / TESTING] Simulación activa. "
                f"Envío simulado hacia {msg['To']} con asunto '{msg['Subject']}' (sin conectar al SMTP real)."
            )
            return

        smtp_host = config.get("smtp_host", "").strip()
        smtp_user = config.get("smtp_user", "").strip()
        smtp_password = config.get("smtp_password", "").strip()

        # Si el servidor SMTP no está configurado (ej: primer arranque local), registrar en log y simular
        if not smtp_host or not smtp_user:
            logger.warning(
                f"[SIMULACIÓN SMTP] Credenciales SMTP no configuradas. "
                f"Simulando envío de correo a {msg['To']} con asunto '{msg['Subject']}'."
            )
            return

        smtp_port = int(config.get("smtp_port", "587"))
        use_tls = config.get("smtp_use_tls", "true").lower() in ["true", "1", "yes"]

        # Si el usuario copió la App Password de Google con espacios ("abcd efgh ijkl mnop"), normalizar
        if "gmail.com" in smtp_host.lower() and len(smtp_password.replace(" ", "")) == 16:
            smtp_password = smtp_password.replace(" ", "")

        # Si el puerto es 465 se suele requerir use_tls=True directamente al conectar
        is_ssl = (smtp_port == 465)

        await aiosmtplib.send(
            msg,
            hostname=smtp_host,
            port=smtp_port,
            username=smtp_user,
            password=smtp_password,
            use_tls=is_ssl,
            start_tls=use_tls if not is_ssl else False,
            timeout=25
        )

    @staticmethod
    async def probar_smtp(config: Dict[str, str]) -> Tuple[bool, str]:
        """Prueba de conexión y autenticación SMTP desde el panel administrativo."""
        smtp_host = config.get("smtp_host", "").strip()
        smtp_user = config.get("smtp_user", "").strip()
        smtp_password = config.get("smtp_password", "").strip()
        smtp_port = int(config.get("smtp_port", "587"))
        use_tls = config.get("smtp_use_tls", "true").lower() in ["true", "1", "yes"]
        is_ssl = (smtp_port == 465)

        # Si el usuario copió la App Password de Google con espacios ("abcd efgh ijkl mnop"), normalizar
        if "gmail.com" in smtp_host.lower() and len(smtp_password.replace(" ", "")) == 16:
            smtp_password = smtp_password.replace(" ", "")

        if not smtp_host:
            return False, "Debe especificar un servidor host SMTP."

        try:
            smtp = aiosmtplib.SMTP(
                hostname=smtp_host,
                port=smtp_port,
                username=smtp_user if smtp_user else None,
                password=smtp_password if smtp_password else None,
                use_tls=is_ssl,
                start_tls=use_tls if not is_ssl else False,
                timeout=15
            )
            await smtp.connect()
            if smtp.is_connected:
                await smtp.quit()
            return True, "Conexión y autenticación SMTP exitosa."
        except aiosmtplib.SMTPAuthenticationError as e:
            return False, f"Error de autenticación SMTP: Usuario o contraseña/App Password incorrectos ({e.message})"
        except aiosmtplib.SMTPConnectError as e:
            return False, f"Error al conectar con el servidor SMTP: {e.message}"
        except Exception as e:
            return False, f"Error de conexión SMTP: {str(e)}"
