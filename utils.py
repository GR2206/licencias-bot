import uuid
from datetime import datetime, timedelta

def generar_serial():
    return str(uuid.uuid4()).replace("-", "").upper()[:16]

def fecha_expiracion(dias):
    return datetime.utcnow() + timedelta(days=dias)