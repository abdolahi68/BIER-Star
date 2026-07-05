# BIER_TE/bier_te_logic.py
# BIER-TE note:
# In this experiment, terminal_to_satellite includes only the active destination
# airplanes selected for the current B case. Therefore, the BIER-TE header size
# is E_ISL + 2*T_dst, where T_dst is the number of destination airplanes.
# Non-destination airplanes and their satellite-terminal links are not included
# in the BIER-TE encoding domain for this experiment.

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

import networkx as nx


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class Tree:
    def __init__(self, addr, branches, is_leaf=False, is_rcv=False):
        self.addr     = addr
        self.branches = branches
        self.is_leaf  = is_leaf
        self.is_rcv   = is_rcv

    def get_addr(self):       return self.addr
    def set_is_leaf(self, v): self.is_leaf = v
    def set_is_rcv(self, v):  self.is_rcv  = v
    def n_branch(self):       return len(self.branches)
    def add_branch(self, t):  self.branches.append(t)


class Packet:
    def __init__(
        self,
        bit_string: int,
        payload: Any,
        header_bitstring_length: int = 0,
        used_bitstring_length: int = 0,
        bitstring_binary: str = "",
    ):
        self.bit_string               = int(bit_string)
        self.payload                  = payload
        self.header_bitstring_length  = int(header_bitstring_length)
        self.used_bitstring_length    = int(used_bitstring_length)
        self.bitstring_binary         = str(bitstring_binary)

        self.used_isl_bits_count       = 0
        self.used_sat_ter_bits_count   = 0
        self.used_ter_decap_bits_count = 0
        self.used_sat_decap_bits_count = 0
        self.used_total_set_bits_count = 0
        self.tree_edges: List[Tuple[str, str]] = []
        self.destination_terminals: List[str]  = []
        self.destination_satellites: List[str] = []

    def copy_payload(self):
        return copy.deepcopy(self.payload)


@dataclass(frozen=True)
class Adjacency:
    kind:      str              # "forward_connected" | "local_decap"
    target:    Optional[str] = None
    bit_index: int           = 0
    name:      str           = ""


def get_tree_size(tree) -> int:
    if tree is None:
        return 0
    return 1 + sum(get_tree_size(b) for b in tree.branches)


# ---------------------------------------------------------------------------
# Step 1 — build topology graph and forwarding tables
# ---------------------------------------------------------------------------

