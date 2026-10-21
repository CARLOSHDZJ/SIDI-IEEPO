import os
import io
import time
import datetime
import logging
import re
import urllib.parse
import json
import pandas as pd
from typing import Optional
from fastapi import APIRouter, Depends, Request, Form, UploadFile, File, HTTPException, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, text

from app.database import get_db, FORMATOS_DIR, LOGOS_DIR, TEMP_DIR
from app.models import Solicitud, FormatoOficial, UsuarioAdmin
from app.config_manager import ConfigManager
from app.auth import verify_password, hash_password
from app.schemas import CCT_REGEX
import asyncio
from app.worker import notificar_nueva_solicitud
from app.mailer import SidiMailer
from app.playwright_bot import PlaywrightInventoryBot
from app.timezone import ahora_mexico

logger = logging.getLogger("sidi.admin")

router = APIRouter(prefix="/admin")
templates = Jinja2Templates(directory=os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates"))

REGIONES_OAXACA = [
    "Valles Centrales",
    "Istmo",
    "Costa",
    "Mixteca",
    "Papaloapan",
    "Cañada",
    "Sierra de Juárez (Norte)",
    "Sierra Sur"
]

NIVELES_EDUCATIVOS = [
    "Educación Inicial",
    "Preescolar General",
    "Preescolar Indígena",
    "Primaria General",
    "Primaria Indígena",
    "Secundaria General",
    "Secundaria Técnica",
    "Telesecundaria",
    "Educación Especial",
    "Educación Física / Extraescolar",
    "Misiones Culturales",
    "Centro de Maestros",
    "Otro"
]


INACTIVIDAD_MAX_SEGUNDOS = 7200  # 2 horas (120 minutos) de inactividad máxima permitida


def verificar_sesion_admin(request: Request):
    """Verifica si existe una sesión administrativa activa y valida tiempo de inactividad."""
    usuario = request.session.get("admin_user")
    if not usuario:
        raise HTTPException(
            status_code=status.HTTP_303_SEE_OTHER,
            headers={"Location": "/admin/login"}
        )

    ahora = time.time()
    ultima_actividad = request.session.get("last_activity")

    if ultima_actividad is not None:
        tiempo_transcurrido = ahora - ultima_actividad
        if tiempo_transcurrido > INACTIVIDAD_MAX_SEGUNDOS:
            request.session.clear()
            raise HTTPException(
                status_code=status.HTTP_303_SEE_OTHER,
                headers={"Location": "/admin/login?inactividad=1"}
            )

    # Actualizar marca de última actividad registrada
    request.session["last_activity"] = ahora


@router.get("/ping")
async def admin_ping(request: Request):
    """Mantiene viva la sesión administrativa mientras el usuario captura o interactúa con el sistema."""
    usuario = request.session.get("admin_user")
    if not usuario:
        return {"status": "unauthenticated"}
    request.session["last_activity"] = time.time()
    return {"status": "ok", "user": usuario}


# ================= AUTENTICACIÓN =================

@router.get("/login", response_class=HTMLResponse)
async def login_form(
    request: Request,
    inactividad: Optional[str] = None,
    db: Session = Depends(get_db)
):
    if request.session.get("admin_user"):
        return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    config = ConfigManager.get_all(db)
    aviso = "Su sesión se cerró automáticamente por inactividad (10 minutos) por seguridad." if inactividad else None
    return templates.TemplateResponse(
        request=request,
        name="admin_login.html",
        context={"config": config, "error": None, "aviso": aviso, "is_admin": False}
    )


@router.post("/login", response_class=HTMLResponse)
async def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    config = ConfigManager.get_all(db)
    user = db.query(UsuarioAdmin).filter(UsuarioAdmin.username == username.strip(), UsuarioAdmin.activo == True).first()

    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request=request,
            name="admin_login.html",
            context={"config": config, "error": "Credenciales inválidas o usuario inactivo.", "aviso": None, "is_admin": False}
        )

    request.session["admin_user"] = user.username
    request.session["admin_name"] = user.nombre or user.username
    request.session["last_activity"] = time.time()
    return RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/logout")
async def logout(request: Request, inactividad: Optional[str] = None):
    request.session.clear()
    url = "/admin/login?inactividad=1" if inactividad else "/admin/login"
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)


# ================= 1. DASHBOARD & ANALÍTICA =================

