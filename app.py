from flask import Flask, request, jsonify
from database import db
from models import Licencia
from utils import generar_serial, fecha_expiracion
from datetime import datetime


app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///licencias.db"
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
    if lic.device_id == "PENDIENTE":
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
    plan = data.get("plan", "mensual")

    if plan == "mensual":
        dias = 30
    elif plan == "anual":
        dias = 365
    elif plan == "lifetime":
        dias = 9999
    else:
        dias = 30

    serial = generar_serial()

    nueva = Licencia(
        serial=serial,
        expira=fecha_expiracion(dias),
        plan=plan
    )

    db.session.add(nueva)
    db.session.commit()

    return jsonify({
        "serial": serial,
        "plan": plan
    })

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)