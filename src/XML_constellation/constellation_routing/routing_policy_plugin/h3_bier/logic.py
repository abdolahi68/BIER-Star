# h3_bier/logic.py


import networkx as nx
import h3

from .packet import H3BIERPacket
from .igmp import deliver_igmp_in_cell, get_delivering_sats_in_cell


# -------------------------
# h3-py v3 / v4 compat helpers
# -------------------------
def _geo_to_h3(lat: float, lon: float, res: int) -> str:
    """Convert lat/lon to an H3 cell index — works on h3-py v3 and v4."""
    if hasattr(h3, "latlng_to_cell"):   # v4.x
        return h3.latlng_to_cell(lat, lon, res)
    return h3.geo_to_h3(lat, lon, res)  # v3.x


def _cell_to_parent_compat(cell: str, parent_res: int) -> str:
    """Return the parent cell — works on h3-py v3 and v4."""
    if hasattr(h3, "cell_to_parent"):   # v4.x
        return h3.cell_to_parent(cell, parent_res)
    return h3.h3_to_parent(cell, parent_res)  # v3.x


def _get_resolution(cell: str) -> int:
    """Return the resolution of an H3 cell — works on h3-py v3 and v4."""
    if hasattr(h3, "get_resolution"):      # v4.x
        return h3.get_resolution(cell)
    return h3.h3_get_resolution(cell)      # v3.x


# -------------------------
# Forwarding trace node
# -------------------------
class ForwardingTrace:
    def __init__(self, addr, branches=None):
        self.addr     = addr
        self.branches = branches if branches is not None else []

    def add_branch(self, trace_node):
        self.branches.append(trace_node)


# -------------------------
# Internal helpers
# -------------------------
def _node_of(sat_id: int) -> str:
    return f"satellite_{sat_id}"


def _id_of(node: str) -> int:
    return int(node.split("_")[1])


def _sat_cell_at(sat_map, sat_id: int, t: int, res: int) -> str:

    s = sat_map[sat_id]
    return _geo_to_h3(float(s.latitude[t - 1]), float(s.longitude[t - 1]), res)


def _cell_center_latlon(cell: str):
    if hasattr(h3, "h3_to_geo"):
        return h3.h3_to_geo(cell)       # (lat, lon) — h3-py v3
    return h3.cell_to_latlng(cell)      # (lat, lon) — h3-py v4


def _haversine_km(lat1, lon1, lat2, lon2):
    from math import radians, cos, sin, asin, sqrt
    lon1, lat1, lon2, lat2 = map(radians, [lon1, lat1, lon2, lat2])
    dlon = lon2 - lon1
    dlat = lat2 - lat1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    c = 2 * asin(sqrt(a))
    return 6371 * c


def _choose_next_hop_xhop(
    G, sat_map, current_id: int, target_cell: str, t: int, res: int, lookahead_hops: int = 2
):

    cur_node  = _node_of(current_id)
    nbr_nodes = list(G.neighbors(cur_node))
    if not nbr_nodes:
        return None

    # 1-hop: direct neighbour in target cell
    best_1hop = None
    best_w    = float("inf")
    for n in nbr_nodes:
        nid = _id_of(n)
        if _sat_cell_at(sat_map, nid, t, res) == target_cell:
            w = G[cur_node][n].get("weight", 1.0)
            if w < best_w:
                best_w    = w
                best_1hop = nid
    if best_1hop is not None:
        return best_1hop

    if lookahead_hops < 2:
        return None

    # 2-hop: evaluate neighbours by best distance reachable in 2 hops
    tgt_lat, tgt_lon = _cell_center_latlon(target_cell)
    best_neighbor = None
    best_score    = float("inf")

    for n1 in nbr_nodes:
        n1_id = _id_of(n1)
        w1    = G[cur_node][n1].get("weight", 1.0)

        try:
            n2_nodes = list(G.neighbors(n1))
        except Exception:
            continue

        local_best = float("inf")
        for n2 in n2_nodes:
            n2_id = _id_of(n2)
            s2    = sat_map[n2_id]
            d     = _haversine_km(float(s2.latitude[t - 1]), float(s2.longitude[t - 1]),
                                  tgt_lat, tgt_lon)
            if d < local_best:
                local_best = d

        score = local_best + 10.0 * w1
        if score < best_score:
            best_score    = score
            best_neighbor = n1_id

    return best_neighbor


