from flask import Flask, request, jsonify
from database import db
from models import Licencia
from utils import generar_serial, fecha_expiracion
from datetime import datetime
import os
from flask_cors import CORS


app = Flask(__name__)
CORS(app)
app.config["SQLALCHEMY_DATABASE_URI"] = "postgresql://admin:NOQNvdbWILYydg5GCGeeQfPm7Tt1gHVJ@dpg-d74prrshg0os73a7ktgg-a.virginia-postgres.render.com/licencias_o0wh"
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

    # Anti-sharing
    if lic.device_id in [None, "PENDIENTE"]:
        lic.device_id = device_id
        db.session.commit()
    elif lic.device_id != device_id:
        return jsonify({"status": "device_mismatch"})

    return jsonify({"status": "ok"})

# ==============================
# CREAR LICENCIA
# ==============================

@app.route("/crear", methods=["POST"])
def crear():
    data = request.json
    nombre = data.get("nombre", "SinNombre")
    apellido = data.get("apellido", "SinApellido")
    plan = data.get("plan", "mensual")
    user_id = data.get("user_id", "manual")
    precios = {
        "BASIC": 30,
        "PRO": 75,
        "VIP": 300,
        "LIFETIME": 1300
    }

    ingreso = precios.get(plan, 0)

    if plan == "mensual":
        dias = 30
    elif plan == "trimestral":
        dias = 90   
    elif plan == "anual":
        dias = 365
    elif plan == "lifetime":
        dias = 9999
    else:
        dias = 30

    user_id = data.get("user_id")

    serial = generar_serial()

    nueva = Licencia(
        serial=serial,
        expira=fecha_expiracion(dias),
        plan=plan,
        nombre=nombre,
        apellido=apellido,
        device_id = "AUTO-" + str(user_id) if user_id else "PENDIENTE",
        ingreso=precios.get(plan, 0),
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
def listar():
    licencias = Licencia.query.all()

    data = []

    for l in licencias:
        data.append({
            "nombre": l.nombre,
            "apellido": l.apellido,
            "serial": l.serial,
            "plan": l.plan,
            "estado": l.estado,
            "expira": l.expira.strftime("%Y-%m-%d")
        })

    return jsonify(data)

# ==============================
# ACTIVAR
# ==============================

@app.route("/activar", methods=["POST"])
def activar():
    data = request.json
    serial = data.get("serial")

    lic = Licencia.query.filter_by(serial=serial).first()

    if not lic:
        return jsonify({"status": "not_found"})

    lic.device_id = "PENDIENTE"
    db.session.commit()

    return jsonify({"status": "activated"})

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
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 10000)))