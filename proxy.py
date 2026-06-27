"""
EduCheck Pro — Proxy de IA
===========================
Servidor intermedio entre las apps de los centros y la API de Claude.
- La API key de Anthropic vive SOLO aquí (variable de entorno), nunca en la app del centro.
- Cada centro se identifica con su propia licencia (EDUCHECK_LICENCIAS).
- Controlas quién consume y puedes cortar acceso a quien no pague.

Desplegar en Railway o Render (~5€/mes). Ver instrucciones al final.
"""

import os
import json
import sqlite3
from datetime import datetime
from flask import Flask, request, jsonify
import anthropic

app = Flask(__name__)

# ── CONFIGURACIÓN (variables de entorno en Railway/Render) ──
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.environ.get("EDUCHECK_CLAUDE_MODEL", "claude-haiku-4-5-20251001")

# Licencias válidas: "centro1:clave1,centro2:clave2"
# Ej: "zulaibar:ZB-2026-A1B2,salesianos:SAL-2026-X9Y8"
LICENCIAS_RAW = os.environ.get("EDUCHECK_LICENCIAS", "")
LICENCIAS = {}
for par in LICENCIAS_RAW.split(","):
    if ":" in par:
        centro, clave = par.split(":", 1)
        LICENCIAS[clave.strip()] = centro.strip()

# Límite mensual de análisis por centro (protección anti-abuso)
LIMITE_MENSUAL = int(os.environ.get("EDUCHECK_LIMITE_MENSUAL", "5000"))

DB_PATH = "consumo.db"


def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS consumo (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        centro TEXT,
        fecha TEXT,
        mes TEXT,
        tokens_in INTEGER,
        tokens_out INTEGER
    )""")
    conn.commit()
    conn.close()


def registrar_consumo(centro, tin, tout):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    ahora = datetime.now()
    c.execute("INSERT INTO consumo (centro, fecha, mes, tokens_in, tokens_out) VALUES (?,?,?,?,?)",
              (centro, ahora.isoformat(), ahora.strftime("%Y-%m"), tin, tout))
    conn.commit()
    conn.close()


def analisis_este_mes(centro):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    mes = datetime.now().strftime("%Y-%m")
    c.execute("SELECT COUNT(*) FROM consumo WHERE centro=? AND mes=?", (centro, mes))
    n = c.fetchone()[0]
    conn.close()
    return n


@app.route("/", methods=["GET"])
def home():
    return jsonify({"servicio": "EduCheck IA Proxy", "estado": "activo", "modelo": CLAUDE_MODEL})


@app.route("/analizar", methods=["POST"])
def analizar():
    # 1. Validar licencia del centro
    licencia = request.headers.get("X-Licencia", "")
    if licencia not in LICENCIAS:
        return jsonify({"error": "Licencia no válida o caducada"}), 403
    centro = LICENCIAS[licencia]

    # 2. Comprobar límite mensual
    if analisis_este_mes(centro) >= LIMITE_MENSUAL:
        return jsonify({"error": "Límite mensual alcanzado"}), 429

    # 3. Recibir el prompt de la app del centro
    data = request.json or {}
    prompt = data.get("prompt", "")
    if not prompt:
        return jsonify({"error": "Falta el prompt"}), 400

    # 4. Llamar a Claude con TU key (protegida aquí)
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1200,
            messages=[{"role": "user", "content": prompt}],
        )
        texto = resp.content[0].text

        # 5. Registrar consumo
        tin = resp.usage.input_tokens
        tout = resp.usage.output_tokens
        registrar_consumo(centro, tin, tout)

        return jsonify({"ok": True, "respuesta": texto, "tokens_in": tin, "tokens_out": tout})

    except Exception as e:
        return jsonify({"error": f"Error IA: {str(e)[:200]}"}), 500


@app.route("/consumo/<licencia>", methods=["GET"])
def ver_consumo(licencia):
    """Panel simple: cuántos análisis lleva un centro este mes."""
    if licencia not in LICENCIAS:
        return jsonify({"error": "Licencia no válida"}), 403
    centro = LICENCIAS[licencia]
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    mes = datetime.now().strftime("%Y-%m")
    c.execute("""SELECT COUNT(*), COALESCE(SUM(tokens_in),0), COALESCE(SUM(tokens_out),0)
                 FROM consumo WHERE centro=? AND mes=?""", (centro, mes))
    n, tin, tout = c.fetchone()
    conn.close()
    coste_eur = (tin / 1_000_000 * 1 + tout / 1_000_000 * 5) * 0.92  # USD→EUR aprox
    return jsonify({
        "centro": centro, "mes": mes, "analisis": n,
        "tokens_in": tin, "tokens_out": tout,
        "coste_aprox_eur": round(coste_eur, 3)
    })


init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)
