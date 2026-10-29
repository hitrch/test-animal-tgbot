import pytest

from bot.places import ALL_PLACES, FOREST_PARTS, MITTEN_PARTS, place_chain


@pytest.mark.parametrize(("place", "chain"), [
    ("весь ліс", ["весь ліс"]),
    ("хвойний", ["хвойний", "весь ліс"]),
    ("У соснах", ["У соснах", "хвойний", "весь ліс"]),
    # A mitten in two forest parts gets both of them
    ("Під вербою", ["Під вербою", "листяний", "біля річки", "весь ліс"]),
    # A mitten outside every forest part goes straight to the whole forest
    ("На узліссі", ["На узліссі", "весь ліс"]),
])
def test_place_chain_goes_from_specific_to_whole_forest(place, chain):
    assert place_chain(place) == chain


def test_place_chain_rejects_unknown_place():
    with pytest.raises(ValueError):
        place_chain("Під березою")


def test_forest_map_has_12_places():
    assert len(ALL_PLACES) == 12
    assert len(set(ALL_PLACES)) == 12


def test_mittens_belong_only_to_known_forest_parts():
    for parts in MITTEN_PARTS.values():
        assert set(parts) <= set(FOREST_PARTS)