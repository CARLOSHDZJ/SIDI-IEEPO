from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from app.models import Configuracion

# Valores por defecto configurables a prueba de cambios de administración
DEFAULT_CONFIGS = {
    # Branding & Apariencia
    "nombre_institucion": ("Departamento de Inventarios", "branding", "Nombre oficial del departamento / institución"),
    "subtitulo_sistema": ("SIDI | Departamento de Inventarios", "branding", "Nombre del sistema"),
    "color_primario": ("#8B1D42", "branding", "Color primario institucional (CSS Hex)"),
    "color_secundario": ("#B28834", "branding", "Color secundario o acento (CSS Hex)"),
    "color_fondo": ("#F8FAFC", "branding", "Color de fondo del portal"),
    "logo_url": ("/static/uploads/logos/logo_default.svg", "branding", "Ruta al logo oficial"),

    # Datos de Contacto y Oficina
    "oficina_departamento": ("Departamento de Control de Bienes Muebles e Inventarios", "contacto", "Nombre del área responsable"),
    "oficina_telefono": ("(000) 000-0000 ext. 000 (Entorno Demo)", "contacto", "Teléfonos oficiales de atención"),
    "oficina_correo": ("inventarios.escolares@ejemplo.gob.mx", "contacto", "Correo institucional de atención"),
    "oficina_direccion": ("Av. Institucional S/N, Oaxaca, México (Entorno de Demostración)", "contacto", "Dirección física de la oficina"),
    "oficina_horario": ("Lunes a Viernes de 8:00 a 16:00 hrs.", "contacto", "Horario de atención al público"),
    "url_sistema": ("http://localhost:8000", "contacto", "URL base pública del sistema SIDI para enlaces de ayuda y consulta"),

    # Configuración SMTP (Mailing)
    "smtp_host": ("smtp.gmail.com", "smtp", "Servidor SMTP saliente"),
    "smtp_port": ("587", "smtp", "Puerto SMTP (587 para TLS, 465 para SSL)"),
    "smtp_user": ("", "smtp", "Usuario o correo remitente SMTP"),
    "smtp_password": ("", "smtp", "Contraseña o App Password de SMTP"),
    "smtp_use_tls": ("true", "smtp", "Habilitar STARTTLS (true/false)"),
    "smtp_from_email": ("no-reply@ejemplo.gob.mx", "smtp", "Correo remitente visible"),
    "smtp_from_name": ("SIDI - Departamento de Inventarios", "smtp", "Nombre remitente visible"),

    # Portal Central ExtJS (Playwright)
    "portal_url": ("https://portal-inventarios.ejemplo.gob.mx/#login", "portal", "URL del sistema central ExtJS"),
    "portal_user": ("", "portal", "Usuario de acceso al portal central"),
    "portal_password": ("", "portal", "Contraseña de acceso al portal central"),
    "portal_simulacion": ("true", "portal", "Modo simulación / diagnóstico local (true/false)"),

    # Reglas de Negocio y Seguridad
    "max_cct_por_solicitante": ("2", "seguridad", "Límite máximo de escuelas permitidas por directivo/solicitante"),
    "requerir_otp": ("false", "seguridad", "Requerir verificación de identidad mediante código OTP de 6 dígitos por correo (true/false)"),

    # Plantillas de Correo: ÉXITO
    "template_exito_asunto": (
        "Constancia de Inventario y Formatos Oficiales - CCT {cct} [Folio: {folio}]",
        "plantilla",
        "Asunto del correo de despacho exitoso"
    ),
    "template_exito_cuerpo": (
        """Estimado(a) <strong>{nombre}</strong> ({cargo}),<br><br>
Le informamos que su solicitud con número de folio <strong>{folio}</strong> para la Clave de Centro de Trabajo <strong>{cct}</strong> ha sido procesada exitosamente por el Sistema de Despacho de Inventarios (SIDI).<br><br>
Se adjuntan a este correo los siguientes documentos oficiales actualizados:<br>
<ul>
  <li><strong>Constancia de Área Resguardante (Cédula Censal de Inventario)</strong> en formato PDF.</li>
  <li><strong>Formatos Oficiales Vigentes (Alta, Baja y Traslado de Bienes Muebles)</strong> en hojas de cálculo Excel.</li>
  <li><strong>Guía y Manual de Procedimientos</strong> para el cotejo físico.</li>
</ul>
<br>
Por favor revise detalladamente la relación de bienes. Si requiere aclaraciones o trámites presenciales, estamos a su disposición en:<br>
<strong>{oficina_departamento}</strong><br>
📍 Dirección: {oficina_direccion}<br>
📞 Teléfonos: {oficina_telefono}<br>
✉️ Correo: {oficina_correo}<br>
⏰ Horario: {oficina_horario}<br><br>
<em>Este es un correo automático generado por el SIDI - Departamento de Inventarios. Por favor no responda directamente a este mensaje.</em>""",
        "plantilla",
        "Cuerpo HTML del correo de éxito"
    ),

    # Plantillas de Correo: CCT NO ENCONTRADA
    "template_no_encontrada_asunto": (
        "AVISO IMPORTANTE: CCT {cct} no localizada en Sistema de Inventarios [Folio: {folio}]",
        "plantilla",
        "Asunto cuando la CCT no existe"
    ),
    "template_no_encontrada_cuerpo": (
        """Estimado(a) <strong>{nombre}</strong> ({cargo}),<br><br>
Hacemos de su conocimiento que al procesar su solicitud con folio <strong>{folio}</strong> para la CCT <strong>{cct}</strong>, el sistema central de inventarios arrojó que dicho Centro de Trabajo <strong>no se encuentra dado de alta o no cuenta con inventario registrado</strong> en el padrón activo.<br><br>
Por disposiciones oficiales, <strong>no ha sido posible despachar su constancia de forma automática</strong>.<br><br>
<strong>¿Qué debe hacer para regularizar su situación?</strong><br>
Le solicitamos comunicarse o presentarse a la brevedad con la documentación de nombramiento y asignación de plantel a nuestras oficinas:<br>
<ul>
  <li><strong>Área:</strong> {oficina_departamento}</li>
  <li><strong>Dirección:</strong> {oficina_direccion}</li>
  <li><strong>Teléfono / WhatsApp de atención:</strong> {oficina_telefono}</li>
  <li><strong>Correo:</strong> {oficina_correo}</li>
  <li><strong>Horario:</strong> {oficina_horario}</li>
</ul>
<br>
Lamentamos los inconvenientes y quedamos a su servicio para apoyarle en su proceso de regularización de inventario.<br><br>
<em>SIDI - Instituto Estatal de Educación Pública de Oaxaca</em>""",
        "plantilla",
        "Cuerpo HTML del correo de CCT no encontrada"
    )
}


