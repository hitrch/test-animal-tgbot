"""Forest map: the fixed list of places and which forest parts each mitten belongs to."""

WHOLE_FOREST = "весь ліс"

FOREST_PARTS = ["хвойний", "листяний", "біля річки"]

# Mitten -> forest parts it belongs to (from the customer and the "Карта лісу" document)
MITTEN_PARTS = {
    "Під ялиною": ["хвойний"],
    "У соснах": ["хвойний"],
    "Біля мурашника": ["хвойний"],
    "Під дубом": ["листяний"],
    "У ліщині": ["листяний"],
    "Під вербою": ["листяний", "біля річки"],
    "Біля бобрової греблі": ["біля річки"],
    "На узліссі": [],
}

MITTENS = list(MITTEN_PARTS)

ALL_PLACES = [WHOLE_FOREST, *FOREST_PARTS, *MITTENS]


def place_chain(place: str) -> list[str]:
    """Return the place and all wider places, from the most specific to the whole forest.

    Example: "Під вербою" -> ["Під вербою", "листяний", "біля річки", "весь ліс"]
    """
    if place == WHOLE_FOREST:
        return [WHOLE_FOREST]
    if place in FOREST_PARTS:
        return [place, WHOLE_FOREST]
    if place in MITTEN_PARTS:
        return [place, *MITTEN_PARTS[place], WHOLE_FOREST]
    raise ValueError(f"Unknown place: {place}")