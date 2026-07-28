"""
Optimizador de mesas para boda usando Simulated Annealing.
Lee los invitados, afinidades y mesas desde un archivo Excel (con hojas
'Invitados', 'Afinidades', 'Mesas') o desde 3 CSV separados, y escribe
el resultado en 'resultado_mesas.xlsx'.

Uso:
    python seating_optimizer.py plantilla_invitados.xlsx
    python seating_optimizer.py invitados.csv afinidades.csv mesas.csv

Dependencias: pandas, openpyxl (pip install pandas openpyxl)
"""

import sys
import random
import math
import pandas as pd
from .tables import Table, CircularTable, RectangularTable, ImperialTable

# ------------------------------------------------------------------
# 1. CARGA DE DATOS
# ------------------------------------------------------------------

def cargar_datos(args):
    """Devuelve (lista_invitados, dict_afinidades, dict_capacidad_mesas)."""
    if len(args) == 1 and args[0].lower().endswith((".xlsx", ".xlsm")):
        path = args[0]
        df_invitados = pd.read_excel(path, sheet_name="Invitados")
        df_afinidades = pd.read_excel(path, sheet_name="Afinidades")
        df_mesas = pd.read_excel(path, sheet_name="Mesas")
    elif len(args) == 3:
        df_invitados = pd.read_csv(args[0])
        df_afinidades = pd.read_csv(args[1])
        df_mesas = pd.read_csv(args[2])
    else:
        raise ValueError(
            "Pasa un .xlsx con hojas Invitados/Afinidades/Mesas, "
            "o 3 rutas CSV: invitados.csv afinidades.csv mesas.csv"
        )

    # Limpieza básica: quitar filas vacías o de ejemplo sin nombre real
    df_invitados = df_invitados.dropna(subset=["Nombre"])
    guests = df_invitados["Nombre"].astype(str).str.strip().tolist()

    affinity = {}
    df_afinidades = df_afinidades.dropna(subset=["Persona A", "Persona B", "Peso"])
    for _, row in df_afinidades.iterrows():
        a = str(row["Persona A"]).strip()
        b = str(row["Persona B"]).strip()
        peso = float(row["Peso"])
        affinity[(a, b)] = peso

    df_mesas = df_mesas.dropna(subset=["Mesa", "Capacidad"])
    tables = {}

    TABLE_TYPES = {
        "circular": CircularTable,
        "rectangular": RectangularTable,
        "imperial": ImperialTable,
    }

    for _, row in df_mesas.iterrows():

        nombre = str(row["Mesa"]).strip()
        capacidad = int(row["Capacidad"])
        tipo = str(row["Tipo"]).strip().lower()

        try:
            mesa = TABLE_TYPES[tipo](
                table_id=nombre,
                capacity=capacidad
            )

            tables[nombre] = mesa  # Guardar la instancia de mesa en el diccionario

        except KeyError:
            raise ValueError(f"Tipo de mesa desconocido: {tipo}")

    # Validaciones simples pero importantes
    nombres_afinidad = {p for par in affinity for p in par}
    desconocidos = nombres_afinidad - set(guests)
    if desconocidos:
        print(f"[Aviso] Nombres en 'Afinidades' que no están en 'Invitados': {desconocidos}")

    capacidad_total = sum(
        mesa.capacity
        for mesa in tables.values()
    )
    if capacidad_total < len(guests):
        raise ValueError(
            f"Capacidad total de mesas ({capacidad_total}) es menor que "
            f"el número de invitados ({len(guests)}). Agrega mesas o aumenta capacidad."
        )

    return guests, affinity, tables


# ------------------------------------------------------------------
# 2. FUNCIÓN OBJETIVO
# ------------------------------------------------------------------

def get_affinity(affinity, a, b):
    return affinity.get((a, b), affinity.get((b, a), 0))


def score(assignment, affinity):
    total = 0
    tables = {}
    for person, table in assignment.items():
        tables.setdefault(table, []).append(person)
    for people in tables.values():
        for i, a in enumerate(people):
            for b in people[i + 1:]:
                total += get_affinity(affinity, a, b)
    return total


# ------------------------------------------------------------------
# 3. SOLUCIÓN INICIAL (respeta capacidad de cada mesa, que puede variar)
# ------------------------------------------------------------------

def random_initial_assignment(guests, table_capacity):
    shuffled = guests[:]
    random.shuffle(shuffled)
    assignment = {}
    remaining = {
        mesa.id: mesa.capacity
        for mesa in table_capacity.values()
    }
    for person in shuffled:
        available = [t for t, cap in remaining.items() if cap > 0]
        table = random.choice(available)
        assignment[person] = table
        remaining[table] -= 1
    return assignment


# ------------------------------------------------------------------
# 4. SIMULATED ANNEALING
# ------------------------------------------------------------------

def simulated_annealing(guests, affinity, table_capacity,
                         iterations=40000, t_start=10.0, t_end=0.01):
    current = random_initial_assignment(guests, table_capacity)
    current_score = score(current, affinity)
    best, best_score = dict(current), current_score

    for it in range(iterations):
        t = t_start * (t_end / t_start) ** (it / iterations)

        p1, p2 = random.sample(guests, 2)
        if current[p1] == current[p2]:
            continue  # el intercambio no cambia nada

        new = dict(current)
        new[p1], new[p2] = new[p2], new[p1]
        new_score = score(new, affinity)

        delta = new_score - current_score
        if delta > 0 or random.random() < math.exp(delta / t):
            current, current_score = new, new_score
            if current_score > best_score:
                best, best_score = dict(current), current_score

    return best, best_score


# ------------------------------------------------------------------
# 5. GUARDAR RESULTADO
# ------------------------------------------------------------------

def guardar_resultado(assignment, final_score, path_salida="resultado_mesas.xlsx"):
    tables = {}
    for person, table in assignment.items():
        tables.setdefault(table, []).append(person)

    filas = []
    for mesa, personas in sorted(tables.items()):
        for persona in sorted(personas):
            filas.append({"Mesa": mesa, "Invitado": persona})

    df = pd.DataFrame(filas)
    with pd.ExcelWriter(path_salida, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Asignacion", index=False)
        resumen = pd.DataFrame(
            [{"Puntuacion total de afinidad": final_score}]
        )
        resumen.to_excel(writer, sheet_name="Resumen", index=False)

    print(f"Resultado guardado en: {path_salida}")


# ------------------------------------------------------------------
# 6. EJECUCIÓN
# ------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Uso:\n"
            "  python seating_optimizer.py plantilla_invitados.xlsx\n"
            "  python seating_optimizer.py invitados.csv afinidades.csv mesas.csv"
        )
        sys.exit(1)

    guests, affinity, table_capacity = cargar_datos(sys.argv[1:])
    print(f"Invitados cargados: {len(guests)}")
    print(f"Pares con afinidad definida: {len(affinity)}")
    print(f"Mesas: {table_capacity}\n")

    solution, final_score = simulated_annealing(guests, affinity, table_capacity)

    print(f"\nPuntuación total de afinidad: {final_score}\n")
    tables = {}
    for person, table in solution.items():
        tables.setdefault(table, []).append(person)
    for mesa, personas in sorted(tables.items()):
        print(f"{mesa}: {', '.join(sorted(personas))}")

    guardar_resultado(solution, final_score)