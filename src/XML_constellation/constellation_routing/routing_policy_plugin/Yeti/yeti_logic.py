from __future__ import annotations

# YETI counts satellite ISL links as router interfaces.
# It also adds one access interface for each selected destination airplane.
# Airplanes that are not destinations are not included in the YETI topology.
# This avoids beam-level over-delivery, where non-destination airplanes in the
# same beam/cell could also receive duplicate multicast packets.



import copy
from math import ceil, log2
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple, Union

import networkx as nx


class Tree:
    def __init__(self, addr, branches, is_leaf=False, is_rcv=False):
        self.addr = addr
        self.branches = branches
        self.is_leaf = is_leaf
        self.is_rcv = is_rcv

    def get_addr(self):
        return self.addr

    def set_is_leaf(self, val):
        self.is_leaf = val

    def set_is_rcv(self, val):
        self.is_rcv = val

    def n_branch(self):
        return len(self.branches)

    def add_branch(self, tree):
        self.branches.append(tree)


class FSPLabel:
    def __init__(self, target_router_id: str, hop_count: int = 0, process_here: bool = False):
        self.target_router_id = target_router_id
        self.hop_count = hop_count
        self.process_here = process_here


class FTELabel:
    def __init__(self, interface_id: int, target_neighbor: Optional[str] = None):
        self.interface_id = interface_id
        self.target_neighbor = target_neighbor


class MCTLabel:
    def __init__(
        self,
        interface_ids: Tuple[int, ...],
        interface_bitmap: int,
        branch_count: int,
        has_cpy: bool = True,
    ):
        self.interface_ids = interface_ids
        self.interface_bitmap = interface_bitmap
        self.branch_count = branch_count
        self.has_cpy = has_cpy


class CPYLabel:
    def __init__(self, label_count: int, offset_bits: int):
        self.label_count = label_count
        self.offset_bits = offset_bits


YetiLabel = Union[FSPLabel, FTELabel, MCTLabel, CPYLabel]


class YetiPacket:
    def __init__(self, labels: Sequence[YetiLabel], payload: Any):
        self.labels: List[YetiLabel] = list(labels)
        self.payload = payload

        self.label_stack_strings: List[str] = []
        self.label_count: int = len(self.labels)
        self.estimated_label_bits: int = 0
        self.estimated_label_bytes: int = 0

        self.num_fsp: int = 0
        self.num_fte: int = 0
        self.num_mct: int = 0
        self.num_cpy: int = 0

        self.fsp_hops_covered: int = 0
        self.fsp_label_savings: int = 0
        self.copy_operations: int = 0

        self.tree_edges: List[Tuple[str, str]] = []
        self.destination_nodes: List[str] = []

    def copy_payload(self):
        return copy.deepcopy(self.payload)


def get_tree_size(tree):
    if tree is None:
        return 0
    size = 1
    for branch in tree.branches:
        size += get_tree_size(branch)
    return size


def _safe_log2_ceil(value: int) -> int:
    if value <= 1:
        return 0
    return int(ceil(log2(value)))


def _sat_id_from_node(node_or_sat: str | int) -> int:
    if isinstance(node_or_sat, int):
        return int(node_or_sat)
    return int(str(node_or_sat).split("_")[-1])


def _sat_node(sat_id: int) -> str:
    return f"satellite_{int(sat_id)}"


def _sat_sort_key(node: str | int) -> int:
    try:
        return _sat_id_from_node(node)
    except Exception:
        return 10**12


# Keep satellite, beam, and airplane nodes in a stable order.
def _node_sort_key(node: str | int) -> Tuple[int, Union[int, str]]:
    node_str = str(node)
    if node_str.startswith("satellite_"):
        try:
            return (0, _sat_id_from_node(node_str))
        except Exception:
            return (0, node_str)
    if node_str.startswith("beam_"):
        return (1, node_str)
    if node_str.startswith("airplane_"):
        return (2, node_str)
    return (3, node_str)


