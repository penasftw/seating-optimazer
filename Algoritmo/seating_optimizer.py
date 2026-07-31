"""
Optimizador de mesas para boda usando Simulated Annealing.

Soporta dos modos de entrada:
  1) CLI clásico: Excel/CSV con hojas 'Invitados', 'Afinidades', 'Mesas'
     (ver cargar_datos / __main__).
  2) Programático: listas de invitados/grupos ya cargadas desde Firestore
     (ver build_affinity_with_groups + simulated_annealing, usadas por main.py).

Dependencias: pandas, openpyxl (pip install pandas openpyxl)
"""

import sys
import random
import math
import pandas as pd
from .tables import Table, CircularTable, RectangularTable, ImperialTable

# ------------------------------------------------------------------
# 1. CARGA DE DATOS (modo CLI / Excel-Legacy)
# ------------------------------------------------------------------

def cargar_datos(args):
    """Devuelve (lista_invitados, dict_afinidades, dict_mesas, dict_guest_group).

    dict_guest_group mapea nombre -> id de grupo (string) si la hoja
    'Invitados' trae una columna 'Grupo'; si no existe, se devuelve {}.
    Se mantiene retrocompatibilidad: el 4to valor puede ignorarse en
    llamadas existentes que solo desempaquetan 3 valores... salvo que
    en Python eso rompe el unpacking, así que quien llame a esta
    función con el CLI legacy debe desempaquetar los 4 valores.
    """
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

    return guests, affinity, tables, guest_group


# ------------------------------------------------------------------
# 2. AFINIDAD IMPLÍCITA POR GRUPO
# ------------------------------------------------------------------

def build_affinity_with_groups(base_affinity, guest_group, group_weight=10.0):
    """Devuelve una copia de base_affinity con un peso positivo agregado
    entre cualquier par de invitados que comparta el mismo group_id,
    salvo que ya exista una afinidad explícita definida para ese par
    (en cualquier orden), en cuyo caso se respeta la afinidad explícita
    y NO se sobreescribe.
    """
    affinity = dict(base_affinity)

    guests_by_group = {}
    for guest, group_id in guest_group.items():
        if group_id:
            guests_by_group.setdefault(group_id, []).append(guest)

    for group_id, members in guests_by_group.items():
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                if (a, b) not in affinity and (b, a) not in affinity:
                    affinity[(a, b)] = group_weight

    return affinity


# ------------------------------------------------------------------
# 3. FUNCIÓN OBJETIVO
# ------------------------------------------------------------------

def get_affinity(affinity, a, b):
    return affinity.get((a, b), affinity.get((b, a), 0))


def score(assignment, affinity, guest_group=None, tables=None,
          fragmentation_penalty=5.0, capacity_bonus=3.0):
    """Puntuación total de una asignación invitado -> mesa.

    - Suma la afinidad par a par dentro de cada mesa (incluye afinidad
      implícita de grupo si se usó build_affinity_with_groups antes de
      llamar a esta función).
    - Resta una penalización por cada mesa "extra" en la que un mismo
      grupo termina fragmentado (si guest_group se provee).
    - Suma un bono por cada mesa que queda exactamente llena (si
      tables, el dict de objetos Mesa con .capacity, se provee), para
      incentivar combinaciones limpias de tamaños de grupo.
    """
    total = 0
    table_members = {}
    for person, table in assignment.items():
        table_members.setdefault(table, []).append(person)

    # Afinidad par a par dentro de cada mesa
    for people in table_members.values():
        for i, a in enumerate(people):
            for b in people[i + 1:]:
                total += get_affinity(affinity, a, b)

    # Penalización por fragmentación de grupos
    if guest_group:
        group_tables = {}
        for guest, table in assignment.items():
            group_id = guest_group.get(guest)
            if group_id:
                group_tables.setdefault(group_id, set()).add(table)
        for group_id, tables_used in group_tables.items():
            if len(tables_used) > 1:
                total -= fragmentation_penalty * (len(tables_used) - 1)

    # Bono por mesas que quedan exactamente llenas
    if tables:
        for table_id, people in table_members.items():
            mesa = tables.get(table_id)
            if mesa is not None and len(people) == mesa.capacity:
                total += capacity_bonus

    return total


# ------------------------------------------------------------------
# 4. SOLUCIÓN INICIAL (respeta capacidad de cada mesa, que puede variar)
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
# 5. SIMULATED ANNEALING
# ------------------------------------------------------------------

def simulated_annealing(guests, affinity, table_capacity,
                         guest_group=None,
                         iterations=40000, t_start=10.0, t_end=0.01,
                         fragmentation_penalty=5.0, capacity_bonus=3.0):
    """
    guest_group: dict opcional {invitado: group_id}. Si se provee, la
    puntuación incluye la penalización de fragmentación y el bono de
    capacidad definidos en score(). La afinidad implícita de grupo debe
    incorporarse ANTES de llamar a esta función usando
    build_affinity_with_groups (así el peso de grupo también participa
    normalmente en los deltas par a par durante el recocido).
    """
    current = random_initial_assignment(guests, table_capacity)
    current_score = score(current, affinity, guest_group, table_capacity,
                           fragmentation_penalty, capacity_bonus)
    best, best_score = dict(current), current_score

    for it in range(iterations):
        t = t_start * (t_end / t_start) ** (it / iterations)

        p1, p2 = random.sample(guests, 2)
        if current[p1] == current[p2]:
            continue  # el intercambio no cambia nada

        new = dict(current)
        new[p1], new[p2] = new[p2], new[p1]
        new_score = score(new, affinity, guest_group, table_capacity,
                           fragmentation_penalty, capacity_bonus)

        delta = new_score - current_score
        if delta > 0 or random.random() < math.exp(delta / t):
            current, current_score = new, new_score
            if current_score > best_score:
                best, best_score = dict(current), current_score

    return best, best_score


# ------------------------------------------------------------------
# 6. GUARDAR RESULTADO
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
# 7. EJECUCIÓN (CLI)
# ------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(
            "Uso:\n"
            "  python seating_optimizer.py plantilla_invitados.xlsx\n"
            "  python seating_optimizer.py invitados.csv afinidades.csv mesas.csv"
        )
        sys.exit(1)

    guests, affinity, table_capacity, guest_group = cargar_datos(sys.argv[1:])
    affinity = build_affinity_with_groups(affinity, guest_group)

    print(f"Invitados cargados: {len(guests)}")
    print(f"Pares con afinidad definida (incluye grupo): {len(affinity)}")
    print(f"Mesas: {table_capacity}\n")

    solution, final_score = simulated_annealing(
        guests, affinity, table_capacity, guest_group=guest_group
    )

    print(f"\nPuntuación total de afinidad: {final_score}\n")
    tables = {}
    for person, table in solution.items():
        tables.setdefault(table, []).append(person)
    for mesa, personas in sorted(tables.items()):
        print(f"{mesa}: {', '.join(sorted(personas))}")

    guardar_resultado(solution, final_score)