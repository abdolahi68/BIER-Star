# h3_bier/igmp.py
from __future__ import annotations

from collections import defaultdict
from typing import Any, DefaultDict, Dict, List, Optional, Set, Tuple

import h3
from geopy.distance import great_circle

from ..h3_routing import utils



# igmp_state[sat_id][group_id][beam_cell] -> list of receiver objects
IGMPState = DefaultDict[int, DefaultDict[str, DefaultDict[str, List[Any]]]]

# -------------------------
# H3 compatibility helpers
# -------------------------
def _geo_to_cell(lat: float, lon: float, res: int) -> str:
    # h3-py v4: latlng_to_cell ; v3: geo_to_h3
    if hasattr(h3, "latlng_to_cell"):
        return h3.latlng_to_cell(lat, lon, res)
    return h3.geo_to_h3(lat, lon, res)


def _cell_to_parent(cell: str, parent_res: int) -> str:
    if hasattr(h3, "cell_to_parent"):
        return h3.cell_to_parent(cell, parent_res)
    return h3.h3_to_parent(cell, parent_res)


def _cell_to_latlng(cell: str) -> Tuple[float, float]:
    if hasattr(h3, "cell_to_latlng"):
        lat, lon = h3.cell_to_latlng(cell)
        return float(lat), float(lon)
    lat, lon = h3.h3_to_geo(cell)
    return float(lat), float(lon)


def _sat_cell(sat: Any, t: int, res: int) -> str:
    return _geo_to_cell(float(sat.latitude[t - 1]), float(sat.longitude[t - 1]), res)



def make_igmp_state() -> IGMPState:
    return defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

def _user_label(u: Any, fallback: str) -> str:
    return str(getattr(u, "user_id", getattr(u, "id", getattr(u, "name", fallback))))


def _get_lat_lon(obj: Any) -> Tuple[float, float]:
    lat = getattr(obj, "latitude", getattr(obj, "lat", None))
    lon = getattr(obj, "longitude", getattr(obj, "lon", getattr(obj, "lng", None)))
    if lat is None or lon is None:
        raise ValueError(f"Object {obj} does not have latitude/longitude fields.")
    return float(lat), float(lon)


# ------------------------------------------------------------
# STRICT UNIQUE MULTI-BEAM SCHEDULER (7 beams, Res1)
# ------------------------------------------------------------
def schedule_spot_beams_res1_unique(
    sat_map: Dict[int, Any],
    users: List[Any],
    t: int,
    beams_per_sat: int = 32,
    beam_resolution: int = 1,
    parent_resolution: int = 0,
    attach_to_sat_objects: bool = True,
) -> Tuple[Dict[str, int], Dict[int, Set[str]]]:
    """
    Assign Res(beam_resolution) cells (demanded by 'users') to satellites as beams.
    """


# 1) Demand: exact beam cells at beam_resolution for the given users
    demand_by_parent: Dict[str, Set[str]] = defaultdict(set)
    for u in users:
        ulat, ulon = _get_lat_lon(u)
        beam_cell = _geo_to_cell(ulat, ulon, beam_resolution)           # e.g., Res2
        parent_cell = _cell_to_parent(beam_cell, parent_resolution)     # e.g., Res0
        demand_by_parent[parent_cell].add(beam_cell)


    # 2) Satellites grouped by their current parent cell (same parent_resolution)
    sats_by_parent: Dict[str, List[Any]] = defaultdict(list)
    for sat in sat_map.values():
        sat_parent = _sat_cell(sat, t, parent_resolution)
        sats_by_parent[sat_parent].append(sat)

    beam_cell_to_sat_id: Dict[str, int] = {}
    sat_id_to_cells_res1: Dict[int, Set[str]] = defaultdict(set)

    # 3) Assign each demanded Res1 cell to the nearest available satellite in the same parent cell
    for parent_cell, demanded_cells in demand_by_parent.items():
        candidates = sats_by_parent.get(parent_cell, [])
        if not candidates:
            continue

        for cell_res1 in demanded_cells:
            if cell_res1 in beam_cell_to_sat_id:
                continue

            cell_lat, cell_lon = _cell_to_latlng(cell_res1)

            best_sat = None
            best_dist = float("inf")

            for sat in candidates:
                if len(sat_id_to_cells_res1[sat.id]) >= beams_per_sat:
                    continue

                d = great_circle(
                    (cell_lat, cell_lon),
                    (float(sat.latitude[t - 1]), float(sat.longitude[t - 1])),
                ).km

                if d < best_dist:
                    best_dist = d
                    best_sat = sat

            if best_sat is None:
                continue

            beam_cell_to_sat_id[cell_res1] = best_sat.id
            sat_id_to_cells_res1[best_sat.id].add(cell_res1)

    # 4) Optional: attach to satellite objects for debugging/visualization
    if attach_to_sat_objects:
        for sat in sat_map.values():
            if not hasattr(sat, "beam_cells_by_t"):
                sat.beam_cells_by_t = {}
            sat.beam_cells_by_t[t] = set(sat_id_to_cells_res1.get(sat.id, set()))

    total_cells = len(beam_cell_to_sat_id)
    used_sats = sum(1 for sid, cells in sat_id_to_cells_res1.items() if cells)
    print(f"[BEAMS] Scheduled {total_cells} Res{beam_resolution} cells using {used_sats} satellites (t={t}).")

    return beam_cell_to_sat_id, sat_id_to_cells_res1