_RESERVED_BEAM_PREFIX = "__reserved_beam_port_"
_RESERVED_ISL_PREFIX = "__reserved_isl_port_"
_ACCESS_ENDPOINT_NODE_TYPES = {"beam_endpoint", "airplane_endpoint"}


# Check whether a node is an active beam endpoint.
def _is_beam_endpoint(node: str | int) -> bool:
    return str(node).startswith("beam_")


# Check whether a node is an active airplane endpoint.
def _is_airplane_endpoint(node: str | int) -> bool:
    return str(node).startswith("airplane_")


# Check whether a node is a leaf access endpoint instead of a Yeti router.
def _is_access_endpoint_node(graph: nx.Graph, node: str | int) -> bool:
    node_str = str(node)
    data = graph.nodes.get(node, graph.nodes.get(node_str, {}))
    return (
        data.get("node_type") in _ACCESS_ENDPOINT_NODE_TYPES
        or _is_beam_endpoint(node_str)
        or _is_airplane_endpoint(node_str)
    )


# Count only Yeti routers, not beam or airplane endpoint leaves.
def count_yeti_routers(graph: nx.Graph) -> int:
    return sum(
        1
        for node, _data in graph.nodes(data=True)
        if not _is_access_endpoint_node(graph, node)
    )


# Ignore missing nodes and reserved placeholder ports during forwarding.
def _is_valid_forwarding_neighbor(graph: nx.Graph, next_hop: Optional[str]) -> bool:
    if next_hop is None:
        return False
    next_hop_str = str(next_hop)
    if next_hop_str.startswith((_RESERVED_BEAM_PREFIX, _RESERVED_ISL_PREFIX)):
        return False
    return next_hop_str in graph


# Add destination H3 beam cells as access endpoints.
def add_beam_access_topology(
    graph: nx.Graph,
    beam_cell_to_satellite: Dict[str, int],
    destination_cells: Iterable[str],
    beam_capacity_per_satellite: int = 32,
    beam_link_weight: float = 1.0,
    strict: bool = True,
) -> Tuple[nx.Graph, List[str]]:
    if beam_capacity_per_satellite < 0:
        raise ValueError("beam_capacity_per_satellite must be non-negative.")

    G = graph.copy()
    destination_beam_nodes: List[str] = []
    missing_cells: List[str] = []

    for cell in sorted(set(str(c) for c in destination_cells)):
        sat_id = beam_cell_to_satellite.get(cell)
        if sat_id is None:
            missing_cells.append(cell)
            continue

        sat_node = f"satellite_{sat_id}"
        if sat_node not in G:
            missing_cells.append(cell)
            continue

        beam_node = f"beam_{cell}"
        G.add_node(
            beam_node,
            node_type="beam_endpoint",
            beam_cell=cell,
            serving_satellite=sat_node,
        )
        G.add_edge(
            sat_node,
            beam_node,
            weight=float(beam_link_weight),
            link_type="beam",
        )
        destination_beam_nodes.append(beam_node)

    if strict and missing_cells:
        raise ValueError(
            "No scheduled serving satellite exists for destination beam cell(s): "
            + ", ".join(missing_cells)
        )

    G.graph["yeti_beam_capacity_per_satellite"] = int(beam_capacity_per_satellite)
    G.graph["yeti_destination_beam_nodes"] = list(destination_beam_nodes)
    return G, destination_beam_nodes


# Build a stable graph node name for an airplane receiver.
def airplane_endpoint_id(airplane: object) -> str:
    raw_id = getattr(airplane, "id", None)
    if raw_id is None:
        raw_id = id(airplane)
    return f"airplane_{raw_id}"


def _normalize_satellite_node_id(satellite_id_or_node: str | int) -> str:
    sat_str = str(satellite_id_or_node)
    if sat_str.startswith("satellite_"):
        return sat_str
    return f"satellite_{int(satellite_id_or_node)}"


