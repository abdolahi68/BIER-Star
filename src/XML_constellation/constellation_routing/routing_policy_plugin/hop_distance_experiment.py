# hop_distance_experiment.py
"""
Hop-Distance Experiment
=======================
Place this file in the SAME folder as BIER_shortest_path.py:
  ...\\routing_policy_plugin\\hop_distance_experiment.py

Goal
----
Find the H3 cell (res-1) that contains the maximum number of airplanes.
Run every multicast protocol from a source satellite at each of hop distances
1-6 from that dense cell.

Bit-cost definitions
--------------------
Protocol         | What is saved
-----------------|--------------------------------------------------------------
BIER-Star R0/R1  | packet.header_bit_length()
                 |   = source_cell ID bits        (1 × bits_per_fwd_cell)
                 |   + routing_tree cell-ID bits  ((N-1) × bits_per_fwd_cell)
                 |   + destination_cells bits     (M × bits_per_dst_cell)
                 |   = N × bits_per_fwd_cell  +  M × bits_per_dst_cell
                 | Routing tree comes from the ACTUAL satellite SPT via
                 | H3_BIER_Function(). Lower bound: tree structural
                 | serialization bits are not counted.
                 | Label as: "BIER-Star Cell-Identifier Header Bits"
-----------------|--------------------------------------------------------------
BIER-TE         | packet.header_bitstring_length  (= E_ISL + 2*T_dst, active destination-airplane bitmap)
----------------|--------------------------------------------------------------
YETI-AirplaneAware | packet.estimated_label_bits  (full label-stack header)
                   | actual 3--4 ISL interfaces per satellite from topology;
                   | destination airplanes are explicit fine-grained access endpoints.
----------------|--------------------------------------------------------------
Geo-BIER        | inter_partition_bits + busiest_partition_bitstring_length
----------------|--------------------------------------------------------------
Traditional BIER| max_bitstring_length  (one bit per node in scope)
----------------|--------------------------------------------------------------

Source-selection strategy
-------------------------
  1. All satellites nearest to destination airplanes = BFS seed (hop=0).
  2. Multi-source BFS expands outward on the ISL graph.
  3. At each target hop d, the satellite geographically closest to the H3
     cell centroid is chosen as source (keeps direction consistent).
"""

from __future__ import annotations

import os
from collections import defaultdict, deque
from math import asin, ceil, cos, log2, radians, sin, sqrt
from typing import Dict, List, Optional, Set, Tuple

import h3
import networkx as nx

# ── BIER ──────────────────────────────────────────────────────────────────────
import src.XML_constellation.constellation_routing.routing_policy_plugin.BIER.protocol_measures as BIER
import src.XML_constellation.constellation_routing.routing_policy_plugin.BIER.bier_helpers as bier_helpers
import src.XML_constellation.constellation_routing.routing_policy_plugin.h3_bier.main as H3_BIER_Main
from src.XML_constellation.constellation_routing.routing_policy_plugin.h3_bier.igmp import (
    schedule_spot_beams_res1_unique,
)

# ── BIER-TE ───────────────────────────────────────────────────────────────────
from src.XML_constellation.constellation_routing.routing_policy_plugin.BIER_TE import (
    build_te_tables,
    ingress_process_te,
)

# ── YETI ──────────────────────────────────────────────────────────────────────
from src.XML_constellation.constellation_routing.routing_policy_plugin.Yeti import (
    add_beam_access_topology,
    add_airplane_access_topology,
    airplane_endpoint_id,
    count_yeti_routers,
    YetiPacket,
    build_router_tables,
    build_shortest_path_multicast_tree,
    count_label_types,
    encode_tree_to_yeti_labels,
    estimate_label_bits,
    find_nearest_satellite_at_time as yeti_find_nearest,
    labels_to_strings,
)
from src.XML_constellation.constellation_routing.routing_policy_plugin.Yeti.yeti_logic import (
    get_paths_from_yeti_tree,
    send_yeti_packet,
)


# ══════════════════════════════════════════════════════════════════════════════
# h3-py v3 / v4 compatibility shims
# ══════════════════════════════════════════════════════════════════════════════

def _geo_to_h3(lat: float, lon: float, res: int) -> str:
    if hasattr(h3, "latlng_to_cell"):          # v4
        return h3.latlng_to_cell(lat, lon, res)
    return h3.geo_to_h3(lat, lon, res)         # v3


