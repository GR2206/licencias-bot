from flask import Flask, request, jsonify
from database import db
from models import Licencia
from utils import generar_serial, fecha_expiracion
from datetime import datetime
import os
from flask_cors import CORS
import requests
import boto3
from botocore.config import Config

s3 = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("AWS_ACCESS_KEY"),
    aws_secret_access_key=os.getenv("AWS_SECRET_KEY"),
    region_name="us-east-1",
    config=Config(signature_version="s3v4")
)

BUCKET_NAME = "sniperpro-download"
FILE_NAME = "SniperV3.0.rar"
FILE_NAME = "forexbot.rar"


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

    # ACTIVACIÓN AUTOMÁTICA
    if lic.device_id is None:
        lic.device_id = device_id
        db.session.commit()

    # MISMO DISPOSITIVO
    elif lic.device_id != device_id:

        if lic.cambios_device < 1:
            lic.device_id = device_id
            lic.cambios_device += 1
            db.session.commit()
        else:
            return jsonify({"status": "device_mismatch"})

    # ✅ SIEMPRE DEVOLVER PLAN
    return jsonify({
        "status": "ok",
        "plan": lic.plan
    })


# ==============================
# CREAR LICENCIA
# ==============================

@app.route("/crear", methods=["POST"])
def crear():
    try:
        data = request.json
        print("DATA RECIBIDA:", data)

        nombre = data.get("nombre", "SinNombre")
        apellido = data.get("apellido", "SinApellido")
        plan = data.get("plan", "mensual").lower()
        mercado = data.get("mercado", "binance")

        planes = {
            "trial": {"dias": 7, "precio": 0},
            "basic": {"dias": 30, "precio": 30},
            "pro": {"dias": 90, "precio": 75},
            "vip": {"dias": 365, "precio": 300},
            "lifetime": {"dias": 3650, "precio": 1300}
        }

        plan_data = planes.get(plan)

        if not plan_data:
            return jsonify({"error": f"Plan inválido: {plan}"}), 400

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

    except Exception as e:
        print("❌ ERROR CREAR:", str(e))
        return jsonify({"error": str(e)}), 500


# ==============================
# TRIAL - FREE 7 DIAS
# ==============================

@app.route("/trial", methods=["POST"])
def trial():

    data = request.json
    device_id = data.get("device_id")

    # 🔍 verificar si ya usó trial en este device
    existente = Licencia.query.filter_by(device_id=device_id, plan="trial").first()

    if existente:
        return jsonify({"status": "ya_usado"})

    serial = generar_serial()

    nueva = Licencia(
        serial=serial,
        expira=fecha_expiracion(7),
        plan="trial",  # 🔥 minúscula (importante)
        nombre="Trial",
        apellido="Trial",
        device_id=device_id,
        estado="activa",
        ingreso=0
    )

    db.session.add(nueva)
    db.session.commit()

    return jsonify({
        "status": "ok",
        "serial": serial
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
# ELIMINAR
# ==============================

@app.route("/eliminar", methods=["POST"])
def eliminar():
    serial = request.json.get("serial")

    lic = Licencia.query.filter_by(serial=serial).first()

    if lic:
        db.session.delete(lic)
        db.session.commit()
        return jsonify({"status": "ok"})

    return jsonify({"status": "not_found"})

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
# LINK DESCARGA
# ==============================

@app.route("/generar_descarga", methods=["POST"])
def generar_descarga():
    try:
        serial = request.json.get("serial")

        lic = Licencia.query.filter_by(serial=serial).first()

        if not lic:
            return jsonify({"error": "Licencia inválida"}), 403

        if lic.estado != "activa":
            return jsonify({"error": "Pago no aprobado"}), 403

        url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": BUCKET_NAME,
                "Key": FILE_NAME
            },
            ExpiresIn=300
        )

        return jsonify({"url": url})

    except Exception as e:
        print("ERROR S3:", str(e))
        return jsonify({"error": str(e)}), 500

# ==============================

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))