# Add destination airplanes as access endpoints.
def add_airplane_access_topology(
    graph: nx.Graph,
    airplane_to_satellite: Dict[str, str | int],
    destination_airplanes: Iterable[object],
    access_link_weight: float = 1.0,
    strict: bool = True,
) -> Tuple[nx.Graph, List[str]]:
    G = graph.copy()
    destination_airplane_nodes: List[str] = []
    missing_airplanes: List[str] = []

    # Preserve input order while removing duplicate endpoint IDs.
    seen: Set[str] = set()
    for airplane in destination_airplanes:
        airplane_node = airplane_endpoint_id(airplane)
        if airplane_node in seen:
            continue
        seen.add(airplane_node)

        serving_satellite = (
            airplane_to_satellite.get(airplane_node)
            or airplane_to_satellite.get(str(getattr(airplane, "id", "")))
        )
        if serving_satellite is None:
            missing_airplanes.append(airplane_node)
            continue

        try:
            sat_node = _normalize_satellite_node_id(serving_satellite)
        except (TypeError, ValueError):
            missing_airplanes.append(airplane_node)
            continue

        if sat_node not in G:
            missing_airplanes.append(airplane_node)
            continue

        G.add_node(
            airplane_node,
            node_type="airplane_endpoint",
            latitude=float(getattr(airplane, "latitude")),
            longitude=float(getattr(airplane, "longitude")),
            serving_satellite=sat_node,
        )
        G.add_edge(
            sat_node,
            airplane_node,
            weight=float(access_link_weight),
            link_type="airplane_access",
        )
        destination_airplane_nodes.append(airplane_node)

    if strict and missing_airplanes:
        raise ValueError(
            "No scheduled serving satellite exists for destination airplane(s): "
            + ", ".join(sorted(missing_airplanes))
        )

    G.graph["yeti_beam_capacity_per_satellite"] = 0
    G.graph["yeti_destination_airplane_nodes"] = list(destination_airplane_nodes)
    return G, destination_airplane_nodes


def labels_to_strings(labels: Sequence[YetiLabel]) -> List[str]:
    output: List[str] = []
    for label in labels:
        if isinstance(label, FSPLabel):
            output.append(
                f"FSP(target={label.target_router_id}, hops={label.hop_count}, process={label.process_here})"
            )
        elif isinstance(label, FTELabel):
            output.append(
                f"FTE(interface={label.interface_id}, neighbor={label.target_neighbor})"
            )
        elif isinstance(label, MCTLabel):
            output.append(
                f"MCT(intfs={list(label.interface_ids)}, bitmap={label.interface_bitmap:b}, branches={label.branch_count})"
            )
        elif isinstance(label, CPYLabel):
            output.append(f"CPY(label_count={label.label_count}, offset_bits={label.offset_bits})")
        else:
            output.append(str(label))
    return output


def count_label_types(labels: Sequence[YetiLabel]) -> Dict[str, int]:
    counts = {"FSP": 0, "FTE": 0, "MCT": 0, "CPY": 0}
    for label in labels:
        if isinstance(label, FSPLabel):
            counts["FSP"] += 1
        elif isinstance(label, FTELabel):
            counts["FTE"] += 1
        elif isinstance(label, MCTLabel):
            counts["MCT"] += 1
        elif isinstance(label, CPYLabel):
            counts["CPY"] += 1
    return counts


# Estimate the Yeti label-stack size in bits.
def estimate_label_bits(
    labels: Sequence[YetiLabel],
    num_nodes: int,
    max_interfaces: int,
) -> int:
    node_bits     = _safe_log2_ceil(max(1, num_nodes))
    iface_bits    = _safe_log2_ceil(max(1, max_interfaces))
    fte_label_size = 2 + iface_bits                                   # full FTE label width in bits
    cpy_bits      = _safe_log2_ceil(max(1, num_nodes * max(1, fte_label_size)))

    total = 0
    for label in labels:
        if isinstance(label, FSPLabel):
            total += 2 + 1 + node_bits
        elif isinstance(label, FTELabel):
            total += 2 + iface_bits
        elif isinstance(label, MCTLabel):
            total += 2 + 1 + max_interfaces
        elif isinstance(label, CPYLabel):
            total += 2 + cpy_bits
    return total


