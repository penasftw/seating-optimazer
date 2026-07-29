import io
import pandas as pd
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel
import os
from pathlib import Path

# Importamos las funciones de tu optimizador existente
from .seating_optimizer import cargar_datos, simulated_annealing

app = FastAPI()

# Obtenemos la ruta absoluta de la carpeta donde está este archivo (Algoritmo)
BASE_DIR = Path(__file__).resolve().parent

# ------------------------------------------------------------------
# FIREBASE / FIRESTORE
# ------------------------------------------------------------------
# Necesitas descargar tu "serviceAccountKey.json" desde:
# Firebase Console > Configuración del proyecto > Cuentas de servicio > Generar nueva clave privada
# y colocarlo en esta misma carpeta (Algoritmo/serviceAccountKey.json).
# Ese archivo NUNCA debe subirse a git (ya está en .gitignore).

import firebase_admin
from firebase_admin import credentials, firestore

db = None
SERVICE_ACCOUNT_PATH = BASE_DIR / "serviceAccountKey.json"

try:
    if SERVICE_ACCOUNT_PATH.exists():
        cred = credentials.Certificate(str(SERVICE_ACCOUNT_PATH))
        firebase_admin.initialize_app(cred)
        db = firestore.client()
    else:
        print(
            f"[Aviso] No se encontró {SERVICE_ACCOUNT_PATH}. "
            "Los endpoints /save-arrangement y /load-arrangement no funcionarán "
            "hasta que agregues tu clave de Firebase."
        )
except Exception as e:
    print(f"[Aviso] No se pudo inicializar Firebase: {e}")

# Como es un proyecto personal (una sola boda a la vez), guardamos todo
# en un único documento dentro de la colección "arrangements".
ARRANGEMENT_DOC = "arrangements/current"


class ArrangementPayload(BaseModel):
    tables: dict[str, list[str]]
    score: float | None = None

# Permitir solicitudes desde cualquier origen
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def home():
    # Construye la ruta exacta: Algoritmo/index.html
    html_path = BASE_DIR / "index.html"
    
    if html_path.exists():
        return FileResponse(html_path)
    
    return {"message": "API activa, pero no se encontró index.html en " + str(html_path)}

@app.post("/optimize")
async def optimize(file: UploadFile = File(...)):
    contents = await file.read()
    excel_bytes = io.BytesIO(contents)

    # Cargar usando Pandas desde el archivo en memoria
    df_invitados = pd.read_excel(excel_bytes, sheet_name="Invitados")
    df_afinidades = pd.read_excel(excel_bytes, sheet_name="Afinidades")
    df_mesas = pd.read_excel(excel_bytes, sheet_name="Mesas")

    # Reutilizamos la lógica de parsing de seating_optimizer.py
    # Para adaptar tu script existente rápidamente sin alterar la interfaz CLI:
    df_invitados = df_invitados.dropna(subset=["Nombre"])
    guests = df_invitados["Nombre"].astype(str).str.strip().tolist()

    affinity = {}
    df_afinidades = df_afinidades.dropna(subset=["Persona A", "Persona B", "Peso"])
    for _, row in df_afinidades.iterrows():
        a = str(row["Persona A"]).strip()
        b = str(row["Persona B"]).strip()
        peso = float(row["Peso"])
        affinity[(a, b)] = peso

    from .tables import CircularTable, RectangularTable, ImperialTable
    TABLE_TYPES = {
        "circular": CircularTable,
        "rectangular": RectangularTable,
        "imperial": ImperialTable,
    }

    df_mesas = df_mesas.dropna(subset=["Mesa", "Capacidad"])
    tables = {}
    for _, row in df_mesas.iterrows():
        nombre = str(row["Mesa"]).strip()
        capacidad = int(row["Capacidad"])
        tipo = str(row["Tipo"]).strip().lower()
        mesa = TABLE_TYPES[tipo](table_id=nombre, capacity=capacidad)
        tables[nombre] = mesa

    # Ejecutar algoritmo
    solution, final_score = simulated_annealing(guests, affinity, tables)

    # Formatear respuesta para el frontend
    tables_assignment = {}
    for person, table in solution.items():
        tables_assignment.setdefault(table, []).append(person)

    return {
        "score": final_score,
        "tables": tables_assignment
    }


def _require_db():
    if db is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Firestore no está configurado. Agrega serviceAccountKey.json "
                "en la carpeta del proyecto y reinicia el servidor."
            ),
        )


@app.post("/save-arrangement")
def save_arrangement(payload: ArrangementPayload):
    """Guarda el estado actual del tablero (después de ajustes manuales)."""
    _require_db()
    doc_ref = db.document(ARRANGEMENT_DOC)
    doc_ref.set({
        "tables": payload.tables,
        "score": payload.score,
        "updated_at": firestore.SERVER_TIMESTAMP,
    })
    return {"status": "ok"}


@app.get("/load-arrangement")
def load_arrangement():
    """Carga la última distribución guardada, si existe."""
    _require_db()
    doc_ref = db.document(ARRANGEMENT_DOC)
    doc = doc_ref.get()
    if not doc.exists:
        return {"tables": None, "score": None}
    data = doc.to_dict()
    return {"tables": data.get("tables"), "score": data.get("score")}