def _cell_to_latlng(cell: str) -> Tuple[float, float]:
    if hasattr(h3, "cell_to_latlng"):          # v4
        return h3.cell_to_latlng(cell)
    return h3.h3_to_geo(cell)                  # v3


# ══════════════════════════════════════════════════════════════════════════════
# Haversine distance (km)
# ══════════════════════════════════════════════════════════════════════════════

def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * asin(sqrt(a)) * 6371.0


# ══════════════════════════════════════════════════════════════════════════════
# Step 1 — Find the densest H3 cell
# ══════════════════════════════════════════════════════════════════════════════

def find_densest_h3_cell(
    all_airplanes: list,
    h3_resolution: int = 1,
) -> Tuple[str, list]:
    cell_map: Dict[str, list] = defaultdict(list)
    for plane in all_airplanes:
        cell = _geo_to_h3(float(plane.latitude), float(plane.longitude), h3_resolution)
        cell_map[cell].append(plane)

    densest_cell   = max(cell_map, key=lambda c: len(cell_map[c]))
    planes_in_cell = cell_map[densest_cell]
    print(
        f"[HopExp] Densest H3 res-{h3_resolution} cell : {densest_cell}"
        f"  ({len(planes_in_cell)} airplanes)"
    )
    return densest_cell, planes_in_cell


# ══════════════════════════════════════════════════════════════════════════════
# Step 2 — Map destination airplanes → nearest satellites (BFS seed)
# ══════════════════════════════════════════════════════════════════════════════

def resolve_destination_satellites(
    planes:  list,
    sat_map: dict,
    t:       int,
) -> Set[str]:
    dst_sats: Set[str] = set()
    for plane in planes:
        user = bier_helpers.GroundUser(
            lat=float(plane.latitude), lon=float(plane.longitude)
        )
        sat = bier_helpers.find_nearest_satellite_at_time(user, sat_map, t)
        if sat is not None:
            dst_sats.add(f"satellite_{sat.id}")
    return dst_sats


# ══════════════════════════════════════════════════════════════════════════════
# Step 3 — BFS hop-distance map
# ══════════════════════════════════════════════════════════════════════════════

def compute_hop_distances(
    G:          nx.Graph,
    seed_nodes: Set[str],
) -> Dict[str, int]:
    sat_nodes = {n for n in G.nodes() if str(n).startswith("satellite_")}
    G_sat     = G.subgraph(sat_nodes)

    dist:  Dict[str, int] = {}
    queue: deque           = deque()

    for seed in seed_nodes:
        if seed in sat_nodes:
            dist[seed] = 0
            queue.append(seed)

    while queue:
        node = queue.popleft()
        for nbr in G_sat.neighbors(node):
            if nbr not in dist:
                dist[nbr] = dist[node] + 1
                queue.append(nbr)

    return dist


# ══════════════════════════════════════════════════════════════════════════════
# Step 4 — Pick one representative source satellite per hop distance
# ══════════════════════════════════════════════════════════════════════════════

def pick_source_at_hop(
    hop_dist_map:      Dict[str, int],
    target_hop:        int,
    cell_centroid_lat: float,
    cell_centroid_lon: float,
    sat_map:           dict,
    t:                 int,
) -> Optional[str]:
    candidates = [n for n, d in hop_dist_map.items() if d == target_hop]
    if not candidates:
        print(f"[HopExp] ⚠  No satellite found at hop distance {target_hop}.")
        return None

    def _dist_to_centroid(node_id: str) -> float:
        sat_id = int(node_id.split("_")[1])
        sat    = sat_map.get(sat_id)
        if sat is None:
            return float("inf")
        return _haversine_km(
            cell_centroid_lat, cell_centroid_lon,
            float(sat.latitude[t - 1]), float(sat.longitude[t - 1]),
        )

    chosen = min(candidates, key=_dist_to_centroid)
    print(
        f"[HopExp] Hop={target_hop}  →  source: {chosen}"
        f"  ({len(candidates)} candidates)"
    )
    return chosen


# ══════════════════════════════════════════════════════════════════════════════
# CSV helpers
# ══════════════════════════════════════════════════════════════════════════════

_CSV_HEADER = "Method,HopDistance,NumBits\n"