# Build the Yeti forwarding tables and interface maps.
def build_router_tables(
    graph: nx.Graph,
    reserved_beam_interfaces_per_satellite: Optional[int] = None,
    reserved_isl_interfaces_per_satellite: Optional[int] = None,
) -> nx.Graph:
    print("[Yeti] Building router tables...")
    G = graph.copy()

    if reserved_beam_interfaces_per_satellite is None:
        beam_capacity = int(G.graph.get("yeti_beam_capacity_per_satellite", 0))
    else:
        beam_capacity = int(reserved_beam_interfaces_per_satellite)

    if reserved_isl_interfaces_per_satellite is None:
        isl_capacity = int(G.graph.get("yeti_reserved_isl_interfaces_per_satellite", 0))
    else:
        isl_capacity = int(reserved_isl_interfaces_per_satellite)

    if beam_capacity < 0:
        raise ValueError("reserved_beam_interfaces_per_satellite must be non-negative.")
    if isl_capacity < 0:
        raise ValueError("reserved_isl_interfaces_per_satellite must be non-negative.")

    for node in G.nodes():
        node_str = str(node)
        actual_neighbors = [str(nbr) for nbr in G.neighbors(node)]

        if node_str.startswith("satellite_"):
            satellite_neighbors = sorted(
                (nbr for nbr in actual_neighbors if str(nbr).startswith("satellite_")),
                key=_node_sort_key,
            )
            access_neighbors = sorted(
                (nbr for nbr in actual_neighbors if _is_access_endpoint_node(G, nbr)),
                key=_node_sort_key,
            )
            other_neighbors = sorted(
                (
                    nbr for nbr in actual_neighbors
                    if not str(nbr).startswith("satellite_")
                    and not _is_access_endpoint_node(G, nbr)
                ),
                key=_node_sort_key,
            )

            if beam_capacity > 0 and len(access_neighbors) > beam_capacity:
                raise ValueError(
                    f"{node_str} has {len(access_neighbors)} active access endpoints "
                    f"but reserved access/beam capacity is {beam_capacity}."
                )

            interface_id_map: Dict[str, int] = {}
            next_id = 0

            for nbr in satellite_neighbors:
                interface_id_map[nbr] = next_id
                next_id += 1

            reserved_isl_capacity = max(int(isl_capacity), len(satellite_neighbors))
            for isl_slot in range(len(satellite_neighbors), reserved_isl_capacity):
                placeholder = f"{_RESERVED_ISL_PREFIX}{node_str}_{isl_slot}"
                interface_id_map[placeholder] = next_id
                next_id += 1

            for nbr in other_neighbors:
                interface_id_map[nbr] = next_id
                next_id += 1

            for nbr in access_neighbors:
                interface_id_map[nbr] = next_id
                next_id += 1

            for access_slot in range(len(access_neighbors), beam_capacity):
                placeholder = f"{_RESERVED_BEAM_PREFIX}{node_str}_{access_slot}"
                interface_id_map[placeholder] = next_id
                next_id += 1
        else:
            neighbors = sorted(actual_neighbors, key=_node_sort_key)
            interface_id_map = {nbr: idx for idx, nbr in enumerate(neighbors)}

        reverse_interface_id_map = {
            idx: nbr for nbr, idx in interface_id_map.items()
        }

        shortest_paths = nx.single_source_dijkstra_path(
            G, source=node_str, weight="weight"
        )
        fib_next_hop: Dict[str, str] = {}
        for dst, path in shortest_paths.items():
            dst_str = str(dst)
            if len(path) <= 1:
                fib_next_hop[dst_str] = node_str
            else:
                fib_next_hop[dst_str] = str(path[1])

        G.nodes[node]["interface_id_map"] = interface_id_map
        G.nodes[node]["reverse_interface_id_map"] = reverse_interface_id_map
        G.nodes[node]["FIB_next_hop"] = fib_next_hop
        G.nodes[node]["router_id"] = node_str
        if node_str.startswith("satellite_"):
            G.nodes[node]["reserved_beam_interfaces"] = beam_capacity
            G.nodes[node]["reserved_isl_interfaces"] = isl_capacity

    return G

