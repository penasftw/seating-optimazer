import io
import json
import os
from pathlib import Path
from typing import Optional

import pandas as pd
import firebase_admin
from fastapi import FastAPI, UploadFile, File, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from firebase_admin import credentials, firestore
from pydantic import BaseModel, Field

# Importamos las funciones de tu optimizador existente
from .seating_optimizer import (
    cargar_datos,
    simulated_annealing,
    build_affinity_with_groups,
)
from .tables import CircularTable, RectangularTable, ImperialTable

app = FastAPI()

# Obtenemos la ruta absoluta de la carpeta donde está este archivo (Algoritmo)
BASE_DIR = Path(__file__).resolve().parent

TABLE_TYPES = {
    "circular": CircularTable,
    "rectangular": RectangularTable,
    "imperial": ImperialTable,
}

# ------------------------------------------------------------------
# FIREBASE / FIRESTORE
# ------------------------------------------------------------------
# Dos formas de darle credenciales a este servidor:
#
# 1) LOCAL (desarrollo en tu máquina):
#    Descarga "serviceAccountKey.json" desde
#    Firebase Console > Configuración del proyecto > Cuentas de servicio > Generar nueva clave privada
#    y colócalo en esta misma carpeta. Nunca se sube a git (está en .gitignore).
#
# 2) RENDER (producción):
#    Render no tiene acceso a ese archivo porque no está en tu repo de GitHub.
#    En su lugar, abre tu servicio en Render > Environment, y crea una variable:
#      Key:   FIREBASE_SERVICE_ACCOUNT_JSON
#      Value: (pega aquí TODO el contenido del archivo serviceAccountKey.json, tal cual, como una sola línea)
#    El código de abajo detecta esa variable automáticamente y la usa en vez del archivo.

db = None
SERVICE_ACCOUNT_PATH = BASE_DIR / "serviceAccountKey.json"
SERVICE_ACCOUNT_ENV = os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON")

try:
    if SERVICE_ACCOUNT_ENV:
        cred_dict = json.loads(SERVICE_ACCOUNT_ENV)
        cred = credentials.Certificate(cred_dict)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
    elif SERVICE_ACCOUNT_PATH.exists():
        cred = credentials.Certificate(str(SERVICE_ACCOUNT_PATH))
        firebase_admin.initialize_app(cred)
        db = firestore.client()
    else:
        print(
            "[Aviso] No se encontraron credenciales de Firebase (ni archivo local "
            "ni variable de entorno FIREBASE_SERVICE_ACCOUNT_JSON). "
            "Los endpoints que dependen de Firestore no funcionarán."
        )
except Exception as e:
    print(f"[Aviso] No se pudo inicializar Firebase: {e}")


def _require_db():
    if db is None:
        raise HTTPException(
            status_code=503,
            detail=(
                "Firestore no está configurado. Agrega serviceAccountKey.json "
                "en la carpeta del proyecto y reinicia el servidor."
            ),
        )


# Colecciones Firestore
GROUPS_COLLECTION = "groups"
GUESTS_COLLECTION = "guests"
TABLES_COLLECTION = "tables"
AFFINITIES_COLLECTION = "affinities"
ARRANGEMENT_DOC = "arrangements/current"

# Permitir solicitudes desde cualquier origen
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ------------------------------------------------------------------
# SCHEMAS
# ------------------------------------------------------------------

class GroupIn(BaseModel):
    name: str
    color: Optional[str] = None


class GroupUpdate(BaseModel):
    name: Optional[str] = None
    color: Optional[str] = None


class GroupOut(BaseModel):
    id: str
    name: str
    color: Optional[str] = None


class GuestIn(BaseModel):
    first_name: str
    last_name_p: Optional[str] = None
    last_name_m: Optional[str] = None
    group_id: Optional[str] = None
    table_id: Optional[str] = None


class GuestUpdate(BaseModel):
    first_name: Optional[str] = None
    last_name_p: Optional[str] = None
    last_name_m: Optional[str] = None
    group_id: Optional[str] = None
    table_id: Optional[str] = None