_ALL_LABELS = [
    "Geo-BIER-R1",
    "Geo-BIER-R0",
    "SatFoot-BIER-all_shells",
    "SatFoot-BIER-per_shell",
    "Traditional-BIER-all_shells",
    "Traditional-BIER-current_shell",
    "BIER-Star-R0",
    "BIER-Star-R1",
    "YETI-AirplaneAware",
    "BIER-TE",
]


def _append_row(filename: str, method: str, hop: int, bits) -> None:
    with open(filename, "a") as fh:
        fh.write(f"{method},{hop},{bits}\n")


def _record_na_for_hop(filename: str, hop: int) -> None:
    for label in _ALL_LABELS:
        _append_row(filename, label, hop, "N/A")


# ══════════════════════════════════════════════════════════════════════════════
# Protocol runners
# ══════════════════════════════════════════════════════════════════════════════

def _run_geo_bier(
    G, sat_map, sh, t, all_airplanes,
    src_node:      str,
    dst_sat_nodes: Set[str],
    h3_res:        int,
    hop:           int,
    filename:      str,
) -> None:
    label = f"Geo-BIER-R{h3_res}"
    print(f"\n[HopExp] {label}  hop={hop}  src={src_node}")
    try:
        results = BIER.BIER_Function(
            network=G.copy(),
            src=src_node,
            dests=list(dst_sat_nodes),
            sh=sh, t=t, sat_map=sat_map,
            partitioning_method="geographical",
            h3_resolution=h3_res,
            all_airplanes=all_airplanes,
        )
        inter_bits   = results.get("bits_for_inter_partition_addressing", 0)
        busiest_bits = results.get("busiest_partition_bitstring_length",  0)
        bits = (
            inter_bits + busiest_bits
            if isinstance(inter_bits,    (int, float))
            and isinstance(busiest_bits, (int, float))
            else "N/A"
        )
    except Exception as exc:
        print(f"[HopExp] {label} ERROR: {exc}")
        bits = "N/A"
    _append_row(filename, label, hop, bits)


def _run_satfoot_bier(
    G, sat_map, sh, t, all_airplanes,
    src_node:      str,
    dst_sat_nodes: Set[str],
    scope:         str,
    hop:           int,
    filename:      str,
) -> None:
    label = f"SatFoot-BIER-{scope}"
    print(f"\n[HopExp] {label}  hop={hop}  src={src_node}")
    try:
        results = BIER.BIER_Function(
            network=G.copy(),
            src=src_node,
            dests=list(dst_sat_nodes),
            sh=sh, t=t, sat_map=sat_map,
            partitioning_method="satellite_footprint",
            all_airplanes=all_airplanes,
            satellite_addressing_scope=scope,
        )
        inter_bits   = results.get("bits_for_inter_partition_addressing", 0)
        busiest_bits = results.get("busiest_partition_bitstring_length",  0)
        bits = (
            inter_bits + busiest_bits
            if isinstance(inter_bits,    (int, float))
            and isinstance(busiest_bits, (int, float))
            else "N/A"
        )
    except Exception as exc:
        print(f"[HopExp] {label} ERROR: {exc}")
        bits = "N/A"
    _append_row(filename, label, hop, bits)


def _run_traditional_bier(
    G, sat_map, sh, t, all_airplanes,
    src_node:      str,
    dst_sat_nodes: Set[str],
    scope:         str,
    hop:           int,
    filename:      str,
) -> None:
    label = f"Traditional-BIER-{scope}"
    print(f"\n[HopExp] {label}  hop={hop}  src={src_node}")
    try:
        results = BIER.BIER_Function(
            network=G.copy(),
            src=src_node,
            dests=list(dst_sat_nodes),
            sh=sh, t=t, sat_map=sat_map,
            partitioning_method="traditional",
            all_airplanes=all_airplanes,
            traditional_scope=scope,
        )
        bits = results.get("max_bitstring_length", "N/A")
    except Exception as exc:
        print(f"[HopExp] {label} ERROR: {exc}")
        bits = "N/A"
    _append_row(filename, label, hop, bits)


