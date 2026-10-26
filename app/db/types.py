"""Column types for exact decimal storage in SQLite."""

from decimal import Decimal

from sqlalchemy.types import Integer, TypeDecorator


class ScaledDecimal(TypeDecorator):
    """Store a Decimal as an integer count of 10**-scale units.

    SQLite has no exact decimal type. Integers keep values exact and let CHECK
    constraints compare amounts. A value with more decimal places than the
    scale is rejected, never rounded.
    """

    impl = Integer
    cache_ok = True

    def __init__(self, scale: int) -> None:
        super().__init__()
        self.scale = scale
        self._unit = Decimal(1).scaleb(-scale)

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if isinstance(value, bool) or isinstance(value, float):
            raise TypeError("decimal columns accept Decimal, int, or str values only")
        amount = value if isinstance(value, Decimal) else Decimal(value)
        scaled = amount.scaleb(self.scale)
        if scaled != scaled.to_integral_value():
            raise ValueError(f"value has more than {self.scale} decimal places")
        return int(scaled)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return Decimal(value).scaleb(-self.scale).quantize(self._unit)
