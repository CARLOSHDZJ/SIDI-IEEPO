import os
import sys
import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.database import SessionLocal
from app.models import Solicitud

def ajustar():
    db = SessionLocal()
    solicitudes = db.query(Solicitud).all()
    count = 0
    for s in solicitudes:
        if s.fecha_solicitud:
            s.fecha_solicitud = s.fecha_solicitud - datetime.timedelta(hours=6)
        if s.fecha_procesado:
            s.fecha_procesado = s.fecha_procesado - datetime.timedelta(hours=6)
        count += 1
    db.commit()
    db.close()
    print(f'Se ajustaron {count} solicitudes con exito a la hora local de Mexico.')

if __name__ == '__main__':
    ajustar()
