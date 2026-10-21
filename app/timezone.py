import datetime
from zoneinfo import ZoneInfo

# Zona horaria estándar para el Estado de Oaxaca y Centro de México (UTC-6)
ZONA_HORARIA_MEXICO = ZoneInfo("America/Mexico_City")


def ahora_mexico() -> datetime.datetime:
    """
    Retorna la fecha y hora actual en la zona horaria de México (America/Mexico_City).
    Se retorna como datetime naive (sin tzinfo) para compatibilidad nativa con SQLite,
    SQLAlchemy, plantillas Jinja2 y consultas de filtrado.
    """
    return datetime.datetime.now(ZONA_HORARIA_MEXICO).replace(tzinfo=None)