def _run_bier_star(
    G, sat_map, sh, t,
    source_user: bier_helpers.GroundUser,
    dst_users:   list,
    plan_res:    int,
    hop:         int,
    filename:    str,
) -> None:
    """
    BIER-Star Cell-Identifier Header Bits = packet.header_bit_length()
      = source_cell ID bits        (1 × bits_per_fwd_cell)
      + routing_tree cell-ID bits  ((N-1) × bits_per_fwd_cell)
      + destination_cells bits     (M × bits_per_dst_cell)
      = N × bits_per_fwd_cell  +  M × bits_per_dst_cell

    The routing tree comes from the ACTUAL satellite SPT built by
    H3_BIER_Function(). No approximation. Lower bound: tree structural
    serialization bits are not counted.
    """
    label = f"BIER-Star-R{plan_res}"
    print(f"\n[HopExp] {label}  hop={hop}  "
          f"src=({source_user.latitude:.3f}, {source_user.longitude:.3f})")
    try:
        packet, _ = H3_BIER_Main.H3_BIER_Function(
            G=G.copy(),
            sat_map=sat_map,
            source_user=source_user,
            destination_users=dst_users,
            sh=sh, t=t,
            plan_resolution=plan_res,
            dest_resolution=2,
        )
        if packet is not None:
            bits = packet.header_bit_length()
        else:
            print(f"[HopExp] {label}: H3_BIER_Function returned None packet.")
            bits = "N/A"
    except Exception as exc:
        print(f"[HopExp] {label} ERROR: {exc}")
        bits = "N/A"
    _append_row(filename, label, hop, bits)


def _run_yeti(
    G, sat_map, sh, t,
    src_node:     str,
    source_user,
    dst_users:    list,
    hop:          int,
    filename:     str,
) -> None:
    """
    Run an airplane-aware Yeti baseline for the selected source satellite.

    The selected ``src_node`` remains the root so the requested hop distance is
    preserved.  Each satellite uses its actual satellite-to-satellite ISL
    degree from the topology, typically 3--4 ISL interfaces.  Destination
    airplanes are explicit fine-grained access endpoints instead of beam/cell
    endpoints, so non-destination airplanes inside the same beam/cell are not
    counted as Yeti receivers.
    """
    label = "YETI-AirplaneAware"
    BEAMS_PER_SAT = 32
    DEST_RESOLUTION = 2
    BEAM_PARENT_RESOLUTION = 0
    RESERVED_ISLS_PER_SAT = None  # None = use actual 3--4 ISL neighbors from the topology

    print(f"\n[HopExp] {label}  hop={hop}  src={src_node}")
    try:
        # Make GroundUser objects addressable as airplane endpoints.
        for idx, user in enumerate(dst_users):
            if not hasattr(user, "id"):
                setattr(user, "id", f"dst_{idx}")

        destination_cells = {
            _geo_to_h3(float(user.latitude), float(user.longitude), DEST_RESOLUTION)
            for user in dst_users
        }
        beam_assignment, _sat_beams = schedule_spot_beams_res1_unique(
            sat_map=sat_map,
            users=[source_user] + list(dst_users),
            t=t,
            beams_per_sat=BEAMS_PER_SAT,
            beam_resolution=DEST_RESOLUTION,
            parent_resolution=BEAM_PARENT_RESOLUTION,
            attach_to_sat_objects=True,
        )

        missing_cells = sorted(destination_cells.difference(beam_assignment))
        if missing_cells:
            raise ValueError(
                f"destination beam cell(s) not scheduled: {missing_cells}"
            )

        airplane_to_satellite = {}
        for user in dst_users:
            cell = _geo_to_h3(float(user.latitude), float(user.longitude), DEST_RESOLUTION)
            sat_id = beam_assignment[cell]
            airplane_to_satellite[airplane_endpoint_id(user)] = f"satellite_{int(sat_id)}"

        G_access, dst_nodes = add_airplane_access_topology(
            graph=G.copy(),
            airplane_to_satellite=airplane_to_satellite,
            destination_airplanes=dst_users,
            strict=True,
        )
        G_yeti = build_router_tables(
            G_access,
            reserved_beam_interfaces_per_satellite=0,
            reserved_isl_interfaces_per_satellite=RESERVED_ISLS_PER_SAT,
        )

        tree_info = build_shortest_path_multicast_tree(
            G_yeti, src_node, list(dst_nodes)
        )
        if tree_info["missing_destinations"]:
            raise ValueError(
                f"unreachable airplane destination(s): {tree_info['missing_destinations']}"
            )

        labels = encode_tree_to_yeti_labels(graph=G_yeti, tree_info=tree_info)
        max_ifaces = max(
            (len(G_yeti.nodes[n].get("interface_id_map", {})) for n in G_yeti.nodes()),
            default=0,
        )
        est_bits = estimate_label_bits(
            labels=labels,
            num_nodes=count_yeti_routers(G_yeti),
            max_interfaces=max_ifaces,
        )
        bits = int(est_bits)

        print(
            f"[HopExp] YETI airplane-aware: target_airplanes={len(dst_nodes)}, "
            f"ISL_mode=actual_topology_3_to_4, "
            f"max_interfaces={max_ifaces}, estimated_label_bits={bits}"
        )
    except Exception as exc:
        print(f"[HopExp] {label} ERROR: {exc}")
        bits = "N/A"
    _append_row(filename, label, hop, bits)