class GuestOut(BaseModel):
    id: str
    first_name: str
    last_name_p: Optional[str] = None
    last_name_m: Optional[str] = None
    group_id: Optional[str] = None
    table_id: Optional[str] = None


class TableIn(BaseModel):
    name: str
    capacity: int
    type: str = Field(description="circular | rectangular | imperial")


class ArrangementPayload(BaseModel):
    tables: dict[str, list[str]]
    score: float | None = None


class OptimizeSettings(BaseModel):
    group_weight: float = 10.0
    fragmentation_penalty: float = 5.0
    capacity_bonus: float = 3.0
    iterations: int = 40000


def guest_full_name(guest: dict) -> str:
    """El optimizador identifica invitados por su nombre completo (string),
    así que ese es el 'guest name' que usamos como llave interna del algoritmo.
    """
    parts = [guest.get("first_name", "")]
    if guest.get("last_name_p"):
        parts.append(guest["last_name_p"])
    if guest.get("last_name_m"):
        parts.append(guest["last_name_m"])
    return " ".join(p.strip() for p in parts if p and p.strip())


# ------------------------------------------------------------------
# HOME
# ------------------------------------------------------------------

@app.get("/")
def home():
    html_path = BASE_DIR / "index.html"
    if html_path.exists():
        return FileResponse(html_path)
    return {"message": "API activa, pero no se encontró index.html en " + str(html_path)}


# ------------------------------------------------------------------
# GROUPS CRUD
# ------------------------------------------------------------------

@app.get("/api/groups", response_model=list[GroupOut])
def list_groups():
    _require_db()
    docs = db.collection(GROUPS_COLLECTION).stream()
    return [GroupOut(id=d.id, **d.to_dict()) for d in docs]


@app.post("/api/groups", response_model=GroupOut)
def create_group(group: GroupIn):
    _require_db()
    doc_ref = db.collection(GROUPS_COLLECTION).document()
    doc_ref.set(group.model_dump())
    return GroupOut(id=doc_ref.id, **group.model_dump())


@app.put("/api/groups/{group_id}", response_model=GroupOut)
def update_group(group_id: str, group: GroupUpdate):
    _require_db()
    doc_ref = db.collection(GROUPS_COLLECTION).document(group_id)
    if not doc_ref.get().exists:
        raise HTTPException(status_code=404, detail="Grupo no encontrado")
    updates = {k: v for k, v in group.model_dump().items() if v is not None}
    if updates:
        doc_ref.update(updates)
    data = doc_ref.get().to_dict()
    return GroupOut(id=group_id, **data)


@app.delete("/api/groups/{group_id}")
def delete_group(group_id: str):
    _require_db()
    doc_ref = db.collection(GROUPS_COLLECTION).document(group_id)
    if not doc_ref.get().exists:
        raise HTTPException(status_code=404, detail="Grupo no encontrado")
    doc_ref.delete()

    # Quitar la referencia de group_id de cualquier invitado que la tuviera
    affected = db.collection(GUESTS_COLLECTION).where("group_id", "==", group_id).stream()
    batch = db.batch()
    count = 0
    for guest_doc in affected:
        batch.update(guest_doc.reference, {"group_id": None})
        count += 1
    if count:
        batch.commit()

    return {"status": "ok", "guests_updated": count}


# ------------------------------------------------------------------
# GUESTS CRUD
# ------------------------------------------------------------------

_SORT_FIELDS = {
    "id": None,  # se ordena por el id del documento, manejado aparte
    "name": "first_name",
    "group": "group_id",
    "table": "table_id",
}


@app.get("/api/guests", response_model=list[GuestOut])
def list_guests(sort_by: str = Query("name", description="id | name | group | table")):
    _require_db()
    docs = list(db.collection(GUESTS_COLLECTION).stream())
    guests = [GuestOut(id=d.id, **d.to_dict()) for d in docs]

    if sort_by == "id":
        guests.sort(key=lambda g: g.id)
    elif sort_by == "group":
        guests.sort(key=lambda g: (g.group_id or ""))
    elif sort_by == "table":
        guests.sort(key=lambda g: (g.table_id or ""))
    else:  # "name" (default)
        guests.sort(key=lambda g: (g.first_name or "", g.last_name_p or ""))

    return guests