def build_te_tables(
    graph: nx.Graph,
    terminal_to_satellite: Dict[str, str],
    satellites_are_destinations: bool = False,
    unique_terminal_decap: bool = True,
) -> nx.Graph:
    """
    Add terminal nodes and satellite-terminal edges to the graph, assign BPs
    to all adjacencies, and build each node's BIFT.
    """
    print("[BIER-TE] Building topology and forwarding tables...")
    G = graph.copy()

    sorted_terminals  = sorted(terminal_to_satellite.keys())
    sorted_satellites = sorted(str(n) for n in graph.nodes())

    # ------------------------------------------------------------------
    # 1a: validate terminal→satellite references
    # ------------------------------------------------------------------
    for terminal_id, sat_node in terminal_to_satellite.items():
        if str(sat_node) not in G.nodes():
            raise ValueError(
                f"terminal_to_satellite: satellite '{sat_node}' for terminal "
                f"'{terminal_id}' is not in the graph."
            )

    # ------------------------------------------------------------------
    # 1b: add terminal nodes and satellite-terminal edges
    # ------------------------------------------------------------------
    for terminal_id, sat_node in terminal_to_satellite.items():
        G.add_node(terminal_id, node_type="terminal")
        G.add_edge(str(sat_node), terminal_id,
                   link_type="terminal_link", weight=1)

    # Separate ISL edges from satellite-terminal edges
    isl_edges = sorted(
        tuple(sorted((str(u), str(v))))
        for u, v in graph.edges()
    )
    sat_ter_edges = sorted(
        tuple(sorted((str(sat_node), terminal_id)))
        for terminal_id, sat_node in terminal_to_satellite.items()
    )
    all_edges = isl_edges + [e for e in sat_ter_edges if e not in isl_edges]

    E_ISL = len(isl_edges)
    T     = len(sorted_terminals)
    N     = len(sorted_satellites)

    next_bit = 0

    # ------------------------------------------------------------------
    # 1c: forwarding BPs — one per edge (ISL first, then sat-terminal)
    # ------------------------------------------------------------------
    edge_bit_index: Dict[Tuple[str, str], int] = {}
    edge_bit_value: Dict[Tuple[str, str], int] = {}
    for edge in all_edges:
        edge_bit_index[edge] = next_bit
        edge_bit_value[edge] = 1 << next_bit
        next_bit += 1

    # ------------------------------------------------------------------
    # 1d: local_decap() BPs for terminals
    # ------------------------------------------------------------------
    terminal_decap_bit_index: Dict[str, int] = {}
    terminal_decap_bit_value: Dict[str, int] = {}

    if unique_terminal_decap:
        # One unique BP per terminal  →  header grows by T
        for terminal_id in sorted_terminals:
            terminal_decap_bit_index[terminal_id] = next_bit
            terminal_decap_bit_value[terminal_id] = 1 << next_bit
            next_bit += 1
    else:
        # One shared BP for all leaf terminals (RFC leaf-BFER optimization).
        # Allocate the shared BP only when at least one terminal exists.
        if sorted_terminals:
            shared_bit_index = next_bit
            shared_bit_value = 1 << next_bit
            next_bit += 1
            for terminal_id in sorted_terminals:
                terminal_decap_bit_index[terminal_id] = shared_bit_index
                terminal_decap_bit_value[terminal_id] = shared_bit_value

    # ------------------------------------------------------------------
    # 1e: local_decap() BPs for satellites (only if they are destinations)
    # ------------------------------------------------------------------
    sat_decap_bit_index: Dict[str, int] = {}
    sat_decap_bit_value: Dict[str, int] = {}
    if satellites_are_destinations:
        for sat_node in sorted_satellites:
            sat_decap_bit_index[sat_node] = next_bit
            sat_decap_bit_value[sat_node] = 1 << next_bit
            next_bit += 1

    header_bitstring_length = next_bit

    # ------------------------------------------------------------------
    # 1f: build per-node BIFT
    # ------------------------------------------------------------------
    for node in G.nodes():
        node_str  = str(node)
        node_type = G.nodes[node].get("node_type", "satellite")
        bift: Dict[int, List[Adjacency]] = {}
        adjacent_mask = 0

        # Forwarding adjacency for every neighbour
        for nbr in G.neighbors(node):
            edge    = tuple(sorted((node_str, str(nbr))))
            bit_val = edge_bit_value[edge]
            bift.setdefault(bit_val, []).append(
                Adjacency(
                    kind="forward_connected",
                    target=str(nbr),
                    bit_index=edge_bit_index[edge],
                    name=f"{node_str}->{nbr}",
                )
            )
            adjacent_mask |= bit_val

        # local_decap() for terminals
        if node_str in terminal_decap_bit_index:
            t_bit_val = terminal_decap_bit_value[node_str]
            bift.setdefault(t_bit_val, []).append(
                Adjacency(
                    kind="local_decap",
                    target=node_str,
                    bit_index=terminal_decap_bit_index[node_str],
                    name=f"local_decap@{node_str}",
                )
            )
            adjacent_mask |= t_bit_val

        # local_decap() for satellites (only if satellites are destinations)
        if satellites_are_destinations and node_str in sat_decap_bit_index:
            s_bit_val = sat_decap_bit_value[node_str]
            bift.setdefault(s_bit_val, []).append(
                Adjacency(
                    kind="local_decap",
                    target=node_str,
                    bit_index=sat_decap_bit_index[node_str],
                    name=f"local_decap@{node_str}",
                )
            )
            adjacent_mask |= s_bit_val

        G.nodes[node]["BIFT"]                   = bift
        G.nodes[node]["AdjacentBitsMask"]        = adjacent_mask
        G.nodes[node]["node_type"]               = node_type
        G.nodes[node]["header_bitstring_length"] = header_bitstring_length

    # Graph-level lookup tables
    G.graph["edge_bit_index"]               = edge_bit_index
    G.graph["edge_bit_value"]               = edge_bit_value
    G.graph["terminal_decap_bit_index"]     = terminal_decap_bit_index
    G.graph["terminal_decap_bit_value"]     = terminal_decap_bit_value
    G.graph["sat_decap_bit_index"]          = sat_decap_bit_index
    G.graph["sat_decap_bit_value"]          = sat_decap_bit_value
    G.graph["header_bitstring_length"]      = header_bitstring_length
    G.graph["terminal_to_satellite"]        = dict(terminal_to_satellite)
    G.graph["isl_edges"]                    = isl_edges
    G.graph["sat_ter_edges"]               = sat_ter_edges
    G.graph["satellites_are_destinations"]  = satellites_are_destinations
    G.graph["unique_terminal_decap"]        = unique_terminal_decap
    G.graph["num_isl_edges"]                = E_ISL
    G.graph["num_terminals"]                = T
    G.graph["num_satellites"]               = N

    formula_terms = [f"E_ISL({E_ISL})"]
    if unique_terminal_decap:
        formula_terms.append(f"2*T({T})")
    elif T > 0:
        formula_terms.extend([f"T({T})", "1(shared terminal local_decap)"])
    if satellites_are_destinations:
        formula_terms.append(f"N({N})")
    formula = " + ".join(formula_terms) + f" = {header_bitstring_length}"
    print(f"[BIER-TE] ISL edges  (E_ISL) = {E_ISL}")
    print(f"[BIER-TE] Terminals      (T) = {T}")
    print(f"[BIER-TE] Satellites     (N) = {N}")
    print(f"[BIER-TE] Header bits        = {formula}")
    return G


