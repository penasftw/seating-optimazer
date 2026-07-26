from abc import ABC, abstractmethod
from dataclasses import dataclass
from math import cos, sin, pi, hypot


# ==========================================================
# ASIENTO
# ==========================================================

@dataclass(frozen=True)
class Seat:
    index: int
    x: float
    y: float


# ==========================================================
# CLASE BASE
# ==========================================================

class Table(ABC):

    def __init__(self, table_id, capacity):
        self.id = table_id
        self.capacity = capacity

        # Cada subclase construye su propia geometría
        self.seats = self._build_geometry()

    @abstractmethod
    def _build_geometry(self):
        """
        Devuelve una lista de objetos Seat.
        """
        pass

    def distance(self, seat_a, seat_b):
        """
        Distancia euclídea entre dos asientos.
        """

        a = self.seats[seat_a]
        b = self.seats[seat_b]

        return hypot(
            a.x - b.x,
            a.y - b.y
        )

    def __repr__(self):
        return f"{self.__class__.__name__}(id={self.id}, capacity={self.capacity})"


# ==========================================================
# MESA CIRCULAR
# ==========================================================

class CircularTable(Table):

    def __init__(self, table_id, capacity=10, radius=1.0):
        self.radius = radius
        super().__init__(table_id, capacity)

    def _build_geometry(self):

        seats = []

        for i in range(self.capacity):

            angle = 2 * pi * i / self.capacity

            seats.append(
                Seat(
                    index=i,
                    x=self.radius * cos(angle),
                    y=self.radius * sin(angle)
                )
            )

        return seats


# ==========================================================
# MESA RECTANGULAR
# ==========================================================

class RectangularTable(Table):

    def __init__(self, table_id, capacity=10):

        super().__init__(table_id, capacity)

    def _build_geometry(self):

        return [

            Seat(0,0,0),
            Seat(1,1,0),
            Seat(2,2,0),
            Seat(3,3,0),

            Seat(4,3,1),
            Seat(5,2,1),
            Seat(6,1,1),
            Seat(7,0,1)

        ]


# ==========================================================
# MESA IMPERIAL
# ==========================================================

class ImperialTable(Table):

    def __init__(self, table_id, capacity=20):

        super().__init__(table_id, capacity)

    def _build_geometry(self):

        seats = []

        # Lado superior
        for i in range(8):
            seats.append(
                Seat(i, i, 0)
            )

        # Costado derecho
        seats.append(Seat(8, 7, 1))
        seats.append(Seat(9, 7, 2))

        # Lado inferior
        x = 7

        for i in range(10, 18):
            seats.append(
                Seat(i, x, 3)
            )
            x -= 1

        # Costado izquierdo
        seats.append(Seat(18, 0, 2))
        seats.append(Seat(19, 0, 1))

        return seats