import os
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

# Configuración de ruta base y directorio de datos
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
STORAGE_DIR = os.path.join(BASE_DIR, "storage")
TEMP_DIR = os.path.join(STORAGE_DIR, "temp")
UPLOADS_DIR = os.path.join(BASE_DIR, "app", "static", "uploads")
FORMATOS_DIR = os.path.join(UPLOADS_DIR, "formatos")
LOGOS_DIR = os.path.join(UPLOADS_DIR, "logos")

# Garantizar que existan los directorios críticos
for folder in [DATA_DIR, STORAGE_DIR, TEMP_DIR, UPLOADS_DIR, FORMATOS_DIR, LOGOS_DIR]:
    os.makedirs(folder, exist_ok=True)

# URL de la base de datos (por defecto SQLite local, compatible con PostgreSQL mediante variable de entorno)
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{os.path.join(DATA_DIR, 'sidi.db')}")

# Argumentos del motor de base de datos
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def get_db():
    """Generador de dependencias de sesión para FastAPI."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Crea todas las tablas si no existen."""
    import app.models  # noqa: F401
    Base.metadata.create_all(bind=engine)
