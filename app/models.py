import os
import datetime
from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime
from app.database import Base
from app.timezone import ahora_mexico


class Solicitud(Base):
    __tablename__ = "solicitudes"

    id = Column(Integer, primary_key=True, index=True)
    folio = Column(String(50), unique=True, index=True, nullable=False)
    cct = Column(String(20), index=True, nullable=False)
    nombre_solicitante = Column(String(200), nullable=False)
    cargo = Column(String(100), nullable=False)
    telefono = Column(String(30), nullable=False)
    correo = Column(String(150), nullable=False)
    region = Column(String(100), nullable=False)
    nivel_educativo = Column(String(100), nullable=False)
    
    # Estados: 'PENDIENTE_VERIFICACION', 'PENDIENTE', 'PROCESANDO', 'ENVIADO', 'CCT_NO_ENCONTRADA', 'ERROR_PORTAL'
    estado = Column(String(30), default="PENDIENTE", index=True, nullable=False)
    reintentos = Column(Integer, default=0, nullable=False)
    mensaje_error = Column(Text, nullable=True)
    archivo_generado = Column(String(255), nullable=True)
    
    # Verificación de identidad por Código OTP (2FA ligero)
    codigo_otp = Column(String(10), nullable=True)
    otp_expira = Column(DateTime, nullable=True)
    otp_intentos = Column(Integer, default=0, nullable=False)

    fecha_solicitud = Column(DateTime, default=ahora_mexico, nullable=False)
    fecha_procesado = Column(DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "folio": self.folio,
            "cct": self.cct,
            "nombre_solicitante": self.nombre_solicitante,
            "cargo": self.cargo,
            "telefono": self.telefono,
            "correo": self.correo,
            "region": self.region,
            "nivel_educativo": self.nivel_educativo,
            "estado": self.estado,
            "reintentos": self.reintentos,
            "mensaje_error": self.mensaje_error,
            "archivo_generado": self.archivo_generado,
            "fecha_solicitud": self.fecha_solicitud.strftime("%Y-%m-%d %H:%M:%S") if self.fecha_solicitud else None,
            "fecha_procesado": self.fecha_procesado.strftime("%Y-%m-%d %H:%M:%S") if self.fecha_procesado else None,
        }


class Configuracion(Base):
    __tablename__ = "configuraciones"

    id = Column(Integer, primary_key=True, index=True)
    clave = Column(String(100), unique=True, index=True, nullable=False)
    valor = Column(Text, nullable=True)
    descripcion = Column(String(255), nullable=True)
    categoria = Column(String(50), default="general", index=True)


class FormatoOficial(Base):
    __tablename__ = "formatos_oficiales"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(150), nullable=False)
    codigo = Column(String(50), nullable=False)  # ALTA, BAJA, TRASLADO, GUIA, etc.
    tipo = Column(String(10), nullable=False)     # EXCEL, PDF
    ruta_archivo = Column(String(255), nullable=False)
    descripcion = Column(String(255), nullable=True)
    activo = Column(Boolean, default=True, nullable=False)
    fecha_subida = Column(DateTime, default=ahora_mexico, nullable=False)

    @property
    def ruta_valida(self) -> str:
        """Devuelve la ruta absoluta válida en cualquier sistema operativo (Windows o Linux)."""
        if self.ruta_archivo and os.path.exists(self.ruta_archivo):
            return self.ruta_archivo
        from app.database import FORMATOS_DIR
        nombre_base = os.path.basename(self.ruta_archivo.replace("\\", "/"))
        posible = os.path.join(FORMATOS_DIR, nombre_base)
        if os.path.exists(posible):
            return posible
        return self.ruta_archivo


class UsuarioAdmin(Base):
    __tablename__ = "usuarios_admin"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    nombre = Column(String(150), nullable=True)
    activo = Column(Boolean, default=True, nullable=False)
    creado_en = Column(DateTime, default=ahora_mexico, nullable=False)