# Build the shortest-path multicast tree used by the Yeti baseline.
def build_shortest_path_multicast_tree(
    graph: nx.Graph,
    src: str,
    dests: Iterable[str],
) -> Dict[str, Any]:
    src          = str(src)
    unique_dests = sorted(set(str(d) for d in dests), key=_node_sort_key)
    spt_paths    = nx.single_source_dijkstra_path(graph, source=src, weight="weight")

    children: Dict[str, List[str]]    = {str(node): [] for node in graph.nodes()}
    tree_edges: Set[Tuple[str, str]]   = set()
    missing_destinations: List[str]    = []

    for dst in unique_dests:
        if dst == src:
            continue
        path = spt_paths.get(dst)
        if not path:
            missing_destinations.append(dst)
            continue

        path = [str(x) for x in path]
        for u, v in zip(path, path[1:]):
            tree_edges.add((u, v))
            if v not in children[u]:
                children[u].append(v)

    for node, node_children in children.items():
        if_map = graph.nodes[node].get("interface_id_map", {})
        node_children.sort(key=lambda child: if_map.get(child, 10**9))

    return {
        "source":               src,
        "destinations":         [d for d in unique_dests if d not in missing_destinations],
        "children":             children,
        "edges":                sorted(tree_edges),
        "missing_destinations": missing_destinations,
    }


# Encode one path segment using FSP and FTE labels.
def _segment_to_labels(
    graph: nx.Graph,
    segment_nodes: Sequence[str],
) -> List[YetiLabel]:
    segment_nodes = [str(x) for x in segment_nodes]
    if len(segment_nodes) <= 1:
        return []

    # An access endpoint (beam cell or airplane) is reached through the serving
    # satellite's local access interface. It is not a Yeti router identifier and
    # must never be the target of an FSP label.
    if _is_access_endpoint_node(graph, segment_nodes[-1]):
        serving_router = segment_nodes[-2]
        access_node = segment_nodes[-1]
        labels: List[YetiLabel] = []

        if len(segment_nodes) > 2:
            labels.extend(_segment_to_labels(graph, segment_nodes[:-1]))

        interface_id = graph.nodes[serving_router]["interface_id_map"].get(access_node)
        if interface_id is None:
            raise ValueError(
                f"No access interface from '{serving_router}' to '{access_node}'."
            )
        labels.append(
            FTELabel(interface_id=int(interface_id), target_neighbor=access_node)
        )
        return labels

    src = segment_nodes[0]
    dst = segment_nodes[-1]

    try:
        shortest = [str(x) for x in
                    nx.shortest_path(graph, source=src, target=dst, weight="weight")]
    except (nx.NetworkXNoPath, nx.NodeNotFound):
        shortest = []

    # Find first position where segment diverges from the shortest path.
    diverge_idx: Optional[int] = None
    for i in range(1, len(segment_nodes)):
        if i >= len(shortest) or segment_nodes[i] != shortest[i]:
            diverge_idx = i
            break

    # Entire segment follows shortest path → single FSP.
    if diverge_idx is None:
        return [FSPLabel(
            target_router_id=dst,
            hop_count=len(segment_nodes) - 1,
            process_here=False,
        )]

    labels: List[YetiLabel] = []

    # On-path prefix of length > 1 → shortcut it with one FSP.
    if diverge_idx > 1:
        on_path_end = segment_nodes[diverge_idx - 1]
        labels.append(FSPLabel(
            target_router_id=on_path_end,
            hop_count=diverge_idx - 1,
            process_here=False,
        ))

    # One FTE for the diverging hop.
    u = segment_nodes[diverge_idx - 1]
    v = segment_nodes[diverge_idx]
    interface_id = int(graph.nodes[u]["interface_id_map"][v])
    labels.append(FTELabel(interface_id=interface_id, target_neighbor=v))

    # Recurse on the remaining suffix starting at v.
    labels.extend(_segment_to_labels(graph, segment_nodes[diverge_idx:]))
    return labels