def _run_bier_te(
    G, sat_map, sh, t, all_airplanes,
    src_node:         str,
    dst_airplane_ids: List[str],
    hop:              int,
    filename:         str,
) -> None:
    label = "BIER-TE"
    print(f"\n[HopExp] {label}  hop={hop}  src={src_node}")
    try:
        requested_destinations = set(str(tid) for tid in dst_airplane_ids)
        terminal_to_satellite: dict = {}

        for i, plane in enumerate(all_airplanes):
            tid = f"airplane_{i}"
            if tid not in requested_destinations:
                continue

            user = bier_helpers.GroundUser(
                lat=float(plane.latitude), lon=float(plane.longitude)
            )
            sat = bier_helpers.find_nearest_satellite_at_time(user, sat_map, t)
            if sat is not None:
                terminal_to_satellite[tid] = f"satellite_{sat.id}"

        destination_terminals = sorted(terminal_to_satellite.keys())

        print(
            "[HopExp] BIER-TE active destination terminals "
            f"T_dst = {len(destination_terminals)}"
        )

        G_te = build_te_tables(
            G.copy(),
            terminal_to_satellite=terminal_to_satellite,
            satellites_are_destinations=False,
            unique_terminal_decap=True,
        )

        _, packet = ingress_process_te(
            G_te,
            src_node,
            "multicast-payload",
            [],
            destination_terminals,
            print_info=False,
            return_tree=True,
            return_packet=True,
        )
        bits = packet.header_bitstring_length
        print(f"[HopExp] BIER-TE header_bitstring_length = {bits}")
    except Exception as exc:
        print(f"[HopExp] {label} ERROR: {exc}")
        bits = "N/A"
    _append_row(filename, label, hop, bits)


# ══════════════════════════════════════════════════════════════════════════════
# Public entry point
# ══════════════════════════════════════════════════════════════════════════════

