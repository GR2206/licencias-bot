from flask import Flask, request, jsonify
from database import db
from models import Licencia
from utils import generar_serial, fecha_expiracion
from datetime import datetime
import os
from flask_cors import CORS
import requests


app = Flask(__name__)
CORS(app)
db_url = os.getenv("DATABASE_URL")

if db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql+psycopg2://")

app.config["SQLALCHEMY_DATABASE_URI"] = db_url
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

db.init_app(app)

with app.app_context():
    db.create_all()

# ==============================
# VALIDAR LICENCIA
# ==============================

@app.route("/validar", methods=["POST"])
def validar():
    data = request.json
    serial = data.get("serial")
    device_id = data.get("device_id")

    lic = Licencia.query.filter_by(serial=serial).first()

    if not lic:
        return jsonify({"status": "invalid"})

    if lic.estado != "activa":
        return jsonify({"status": "blocked"})

    if lic.expira < datetime.utcnow():
        return jsonify({"status": "expired"})

    # 🔥 ACTIVACIÓN AUTOMÁTICA
    if lic.device_id is None:
        lic.device_id = device_id
        db.session.commit()
        return jsonify({"status": "activated"})

    # ✅ MISMO DISPOSITIVO
    if lic.device_id == device_id:
        return jsonify({"status": "ok"})

    # 🔄 PERMITIR 1 CAMBIO
    if lic.cambios_device < 1:
        lic.device_id = device_id
        lic.cambios_device += 1
        db.session.commit()
        return jsonify({"status": "relinked"})

    return jsonify({"status": "device_mismatch"})

# ==============================
# CREAR LICENCIA
# ==============================

@app.route("/crear", methods=["POST"])
def crear():
    data = request.json

    nombre = data.get("nombre", "SinNombre")
    apellido = data.get("apellido", "SinApellido")
    plan = data.get("plan", "BASIC")
    user_id = data.get("user_id")

    planes = {
        "BASIC": {"dias": 30, "precio": 30},
        "PRO": {"dias": 90, "precio": 75},
        "VIP": {"dias": 365, "precio": 300},
        "LIFETIME": {"dias": 9999, "precio": 1300}
    }

    plan_data = planes.get(plan, planes["BASIC"])

    dias = plan_data["dias"]
    ingreso = plan_data["precio"]

    serial = generar_serial()

    nueva = Licencia(
        serial=serial,
        expira=fecha_expiracion(dias),
        plan=plan,
        nombre=nombre,
        apellido=apellido,
        device_id=None,
        ingreso=ingreso,
        estado="activa"
    )

    db.session.add(nueva)
    db.session.commit()

    return jsonify({
        "serial": serial,
        "plan": plan
    })

# ==============================
# LICENCIAS
# ==============================

@app.route("/licencias", methods=["GET"])
def obtener_licencias():
    try:
        licencias = Licencia.query.all()

        resultado = []

        for l in licencias:
            resultado.append({
                "nombre": getattr(l, "nombre", ""),
                "apellido": getattr(l, "apellido", ""),
                "serial": l.serial,
                "plan": l.plan,
                "estado": l.estado,
                "expira": str(l.expira),
                "device_id": l.device_id
            })

        return jsonify(resultado)

    except Exception as e:
        return jsonify({
            "error": str(e)
        })

# ==============================
# ACTIVAR
# ==============================

@app.route("/activar", methods=["POST"])
def activar():
    data = request.json
    serial = data.get("serial")
    device_id = data.get("device_id")

    lic = Licencia.query.filter_by(serial=serial).first()

    if not lic:
        return jsonify({"status": "invalid"})

    if lic.estado != "activa":
        return jsonify({"status": "blocked"})

    if lic.expira < datetime.utcnow():
        return jsonify({"status": "expired"})

    # 🔥 SI NO TIENE DEVICE → LO ASIGNAMOS
    if lic.device_id in [None, "PENDIENTE"]:
        lic.device_id = device_id
        db.session.commit()
        return jsonify({"status": "activated"})

    # 🔒 SI YA TIENE Y ES DISTINTO → BLOQUEADO
    if lic.device_id != device_id:
        return jsonify({"status": "device_mismatch"})

    return jsonify({"status": "ok"})

# ==============================
# BLOQUEAR
# ==============================

@app.route("/bloquear", methods=["POST"])
def bloquear():
    data = request.json
    serial = data.get("serial")

    lic = Licencia.query.filter_by(serial=serial).first()

    if lic:
        lic.estado = "bloqueada"
        db.session.commit()
        return jsonify({"status": "ok"})

    return jsonify({"status": "not_found"})

# ==============================
# RENOVAR
# ==============================

@app.route("/renovar", methods=["POST"])
def renovar():
    data = request.json
    serial = data.get("serial")

    lic = Licencia.query.filter_by(serial=serial).first()

    if not lic:
        return jsonify({"status": "not_found"})

    lic.expira = fecha_expiracion(30)
    db.session.commit()

    return jsonify({"status": "ok"})

# ==============================
# ESTADISTICAS
# ==============================

@app.route("/estadisticas")
def estadisticas():
    licencias = Licencia.query.all()

    total = sum(l.ingreso or 0 for l in licencias)
    cantidad = len(licencias)

    return jsonify({
        "total": total,
        "ventas": cantidad
    })

# ==============================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))