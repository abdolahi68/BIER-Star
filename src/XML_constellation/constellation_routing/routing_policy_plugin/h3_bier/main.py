"""
h3_bier/main.py

Main entry point for the H3-BIER routing simulation (IGMP last-hop only).
Packet header contains (wire format, as defined in packet.py):
  - forwarding_resolution  
  - destination_resolution 
  - source_cell            
  - routing_tree           
  - destination_cells      
  - group_id            
"""

import networkx as nx
import h3
import numpy as np
from geopy.distance import great_circle

import os
from pathlib import Path
from .h3_binary_codebook import H3BinaryCodebook
from ..h3_routing import utils
from .packet import H3BIERPacket
from .logic import forward_packet
from . import bier_visualizer
from .igmp import (
    make_igmp_state,
    igmp_join_receivers_to_nearest_satellite,
    schedule_spot_beams_res1_unique,
)


# -------------------------
# H3 compatibility helpers
# -------------------------
def _geo_to_cell(lat: float, lon: float, res: int) -> str:
    if hasattr(h3, "latlng_to_cell"):  # h3-py v4.x
        return h3.latlng_to_cell(lat, lon, res)
    return h3.geo_to_h3(lat, lon, res)  # h3-py v3.x


def _cell_to_parent(cell: str, parent_res: int) -> str:
    if hasattr(h3, "cell_to_parent"):  # h3-py v4.x
        return h3.cell_to_parent(cell, parent_res)
    return h3.h3_to_parent(cell, parent_res)  # h3-py v3.x


def _get_resolution(cell: str) -> int:
    if hasattr(h3, "get_resolution"):   # h3-py v4.x
        return h3.get_resolution(cell)
    return h3.h3_get_resolution(cell)   # h3-py v3.x


# -------------------------
# Path generation helpers
# -------------------------
def generate_h3_path(start_cell, end_cell):
    """
    Generates a list of H3 cells between start_cell and end_cell.
    Uses h3_line if available; falls back to manual sampling.
    """
    try:
        if hasattr(h3, "h3_line"):          # v3.x
            return list(h3.h3_line(start_cell, end_cell))
        if hasattr(h3, "grid_path_cells"):  # v4.x
            return list(h3.grid_path_cells(start_cell, end_cell))
        raise AttributeError("No H3 line/path API found")
    except Exception:
        print(
            f"Warning: H3 line/path failed for start={start_cell}, end={end_cell}. "
            f"Falling back to manual path generation."
        )
        return _manual_generate_h3_path(start_cell, end_cell)


def _manual_generate_h3_path(start_cell, end_cell):
    resolution = _get_resolution(start_cell)

    if hasattr(h3, "h3_to_geo"):
        start_lat, start_lon = h3.h3_to_geo(start_cell)
        end_lat, end_lon   = h3.h3_to_geo(end_cell)
    else:
        start_lat, start_lon = h3.cell_to_latlng(start_cell)
        end_lat, end_lon     = h3.cell_to_latlng(end_cell)

    def to_xyz(lat, lon):
        lat_rad, lon_rad = np.radians(lat), np.radians(lon)
        x = np.cos(lat_rad) * np.cos(lon_rad)
        y = np.cos(lat_rad) * np.sin(lon_rad)
        z = np.sin(lat_rad)
        return np.array([x, y, z])

    def to_latlon(xyz):
        x, y, z = xyz
        z = np.clip(z, -1.0, 1.0)
        return np.degrees(np.arcsin(z)), np.degrees(np.arctan2(y, x))

    v_start, v_end = to_xyz(start_lat, start_lon), to_xyz(end_lat, end_lon)
    num_steps = int(great_circle((start_lat, start_lon), (end_lat, end_lon)).kilometers / 200) + 2

    path_cells = []
    last_cell  = None
    for i in range(num_steps + 1):
        alpha     = i / num_steps
        v_interp  = (1 - alpha) * v_start + alpha * v_end
        v_norm    = v_interp / np.linalg.norm(v_interp)
        interp_lat, interp_lon = to_latlon(v_norm)
        current_cell = _geo_to_cell(interp_lat, interp_lon, resolution)
        if current_cell != last_cell:
            path_cells.append(current_cell)
            last_cell = current_cell

    if not path_cells or path_cells[-1] != end_cell:
        path_cells.append(end_cell)

    return path_cells