# Encode the multicast tree into the Yeti label stack.
def encode_tree_to_yeti_labels(
    graph: nx.Graph,
    tree_info: Dict[str, Any],
) -> List[YetiLabel]:
    children: Dict[str, List[str]] = tree_info["children"]
    source = str(tree_info["source"])

    # Pre-compute graph metrics once for CPY offset_bits calculation.
    num_nodes      = count_yeti_routers(graph)
    max_interfaces = max(
        (len(graph.nodes[n].get("interface_id_map", {})) for n in graph.nodes()),
        default=0,
    )

    # Count the bit size of one copied branch label stack.
    def _branch_bits(branch_labels: List[YetiLabel]) -> int:
        return estimate_label_bits(
            labels=branch_labels,
            num_nodes=num_nodes,
            max_interfaces=max_interfaces,
        )

    def encode_from_node(node: str) -> List[YetiLabel]:
        node          = str(node)
        node_children = list(children.get(node, []))

        if not node_children:
            return []

        if len(node_children) == 1:
            segment_nodes = [node]
            current       = node

            while True:
                current_children = list(children.get(current, []))
                if len(current_children) != 1:
                    break
                nxt = str(current_children[0])
                segment_nodes.append(nxt)
                current = nxt
                if len(children.get(current, [])) != 1:
                    break

            segment_labels = _segment_to_labels(graph, segment_nodes)
            tail_labels    = encode_from_node(current)
            return segment_labels + tail_labels

        # --- Branching point ---
        interface_ids: List[int]                   = []
        branch_label_stacks: List[List[YetiLabel]] = []

        for child in node_children:
            child        = str(child)
            interface_id = int(graph.nodes[node]["interface_id_map"][child])
            interface_ids.append(interface_id)
            branch_label_stacks.append(encode_from_node(child))

        bitmap = 0
        for iid in interface_ids:
            bitmap |= 1 << iid

        # CPY is only needed when at least one branch requires further labels.
        has_cpy = any(len(bl) > 0 for bl in branch_label_stacks)

        labels: List[YetiLabel] = [
            MCTLabel(
                interface_ids=tuple(interface_ids),
                interface_bitmap=bitmap,
                branch_count=len(interface_ids),
                has_cpy=has_cpy,
            )
        ]

        if has_cpy:
            for branch_labels in branch_label_stacks:
                offset_bits = _branch_bits(branch_labels)
                labels.append(CPYLabel(
                    label_count=len(branch_labels),
                    offset_bits=offset_bits,
                ))
                labels.extend(branch_labels)

        return labels

    return encode_from_node(source)