@router.get("", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    fecha_inicio: Optional[str] = None,
    fecha_fin: Optional[str] = None,
    region: Optional[str] = None,
    nivel: Optional[str] = None,
    db: Session = Depends(get_db)
):
    verificar_sesion_admin(request)
    config = ConfigManager.get_all(db)

    # Construcción de consulta con filtros
    query = db.query(Solicitud)

    if fecha_inicio:
        try:
            f_ini = datetime.datetime.strptime(fecha_inicio, "%Y-%m-%d")
            query = query.filter(Solicitud.fecha_solicitud >= f_ini)
        except ValueError:
            pass

    if fecha_fin:
        try:
            f_fin = datetime.datetime.strptime(fecha_fin, "%Y-%m-%d") + datetime.timedelta(days=1)
            query = query.filter(Solicitud.fecha_solicitud < f_fin)
        except ValueError:
            pass

    if region:
        query = query.filter(Solicitud.region == region)

    if nivel:
        query = query.filter(Solicitud.nivel_educativo == nivel)

    solicitudes_filtradas = query.all()

    # Cálculo de KPIs
    total = len(solicitudes_filtradas)
    enviadas = sum(1 for s in solicitudes_filtradas if s.estado == "ENVIADO")
    no_encontradas = sum(1 for s in solicitudes_filtradas if s.estado == "CCT_NO_ENCONTRADA")
    en_cola = sum(1 for s in solicitudes_filtradas if s.estado in ["PENDIENTE", "PROCESANDO"])
    errores = sum(1 for s in solicitudes_filtradas if s.estado == "ERROR_PORTAL")

    tasa_exito = round((enviadas / total * 100), 1) if total > 0 else 0
    tasa_no_encontradas = round((no_encontradas / total * 100), 1) if total > 0 else 0
    tasa_errores = round((errores / total * 100), 1) if total > 0 else 0

    kpis = {
        "total": total,
        "enviadas": enviadas,
        "no_encontradas": no_encontradas,
        "en_cola": en_cola,
        "errores": errores,
        "tasa_exito": tasa_exito,
        "tasa_no_encontradas": tasa_no_encontradas,
        "tasa_errores": tasa_errores
    }

    # Distribución por Regiones para Gráfica
    conteo_regiones = {}
    for r in REGIONES_OAXACA:
        conteo_regiones[r] = sum(1 for s in solicitudes_filtradas if s.region == r)

    datos_grafica_regiones = {
        "etiquetas": list(conteo_regiones.keys()),
        "totales": list(conteo_regiones.values())
    }

    # Últimas 10 solicitudes
    solicitudes_recientes = query.order_by(Solicitud.fecha_solicitud.desc()).limit(10).all()

    filtros = {
        "fecha_inicio": fecha_inicio or "",
        "fecha_fin": fecha_fin or "",
        "region": region or "",
        "nivel": nivel or ""
    }

    return templates.TemplateResponse(
        request=request,
        name="admin_dashboard.html",
        context={
            "config": config,
            "is_admin": True,
            "kpis": kpis,
            "datos_grafica_regiones": datos_grafica_regiones,
            "solicitudes_recientes": solicitudes_recientes,
            "catalogo_regiones": REGIONES_OAXACA,
            "catalogo_niveles": NIVELES_EDUCATIVOS,
            "filtros": filtros
        }
    )