def run_hop_distance_experiment(
    G:                nx.Graph,
    sat_map:          dict,
    sh,
    t:                int,
    all_airplanes:    list,
    results_filename: str,
    h3_resolution:    int             = 1,
    hop_distances:    Tuple[int, ...] = (1, 2, 3, 4, 5, 6),
) -> None:
    print("\n" + "=" * 60)
    print("  HOP-DISTANCE EXPERIMENT  —  START")
    print("=" * 60)

    hop_csv = results_filename.replace(".csv", "_hop_distance.csv")
    if not os.path.exists(hop_csv) or os.path.getsize(hop_csv) == 0:
        with open(hop_csv, "w") as fh:
            fh.write(_CSV_HEADER)

    # ── Step 1: densest H3 cell ───────────────────────────────────────────────
    densest_cell, dst_planes = find_densest_h3_cell(all_airplanes, h3_resolution)
    if not dst_planes:
        print("[HopExp] ⚠  No airplanes found — aborting.")
        return

    cell_lat, cell_lon = _cell_to_latlng(densest_cell)
    print(f"[HopExp] Cell centroid : lat={cell_lat:.4f}  lon={cell_lon:.4f}")
    print(f"[HopExp] Destination airplanes : {len(dst_planes)}")

    # ── Step 2: destination satellite cluster (BFS seed) ─────────────────────
    dst_sat_nodes = resolve_destination_satellites(dst_planes, sat_map, t)
    print(f"[HopExp] Destination satellites ({len(dst_sat_nodes)}): {sorted(dst_sat_nodes)}")
    if not dst_sat_nodes:
        print("[HopExp] ⚠  No destination satellites resolved — aborting.")
        return

    # GroundUser list for BIER-Star
    dst_ground_users = [
        bier_helpers.GroundUser(lat=float(p.latitude), lon=float(p.longitude))
        for p in dst_planes
    ]

    # Destination terminal ID list for BIER-TE (index must match enumerate(all_airplanes))
    pos_to_idx: Dict[Tuple[float, float], int] = {
        (float(p.latitude), float(p.longitude)): i
        for i, p in enumerate(all_airplanes)
    }
    dst_airplane_ids: List[str] = []
    for plane in dst_planes:
        key = (float(plane.latitude), float(plane.longitude))
        if key in pos_to_idx:
            dst_airplane_ids.append(f"airplane_{pos_to_idx[key]}")
    dst_airplane_ids = list(set(dst_airplane_ids))
    print(
        f"[HopExp] Destination terminal IDs ({len(dst_airplane_ids)}): "
        f"{sorted(dst_airplane_ids)[:8]}"
        f"{'...' if len(dst_airplane_ids) > 8 else ''}"
    )

    # ── Step 3: BFS hop-distance map ─────────────────────────────────────────
    print("\n[HopExp] Running multi-source BFS from destination satellite cluster ...")
    hop_dist_map = compute_hop_distances(G, dst_sat_nodes)

    hop_count: Dict[int, int] = defaultdict(int)
    for d in hop_dist_map.values():
        hop_count[d] += 1
    for d in sorted(hop_count):
        print(f"  hop {d:2d} : {hop_count[d]:4d} satellite(s)")

    # ── Steps 4-6: per-hop-distance runs ─────────────────────────────────────
    for hop in hop_distances:
        print(f"\n{'─' * 60}")
        print(f"  HOP DISTANCE = {hop}")
        print(f"{'─' * 60}")

        src_node = pick_source_at_hop(
            hop_dist_map, hop, cell_lat, cell_lon, sat_map, t
        )
        if src_node is None:
            _record_na_for_hop(hop_csv, hop)
            continue

        src_sat_id  = int(src_node.split("_")[1])
        src_sat_obj = sat_map.get(src_sat_id)
        if src_sat_obj is not None:
            src_ground_user = bier_helpers.GroundUser(
                lat=float(src_sat_obj.latitude[t - 1]),
                lon=float(src_sat_obj.longitude[t - 1]),
            )
        else:
            src_ground_user = bier_helpers.GroundUser(lat=cell_lat, lon=cell_lon)

        _run_geo_bier(G, sat_map, sh, t, all_airplanes,
                      src_node, dst_sat_nodes, h3_res=1, hop=hop, filename=hop_csv)

        _run_geo_bier(G, sat_map, sh, t, all_airplanes,
                      src_node, dst_sat_nodes, h3_res=0, hop=hop, filename=hop_csv)

        _run_satfoot_bier(G, sat_map, sh, t, all_airplanes,
                          src_node, dst_sat_nodes, scope="all_shells", hop=hop, filename=hop_csv)

        _run_satfoot_bier(G, sat_map, sh, t, all_airplanes,
                          src_node, dst_sat_nodes, scope="per_shell", hop=hop, filename=hop_csv)

        _run_traditional_bier(G, sat_map, sh, t, all_airplanes,
                               src_node, dst_sat_nodes, scope="all_shells", hop=hop, filename=hop_csv)

        _run_traditional_bier(G, sat_map, sh, t, all_airplanes,
                               src_node, dst_sat_nodes, scope="current_shell", hop=hop, filename=hop_csv)

        _run_bier_star(G, sat_map, sh, t,
                       src_ground_user, dst_ground_users, plan_res=0, hop=hop, filename=hop_csv)

        _run_bier_star(G, sat_map, sh, t,
                       src_ground_user, dst_ground_users, plan_res=1, hop=hop, filename=hop_csv)

        _run_yeti(G, sat_map, sh, t,
                  src_node, src_ground_user, dst_ground_users,
                  hop=hop, filename=hop_csv)

        _run_bier_te(G, sat_map, sh, t, all_airplanes,
                     src_node, dst_airplane_ids, hop=hop, filename=hop_csv)

    print("\n" + "=" * 60)
    print(f"  HOP-DISTANCE EXPERIMENT  —  DONE  →  {hop_csv}")
    print("=" * 60)