# Build the Yeti packet at the ingress and run the forwarding simulation.
def ingress_process_yeti(
    graph: nx.Graph,
    ingress_node: str,
    pkt: Any,
    dst: Iterable[str],
    print_info: bool = False,
    print_tree: bool = False,
    return_tree: bool = False,
    return_packet: bool = False,
    return_tree_info: bool = False,
):
    dst = sorted(set(str(x) for x in dst), key=_node_sort_key)

    tree_info = build_shortest_path_multicast_tree(graph, str(ingress_node), dst)
    labels    = encode_tree_to_yeti_labels(graph=graph, tree_info=tree_info)
    packet    = YetiPacket(labels=labels, payload=pkt)

    counts         = count_label_types(labels)
    max_interfaces = max(
        (len(graph.nodes[node].get("interface_id_map", {})) for node in graph.nodes()),
        default=0,
    )
    est_bits = estimate_label_bits(
        labels=labels,
        num_nodes=count_yeti_routers(graph),
        max_interfaces=max_interfaces,
    )

    packet.label_stack_strings  = labels_to_strings(labels)
    packet.label_count           = len(labels)
    packet.estimated_label_bits  = int(est_bits)
    packet.estimated_label_bytes = int(ceil(est_bits / 8)) if est_bits > 0 else 0

    packet.num_fsp = counts["FSP"]
    packet.num_fte = counts["FTE"]
    packet.num_mct = counts["MCT"]
    packet.num_cpy = counts["CPY"]

    packet.fsp_hops_covered = sum(
        label.hop_count for label in labels if isinstance(label, FSPLabel)
    )
    packet.fsp_label_savings = sum(
        max(0, label.hop_count - 1) for label in labels if isinstance(label, FSPLabel)
    )
    packet.copy_operations = sum(
        max(0, len(label.interface_ids) - 1)
        for label in labels
        if isinstance(label, MCTLabel)
    )

    packet.tree_edges        = list(tree_info["edges"])
    packet.destination_nodes = list(tree_info["destinations"])

    if print_info:
        print("\n[Yeti] Label stack:")
        for idx, label_str in enumerate(packet.label_stack_strings):
            print(f"  {idx:02d}: {label_str}")
        print(f"[Yeti] estimated label bits  = {packet.estimated_label_bits}")
        print(f"[Yeti] estimated label bytes = {packet.estimated_label_bytes}")
        print(f"[Yeti] FSP labels            = {packet.num_fsp}")
        print(f"[Yeti] FTE labels            = {packet.num_fte}")
        print(f"[Yeti] MCT labels            = {packet.num_mct}")
        print(f"[Yeti] CPY labels            = {packet.num_cpy}")
        print(f"[Yeti] FSP hop coverage      = {packet.fsp_hops_covered}")
        print(f"[Yeti] FSP savings           = {packet.fsp_label_savings}")
        print(f"[Yeti] copy operations       = {packet.copy_operations}")

    entire_tree = process_yeti_packet(
        graph=graph,
        curr_node=str(ingress_node),
        packet=packet,
        destinations=set(tree_info["destinations"]),
        print_info=print_info,
        deliver_here=True,
    )

    if print_tree:
        print("\n ENTIRE YETI TREE:")
        printTree(entire_tree)
        print()

    outputs = []
    if return_tree:
        outputs.append(entire_tree)
    if return_packet:
        outputs.append(packet)
    if return_tree_info:
        outputs.append(tree_info)

    if not outputs:
        return None
    if len(outputs) == 1:
        return outputs[0]
    return tuple(outputs)