# ------------------------------------------------------------
# IGMP JOIN (last hop only, beam-aware)
# ------------------------------------------------------------
def igmp_join_receivers_to_nearest_satellite(
    sat_map: Dict[int, Any],
    receivers: List[Any],
    group_id: str,
    t: int,
    igmp_state: IGMPState,
    *,
    # If provided, use this strict ownership mapping first:
    beam_assignment_res1: Optional[Dict[str, int]] = None,
    # If True, attempt to prefer satellites located inside user's parent cell
    constrain_to_user_parent_res0: bool = True,
    # NEW: match main.py keyword args (and avoid crashes)
    beam_resolution: int = 1,
    parent_resolution: int = 0,
) -> None:
    """
    IGMP join is only for the last hop (sat->ground).

      1) If beam_assignment_res1 exists: user joins owner satellite of its beam cell (Res beam_resolution).
      2) Else: user joins nearest satellite (optionally constrained to same parent cell at parent_resolution).
    """
    for i, user in enumerate(receivers):
        ulat, ulon = _get_lat_lon(user)

        user_cell = _geo_to_cell(ulat, ulon, beam_resolution)
        user_parent = _cell_to_parent(user_cell, parent_resolution)

        chosen_sat = None
        dist_km = None

        # 1) strict beam ownership
        if beam_assignment_res1 is not None:
            owner_id = beam_assignment_res1.get(user_cell, None)
            if owner_id is not None and owner_id in sat_map:
                chosen_sat = sat_map[owner_id]
                dist_km = great_circle(
                    (ulat, ulon),
                    (float(chosen_sat.latitude[t - 1]), float(chosen_sat.longitude[t - 1])),
                ).km

        # 2) fallback: nearest sat (prefer same parent cell if requested)
        if chosen_sat is None:
            candidates = sat_map
            if constrain_to_user_parent_res0:
                same_parent = {
                    sid: s for sid, s in sat_map.items()
                    if _sat_cell(s, t, parent_resolution) == user_parent
                }
                if same_parent:
                    candidates = same_parent

            chosen_sat, dist_km = utils.find_nearest_satellite(user, candidates, t)

        if chosen_sat is None:
            print(f"[IGMP] join failed: receiver {_user_label(user, f'dst{i}')} has no satellite")
            continue

        igmp_state[chosen_sat.id][group_id][user_cell].append(user)

        print(
            f"[IGMP] user {_user_label(user, f'dst{i}')} joined group {group_id} "
            f"via satellite {chosen_sat.id} (dist={dist_km:.3f} km)"
        )


# ------------------------------------------------------------
# REQUIRED BY logic.py (do not remove)
# ------------------------------------------------------------
def get_delivering_sats_in_cell(
    sat_map: Dict[int, Any],
    igmp_state: IGMPState,
    cell_res0: str,
    group_id: str,
    t: int,
) -> Dict[int, int]:

    delivering: Dict[int, int] = {}

    for sat in sat_map.values():
        if _sat_cell(sat, t, 0) != cell_res0:
            continue

        group_state = igmp_state.get(sat.id, {}).get(group_id, None)
        if not group_state:
            continue

        # --- Backward compatible: group_state is a list of receivers ---
        if isinstance(group_state, list):
            if len(group_state) > 0:
                delivering[sat.id] = len(group_state)
            continue

        # --- Beam-aware: group_state is a dict: beam_cell -> list(receivers) ---
        if not isinstance(group_state, dict):
            # Unexpected shape; ignore safely
            continue

        # Find active beams set at time t if modeled
        active_beams = None
        if hasattr(sat, "beam_cells_by_t"):
            active_beams = sat.beam_cells_by_t.get(t, None)
        elif hasattr(sat, "beams_res2_by_t"):
            active_beams = sat.beams_res2_by_t.get(t, None)
        elif hasattr(sat, "beams_res1_by_t"):
            active_beams = sat.beams_res1_by_t.get(t, None)

        count = 0
        if active_beams is None:
            # No scheduling info -> count all beams that have members
            for members in group_state.values():
                if isinstance(members, list) and members:
                    count += len(members)
        else:
            # Count only beams that are active now
            for beam_cell, members in group_state.items():
                if beam_cell not in active_beams:
                    continue
                if isinstance(members, list) and members:
                    count += len(members)

        if count > 0:
            delivering[sat.id] = count

    return delivering



def deliver_igmp_in_cell(
    sat_map: Dict[int, Any],
    igmp_state: IGMPState,
    cell_res0: str,
    group_id: str,
    t: int,
) -> int:
    delivered = 0

    for sat in sat_map.values():
        if _sat_cell(sat, t, 0) != cell_res0:
            continue

        beam_map = igmp_state.get(sat.id, {}).get(group_id, {})
        if not beam_map:
            continue

        active_beams = None
        if hasattr(sat, "beam_cells_by_t"):
            active_beams = sat.beam_cells_by_t.get(t, None)

        # Deliver only on beams that (a) have members and (b) are active (if we model scheduling)
        for beam_cell, members in beam_map.items():
            if not members:
                continue
            if active_beams is not None and beam_cell not in active_beams:
                continue

            print(
                f"[IGMP] Egress sat {sat.id} in Res0 cell {cell_res0}: "
                f"beam {beam_cell} -> {len(members)} receivers"
            )
            delivered += len(members)

    if delivered == 0:
        print(f"[IGMP] No joined receivers for group {group_id} in cell {cell_res0}")

    return delivered
