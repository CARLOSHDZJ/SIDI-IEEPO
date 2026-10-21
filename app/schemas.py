import re
from typing import Optional
from pydantic import BaseModel, Field, field_validator


# Expresión regular oficial para Claves de Centro de Trabajo (CCT) en México
# Formato: 2 dígitos entidad (ej: 20 Oaxaca) + 3 letras tipo + 4 dígitos consecutivo + 1 letra verificadora
CCT_REGEX = r"^[0-9]{2}[A-Za-z]{3}[0-9]{4}[A-Za-z]$"


class SolicitudCreate(BaseModel):
    cct: str = Field(..., description="Clave de Centro de Trabajo oficial (10 caracteres)")
    nombre_solicitante: str = Field(..., min_length=3, max_length=200)
    cargo: str = Field(..., min_length=2, max_length=100)
    telefono: str = Field(..., min_length=10, max_length=20)
    correo: str = Field(..., min_length=5, max_length=150)
    region: str = Field(..., min_length=2, max_length=100)
    nivel_educativo: str = Field(..., min_length=2, max_length=100)

    @field_validator("cct")
    @classmethod
    def validar_cct(cls, v: str) -> str:
        clean = v.strip().upper()
        if not re.match(CCT_REGEX, clean):
            raise ValueError("El formato de CCT es inválido. Debe tener 10 caracteres (ej: 20DPR1234A).")
        return clean

    @field_validator("correo")
    @classmethod
    def validar_correo(cls, v: str) -> str:
        clean = v.strip().lower()
        email_regex = r"^[\w\.-]+@[\w\.-]+\.[a-zA-Z]{2,}$"
        if not re.match(email_regex, clean):
            raise ValueError("El correo electrónico proporcionado no tiene un formato válido.")
        
        dominios_desechables = {
            "yopmail.com", "mailinator.com", "tempmail.com", "guerrillamail.com",
            "10minutemail.com", "throwawaymail.com", "sharklasers.com", "dispostable.com"
        }
        dominio = clean.split("@")[-1]
        if dominio in dominios_desechables:
            raise ValueError("No se permiten correos electrónicos temporales o desechables.")
        return clean

    @field_validator("telefono")
    @classmethod
    def validar_telefono(cls, v: str) -> str:
        clean = re.sub(r"\D", "", v)
        if len(clean) != 10:
            raise ValueError("El número telefónico debe contener exactamente 10 dígitos (ej: 9510000001).")
        numeros_invalidos = {"1234567890", "0123456789", "0000000000", "1111111111", "9999999999"}
        if clean in numeros_invalidos or len(set(clean)) == 1:
            raise ValueError("Ingrese un número telefónico válido y verídico para contacto oficial.")
        return clean


class SolicitudResponse(BaseModel):
    id: int
    folio: str
    cct: str
    nombre_solicitante: str
    cargo: str
    telefono: str
    correo: str
    region: str
    nivel_educativo: str
    estado: str
    fecha_solicitud: str
    fecha_procesado: Optional[str] = None
    mensaje_error: Optional[str] = None

    model_config = {"from_attributes": True}


class FormatoCreate(BaseModel):
    nombre: str
    codigo: str
    tipo: str
    descripcion: Optional[str] = None