def _walk_to_target_cell_xhop(
    G,
    sat_map,
    start_id: int,
    target_cell: str,
    t: int,
    res: int,
    preferred_terminal_ids=None,
    hop_limit: int = 60,
):

    if preferred_terminal_ids:
        preferred_terminal_ids = set(preferred_terminal_ids)

    path    = [start_id]
    visited = {start_id}

    def reached(sid: int) -> bool:
        if preferred_terminal_ids:
            return sid in preferred_terminal_ids
        return _sat_cell_at(sat_map, sid, t, res) == target_cell

    if reached(start_id):
        return path

    for _ in range(hop_limit):
        cur = path[-1]
        nxt = _choose_next_hop_xhop(G, sat_map, cur, target_cell, t, res, lookahead_hops=2)
        if nxt is None or nxt in visited:
            break
        path.append(nxt)
        visited.add(nxt)
        if reached(nxt):
            return path

    # Dijkstra fallback
    if preferred_terminal_ids:
        candidates = list(preferred_terminal_ids)
    else:
        candidates = [
            s.id for s in sat_map.values()
            if _geo_to_h3(float(s.latitude[t - 1]), float(s.longitude[t - 1]), res) == target_cell
        ]

    if not candidates:
        return path

    best_path = None
    best_cost = float("inf")
    for cid in candidates:
        try:
            p = nx.dijkstra_path(G, _node_of(start_id), _node_of(cid), weight="weight")
            c = _calculate_path_cost(G, p, "weight")
            if c < best_cost:
                best_cost = c
                best_path = [_id_of(n) for n in p]
        except Exception:
            continue

    return best_path if best_path else path


def build_trace_from_path(path_of_ids):
    if not path_of_ids:
        return None, None
    head         = ForwardingTrace(path_of_ids[0])
    current_node = head
    for sat_id in path_of_ids[1:]:
        new_node = ForwardingTrace(sat_id)
        current_node.add_branch(new_node)
        current_node = new_node
    return head, current_node


def _calculate_path_cost(G, path, weight_attribute):
    cost = 0
    for i in range(len(path) - 1):
        try:
            cost += G[path[i]][path[i + 1]][weight_attribute]
        except KeyError:
            return float("inf")
    return cost


def _select_best_sat_in_cell(
    G, sat_map, current_sat, target_cell, t, cell_resolution, preferred_sat_ids=None
):
    """
    Choose the satellite in target_cell (at time t) that is reachable from
    current_sat with the lowest Dijkstra path cost.

    """
    candidates_all = [
        s for s in sat_map.values()
        if _geo_to_h3(float(s.latitude[t - 1]), float(s.longitude[t - 1]), cell_resolution) == target_cell
    ]
    if not candidates_all:
        return None, None

    candidates = candidates_all
    if preferred_sat_ids:
        filtered = [s for s in candidates_all if s.id in preferred_sat_ids]
        if filtered:
            candidates = filtered

    best_sat  = None
    best_path = None
    best_cost = float("inf")
    src_node  = f"satellite_{current_sat.id}"

    for cand in candidates:
        if cand.id == current_sat.id:
            return cand, [src_node]

        dst_node = f"satellite_{cand.id}"
        try:
            path = nx.dijkstra_path(G, src_node, dst_node, weight="weight")
            cost = _calculate_path_cost(G, path, "weight")
            if cost < best_cost:
                best_cost = cost
                best_sat  = cand
                best_path = path
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue

    return best_sat, best_path


