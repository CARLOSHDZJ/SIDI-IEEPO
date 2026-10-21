import os
import re
import time
import logging
from pathlib import Path
from typing import Tuple, Optional
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
from app.database import TEMP_DIR

logger = logging.getLogger("sidi.playwright")

DEFAULT_PORTAL_URL = "https://portal-inventarios.ejemplo.gob.mx/#login"


def limpiar_nombre_archivo(cadena: str) -> str:
    """Limpia caracteres no válidos para nombres de archivo en Windows."""
    return re.sub(r'[\\/*?:"<>|]', "_", str(cadena).strip())


class PlaywrightInventoryBot:
    """
    Motor de extracción adaptado del proyecto validado Bot-Inventarios-IEEPO (v2.0 Pro).
    Utiliza Playwright Sync ejecutado en subproceso/hilo worker dedicado, garantizando
    inmunidad ante problemas de bucle de eventos en Windows y máxima estabilidad ExtJS.
    """

    def __init__(self, config: dict):
        self.portal_url = config.get("portal_url", DEFAULT_PORTAL_URL).strip()
        if not self.portal_url:
            self.portal_url = DEFAULT_PORTAL_URL
        self.portal_user = config.get("portal_user", "").strip()
        self.portal_password = config.get("portal_password", "").strip()
        self.modo_simulacion = config.get("portal_simulacion", "false").lower() in ["true", "1", "yes"]
        self.timeout_segundos = int(config.get("portal_timeout", 30)) * 1000

    def _generar_pdf_simulado(self, cct: str, folio: str, destino_pdf: str) -> str:
        """Genera un PDF simulado para pruebas locales sin conexión a la red de IEEPO."""
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page()
                html_content = f"""
                <!DOCTYPE html>
                <html lang="es">
                <head>
                    <meta charset="UTF-8">
                    <style>
                        body {{ font-family: Arial, sans-serif; margin: 30px; color: #222; font-size: 11px; }}
                        .header {{ border-bottom: 2px solid #8B1D42; padding-bottom: 10px; margin-bottom: 20px; }}
                        .title {{ font-size: 16px; font-weight: bold; color: #8B1D42; text-align: center; }}
                        .subtitle {{ font-size: 12px; color: #444; text-align: center; margin-top: 3px; }}
                        .box {{ border: 1px solid #ccc; padding: 8px; margin: 15px 0; background: #fafafa; }}
                        table {{ width: 100%; border-collapse: collapse; margin-top: 15px; font-size: 10px; }}
                        th, td {{ border: 1px solid #bbb; padding: 6px; text-align: left; }}
                        th {{ background: #8B1D42; color: #fff; }}
                    </style>
                </head>
                <body>
                    <div class="header">
                        <div class="title">INSTITUTO ESTATAL DE EDUCACIÓN PÚBLICA DE OAXACA</div>
                        <div class="subtitle">DEPARTAMENTO DE CONTROL DE BIENES MUEBLES E INVENTARIOS</div>
                        <div class="subtitle" style="font-weight: bold; margin-top: 5px;">AREA RESGUARDANTE DE BIENES MUEBLES (CÉDULA CENSAL)</div>
                    </div>
                    <div class="box">
                        <strong>CLAVE DE CENTRO DE TRABAJO:</strong> {cct}<br>
                        <strong>FOLIO OFICIAL DE DESPACHO:</strong> {folio}<br>
                        <strong>ESTADO:</strong> INVENTARIO VIGENTE Y CERTIFICADO
                    </div>
                    <table>
                        <thead>
                            <tr>
                                <th>No.</th>
                                <th>No. Inventario</th>
                                <th>Bien</th>
                                <th>Descripción</th>
                                <th>Edo. F.</th>
                                <th>Tipo Adquisición</th>
                                <th>Precio</th>
                            </tr>
                        </thead>
                        <tbody>
                            <tr><td>1</td><td>2520731</td><td>BUTACA SIN TIPO</td><td>ESTRUCTURA DE FIERRO Y MADERA</td><td>BUENO</td><td>IEEPO</td><td>$60.00</td></tr>
                            <tr><td>2</td><td>2520747</td><td>MESA PARA MAESTRO</td><td>TUBULAR METÁLICA CON CUBIERTA FORMAICA</td><td>REGULAR</td><td>IEEPO</td><td>$450.00</td></tr>
                            <tr><td>3</td><td>2520761</td><td>PIZARRÓN BLANCO</td><td>PORCELANIZADO 1.20 X 2.40</td><td>BUENO</td><td>IEEPO</td><td>$850.00</td></tr>
                        </tbody>
                    </table>
                </body>
                </html>
                """
                page.set_content(html_content)
                page.pdf(path=destino_pdf, format="A4", print_background=True)
                browser.close()
        except Exception:
            # Fallback ligero en Python puro si el contenedor aún no tiene instalado el binario de Chromium
            minimal_pdf = (
                b"%PDF-1.4\n"
                b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
                b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
                b"3 0 obj << /Type /Page /Parent 2 0 R /Resources << /Font << /F1 4 0 R >> >> /MediaBox [0 0 612 792] /Contents 5 0 R >> endobj\n"
                b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n"
                b"5 0 obj << /Length 140 >> stream\n"
                b"BT /F1 14 Tf 72 700 Td (CEDULA DE INVENTARIO DEMO - SIDI IEEPO) Tj /F1 11 Tf 0 -25 Td (CCT: "
                + cct.encode("ascii", "ignore")
                + b" | Folio: "
                + folio.encode("ascii", "ignore")
                + b") Tj ET\n"
                b"endstream endobj\n"
                b"xref\n0 6\n0000000000 65535 f \n0000000009 00000 n \n0000000058 00000 n \n0000000115 00000 n \n0000000229 00000 n \n0000000297 00000 n \n"
                b"trailer << /Size 6 /Root 1 0 R >>\nstartxref\n487\n%%EOF\n"
            )
            with open(destino_pdf, "wb") as fp:
                fp.write(minimal_pdf)
        return destino_pdf

    def extraer_constancia(self, cct: str, folio: str) -> Tuple[bool, str, Optional[str]]:
        """
        Ejecuta la extracción de inventario según la arquitectura probada de Bot-Inventarios-IEEPO:
        1. Login con credenciales configurables en #login.
        2. Navega a Mobiliario -> Constancia de Área.
        3. Escribe la CCT en el combo de Área y la selecciona de la lista desplegable.
        4. Hace clic en 'Reporte' y captura la descarga directa del PDF.
        """
        nombre_limpio = limpiar_nombre_archivo(cct.strip().upper())
        destino_pdf = str(Path(TEMP_DIR) / f"{nombre_limpio}_{folio}.pdf")

        # Modo Simulación
        if self.modo_simulacion:
            logger.info(f"[SIMULACIÓN] Procesando CCT {cct} con Folio {folio}")
            time.sleep(1.5)
            if cct.endswith("999X") or cct.endswith("0000Z") or cct == "20DPR0000Z":
                logger.info(f"[SIMULACIÓN] CCT {cct} simulada como NO ENCONTRADA")
                return False, "CCT_NO_ENCONTRADA", None

            self._generar_pdf_simulado(cct, folio, destino_pdf)
            return True, "EXITO", destino_pdf

        # Validar credenciales
        if not self.portal_user or not self.portal_password:
            msg = (
                "Credenciales del portal central no configuradas. "
                "Configure Usuario y Contraseña en Panel de Administración > Configuración > Portal ExtJS."
            )
            logger.error(msg)
            raise Exception(msg)

        logger.info(f"Lanzando motor Chromium para CCT {cct} (Usuario: {self.portal_user})...")

        with sync_playwright() as p:
            # Argumento clave: --disable-pdf-extension convierte cualquier visor en descarga nativa
            args_launch = ["--disable-pdf-extension", "--no-sandbox", "--disable-dev-shm-usage"]
            browser = p.chromium.launch(
                headless=True,
                downloads_path=str(TEMP_DIR),
                args=args_launch
            )

            context = browser.new_context(
                accept_downloads=True,
                ignore_https_errors=True
            )

            page = context.new_page()
            page.set_default_timeout(self.timeout_segundos)

            try:
                # -------------------------------------------------------------
                # 1. LOGIN
                # -------------------------------------------------------------
                logger.info(f"Navegando a {self.portal_url}...")
                try:
                    page.goto(self.portal_url, wait_until="networkidle", timeout=self.timeout_segundos)
                except Exception:
                    page.goto(self.portal_url, wait_until="domcontentloaded", timeout=self.timeout_segundos)

                logger.info("Ingresando credenciales en portal ExtJS...")
                user_input = page.locator("input[name='username'], input[name='usuario'], input[type='text']").first
                user_input.wait_for(state="visible", timeout=self.timeout_segundos)
                user_input.fill(self.portal_user)

                pass_input = page.locator("input[name='password'], input[name='contrasena'], input[type='password']").first
                pass_input.wait_for(state="visible", timeout=self.timeout_segundos)
                pass_input.fill(self.portal_password)

                btn_login = page.locator(
                    ".x-btn-inner:has-text('Login'), button:has-text('Login'), span:has-text('Login'), input[type='submit']"
                ).first

                if btn_login.is_visible():
                    btn_login.click()
                else:
                    pass_input.press("Enter")

                time.sleep(3)
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:
                    pass

                # Comprobar si hubo error de login
                error_box = page.locator(".x-message-box, .x-window-header-text:has-text('Error')")
                if error_box.count() > 0 and error_box.first.is_visible():
                    err_txt = page.locator(".x-window-text").first.inner_text() if page.locator(".x-window-text").count() > 0 else "Credenciales incorrectas"
                    logger.error(f"Fallo de autenticación en portal central: {err_txt}")
                    raise Exception(f"Error de credenciales en portal IEEPO: {err_txt}")

                logger.info("Sesión iniciada con éxito en portal IEEPO.")

                # -------------------------------------------------------------
                # 2. MENÚ: Mobiliario -> Constancia de Área
                # -------------------------------------------------------------
                logger.info("Abriendo menú 'Mobiliario' -> 'Constancia de Área'...")
                menu_mob = page.locator("text='Mobiliario'").first
                menu_mob.wait_for(state="visible", timeout=self.timeout_segundos)
                menu_mob.click()
                time.sleep(1)

                item_constancia = page.locator("text=/Constancia de .?rea/i").first
                item_constancia.wait_for(state="visible", timeout=10000)
                item_constancia.click()
                time.sleep(2)

                # Mover ratón a la esquina para disipar overlays
                page.mouse.move(0, 0)
                time.sleep(1)

                # -------------------------------------------------------------
                # 3. CAMPO ÁREA & BÚSQUEDA DE CCT
                # -------------------------------------------------------------
                logger.info(f"Localizando campo Área para CCT {cct}...")
                campo_area = page.locator(
                    "input[name='idarea'], input[role='combobox'], label:has-text('Área') + div input, label:has-text('Area') + div input"
                ).first

                campo_area.wait_for(state="visible", timeout=15000)
                campo_area.click(force=True)
                campo_area.fill("")
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
                time.sleep(0.3)

                # Teclear CCT con retardo para alimentar el autocomplete
                campo_area.type(cct, delay=80)
                time.sleep(2)

                # Seleccionar elemento del desplegable
                item_desplegable = page.locator(f".x-boundlist-item:has-text('{cct}')").first
                encontrado = False

                try:
                    if item_desplegable.is_visible():
                        item_desplegable.click()
                        encontrado = True
                        time.sleep(1)
                except Exception:
                    pass

                if not encontrado:
                    # Selección mediante teclado
                    page.keyboard.press("ArrowDown")
                    time.sleep(0.5)
                    page.keyboard.press("Enter")
                    time.sleep(1.5)

                # Comprobar si el campo tomó el valor o si la CCT no existe
                valor_actual = campo_area.input_value().strip()
                if not valor_actual or cct.upper() not in valor_actual.upper():
                    # Verificar ventanas de error o boundlist vacío
                    boundlist_vacio = page.locator(".x-boundlist-empty")
                    if boundlist_vacio.count() > 0 and boundlist_vacio.first.is_visible():
                        logger.warning(f"La CCT {cct} no existe en el catálogo central.")
                        return False, "CCT_NO_ENCONTRADA", None

                    # Si después de intentar sigue vacío, no está registrada
                    logger.warning(f"CCT {cct} no pudo ser seleccionada (posiblemente no registrada).")
                    return False, "CCT_NO_ENCONTRADA", None

                # -------------------------------------------------------------
                # 4. BOTÓN 'Reporte' Y DESCARGA NATIVA
                # -------------------------------------------------------------
                logger.info(f"CCT {cct} seleccionada ({valor_actual}). Localizando botón 'Reporte'...")
                btn_reporte = page.locator(
                    "a.x-btn:has-text('Reporte'), button:has-text('Reporte'), span.x-btn-inner:has-text('Reporte'), .x-btn:has-text('Reporte')"
                ).first

                btn_reporte.wait_for(state="visible", timeout=10000)

                download_ok = False
                try:
                    # Al tener --disable-pdf-extension, Chromium dispara download directo
                    with page.expect_download(timeout=20000) as download_info:
                        btn_reporte.click(force=True)

                    download = download_info.value
                    download.save_as(destino_pdf)
                    download_ok = True
                    logger.info(f"Descarga nativa capturada en: {destino_pdf}")

                except PlaywrightTimeoutError:
                    logger.warning("Download nativo demoró; verificando si abrió nueva pestaña...")
                    pages_actuales = context.pages
                    if len(pages_actuales) > 1:
                        new_tab = pages_actuales[-1]
                        tab_url = new_tab.url
                        if "pdf" in tab_url.lower():
                            res = new_tab.request.get(tab_url)
                            if res.ok and len(res.body()) > 500:
                                with open(destino_pdf, "wb") as f:
                                    f.write(res.body())
                                download_ok = True
                                logger.info(f"PDF descargado de pestaña emergente: {destino_pdf}")
                        new_tab.close()

                if download_ok and os.path.exists(destino_pdf) and os.path.getsize(destino_pdf) > 500:
                    tam_kb = os.path.getsize(destino_pdf) // 1024
                    logger.info(f"✓ Cédula oficial descargada exitosamente ({tam_kb} KB): {destino_pdf}")
                    return True, "EXITO", destino_pdf
                else:
                    raise Exception("No se generó el archivo PDF del inventario o el tamaño es menor a 500 bytes.")

            except Exception as e:
                logger.error(f"Fallo durante la extracción de CCT {cct}: {e}")
                raise e
            finally:
                context.close()
                browser.close()

    @staticmethod
    def probar_conexion_portal(url: str, user: str, password: str) -> Tuple[bool, str]:
        """
        Prueba la conexión y credenciales contra el portal IEEPO.
        Adaptado directamente de test_connection_and_credentials de Bot-Inventarios-IEEPO.
        """
        if not user or not password:
            return False, "Debe ingresar tanto el usuario como la contraseña del portal central."

        url_test = url.strip() if url.strip() else DEFAULT_PORTAL_URL

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    headless=True,
                    args=["--disable-pdf-extension", "--no-sandbox"]
                )
                context = browser.new_context(ignore_https_errors=True)
                page = context.new_page()
                page.set_default_timeout(25000)

                try:
                    page.goto(url_test, wait_until="networkidle", timeout=25000)
                except Exception:
                    page.goto(url_test, wait_until="domcontentloaded", timeout=25000)

                page.wait_for_selector("input[name='username'], input[name='usuario'], input[type='text']", timeout=15000)
                time.sleep(1)

                user_input = page.locator("input[name='username'], input[name='usuario'], input[type='text']").first
                pass_input = page.locator("input[name='password'], input[name='contrasena'], input[type='password']").first

                user_input.fill(user)
                pass_input.fill(password)

                btn_login = page.locator(
                    ".x-btn-inner:has-text('Login'), button:has-text('Login'), span:has-text('Login'), input[type='submit']"
                ).first

                if btn_login.is_visible():
                    btn_login.click()
                else:
                    pass_input.press("Enter")

                time.sleep(3)
                current_url = page.url

                # Comprobar error
                error_box = page.locator(".x-message-box, .x-window-header-text:has-text('Error')")
                if error_box.count() > 0 and error_box.first.is_visible():
                    err_text = page.locator(".x-window-text").first.inner_text() if page.locator(".x-window-text").count() > 0 else "Credenciales incorrectas"
                    browser.close()
                    return False, f"Fallo de autenticación: {err_text}"

                if "#home" in current_url or page.locator("text='Mobiliario'").count() > 0:
                    browser.close()
                    return True, "¡Conexión y autenticación EXITOSAS! Las credenciales son válidas y el portal IEEPO respondió correctamente."

                time.sleep(2)
                if page.locator("text='Mobiliario'").count() > 0 or "#home" in page.url:
                    browser.close()
                    return True, "¡Conexión y autenticación exitosas en el portal IEEPO!"

                browser.close()
                return False, "El inicio de sesión finalizó pero no se detectó el menú principal '#home'."

        except PlaywrightTimeoutError:
            return False, "Tiempo de espera agotado al intentar conectar con el portal IEEPO."
        except Exception as ex:
            return False, f"Error durante la prueba de conexión: {str(ex)}"
