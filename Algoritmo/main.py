import io
import pandas as pd
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
import os
from pathlib import Path

# Importamos las funciones de tu optimizador existente
from .seating_optimizer import cargar_datos, simulated_annealing

app = FastAPI()

# Obtenemos la ruta absoluta de la carpeta donde está este archivo (Algoritmo)
BASE_DIR = Path(__file__).resolve().parent

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
    # Sirve directamente la interfaz frontend
    if os.path.exists("index.html"):
        return FileResponse("index.html")
    return {"message": "API activa. Visita /docs para ver Swagger."}

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

@app.get("/", response_class=FileResponse)
def read_index():
    # Devuelve el archivo index.html
    return FileResponse(BASE_DIR / "index.html")