def process_yeti_packet(
    graph: nx.Graph,
    curr_node: str,
    packet: YetiPacket,
    destinations: Set[str],
    print_info: bool = False,
    deliver_here: bool = True,
):
    curr_node = str(curr_node)
    tree      = Tree(curr_node, [])

    if not packet.labels:
        tree.set_is_leaf(True)
        if curr_node in destinations:
            tree.set_is_rcv(True)
        return tree

    head = packet.labels[0]

    if isinstance(head, FSPLabel):
        if curr_node == str(head.target_router_id):
            next_packet = YetiPacket(labels=packet.labels[1:], payload=packet.payload)
            return process_yeti_packet(
                graph=graph,
                curr_node=curr_node,
                packet=next_packet,
                destinations=destinations,
                print_info=print_info,
                deliver_here=False,
            )

        next_hop = graph.nodes[curr_node]["FIB_next_hop"].get(str(head.target_router_id))
        if next_hop is None or next_hop == curr_node:
            tree.set_is_leaf(True)
            return tree

        branch = send_yeti_packet(
            graph=graph,
            dest_node=next_hop,
            packet=packet,
            destinations=destinations,
            print_info=print_info,
        )
        tree.add_branch(branch)
        return tree

    if isinstance(head, FTELabel):
        reverse_map: Dict[int, str] = graph.nodes[curr_node]["reverse_interface_id_map"]
        next_hop = reverse_map.get(int(head.interface_id))
        if not _is_valid_forwarding_neighbor(graph, next_hop):
            tree.set_is_leaf(True)
            return tree

        next_packet = YetiPacket(labels=packet.labels[1:], payload=packet.copy_payload())
        branch = send_yeti_packet(
            graph=graph,
            dest_node=next_hop,
            packet=next_packet,
            destinations=destinations,
            print_info=print_info,
        )
        tree.add_branch(branch)
        return tree

    if isinstance(head, MCTLabel):
        labels_after = packet.labels[1:]
        cursor       = 0
        reverse_map: Dict[int, str] = graph.nodes[curr_node]["reverse_interface_id_map"]

        for interface_id in head.interface_ids:
            branch_labels: List[YetiLabel] = []

            if head.has_cpy:
                # Consume one CPY label and the label range it covers.
                if cursor < len(labels_after) and isinstance(labels_after[cursor], CPYLabel):
                    cpy_label     = labels_after[cursor]
                    cursor       += 1
                    stop          = cursor + int(cpy_label.label_count)
                    branch_labels = list(labels_after[cursor:stop])
                    cursor        = stop
            # has_cpy=False: all branches are leaves; branch_labels stays empty.

            next_hop = reverse_map.get(int(interface_id))
            if not _is_valid_forwarding_neighbor(graph, next_hop):
                continue

            next_packet = YetiPacket(labels=branch_labels, payload=packet.copy_payload())
            branch = send_yeti_packet(
                graph=graph,
                dest_node=next_hop,
                packet=next_packet,
                destinations=destinations,
                print_info=print_info,
            )
            tree.add_branch(branch)

        if tree.n_branch() == 0:
            tree.set_is_leaf(True)
        return tree

    if isinstance(head, CPYLabel):
        next_packet = YetiPacket(labels=packet.labels[1:], payload=packet.payload)
        return process_yeti_packet(
            graph=graph,
            curr_node=curr_node,
            packet=next_packet,
            destinations=destinations,
            print_info=print_info,
            deliver_here=False,
        )

    tree.set_is_leaf(True)
    return tree


def send_yeti_packet(
    graph: nx.Graph,
    dest_node: str,
    packet: YetiPacket,
    destinations: Set[str],
    print_info: bool = False,
):
    return process_yeti_packet(
        graph=graph,
        curr_node=str(dest_node),
        packet=packet,
        destinations=destinations,
        print_info=print_info,
        deliver_here=True,
    )


# Extract root-to-leaf paths from the Yeti forwarding tree.
def get_paths_from_yeti_tree(tree_root):
    if not tree_root:
        return []

    all_paths = []

    def dfs(node, current_path):
        current_path.append(str(node.addr))
        if not node.branches:
            all_paths.append(list(current_path))
        else:
            for branch in node.branches:
                dfs(branch, list(current_path))

    dfs(tree_root, [])
    return all_paths


def printTree(root, markerStr="└──", levelMarkers=None):
    if levelMarkers is None:
        levelMarkers = []

    emptyStr      = " " * len(markerStr)
    connectionStr = "|" + emptyStr[:-1]
    level         = len(levelMarkers)
    mapper        = lambda draw: connectionStr if draw else emptyStr
    markers       = "".join(map(mapper, levelMarkers[:-1]))
    markers      += markerStr if level > 0 else ""
    print(f"{markers}{root.addr}")

    branches = root.branches.copy()
    branches.reverse()
    for i, child in enumerate(branches):
        isLast = i == len(branches) - 1
        printTree(child, markerStr, [*levelMarkers, not isLast])