# ---------------------------------------------------------------------------
# Step 2 — encapsulation at ingress satellite
# ---------------------------------------------------------------------------

def _build_shortest_path_tree_edges(
    graph: nx.Graph,
    src: str,
    dests: Iterable[str],
) -> Set[Tuple[str, str]]:
    """Union of Dijkstra shortest-path edges from src to every destination."""
    tree_edges: Set[Tuple[str, str]] = set()
    for dst in dests:
        if dst == src:
            continue
        try:
            path = nx.shortest_path(graph, source=src, target=dst, weight="weight")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue
        for u, v in zip(path, path[1:]):
            tree_edges.add(tuple(sorted((str(u), str(v)))))
    return tree_edges


def encapsulate_te_pkt(
    graph: nx.Graph,
    ingress_node: str,
    pkt: Any,
    dst_satellites: Iterable[str] = (),   # FIX 2: restored original argument order
    dst_terminals: Iterable[str]  = (),
    print_info: bool = False,
) -> Packet:

    dst_satellites = sorted(set(str(d) for d in dst_satellites))
    dst_terminals  = sorted(set(str(d) for d in dst_terminals))

    # FIX 3: raise immediately if satellite destinations are requested but
    # satellite local_decap() bits were not allocated.
    if dst_satellites and not graph.graph.get("satellites_are_destinations", False):
        raise ValueError(
            "dst_satellites were provided, but satellite local_decap() bits "
            "were not enabled in build_te_tables(). "
            "Rebuild with satellites_are_destinations=True."
        )

    all_dsts   = dst_terminals + dst_satellites
    tree_edges = _build_shortest_path_tree_edges(graph, str(ingress_node), all_dsts)

    edge_bit_value           = graph.graph["edge_bit_value"]
    terminal_decap_bit_value = graph.graph["terminal_decap_bit_value"]
    sat_decap_bit_value      = graph.graph["sat_decap_bit_value"]
    isl_edges_set            = set(map(tuple, graph.graph["isl_edges"]))
    header_bitstring_length  = int(graph.graph["header_bitstring_length"])
    unique_decap             = graph.graph.get("unique_terminal_decap", True)

    bit_string = 0

    # Set forwarding BPs for all tree edges
    used_isl_edges:     List[Tuple[str, str]] = []
    used_sat_ter_edges: List[Tuple[str, str]] = []
    for edge in tree_edges:
        bit_string |= edge_bit_value[edge]
        (used_isl_edges if edge in isl_edges_set else used_sat_ter_edges).append(edge)

    # Set terminal local_decap() BPs
    used_ter_decaps: List[str] = []
    if unique_decap:
        for terminal_id in dst_terminals:
            if terminal_id in terminal_decap_bit_value:
                bit_string |= terminal_decap_bit_value[terminal_id]
                used_ter_decaps.append(terminal_id)
    else:
        # Shared BP: set it once if there is at least one destination terminal
        if dst_terminals:
            shared_val = next(iter(terminal_decap_bit_value.values()))
            bit_string |= shared_val
            used_ter_decaps = ["shared_local_decap"]

    # Set satellite local_decap() BPs
    used_sat_decaps: List[str] = []
    for sat_node in dst_satellites:
        if sat_node in sat_decap_bit_value:
            bit_string |= sat_decap_bit_value[sat_node]
            used_sat_decaps.append(sat_node)

    used_bitstring_length = bit_string.bit_length() if bit_string else 0
    bitstring_binary = (
        format(bit_string, f"0{header_bitstring_length}b")
        if header_bitstring_length > 0 else "0"
    )

    packet = Packet(
        bit_string=bit_string,
        payload=pkt,
        header_bitstring_length=header_bitstring_length,
        used_bitstring_length=used_bitstring_length,
        bitstring_binary=bitstring_binary,
    )
    packet.used_isl_bits_count       = len(used_isl_edges)
    packet.used_sat_ter_bits_count   = len(used_sat_ter_edges)
    packet.used_ter_decap_bits_count = len(used_ter_decaps)
    packet.used_sat_decap_bits_count = len(used_sat_decaps)
    packet.used_total_set_bits_count = bin(bit_string).count("1")
    packet.tree_edges                = sorted(tree_edges)
    packet.destination_terminals     = dst_terminals
    packet.destination_satellites    = dst_satellites

    if print_info:
        print("\n[BIER-TE] Bitstring:")
        print(f"  {bitstring_binary}")
        print(f"  Header length              = {header_bitstring_length}")
        print(f"  ISL forwarding bits used   = {packet.used_isl_bits_count}")
        print(f"  Sat-terminal bits used     = {packet.used_sat_ter_bits_count}")
        print(f"  Terminal local_decap used  = {packet.used_ter_decap_bits_count}")
        print(f"  Satellite local_decap used = {packet.used_sat_decap_bits_count}")
        print(f"  Total set bits             = {packet.used_total_set_bits_count}")

    return packet