def convert_graph_to_dict_tree(graph, root):
    """Converts a NetworkX graph into a nested dictionary tree structure."""
    bfs_tree = nx.bfs_tree(graph, source=root)
    tree = {}
    for parent, child in nx.bfs_edges(bfs_tree, source=root):
        path  = nx.shortest_path(bfs_tree, source=root, target=parent)
        level = tree
        for node in path:
            level = level.setdefault(node, {})
        level.setdefault(child, {})
    return tree.get(root, {})


# -------------------------
# Clean pipeline helpers
# -------------------------
def _compute_source_and_dest_cells(source_user, destination_users, plan_res0=0, dest_res1=1):
    """
    Compute H3 cells for source and destination users at the specified resolutions.
    """
    source_cell_dest   = _geo_to_cell(float(source_user.latitude), float(source_user.longitude), dest_res1)
    destination_cells_dest = {
        _geo_to_cell(float(dest.latitude), float(dest.longitude), dest_res1)
        for dest in destination_users
    }

    
    # When plan_res == dest_res the cell IS its own parent.
    if plan_res0 < dest_res1:
        source_cell_plan       = _cell_to_parent(source_cell_dest, plan_res0)
        destination_cells_plan = {_cell_to_parent(c, plan_res0) for c in destination_cells_dest}
    else:
        source_cell_plan       = source_cell_dest
        destination_cells_plan = set(destination_cells_dest)

    return source_cell_dest, destination_cells_dest, source_cell_plan, destination_cells_plan


def _build_multicast_cell_tree(
    G,
    sat_map,
    ingress_sat,
    egress_sat_ids,
    t: int,
    forwarding_res: int,
    weight_attr: str = "weight",
    debug: bool = False,
):
    """
    Multicast shortest-path plan (SPT), then pruned:
      1) single-source Dijkstra from ingress on G
      2) for each egress, take shortest path src->egress
      3) map each sat-path into H3 cells at forwarding_res
      4) union into a cell_graph
      5) prune cell_graph to keep ONLY edges on root->terminal paths
    """
    cell_graph = nx.Graph()
    if ingress_sat is None or not egress_sat_ids:
        return cell_graph

    ti = max(0, t - 1)

    def sat_node(sid: int) -> str:
        return f"satellite_{sid}"

    def node_to_sid(node: str) -> int:
        return int(str(node).split("_")[1])

    def sat_to_cell(sid: int) -> str:
        s = sat_map[sid]
        return _geo_to_cell(float(s.latitude[ti]), float(s.longitude[ti]), forwarding_res)

    src       = sat_node(ingress_sat.id)
    dst_nodes = [sat_node(sid) for sid in sorted(set(egress_sat_ids))]   # noqa: F841 (kept for clarity)

    if src not in G:
        if debug:
            print(f"[SPT] ingress node missing in G: {src}")
        return cell_graph

    # Step 1: single-source shortest paths on satellite graph
    try:
        paths_to = nx.single_source_dijkstra_path(G, src, weight=weight_attr)
    except Exception as e:
        if debug:
            print(f"[SPT] dijkstra failed ({e}), fallback to unweighted shortest paths")
        paths_to = nx.single_source_shortest_path(G, src)

    # Steps 2/3: build union cell_graph
    root_cell = sat_to_cell(ingress_sat.id)
    cell_graph.add_node(root_cell)

    terminal_cells = set()
    for sid in sorted(set(egress_sat_ids)):
        if sid in sat_map:
            terminal_cells.add(sat_to_cell(sid))

    reached = 0
    for sid in sorted(set(egress_sat_ids)):
        dst        = sat_node(sid)
        path_nodes = paths_to.get(dst)
        if not path_nodes:
            continue

        h3_seq = []
        last   = None
        for n in path_nodes:
            s = node_to_sid(n)
            if s not in sat_map:
                continue
            c = sat_to_cell(s)
            if c != last:
                h3_seq.append(c)
                last = c

        if len(h3_seq) >= 2:
            nx.add_path(cell_graph, h3_seq)
            reached += 1
        elif len(h3_seq) == 1:
            cell_graph.add_node(h3_seq[0])
            reached += 1

    if debug:
        print(f"[SPT] pre-prune: reached={reached}, nodes={cell_graph.number_of_nodes()}, "
              f"edges={cell_graph.number_of_edges()}")
        print(f"[SPT] root_cell={root_cell}, terminal_cells={sorted(terminal_cells)}")

    # Step 4: prune — keep only root->terminal paths in cell space
    keep_edges = set()
    keep_nodes = {root_cell}

    for tc in terminal_cells:
        if tc == root_cell:
            keep_nodes.add(tc)
            continue
        if tc not in cell_graph:
            continue
        try:
            p = nx.shortest_path(cell_graph, root_cell, tc)
        except Exception:
            continue
        keep_nodes.update(p)
        keep_edges.update({tuple(sorted((u, v))) for u, v in zip(p, p[1:])})

    pruned = nx.Graph()
    pruned.add_nodes_from(keep_nodes)
    pruned.add_edges_from(list(keep_edges))

    if debug:
        print(f"[SPT] post-prune: nodes={pruned.number_of_nodes()}, "
              f"edges={pruned.number_of_edges()}")

    return pruned