@app.post("/api/guests", response_model=GuestOut)
def create_guest(guest: GuestIn):
    _require_db()
    doc_ref = db.collection(GUESTS_COLLECTION).document()
    doc_ref.set(guest.model_dump())
    return GuestOut(id=doc_ref.id, **guest.model_dump())


@app.put("/api/guests/{guest_id}", response_model=GuestOut)
def update_guest(guest_id: str, guest: GuestUpdate):
    _require_db()
    doc_ref = db.collection(GUESTS_COLLECTION).document(guest_id)
    if not doc_ref.get().exists:
        raise HTTPException(status_code=404, detail="Invitado no encontrado")
    updates = {k: v for k, v in guest.model_dump(exclude_unset=True).items()}
    if updates:
        doc_ref.update(updates)
    data = doc_ref.get().to_dict()
    return GuestOut(id=guest_id, **data)


@app.delete("/api/guests/{guest_id}")
def delete_guest(guest_id: str):
    _require_db()
    doc_ref = db.collection(GUESTS_COLLECTION).document(guest_id)
    if not doc_ref.get().exists:
        raise HTTPException(status_code=404, detail="Invitado no encontrado")
    doc_ref.delete()
    return {"status": "ok"}


@app.post("/api/guests/bulk-import")
async def bulk_import(file: UploadFile = File(...)):
    """Sube el Excel clásico (hojas Invitados / Afinidades / Mesas, con
    columna 'Grupo' opcional en Invitados) y convierte cada fila en
    documentos reales de Firestore: guests, groups, tables y affinities.
    Es idempotente en el sentido de que corre 'limpio' cada vez: no
    intenta hacer merge fila por fila con lo que ya existía.
    """
    _require_db()
    contents = await file.read()
    excel_bytes = io.BytesIO(contents)

    df_invitados = pd.read_excel(excel_bytes, sheet_name="Invitados").dropna(subset=["Nombre"])
    df_afinidades = pd.read_excel(excel_bytes, sheet_name="Afinidades")
    df_mesas = pd.read_excel(excel_bytes, sheet_name="Mesas").dropna(subset=["Mesa", "Capacidad"])

    batch = db.batch()

    # --- Grupos: uno por cada valor distinto en la columna 'Grupo' ---
    group_name_to_id = {}
    if "Grupo" in df_invitados.columns:
        nombres_grupo = sorted({
            str(g).strip() for g in df_invitados["Grupo"].dropna().tolist() if str(g).strip()
        })
        for nombre_grupo in nombres_grupo:
            doc_ref = db.collection(GROUPS_COLLECTION).document()
            batch.set(doc_ref, {"name": nombre_grupo, "color": None})
            group_name_to_id[nombre_grupo] = doc_ref.id

    # --- Mesas ---
    table_name_to_id = {}
    for _, row in df_mesas.iterrows():
        nombre = str(row["Mesa"]).strip()
        capacidad = int(row["Capacidad"])
        tipo = str(row.get("Tipo", "circular")).strip().lower()
        if tipo not in TABLE_TYPES:
            raise HTTPException(status_code=400, detail=f"Tipo de mesa desconocido: {tipo}")
        doc_ref = db.collection(TABLES_COLLECTION).document()
        batch.set(doc_ref, {"name": nombre, "capacity": capacidad, "type": tipo})
        table_name_to_id[nombre] = doc_ref.id

    # --- Invitados ---
    full_name_to_id = {}
    for _, row in df_invitados.iterrows():
        nombre_completo = str(row["Nombre"]).strip()
        partes = nombre_completo.split(" ", 1)
        first_name = partes[0]
        resto = partes[1] if len(partes) > 1 else None

        grupo_val = row.get("Grupo") if "Grupo" in df_invitados.columns else None
        group_id = group_name_to_id.get(str(grupo_val).strip()) if pd.notna(grupo_val) else None

        doc_ref = db.collection(GUESTS_COLLECTION).document()
        batch.set(doc_ref, {
            "first_name": first_name,
            "last_name_p": resto,
            "last_name_m": None,
            "group_id": group_id,
            "table_id": None,
        })
        full_name_to_id[nombre_completo] = doc_ref.id

    # --- Afinidades explícitas (guardadas por nombre, se resuelven al optimizar) ---
    if df_afinidades is not None:
        df_afinidades = df_afinidades.dropna(subset=["Persona A", "Persona B", "Peso"])
        for _, row in df_afinidades.iterrows():
            a = str(row["Persona A"]).strip()
            b = str(row["Persona B"]).strip()
            peso = float(row["Peso"])
            doc_ref = db.collection(AFFINITIES_COLLECTION).document()
            batch.set(doc_ref, {"person_a": a, "person_b": b, "weight": peso})

    batch.commit()

    return {
        "status": "ok",
        "guests_created": len(full_name_to_id),
        "groups_created": len(group_name_to_id),
        "tables_created": len(table_name_to_id),
    }