class ConfigManager:
    @staticmethod
    def init_defaults(db: Session):
        """Inicializa en la base de datos las configuraciones faltantes."""
        for clave, (valor, categoria, descripcion) in DEFAULT_CONFIGS.items():
            existente = db.query(Configuracion).filter(Configuracion.clave == clave).first()
            if not existente:
                nueva_cfg = Configuracion(
                    clave=clave,
                    valor=valor,
                    categoria=categoria,
                    descripcion=descripcion
                )
                db.add(nueva_cfg)
        db.commit()

    @staticmethod
    def get_all(db: Session) -> Dict[str, str]:
        """Retorna un diccionario clave-valor con toda la configuración."""
        registros = db.query(Configuracion).all()
        config_dict = {reg.clave: reg.valor for reg in registros}
        # Si faltan claves por defecto, completarlas
        for k, v in DEFAULT_CONFIGS.items():
            if k not in config_dict:
                config_dict[k] = v[0]
        return config_dict

    @staticmethod
    def get_by_category(db: Session, categoria: str) -> Dict[str, str]:
        """Retorna las configuraciones asociadas a una categoría específica."""
        registros = db.query(Configuracion).filter(Configuracion.categoria == categoria).all()
        return {reg.clave: reg.valor for reg in registros}

    @staticmethod
    def get(db: Session, clave: str, default: Optional[str] = None) -> str:
        """Obtiene el valor de una configuración individual."""
        reg = db.query(Configuracion).filter(Configuracion.clave == clave).first()
        if reg and reg.valor is not None:
            return reg.valor
        if default is not None:
            return default
        if clave in DEFAULT_CONFIGS:
            return DEFAULT_CONFIGS[clave][0]
        return ""

    @staticmethod
    def set(db: Session, clave: str, valor: str, categoria: str = "general"):
        """Establece o actualiza un valor de configuración."""
        reg = db.query(Configuracion).filter(Configuracion.clave == clave).first()
        if reg:
            reg.valor = valor
        else:
            reg = Configuracion(clave=clave, valor=valor, categoria=categoria)
            db.add(reg)
        db.commit()

    @staticmethod
    def set_many(db: Session, values: Dict[str, str]):
        """Actualiza múltiples configuraciones a la vez."""
        for k, v in values.items():
            reg = db.query(Configuracion).filter(Configuracion.clave == k).first()
            if reg:
                reg.valor = v
            else:
                cat = DEFAULT_CONFIGS.get(k, ("", "general", ""))[1]
                db.add(Configuracion(clave=k, valor=v, categoria=cat))
        db.commit()

    @staticmethod
    def render_text(template_str: str, context: Dict[str, Any]) -> str:
        """
        Reemplaza variables dinámicas {variable} de forma tolerante a fallos
        para que nunca rompa si falta algún parámetro.
        """
        class SafeDict(dict):
            def __missing__(self, key):
                return f"{{{key}}}"

        try:
            return template_str.format_map(SafeDict(**context))
        except Exception:
            # Fallback simple de reemplazo
            res = template_str
            for k, val in context.items():
                res = res.replace(f"{{{k}}}", str(val))
            return res