def _make_packet(
    source_cell_plan: str,
    routing_tree_dict_plan: dict,
    destination_cells_dest: set,
    destination_users: list,
    group_id: str,
    forwarding_resolution: int,
    destination_resolution: int,
    codebook,
):
    dest_names = [
        getattr(d, "user_id", getattr(d, "id", getattr(d, "name", f"user-{i}")))
        for i, d in enumerate(destination_users)
    ]

    pkt = H3BIERPacket(
        source_cell_h3=source_cell_plan,
        current_tree_h3=routing_tree_dict_plan,
        destination_cells_h3=destination_cells_dest,
        forwarding_resolution=forwarding_resolution,
        destination_resolution=destination_resolution,
        codebook=codebook,
        payload=f"multicast data for {dest_names}",
        group_id=group_id,
    )
    return pkt


# ---------------------------------------------------------------------------
# Codebook cache — keyed by path so it survives repeated calls with the same
# file but is never shared across different .h5 files.
# ---------------------------------------------------------------------------
_codebook_cache: dict = {}


def _load_codebook() -> H3BinaryCodebook:

    env_path = os.getenv("H3_CODEBOOK_H5", "").strip()
    if env_path:
        codebook_path = Path(env_path)
    else:
        here  = Path(__file__).resolve()
        found = None
        for p in [here] + list(here.parents):
            candidate = p / "data" / "h3_cells_binary_res0-4.h5"
            if candidate.exists():
                found = candidate
                break
        if found is None:
            found = Path(
                r"C:\Users\Kmale\OneDrive - University of Victoria"
                r"\BIER_Star_Simulation_Journal\StarPerf_v1"
                r"\data\h3_cells_binary_res0-4.h5"
            )
        codebook_path = found

    key = str(codebook_path)
    if key not in _codebook_cache:
        _codebook_cache[key] = H3BinaryCodebook(codebook_path)
    return _codebook_cache[key]