# ------------------------------------------------------------------
# TABLES CRUD (soporte mínimo, necesario para correr /optimize sin Excel)
# ------------------------------------------------------------------

@app.get("/api/tables")
def list_tables():
    _require_db()
    docs = db.collection(TABLES_COLLECTION).stream()
    return [{"id": d.id, **d.to_dict()} for d in docs]


@app.post("/api/tables")
def create_table(table: TableIn):
    _require_db()
    if table.type not in TABLE_TYPES:
        raise HTTPException(status_code=400, detail=f"Tipo de mesa desconocido: {table.type}")
    doc_ref = db.collection(TABLES_COLLECTION).document()
    doc_ref.set(table.model_dump())
    return {"id": doc_ref.id, **table.model_dump()}


@app.delete("/api/tables/{table_id}")
def delete_table(table_id: str):
    _require_db()
    doc_ref = db.collection(TABLES_COLLECTION).document(table_id)
    if not doc_ref.get().exists:
        raise HTTPException(status_code=404, detail="Mesa no encontrada")
    doc_ref.delete()
    return {"status": "ok"}


# ------------------------------------------------------------------
# OPTIMIZE
# ------------------------------------------------------------------

@app.post("/optimize")
async def optimize(
    file: Optional[UploadFile] = File(None),
    settings: OptimizeSettings = OptimizeSettings(),
):
    """Dos modos:

    1) Con archivo (legacy): sube un Excel con las 3 hojas y corre la
       optimización sin tocar Firestore. Útil para pruebas rápidas.
    2) Sin archivo (nuevo, recomendado): usa los invitados, grupos y
       mesas ya guardados en Firestore vía las rutas /api/*, corre la
       optimización, y persiste el table_id resultante en cada
       documento de invitado más el arreglo en arrangements/current.
    """
    if file is not None:
        contents = await file.read()
        excel_bytes = io.BytesIO(contents)

        df_invitados = pd.read_excel(excel_bytes, sheet_name="Invitados").dropna(subset=["Nombre"])
        df_afinidades = pd.read_excel(excel_bytes, sheet_name="Afinidades")
        df_mesas = pd.read_excel(excel_bytes, sheet_name="Mesas").dropna(subset=["Mesa", "Capacidad"])

        guests = df_invitados["Nombre"].astype(str).str.strip().tolist()

        guest_group = {}
        if "Grupo" in df_invitados.columns:
            for _, row in df_invitados.iterrows():
                nombre = str(row["Nombre"]).strip()
                grupo = row.get("Grupo")
                if pd.notna(grupo) and str(grupo).strip():
                    guest_group[nombre] = str(grupo).strip()

        affinity = {}
        df_afinidades = df_afinidades.dropna(subset=["Persona A", "Persona B", "Peso"])
        for _, row in df_afinidades.iterrows():
            a = str(row["Persona A"]).strip()
            b = str(row["Persona B"]).strip()
            peso = float(row["Peso"])
            affinity[(a, b)] = peso

        tables = {}
        for _, row in df_mesas.iterrows():
            nombre = str(row["Mesa"]).strip()
            capacidad = int(row["Capacidad"])
            tipo = str(row["Tipo"]).strip().lower()
            tables[nombre] = TABLE_TYPES[tipo](table_id=nombre, capacity=capacidad)

    else:
        _require_db()

        guest_docs = list(db.collection(GUESTS_COLLECTION).stream())
        if not guest_docs:
            raise HTTPException(status_code=400, detail="No hay invitados guardados en Firestore.")
        table_docs = list(db.collection(TABLES_COLLECTION).stream())
        if not table_docs:
            raise HTTPException(status_code=400, detail="No hay mesas guardadas en Firestore.")
        affinity_docs = list(db.collection(AFFINITIES_COLLECTION).stream())

        guest_id_to_name = {}
        guests = []
        guest_group = {}
        for d in guest_docs:
            data = d.to_dict()
            name = guest_full_name(data)
            guest_id_to_name[d.id] = name
            guests.append(name)
            if data.get("group_id"):
                guest_group[name] = data["group_id"]

        tables = {}
        table_id_to_name = {}
        for d in table_docs:
            data = d.to_dict()
            nombre = data["name"]
            tipo = data["type"]
            if tipo not in TABLE_TYPES:
                raise HTTPException(status_code=400, detail=f"Tipo de mesa desconocido: {tipo}")
            tables[nombre] = TABLE_TYPES[tipo](table_id=nombre, capacity=int(data["capacity"]))
            table_id_to_name[d.id] = nombre

        affinity = {}
        for d in affinity_docs:
            data = d.to_dict()
            affinity[(data["person_a"], data["person_b"])] = float(data["weight"])

    capacidad_total = sum(mesa.capacity for mesa in tables.values())
    if capacidad_total < len(guests):
        raise HTTPException(
            status_code=400,
            detail=(
                f"Capacidad total de mesas ({capacidad_total}) es menor que "
                f"el número de invitados ({len(guests)})."
            ),
        )

    affinity = build_affinity_with_groups(affinity, guest_group, group_weight=settings.group_weight)

    solution, final_score = simulated_annealing(
        guests, affinity, tables,
        guest_group=guest_group,
        iterations=settings.iterations,
        fragmentation_penalty=settings.fragmentation_penalty,
        capacity_bonus=settings.capacity_bonus,
    )

    tables_assignment = {}
    for person, table in solution.items():
        tables_assignment.setdefault(table, []).append(person)

    response = {"score": final_score, "tables": tables_assignment}

    if file is None:
        # Persistimos el table_id resultante en cada invitado y guardamos
        # el arreglo, para que el frontend pueda simplemente recargar.
        name_to_guest_id = {v: k for k, v in guest_id_to_name.items()}
        batch = db.batch()
        for person, table_name in solution.items():
            guest_id = name_to_guest_id.get(person)
            if guest_id is None:
                continue
            doc_ref = db.collection(GUESTS_COLLECTION).document(guest_id)
            batch.update(doc_ref, {"table_id": table_name})
        batch.commit()

        # Releer invitados actualizados para devolverlos completos al frontend
        updated_guests = []
        for d in db.collection(GUESTS_COLLECTION).stream():
            updated_guests.append(GuestOut(id=d.id, **d.to_dict()))
        response["guests"] = [g.model_dump() for g in updated_guests]

        db.document(ARRANGEMENT_DOC).set({
            "tables": tables_assignment,
            "score": final_score,
            "updated_at": firestore.SERVER_TIMESTAMP,
        })

    return response


# ------------------------------------------------------------------
# ARRANGEMENTS (estado manual del tablero, drag & drop)
# ------------------------------------------------------------------

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