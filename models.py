from database import db
from datetime import datetime

class Licencia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    serial = db.Column(db.String(100), unique=True, nullable=False)
    estado = db.Column(db.String(20), default="activa")
    expira = db.Column(db.DateTime)
    device_id = db.Column(db.String(200))
    plan = db.Column(db.String(50))
    creada = db.Column(db.DateTime, default=datetime.utcnow)
    nombre = db.Column(db.String(50))
    apellido = db.Column(db.String(50))
    ingreso = db.Column(db.Float)
    cambios_device = db.Column(db.Integer, default=0)
    trial_usado = db.Column(db.Boolean, default=False)
    user_id = db.Column(db.String)