# ---------------------------------------------------------------------------
# Step 3 — forwarding simulation
# ---------------------------------------------------------------------------

def ingress_process_te(
    graph: nx.Graph,
    ingress_node: str,
    pkt: Any,
    dst_satellites: Iterable[str] = (),   # FIX 2: restored original argument order
    dst_terminals: Iterable[str]  = (),
    print_info: bool = False,
    print_tree: bool = False,
    return_tree: bool = False,
    return_packet: bool = False,
) -> Any:
    """
    Encapsulate and simulate BIER-TE forwarding from ingress_node.

    Argument order matches the previous version to avoid breaking
    existing callers that use positional arguments.
    """
    dst_satellites = sorted(set(str(d) for d in dst_satellites))
    dst_terminals  = sorted(set(str(d) for d in dst_terminals))

    packet     = encapsulate_te_pkt(
        graph, ingress_node, pkt,
        dst_satellites, dst_terminals, print_info,
    )
    whole_tree = process_te_packet(graph, str(ingress_node), packet, print_info)

    if print_tree:
        print("\n[BIER-TE] Forwarding tree:")
        printTree(whole_tree)

    if return_tree and return_packet:
        return whole_tree, packet
    if return_tree:
        return whole_tree
    if return_packet:
        return packet
    return None


def process_te_packet(
    graph: nx.Graph,
    curr_node: str,
    packet: Packet,
    print_info: bool = False,
) -> Tree:
    """
    Process a BIER-TE packet at curr_node (satellite or terminal).

    For each bit set that belongs to this node:
      forward_connected  →  strip the bit, send packet copy to the neighbour
      local_decap        →  deliver here

    Each BP is consumed exactly once at the node that owns it.
    """
    tree = Tree(curr_node, [])

    if packet.bit_string == 0:
        tree.set_is_leaf(True)
        return tree

    node_data      = graph.nodes[curr_node]
    adjacent_mask  = int(node_data.get("AdjacentBitsMask", 0))
    pkt_local_bits = packet.bit_string & adjacent_mask

    if pkt_local_bits == 0:
        tree.set_is_leaf(True)
        return tree

    remaining_bits = packet.bit_string & ~adjacent_mask
    bift: Dict[int, List[Adjacency]] = node_data.get("BIFT", {})

    for bit_val, adjacencies in bift.items():
        if not (pkt_local_bits & bit_val):
            continue

        for adj in adjacencies:
            fwd_packet = Packet(
                bit_string=remaining_bits,
                payload=packet.copy_payload(),
                header_bitstring_length=packet.header_bitstring_length,
                used_bitstring_length=(
                    remaining_bits.bit_length() if remaining_bits else 0
                ),
                bitstring_binary=(
                    format(remaining_bits, f"0{packet.header_bitstring_length}b")
                    if packet.header_bitstring_length > 0 else "0"
                ),
            )

            if adj.kind == "local_decap":
                tree.set_is_rcv(True)
                if print_info:
                    print(f"[BIER-TE] local_decap() at {curr_node}"
                          f"  →  delivered to {adj.target}")

            elif adj.kind == "forward_connected" and adj.target:
                branch = process_te_packet(graph, adj.target, fwd_packet, print_info)
                tree.add_branch(branch)

    if tree.n_branch() == 0:
        tree.set_is_leaf(True)
    return tree


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def get_paths_from_bier_tree(tree_root) -> List[List[str]]:
    """DFS traversal — returns all root-to-leaf paths as lists of node IDs."""
    if not tree_root:
        return []
    all_paths: List[List[str]] = []

    def dfs(node, path: List[str]):
        path.append(str(node.addr))
        if not node.branches:
            all_paths.append(list(path))
        else:
            for branch in node.branches:
                dfs(branch, list(path))

    dfs(tree_root, [])
    return all_paths


def printTree(root, markerStr="└──", levelMarkers=None):
    if levelMarkers is None:
        levelMarkers = []
    emptyStr      = " " * len(markerStr)
    connectionStr = "|" + emptyStr[:-1]
    markers       = "".join(connectionStr if d else emptyStr for d in levelMarkers[:-1])
    markers      += markerStr if levelMarkers else ""
    print(f"{markers}{root.addr}")
    branches = list(reversed(root.branches))
    for i, child in enumerate(branches):
        printTree(child, markerStr, [*levelMarkers, i != len(branches) - 1])