@router.get("/exportar")
async def exportar_datos(
    request: Request,
    formato: str = "excel",
    fecha_inicio: Optional[str] = None,
    fecha_fin: Optional[str] = None,
    region: Optional[str] = None,
    nivel: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Exporta las solicitudes con 1 clic a Excel (.xlsx) o CSV (.csv)."""
    verificar_sesion_admin(request)

    query = db.query(Solicitud)
    if fecha_inicio:
        try:
            f_ini = datetime.datetime.strptime(fecha_inicio, "%Y-%m-%d")
            query = query.filter(Solicitud.fecha_solicitud >= f_ini)
        except ValueError:
            pass

    if fecha_fin:
        try:
            f_fin = datetime.datetime.strptime(fecha_fin, "%Y-%m-%d") + datetime.timedelta(days=1)
            query = query.filter(Solicitud.fecha_solicitud < f_fin)
        except ValueError:
            pass

    if region:
        query = query.filter(Solicitud.region == region)

    if nivel:
        query = query.filter(Solicitud.nivel_educativo == nivel)

    solicitudes = query.order_by(Solicitud.fecha_solicitud.desc()).all()

    data = []
    for s in solicitudes:
        data.append({
            "Folio": s.folio,
            "CCT": s.cct,
            "Solicitante": s.nombre_solicitante,
            "Cargo": s.cargo,
            "Teléfono": s.telefono,
            "Correo Electrónico": s.correo,
            "Región": s.region,
            "Nivel Educativo": s.nivel_educativo,
            "Estado": s.estado,
            "Reintentos": s.reintentos,
            "Mensaje / Error": s.mensaje_error or "",
            "Fecha Solicitud": s.fecha_solicitud.strftime("%Y-%m-%d %H:%M:%S") if s.fecha_solicitud else "",
            "Fecha Procesado": s.fecha_procesado.strftime("%Y-%m-%d %H:%M:%S") if s.fecha_procesado else ""
        })

    df = pd.DataFrame(data)
    timestamp = ahora_mexico().strftime("%Y%m%d_%H%M%S")

    if formato.lower() == "csv":
        csv_buffer = io.StringIO()
        # Incluir BOM UTF-8 para visualización de acentos directa en Excel
        df.to_csv(csv_buffer, index=False, encoding="utf-8-sig")
        response = Response(content=csv_buffer.getvalue(), media_type="text/csv")
        response.headers["Content-Disposition"] = f"attachment; filename=SIDI_Reporte_Inventarios_{timestamp}.csv"
        return response

    # Exportación a Excel con openpyxl
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Solicitudes SIDI")
    excel_buffer.seek(0)

    return StreamingResponse(
        excel_buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=SIDI_Reporte_Inventarios_{timestamp}.xlsx"}
    )


# ================= 2. DIRECTORIO VIVO Y SOLICITUDES =================

def generar_folios_en_lote(db: Session, cantidad: int) -> list:
    anio_actual = ahora_mexico().year
    existing_folios = db.query(Solicitud.folio).filter(Solicitud.folio.like(f"SIDI-{anio_actual}-%")).all()
    max_num = 0
    for (f_str,) in existing_folios:
        try:
            parts = f_str.split("-")
            if len(parts) == 3 and parts[2].isdigit():
                val = int(parts[2])
                if val > max_num:
                    max_num = val
        except Exception:
            pass

    folios = []
    for _ in range(cantidad):
        max_num += 1
        folios.append(f"SIDI-{anio_actual}-{max_num:04d}")
    return folios


@router.get("/solicitudes", response_class=HTMLResponse)
async def admin_solicitudes(
    request: Request,
    q: Optional[str] = None,
    estado: Optional[str] = None,
    aprobado: Optional[str] = None,
    masivo: Optional[str] = None,
    total_masivo: Optional[int] = None,
    zona_masivo: Optional[str] = None,
    db: Session = Depends(get_db)
):
    verificar_sesion_admin(request)
    config = ConfigManager.get_all(db)

    query = db.query(Solicitud)

    if q:
        q_term = f"%{q.strip()}%"
        query = query.filter(
            (Solicitud.cct.ilike(q_term)) |
            (Solicitud.folio.ilike(q_term)) |
            (Solicitud.nombre_solicitante.ilike(q_term)) |
            (Solicitud.correo.ilike(q_term)) |
            (Solicitud.telefono.ilike(q_term)) |
            (Solicitud.mensaje_error.ilike(q_term))
        )

    if estado:
        query = query.filter(Solicitud.estado == estado)

    solicitudes = query.order_by(Solicitud.fecha_solicitud.desc()).all()
    total_solicitudes = len(solicitudes)

    return templates.TemplateResponse(
        request=request,
        name="admin_solicitudes.html",
        context={
            "config": config,
            "is_admin": True,
            "solicitudes": solicitudes,
            "total_solicitudes": total_solicitudes,
            "query": q or "",
            "estado_filtro": estado or "",
            "aprobado": bool(aprobado),
            "masivo": bool(masivo),
            "total_masivo": total_masivo or 0,
            "zona_masivo": zona_masivo or "",
            "catalogo_regiones": REGIONES_OAXACA,
            "catalogo_niveles": NIVELES_EDUCATIVOS
        }
    )


@router.get("/solicitudes/plantilla-excel")
async def admin_solicitudes_plantilla_excel(
    request: Request,
    ccts: Optional[str] = None
):
    """
    Genera y descarga un archivo Excel (.xlsx) estructurado con las columnas requeridas
    para la carga masiva (CCT, Correo, Nombre_Director, Telefono).
    Si se proporciona una lista de CCTs separadas por coma, las pre-llena en la columna CCT.
    """
    verificar_sesion_admin(request)

    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

    wb = Workbook()
    ws = wb.active
    ws.title = "Directores_CT"
    ws.views.sheetView[0].showGridLines = True

    # Encabezados
    headers = ["CCT", "Correo", "Nombre_Director", "Telefono"]
    ws.append(headers)

    # Estilos del encabezado institucional IEEPO
    header_fill = PatternFill(start_color="691B31", end_color="691B31", fill_type="solid")
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_align = Alignment(horizontal="center", vertical="center", wrap_text=True)

    border_thin = Border(
        left=Side(style="thin", color="D0D5DD"),
        right=Side(style="thin", color="D0D5DD"),
        top=Side(style="thin", color="D0D5DD"),
        bottom=Side(style="thin", color="D0D5DD")
    )

    ws.row_dimensions[1].height = 28
    for col_num in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_num)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = header_align
        cell.border = border_thin

    # Anchos de columna
    col_widths = {"A": 18, "B": 35, "C": 38, "D": 20}
    for col_letter, width in col_widths.items():
        ws.column_dimensions[col_letter].width = width

    font_data = Font(name="Calibri", size=10)
    font_cct = Font(name="Consolas", size=10, bold=True, color="1E293B")
    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")

    # Filas de datos (pre-llenadas si se enviaron CCTs)
    ccts_list = []
    if ccts:
        for c in ccts.split(","):
            c_clean = re.sub(r"[^A-Za-z0-9]", "", c).upper()
            if re.match(CCT_REGEX, c_clean) and c_clean not in ccts_list:
                ccts_list.append(c_clean)

    if ccts_list:
        for cct in ccts_list[:50]:
            row_idx = ws.max_row + 1
            ws.append([cct, "", "", ""])
            ws.row_dimensions[row_idx].height = 22
            c_cct = ws.cell(row=row_idx, column=1)
            c_cct.font = font_cct
            c_cct.alignment = align_center
            c_cct.border = border_thin
            for c_idx in range(2, 5):
                cell_other = ws.cell(row=row_idx, column=c_idx)
                cell_other.font = font_data
                cell_other.alignment = align_left
                cell_other.border = border_thin
    else:
        # Filas de ejemplo / orientación si la plantilla es en blanco
        ejemplos = [
            ("20DJN0001A", "director.preescolar1@gmail.com", "Mtra. Rosa Martínez Pérez", "9510000001"),
            ("20DJN0002B", "direccion.escuela2@hotmail.com", "Prof. Juan Gómez López", "9510000002"),
            ("20DJN0003C", "", "Mtra. Laura Díaz Ruiz", "9510000003"),
        ]
        for ej in ejemplos:
            row_idx = ws.max_row + 1
            ws.append(list(ej))
            ws.row_dimensions[row_idx].height = 22
            c_cct = ws.cell(row=row_idx, column=1)
            c_cct.font = font_cct
            c_cct.alignment = align_center
            c_cct.border = border_thin
            for c_idx in range(2, 5):
                cell_other = ws.cell(row=row_idx, column=c_idx)
                cell_other.font = font_data
                cell_other.alignment = align_left
                cell_other.border = border_thin

    # Hoja de instrucciones
    ws_inst = wb.create_sheet(title="Instrucciones")
    ws_inst.views.sheetView[0].showGridLines = True
    ws_inst.column_dimensions["A"].width = 30
    ws_inst.column_dimensions["B"].width = 75

    inst_title = ws_inst.cell(row=1, column=1, value="INSTRUCCIONES PARA LLENADO DE PLANTILLA SIDI")
    inst_title.font = Font(name="Calibri", size=12, bold=True, color="691B31")
    ws_inst.row_dimensions[1].height = 26

    instrucciones = [
        ("Columna CCT (Obligatoria)", "Clave oficial de 10 caracteres del Centro de Trabajo (Ejemplo: 20DJN0001A)."),
        ("Columna Correo (Opcional)", "Correo electrónico del Director o encargado de la escuela. Si se deja en blanco o no es válido, el sistema enviará automáticamente el inventario al correo de la Supervisión Escolar."),
        ("Columna Nombre_Director (Opcional)", "Nombre completo del Director o responsable del centro de trabajo."),
        ("Columna Telefono (Opcional)", "Teléfono de contacto a 10 dígitos (Ejemplo: 9510000001)."),
        ("Límite de Escuelas", "Puede incluir hasta 50 centros de trabajo por paquete de despacho masivo."),
        ("Cómo Subir", "Una vez llenado o revisado este archivo, guárdelo en su computadora y cárguelo en la sección '¿Tiene un archivo de Excel o CSV?' del panel administrativo de SIDI.")
    ]

    ws_inst.append([])
    for concepto, detalle in instrucciones:
        ws_inst.append([concepto, detalle])
        r = ws_inst.max_row
        ws_inst.cell(row=r, column=1).font = Font(name="Calibri", size=10, bold=True, color="1E293B")
        ws_inst.cell(row=r, column=2).font = Font(name="Calibri", size=10)
        ws_inst.row_dimensions[r].height = 20

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)

    nombre_archivo = "plantilla_carga_masiva_sidi.xlsx"
    return Response(
        content=output.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f'attachment; filename="{nombre_archivo}"',
            "Cache-Control": "no-cache, no-store, must-revalidate"
        }
    )


@router.post("/solicitudes/masivo")
async def admin_solicitudes_masivo(
    request: Request,
    nombre_supervisor: str = Form(...),
    zona_escolar: Optional[str] = Form(""),
    cct_supervision: Optional[str] = Form(""),
    incluir_cct_supervision: Optional[str] = Form(""),
    cargo_solicitante: str = Form("Supervisor(a) Escolar"),
    telefono_contacto: str = Form(...),
    correo_supervision: str = Form(...),
    region: str = Form(...),
    nivel_educativo: str = Form(...),
    modo_envio: str = Form("supervision"),
    ccts_texto: Optional[str] = Form(None),
    directores_json: Optional[str] = Form(None),
    archivo_excel: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db)
):
    """
    Carga y encolamiento masivo de solicitudes para Supervisiones Escolares o Jefaturas de Sector.
    Omite el código OTP e ingresa todas las escuelas directamente a la cola PENDIENTE.
    """
    verificar_sesion_admin(request)

    items_crudos = []

    # 0. Incluir CCT de la propia oficina de la Supervisión si se especificó
    if cct_supervision and incluir_cct_supervision in ["true", "1", "on", "yes"]:
        clean_sup_cct = re.sub(r"[^A-Za-z0-9]", "", cct_supervision).upper()
        if re.match(CCT_REGEX, clean_sup_cct):
            items_crudos.append({
                "cct": clean_sup_cct,
                "correo": correo_supervision.strip().lower(),
                "nombre": f"{nombre_supervisor.strip()} (Oficina Supervisión)",
                "telefono": telefono_contacto.strip()
            })

    # 1. Parsear archivo Excel / CSV si fue provisto
    if archivo_excel and archivo_excel.filename:
        try:
            contents = await archivo_excel.read()
            if len(contents) > 0:
                if archivo_excel.filename.lower().endswith(".csv"):
                    df_in = pd.read_csv(io.BytesIO(contents), dtype=str)
                else:
                    df_in = pd.read_excel(io.BytesIO(contents), dtype=str)

                col_cct = None
                col_email = None
                col_nombre = None
                col_tel = None

                for c in df_in.columns:
                    clow = str(c).strip().lower()
                    if not col_cct and any(k in clow for k in ["cct", "clave", "centro"]):
                        col_cct = c
                    elif not col_email and any(k in clow for k in ["correo", "email", "mail"]):
                        col_email = c
                    elif not col_nombre and any(k in clow for k in ["director", "nombre", "solicitante"]):
                        col_nombre = c
                    elif not col_tel and any(k in clow for k in ["tel", "cel", "movil"]):
                        col_tel = c

                if col_cct:
                    for _, row in df_in.iterrows():
                        raw_c = str(row[col_cct]).strip() if pd.notna(row[col_cct]) else ""
                        raw_m = str(row[col_email]).strip() if col_email and pd.notna(row[col_email]) else ""
                        raw_n = str(row[col_nombre]).strip() if col_nombre and pd.notna(row[col_nombre]) else ""
                        raw_t = str(row[col_tel]).strip() if col_tel and pd.notna(row[col_tel]) else ""
                        if raw_c:
                            items_crudos.append({"cct": raw_c, "correo": raw_m, "nombre": raw_n, "telefono": raw_t})
        except Exception as e:
            logger.error(f"Error al leer archivo de carga masiva: {e}")

    # 2. Parsear directores_json si fue provisto desde la tabla interactiva
    if directores_json:
        try:
            lista_dirs = json.loads(directores_json)
            for d in lista_dirs:
                if isinstance(d, dict) and d.get("cct"):
                    items_crudos.append({
                        "cct": d.get("cct", ""),
                        "correo": d.get("correo", ""),
                        "nombre": d.get("nombre", ""),
                        "telefono": d.get("telefono", "")
                    })
        except Exception as e:
            logger.warning(f"Error parseando directores_json: {e}")

    # 3. Parsear área de texto
    if ccts_texto:
        for linea in ccts_texto.splitlines():
            linea_limpia = linea.strip()
            if not linea_limpia or linea_limpia.startswith("#"):
                continue

            partes = re.split(r"[,;\t]+", linea_limpia)
            if len(partes) >= 2:
                raw_c = partes[0].strip()
                raw_m = partes[1].strip() if len(partes) > 1 else ""
                raw_n = partes[2].strip() if len(partes) > 2 else ""
                raw_t = partes[3].strip() if len(partes) > 3 else ""
                items_crudos.append({"cct": raw_c, "correo": raw_m, "nombre": raw_n, "telefono": raw_t})
            else:
                items_crudos.append({"cct": linea_limpia, "correo": "", "nombre": "", "telefono": ""})

    # 3. Limpieza, Validación con Regex y Desduplicación
    ccts_procesadas = []
    seen_ccts = set()

    for item in items_crudos:
        clean_cct = re.sub(r"[^A-Za-z0-9]", "", item["cct"]).upper()
        if re.match(CCT_REGEX, clean_cct) and clean_cct not in seen_ccts:
            seen_ccts.add(clean_cct)
            ccts_procesadas.append({
                "cct": clean_cct,
                "correo": item["correo"].strip().lower(),
                "nombre": item["nombre"].strip(),
                "telefono": re.sub(r"\D", "", item["telefono"]) if item["telefono"] else ""
            })

    if not ccts_procesadas:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No se detectaron CCTs válidas en el formato oficial de 10 caracteres."
        )

    # Limitar a máximo 50 CCTs por paquete para protección del sistema
    ccts_procesadas = ccts_procesadas[:50]

    # 4. Generar folios consecutivos
    folios = generar_folios_en_lote(db, len(ccts_procesadas))

    zona_clean = zona_escolar.strip() if zona_escolar else ""
    nombre_sup_clean = nombre_supervisor.strip()
    tel_sup_clean = re.sub(r"\D", "", telefono_contacto.strip()) or "9510000000"
    correo_sup_clean = correo_supervision.strip().lower()

    for i, item in enumerate(ccts_procesadas):
        folio = folios[i]
        cct = item["cct"]

        # Determinación de destinatarios según modo
        if modo_envio == "supervision":
            dest_correo = correo_sup_clean
            dest_nombre = f"{nombre_sup_clean} ({zona_clean})" if zona_clean else nombre_sup_clean
            dest_cargo = cargo_solicitante.strip() or "Supervisor(a) Escolar"
            dest_tel = tel_sup_clean
        elif modo_envio == "directores":
            dest_correo = item["correo"] if ("@" in item["correo"]) else correo_sup_clean
            dest_nombre = item["nombre"] if item["nombre"] else f"Director(a) CCT {cct}"
            dest_cargo = "Director(a)"
            dest_tel = item["telefono"] if len(item["telefono"]) == 10 else tel_sup_clean
        elif modo_envio == "directores_cc_supervision":
            dest_correo = item["correo"] if ("@" in item["correo"]) else correo_sup_clean
            dest_nombre = item["nombre"] if item["nombre"] else f"Director(a) CCT {cct}"
            dest_cargo = "Director(a)"
            dest_tel = item["telefono"] if len(item["telefono"]) == 10 else tel_sup_clean
            nota_cc = f" [CC: {correo_sup_clean}]"
        else:
            dest_correo = correo_sup_clean
            dest_nombre = nombre_sup_clean
            dest_cargo = cargo_solicitante.strip()
            dest_tel = tel_sup_clean
            nota_cc = ""

        if modo_envio != "directores_cc_supervision":
            nota_cc = ""

        nueva_solicitud = Solicitud(
            folio=folio,
            cct=cct,
            nombre_solicitante=dest_nombre,
            cargo=dest_cargo,
            telefono=dest_tel,
            correo=dest_correo,
            region=region.strip(),
            nivel_educativo=nivel_educativo.strip(),
            estado="PENDIENTE",
            reintentos=0,
            codigo_otp=None,
            otp_expira=None,
            otp_intentos=0,
            fecha_solicitud=ahora_mexico(),
            mensaje_error=f"[SUPERVISION: {zona_clean or nombre_sup_clean}]{nota_cc} Despacho Masivo en Cola"
        )
        db.add(nueva_solicitud)

    db.commit()

    # Despertar al worker FIFO
    notificar_nueva_solicitud()

    zona_param = urllib.parse.quote(zona_clean or nombre_sup_clean)
    return RedirectResponse(
        url=f"/admin/solicitudes?masivo=1&total_masivo={len(ccts_procesadas)}&zona_masivo={zona_param}",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.get("/supervision/reporte")
async def reporte_supervision_excel(
    request: Request,
    zona: Optional[str] = None,
    db: Session = Depends(get_db)
):
    """Genera un archivo Excel membretado de acuse de entrega para la supervisión escolar."""
    verificar_sesion_admin(request)

    query = db.query(Solicitud)
    if zona:
        query = query.filter(
            (Solicitud.mensaje_error.ilike(f"%{zona.strip()}%")) |
            (Solicitud.nombre_solicitante.ilike(f"%{zona.strip()}%"))
        )
    else:
        query = query.filter(Solicitud.mensaje_error.ilike("%[SUPERVISION:%"))

    solicitudes = query.order_by(Solicitud.fecha_solicitud.desc()).all()

    data = []
    for s in solicitudes:
        data.append({
            "Folio SIDI": s.folio,
            "CCT": s.cct,
            "Supervisión / Solicitante": s.nombre_solicitante,
            "Cargo": s.cargo,
            "Correo Destino": s.correo,
            "Teléfono": s.telefono,
            "Región": s.region,
            "Nivel Educativo": s.nivel_educativo,
            "Estado Actual": s.estado,
            "Fecha Registro": s.fecha_solicitud.strftime("%d/%m/%Y %H:%M") if s.fecha_solicitud else "",
            "Fecha Despacho": s.fecha_procesado.strftime("%d/%m/%Y %H:%M") if s.fecha_procesado else "Pendiente / En cola",
            "Detalle / Archivo": s.archivo_generado or s.mensaje_error or ""
        })

    df = pd.DataFrame(data)
    timestamp = ahora_mexico().strftime("%Y%m%d_%H%M%S")
    excel_buffer = io.BytesIO()
    with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Acuse Supervision")

    response = Response(
        content=excel_buffer.getvalue(),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    nombre_limpio = re.sub(r"[^A-Za-z0-9_]", "_", zona) if zona else "Supervision"
    response.headers["Content-Disposition"] = f"attachment; filename=Acuse_Supervision_{nombre_limpio}_{timestamp}.xlsx"
    return response


@router.post("/solicitud/{solicitud_id}/aprobar-otp")
async def aprobar_solicitud_otp_manual(
    solicitud_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Permite al administrador omitir el código OTP y encolar inmediatamente la solicitud."""
    verificar_sesion_admin(request)

    solicitud = db.query(Solicitud).filter(Solicitud.id == solicitud_id).first()
    if solicitud and solicitud.estado == "PENDIENTE_VERIFICACION":
        solicitud.estado = "PENDIENTE"
        solicitud.reintentos = 0
        solicitud.mensaje_error = "Aprobada manualmente desde el Panel Administrativo (Bypass OTP)."
        db.commit()
        notificar_nueva_solicitud()

    return RedirectResponse(url="/admin/solicitudes?aprobado=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/solicitud/{solicitud_id}/reenviar")
async def reenviar_solicitud_manual(
    solicitud_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Permite al administrador forzar el reenvío o reprocesamiento de una solicitud."""
    verificar_sesion_admin(request)

    solicitud = db.query(Solicitud).filter(Solicitud.id == solicitud_id).first()
    if solicitud:
        solicitud.estado = "PENDIENTE"
        solicitud.reintentos = 0
        solicitud.mensaje_error = "Reenvío manual solicitado desde el panel administrativo."
        db.commit()
        # Despertar worker
        notificar_nueva_solicitud()

    return RedirectResponse(url="/admin/solicitudes", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/solicitud/{solicitud_id}/eliminar")
async def eliminar_solicitud(
    solicitud_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Permite al administrador eliminar una solicitud individual del directorio vivo."""
    verificar_sesion_admin(request)
    solicitud = db.query(Solicitud).filter(Solicitud.id == solicitud_id).first()
    if solicitud:
        if solicitud.archivo_generado and os.path.exists(solicitud.archivo_generado):
            try:
                os.remove(solicitud.archivo_generado)
            except Exception:
                pass
        db.delete(solicitud)
        db.commit()

    return RedirectResponse(url="/admin/solicitudes", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/solicitudes/limpiar-todo")
async def limpiar_todas_solicitudes(
    request: Request,
    db: Session = Depends(get_db)
):
    """Permite al administrador vaciar todas las solicitudes de prueba para dejar el sistema como nuevo."""
    verificar_sesion_admin(request)

    # Eliminar todos los registros de la tabla Solicitud
    db.query(Solicitud).delete()

    # Reiniciar la secuencia autoincremental de SQLite para que los IDs vuelvan a comenzar desde 1
    try:
        db.execute(text("DELETE FROM sqlite_sequence WHERE name='solicitudes'"))
    except Exception:
        pass

    db.commit()

    # Limpiar cualquier archivo temporal residual en TEMP_DIR
    if os.path.exists(TEMP_DIR):
        for f in os.listdir(TEMP_DIR):
            fpath = os.path.join(TEMP_DIR, f)
            try:
                if os.path.isfile(fpath):
                    os.remove(fpath)
            except Exception:
                pass

    return RedirectResponse(url="/admin/solicitudes?limpiado=1", status_code=status.HTTP_303_SEE_OTHER)


# ================= 3. GESTIÓN DE ARCHIVOS (FORMATOS E INSTRUCTIVOS) =================

@router.get("/formatos", response_class=HTMLResponse)
async def admin_formatos(request: Request, db: Session = Depends(get_db)):
    verificar_sesion_admin(request)
    config = ConfigManager.get_all(db)
    formatos = db.query(FormatoOficial).order_by(FormatoOficial.fecha_subida.desc()).all()

    return templates.TemplateResponse(
        request=request,
        name="admin_formatos.html",
        context={
            "config": config,
            "is_admin": True,
            "formatos": formatos
        }
    )


@router.post("/formatos/subir")
async def subir_formato(
    request: Request,
    nombre: str = Form(...),
    codigo: str = Form(...),
    tipo: str = Form(...),
    descripcion: Optional[str] = Form(None),
    archivo: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    verificar_sesion_admin(request)

    ext = os.path.splitext(archivo.filename)[1]
    nombre_seguro = f"{codigo.strip().upper()}_{ahora_mexico().strftime('%Y%m%d%H%M%S')}{ext}"
    ruta_guardado = os.path.join(FORMATOS_DIR, nombre_seguro)

    with open(ruta_guardado, "wb") as f:
        contenido = await archivo.read()
        f.write(contenido)

    nuevo_formato = FormatoOficial(
        nombre=nombre.strip(),
        codigo=codigo.strip().upper(),
        tipo=tipo.strip().upper(),
        ruta_archivo=ruta_guardado,
        descripcion=descripcion.strip() if descripcion else None,
        activo=True,
        fecha_subida=ahora_mexico()
    )

    db.add(nuevo_formato)
    db.commit()

    return RedirectResponse(url="/admin/formatos", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/formatos/{formato_id}/toggle")
async def toggle_formato(
    formato_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    verificar_sesion_admin(request)
    formato = db.query(FormatoOficial).filter(FormatoOficial.id == formato_id).first()
    if formato:
        formato.activo = not formato.activo
        db.commit()
    return RedirectResponse(url="/admin/formatos", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/formatos/{formato_id}/eliminar")
async def eliminar_formato(
    formato_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    verificar_sesion_admin(request)
    formato = db.query(FormatoOficial).filter(FormatoOficial.id == formato_id).first()
    if formato:
        ruta = formato.ruta_valida
        if os.path.exists(ruta):
            try:
                os.remove(ruta)
            except Exception:
                pass
        db.delete(formato)
        db.commit()
    return RedirectResponse(url="/admin/formatos", status_code=status.HTTP_303_SEE_OTHER)


# ================= 4. IDENTIDAD, MENSAJES Y CONFIGURACIÓN =================

@router.get("/configuracion", response_class=HTMLResponse)
async def admin_configuracion(
    request: Request,
    guardado: Optional[str] = None,
    usuario_msg: Optional[str] = None,
    usuario_err: Optional[str] = None,
    tab: Optional[str] = None,
    db: Session = Depends(get_db)
):
    verificar_sesion_admin(request)
    config = ConfigManager.get_all(db)
    usuarios = db.query(UsuarioAdmin).order_by(UsuarioAdmin.id.asc()).all()

    return templates.TemplateResponse(
        request=request,
        name="admin_config.html",
        context={
            "config": config,
            "is_admin": True,
            "usuarios": usuarios,
            "current_user": request.session.get("admin_user"),
            "mensaje_guardado": bool(guardado),
            "usuario_msg": usuario_msg,
            "usuario_err": usuario_err,
            "tab_activo": tab or "tab-branding"
        }
    )


@router.post("/usuarios/crear")
async def crear_usuario_admin(
    request: Request,
    username: str = Form(...),
    nombre: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db)
):
    """Crea un nuevo usuario administrativo en el sistema."""
    verificar_sesion_admin(request)
    user_clean = username.strip().lower()
    if not user_clean or not password:
        return RedirectResponse(
            url="/admin/configuracion?tab=tab-usuarios&usuario_err=El+nombre+de+usuario+y+la+contrase%C3%B1a+son+obligatorios.",
            status_code=status.HTTP_303_SEE_OTHER
        )

    existe = db.query(UsuarioAdmin).filter(UsuarioAdmin.username == user_clean).first()
    if existe:
        return RedirectResponse(
            url="/admin/configuracion?tab=tab-usuarios&usuario_err=El+nombre+de+usuario+ya+se+encuentra+registrado.",
            status_code=status.HTTP_303_SEE_OTHER
        )

    nuevo_usuario = UsuarioAdmin(
        username=user_clean,
        nombre=nombre.strip() or user_clean,
        password_hash=hash_password(password),
        activo=True
    )
    db.add(nuevo_usuario)
    db.commit()

    return RedirectResponse(
        url="/admin/configuracion?tab=tab-usuarios&usuario_msg=Usuario+registrado+exitosamente.",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/usuarios/{user_id}/editar")
async def editar_usuario_admin(
    user_id: int,
    request: Request,
    nombre: str = Form(...),
    password: Optional[str] = Form(None),
    activo: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """Edita un usuario administrativo (nombre, contraseña o estatus)."""
    verificar_sesion_admin(request)
    user = db.query(UsuarioAdmin).filter(UsuarioAdmin.id == user_id).first()
    if not user:
        return RedirectResponse(
            url="/admin/configuracion?tab=tab-usuarios&usuario_err=Usuario+no+encontrado.",
            status_code=status.HTTP_303_SEE_OTHER
        )

    current_user = request.session.get("admin_user")
    is_activo = bool(activo and activo.lower() in ["true", "1", "on", "yes"])

    # Regla de seguridad: No puede desactivar su propia cuenta activa
    if user.username == current_user and not is_activo:
        return RedirectResponse(
            url="/admin/configuracion?tab=tab-usuarios&usuario_err=No+puedes+desactivar+tu+propia+cuenta+conectada.",
            status_code=status.HTTP_303_SEE_OTHER
        )

    user.nombre = nombre.strip() or user.username
    user.activo = is_activo

    if password and password.strip():
        user.password_hash = hash_password(password.strip())

    db.commit()
    return RedirectResponse(
        url="/admin/configuracion?tab=tab-usuarios&usuario_msg=Usuario+actualizado+correctamente.",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/usuarios/{user_id}/eliminar")
async def eliminar_usuario_admin(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db)
):
    """Elimina un usuario administrativo del sistema con candados de seguridad."""
    verificar_sesion_admin(request)
    user = db.query(UsuarioAdmin).filter(UsuarioAdmin.id == user_id).first()
    if not user:
        return RedirectResponse(
            url="/admin/configuracion?tab=tab-usuarios&usuario_err=Usuario+no+encontrado.",
            status_code=status.HTTP_303_SEE_OTHER
        )

    current_user = request.session.get("admin_user")
    if user.username == current_user:
        return RedirectResponse(
            url="/admin/configuracion?tab=tab-usuarios&usuario_err=No+puedes+eliminar+la+cuenta+con+la+que+iniciaste+sesi%C3%B3n.",
            status_code=status.HTTP_303_SEE_OTHER
        )

    total_admins = db.query(UsuarioAdmin).count()
    if total_admins <= 1:
        return RedirectResponse(
            url="/admin/configuracion?tab=tab-usuarios&usuario_err=No+se+puede+eliminar+el+%C3%BAnico+usuario+del+sistema.",
            status_code=status.HTTP_303_SEE_OTHER
        )

    db.delete(user)
    db.commit()
    return RedirectResponse(
        url="/admin/configuracion?tab=tab-usuarios&usuario_msg=Usuario+eliminado+correctamente.",
        status_code=status.HTTP_303_SEE_OTHER
    )


@router.post("/configuracion")
async def guardar_configuracion(request: Request, db: Session = Depends(get_db)):
    verificar_sesion_admin(request)
    form_data = await request.form()
    valores_actualizar = {}

    for clave, valor in form_data.items():
        valores_actualizar[clave] = str(valor)

    ConfigManager.set_many(db, valores_actualizar)
    return RedirectResponse(url="/admin/configuracion?guardado=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/configuracion/logo")
async def subir_logo(
    request: Request,
    logo: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    verificar_sesion_admin(request)
    ext = os.path.splitext(logo.filename)[1]
    nombre_archivo = f"logo_{ahora_mexico().strftime('%Y%m%d_%H%M%S')}{ext}"
    ruta_guardado = os.path.join(LOGOS_DIR, nombre_archivo)

    with open(ruta_guardado, "wb") as f:
        contenido = await logo.read()
        f.write(contenido)

    ruta_web = f"/static/uploads/logos/{nombre_archivo}"
    ConfigManager.set(db, "logo_url", ruta_web, categoria="branding")

    return RedirectResponse(url="/admin/configuracion?guardado=1", status_code=status.HTTP_303_SEE_OTHER)


@router.post("/configuracion/probar-smtp")
async def probar_smtp_endpoint(request: Request):
    """Endpoint AJAX para probar la conexión con el servidor SMTP configurado."""
    verificar_sesion_admin(request)
    form_data = await request.form()
    cfg_prueba = {
        "smtp_host": str(form_data.get("smtp_host", "")),
        "smtp_port": str(form_data.get("smtp_port", "587")),
        "smtp_user": str(form_data.get("smtp_user", "")),
        "smtp_password": str(form_data.get("smtp_password", "")),
        "smtp_use_tls": str(form_data.get("smtp_use_tls", "true"))
    }

    exito, mensaje = await SidiMailer.probar_smtp(cfg_prueba)
    return {"exito": exito, "mensaje": mensaje}


@router.post("/configuracion/probar-portal")
async def probar_portal_endpoint(request: Request):
    """Endpoint AJAX para probar la conexión y credenciales contra el portal central IEEPO."""
    verificar_sesion_admin(request)
    form_data = await request.form()
    url = str(form_data.get("portal_url", "https://portal-inventarios.ejemplo.gob.mx/#login"))
    user = str(form_data.get("portal_user", ""))
    pwd = str(form_data.get("portal_password", ""))

    loop = asyncio.get_running_loop()
    exito, mensaje = await loop.run_in_executor(
        None, PlaywrightInventoryBot.probar_conexion_portal, url, user, pwd
    )
    return {"exito": exito, "mensaje": mensaje}