def forward_packet(G, sat_map, current_sat, packet: H3BIERPacket, t: int, igmp_state):
    """
    Cell-based forwarding + packet replication (branching).
    IGMP is used only at the last cell (satellite -> ground users).

    Uses x-hop lookahead to move toward each target forwarding cell and
    prefers IGMP-registered satellites when entering a destination cell.
    """
    print(f"[H3-BIER] forward_packet at satellite {current_sat.id}")
    packet.add_to_trace(current_sat.id)
    trace_node = ForwardingTrace(current_sat.id)

    fwd_res = packet.forwarding_resolution

    
    current_cell = _geo_to_h3(
        float(current_sat.latitude[t - 1]),
        float(current_sat.longitude[t - 1]),
        fwd_res,
    )

    # Map each destination cell to its ancestor at the forwarding resolution
    destination_parents = set()
    for c in packet.destination_cells_h3:
        c_res = _get_resolution(c)
        if c_res > fwd_res:
            destination_parents.add(_cell_to_parent_compat(c, fwd_res))
        else:
            destination_parents.add(c)

    next_cells = packet.get_next_target_cells()   # H3 hex ids at forwarding resolution

    # ------------------------------------------------------------------
    # LAST CELL: IGMP delivery only
    # ------------------------------------------------------------------
    if not next_cells:
        if current_cell in destination_parents:
            group_id = packet.header.get("group_id", None)
            if group_id is None:
                print("[IGMP] WARNING: packet has no group_id — skipping delivery.")
                return trace_node

            deliver_igmp_in_cell(
                sat_map=sat_map,
                igmp_state=igmp_state,
                cell_res0=current_cell,
                group_id=group_id,
                t=t,
            )

            # Visualisation: add trace branches to all delivering sats in this cell
            delivering     = get_delivering_sats_in_cell(
                sat_map=sat_map,
                igmp_state=igmp_state,
                cell_res0=current_cell,
                group_id=group_id,
                t=t,
            )
            delivering_sat_ids = list(delivering.keys())

            for egress_sat_id in delivering_sat_ids:
                if egress_sat_id == current_sat.id:
                    continue
                try:
                    p    = nx.dijkstra_path(
                        G,
                        f"satellite_{current_sat.id}",
                        f"satellite_{egress_sat_id}",
                        weight="weight",
                    )
                    ids  = [int(x.split("_")[1]) for x in p]
                    head, _ = build_trace_from_path(ids[1:])
                    if head is not None:
                        trace_node.add_branch(head)
                    else:
                        trace_node.add_branch(ForwardingTrace(egress_sat_id))
                except Exception:
                    continue
        else:
            print("[H3-BIER] Leaf reached but current cell is not a destination cell.")

        return trace_node

    # ------------------------------------------------------------------
    # Forward / replicate per branch
    # ------------------------------------------------------------------
    if len(next_cells) > 1:
        packet.record_replication(current_sat.id)

    for target_cell in next_cells:
        # 1) Replicate packet for this branch (prune routing tree)
        branch_packet = packet.create_replicated_packet_for_branch(target_cell)
        if branch_packet is None:
            continue

        # 2) Prefer IGMP-member sats when entering a destination cell
        preferred_terminal_ids = None
        if target_cell in destination_parents:
            group_id = branch_packet.header.get("group_id", None)
            if group_id is not None:
                delivering = get_delivering_sats_in_cell(
                    sat_map=sat_map,
                    igmp_state=igmp_state,
                    cell_res0=target_cell,
                    group_id=group_id,
                    t=t,
                )
                preferred_terminal_ids = set(delivering.keys()) if delivering else None

        # 3) x-hop walk toward the target cell (or preferred terminal satellite)
        sat_path_ids = _walk_to_target_cell_xhop(
            G=G,
            sat_map=sat_map,
            start_id=current_sat.id,
            target_cell=target_cell,
            t=t,
            res=fwd_res,
            preferred_terminal_ids=preferred_terminal_ids,
            hop_limit=60,
        )

        reached_id  = sat_path_ids[-1]
        reached_sat = sat_map[reached_id]

        print(f"[H3-BIER] Branch -> cell {target_cell}: physical path {sat_path_ids}")

        # 4) Build trace for the physical hops up to reached_sat
        physical_head, physical_tail = build_trace_from_path(sat_path_ids[1:])

        # 5) Recurse from reached satellite
        future_trace = forward_packet(
            G=G,
            sat_map=sat_map,
            current_sat=reached_sat,
            packet=branch_packet,
            t=t,
            igmp_state=igmp_state,
        )

        # 6) Attach traces
        if physical_head is not None:
            physical_tail.add_branch(future_trace)
            trace_node.add_branch(physical_head)
        else:
            trace_node.add_branch(future_trace)

    return trace_node