# ---------------------------------------------------------------------------
# Main H3-BIER entry point
# ---------------------------------------------------------------------------
def H3_BIER_Function(
    G,
    sat_map,
    source_user,
    destination_users,
    sh,
    t,
    plan_resolution: int = 0,
    dest_resolution: int = 2,
    beam_assignment_override=None,
    forced_ingress_sat_id=None,
    enable_visualization: bool = True,
):
    """
    Run H3-BIER multicast routing.
    """
    print("\n======================================================")
    print(f"--- Running H3-BIER MULTICAST (plan_res={plan_resolution}, "
          f"dest_res={dest_resolution}) ---")
    print("======================================================")

    # ------------------------------------------------------------------
    # Step 0) Validate resolutions and normalise inputs
    # ------------------------------------------------------------------
    if plan_resolution > dest_resolution:
        raise ValueError(
            f"plan_resolution ({plan_resolution}) must be <= "
            f"dest_resolution ({dest_resolution})."
        )

    if not isinstance(destination_users, list):
        destination_users = [destination_users]
    print(f"[H3-BIER] destination_users count: {len(destination_users)}")

    BEAMS_PER_SAT = 32  # beams per satellite (each beam covers one dest_resolution cell)

    # ------------------------------------------------------------------
    # Step 1) Compute source / destination H3 cells
    # ------------------------------------------------------------------
    (source_cell_dest,
     destination_cells_dest,
     source_cell_plan_ground,    # noqa: F841
     destination_cells_plan) = _compute_source_and_dest_cells(   # noqa: F841
        source_user=source_user,
        destination_users=destination_users,
        plan_res0=plan_resolution,
        dest_res1=dest_resolution,
    )

    print(f"[H3-BIER] Source cell (res={dest_resolution}):       {source_cell_dest}")
    print(f"[H3-BIER] Destination cells (res={dest_resolution}): {destination_cells_dest}")

    # ------------------------------------------------------------------
    # Step 2) Beam scheduling — unique dest_resolution cell ownership
    # ------------------------------------------------------------------
    if beam_assignment_override is None:
        beam_assignment, _sat_beams = schedule_spot_beams_res1_unique(
            sat_map=sat_map,
            users=[source_user] + destination_users,
            t=t,
            beams_per_sat=BEAMS_PER_SAT,
            beam_resolution=dest_resolution,
            parent_resolution=plan_resolution,
            attach_to_sat_objects=True,
        )
    else:
        beam_assignment = {
            str(cell): int(sat_id)
            for cell, sat_id in beam_assignment_override.items()
        }
        missing_destination_cells = destination_cells_dest.difference(beam_assignment.keys())
        if missing_destination_cells:
            raise ValueError(
                "beam_assignment_override does not assign destination cell(s): "
                + ", ".join(sorted(missing_destination_cells))
            )
        _sat_beams = {}
        for cell, sat_id in beam_assignment.items():
            _sat_beams.setdefault(int(sat_id), set()).add(cell)

    print("\n--- Beam Assignment (Cell -> Sat ID) ---")
    for cell, sat_id in beam_assignment.items():
        print(f"  Cell {cell} -> Satellite {sat_id}")

    # ------------------------------------------------------------------
    # Step 3) IGMP join (last-hop / access only), beam-aware
    # ------------------------------------------------------------------
    group_id   = f"G_t{t}_pr{plan_resolution}"   # unique per resolution run
    igmp_state = make_igmp_state()
    igmp_join_receivers_to_nearest_satellite(
        sat_map=sat_map,
        receivers=destination_users,
        group_id=group_id,
        t=t,
        igmp_state=igmp_state,
        beam_assignment_res1=beam_assignment,
        beam_resolution=dest_resolution,
        parent_resolution=plan_resolution,
    )

    # ------------------------------------------------------------------
    # Step 4) Choose ingress satellite (prefer beam owner, else nearest)
    # ------------------------------------------------------------------
    ingress_sat = None
    if forced_ingress_sat_id is not None:
        ingress_sat = sat_map.get(int(forced_ingress_sat_id))
        if ingress_sat is None:
            raise ValueError(
                f"forced_ingress_sat_id={forced_ingress_sat_id} is not in sat_map."
            )
    else:
        owner_id = beam_assignment.get(source_cell_dest, None)
        if owner_id is not None and owner_id in sat_map:
            ingress_sat = sat_map[owner_id]
        else:
            ingress_sat, _ = utils.find_nearest_satellite(source_user, sat_map, t)

    if not ingress_sat:
        print("[H3-BIER] ERROR: No ingress satellite found for source user.")
        return None, None

    # Root of the forwarding plan = ingress satellite's current planning cell
    source_cell_plan = _geo_to_cell(
        float(ingress_sat.latitude[t - 1]),
        float(ingress_sat.longitude[t - 1]),
        plan_resolution,
    )
    print(f"[H3-BIER] Ingress satellite: {ingress_sat.id}  "
          f"(plan cell res={plan_resolution}: {source_cell_plan})")

    # ------------------------------------------------------------------
    # Step 5) Collect egress satellites from IGMP state
    # ------------------------------------------------------------------
    egress_sat_ids = []
    for sat_id, groups in igmp_state.items():
        beam_map      = groups.get(group_id, {})
        total_members = sum(len(members) for members in beam_map.values())
        if total_members > 0:
            egress_sat_ids.append(sat_id)

    egress_sat_ids = sorted(set(egress_sat_ids))
    print(f"[H3-BIER] Egress satellites ({len(egress_sat_ids)}): {egress_sat_ids}")
    if not egress_sat_ids:
        print("[H3-BIER] ERROR: No egress satellites joined the multicast group.")
        return None, None

    # ------------------------------------------------------------------
    # Step 6) Build multicast forwarding plan (SPT → H3 cell tree)
    # ------------------------------------------------------------------
    multicast_cell_tree = _build_multicast_cell_tree(
        G=G,
        sat_map=sat_map,
        ingress_sat=ingress_sat,
        egress_sat_ids=egress_sat_ids,
        t=t,
        forwarding_res=plan_resolution,
    )

    # Ensure the root cell is always present
    multicast_cell_tree.add_node(source_cell_plan)

    if not multicast_cell_tree.nodes:
        print("[H3-BIER] ERROR: MSPT-based cell plan is empty.")
        return None, None

    routing_tree_dict = convert_graph_to_dict_tree(multicast_cell_tree, source_cell_plan)

    # ------------------------------------------------------------------
    # Step 7) Load binary codebook + build packet header
    # ------------------------------------------------------------------
    codebook = _load_codebook()

    packet = _make_packet(
        source_cell_plan=source_cell_plan,
        routing_tree_dict_plan=routing_tree_dict,
        destination_cells_dest=destination_cells_dest,
        destination_users=destination_users,
        group_id=group_id,
        forwarding_resolution=plan_resolution,
        destination_resolution=dest_resolution,
        codebook=codebook,
    )

    print("\n--- H3-BIER Packet Header (BINARY cell ids) ---")
    print(f"  Group ID:           {packet.header.get('group_id')}")
    print(f"  Forwarding res:     {packet.header['forwarding_resolution']}")
    print(f"  Destination res:    {packet.header['destination_resolution']}")
    print(f"  Source cell (bin):  {packet.header['source_cell']}")
    print(f"  #Forwarding cells:  {packet.forwarding_cell_count()}")
    print(f"  #Destination cells: {len(packet.header['destination_cells'])}")
    print(f"  Identifier bits:    {packet.header_bit_length()}")
    print("-------------------------------------\n")

    # ------------------------------------------------------------------
    # Step 8) Forward through constellation (x-hop lookahead) + IGMP last hop
    # ------------------------------------------------------------------
    forwarding_trace_tree = forward_packet(
        G=G,
        sat_map=sat_map,
        current_sat=ingress_sat,
        packet=packet,
        t=t,
        igmp_state=igmp_state,
    )

    print("\n--- H3-BIER Simulation Complete ---")

    # ------------------------------------------------------------------
    # Step 9) Visualise
    # ------------------------------------------------------------------
    altitudes    = [sat.altitude[t - 1] for sat in sat_map.values()]
    avg_altitude = sum(altitudes) / len(altitudes) if altitudes else 0
    h3_altitude_km = 550 if avg_altitude < 1000 else 1200

    if enable_visualization:
        bier_visualizer.plot_h3_bier_trace(
            sh=sh,
            t=t,
            sat_map=sat_map,
            G=G,
            source_cell_res1=source_cell_dest,
            destination_cells_res1=destination_cells_dest,
            multicast_cell_mst_res0=multicast_cell_tree,
            forwarding_trace_tree=forwarding_trace_tree,
            h3_altitude_km=h3_altitude_km,
        )

    return packet, forwarding_trace_tree
