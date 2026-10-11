"""Offline sanity checks on the static graph model data (no Docker/Neo4j
needed) -- catches data-entry errors like a room assigned to two AC units
or left out entirely, before they ever reach seed.py."""
from app.model import (
    AC_ASSIGNMENTS,
    DORM_ROOMS,
    MECHANICAL_ROOMS,
    occupancy_sensor_label,
    temp_sensor_label,
)


def test_each_ac_services_exactly_three_dorms():
    for ac_label, info in AC_ASSIGNMENTS.items():
        assert len(info["dorms"]) == 3, f"{ac_label} services {len(info['dorms'])} dorms, expected 3"


def test_every_dorm_room_is_serviced_by_exactly_one_ac():
    serviced_counts = {}
    for info in AC_ASSIGNMENTS.values():
        for dorm in info["dorms"]:
            serviced_counts[dorm] = serviced_counts.get(dorm, 0) + 1

    assert set(serviced_counts) == set(DORM_ROOMS), (
        f"serviced rooms {sorted(serviced_counts)} != DORM_ROOMS {sorted(DORM_ROOMS)}"
    )
    for dorm, count in serviced_counts.items():
        assert count == 1, f"room {dorm} is serviced by {count} AC units, expected exactly 1"


def test_mechanical_rooms_are_each_located_by_exactly_one_ac():
    mech_rooms_used = [info["mechanical_room"] for info in AC_ASSIGNMENTS.values()]
    assert sorted(mech_rooms_used) == sorted(MECHANICAL_ROOMS)
    assert len(mech_rooms_used) == len(set(mech_rooms_used)), "two AC units share a mechanical room"


def test_dorm_and_mechanical_room_lists_are_disjoint():
    assert set(DORM_ROOMS).isdisjoint(set(MECHANICAL_ROOMS))


def test_sensor_label_helpers_are_unique_per_room():
    temp_labels = {temp_sensor_label(r) for r in DORM_ROOMS}
    occ_labels = {occupancy_sensor_label(r) for r in DORM_ROOMS}
    assert len(temp_labels) == len(DORM_ROOMS)
    assert len(occ_labels) == len(DORM_ROOMS)
    assert temp_labels.isdisjoint(occ_labels)
