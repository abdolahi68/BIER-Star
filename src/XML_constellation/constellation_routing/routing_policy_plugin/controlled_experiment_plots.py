from collections import defaultdict
from pathlib import Path

import h3
import networkx as nx

import src.XML_constellation.constellation_routing.routing_policy_plugin.h3_bier.main as H3_BIER_Main
from src.XML_constellation.constellation_routing.routing_policy_plugin.Yeti import airplane_endpoint_id
from src.XML_constellation.constellation_routing.routing_policy_plugin.Yeti.yeti_logic import get_paths_from_yeti_tree
from src.XML_constellation.constellation_routing.routing_policy_plugin.Yeti.YETI_visualization import plot_yeti_forwarding_paths
from src.XML_constellation.constellation_routing.routing_policy_plugin.BIER_TE.bier_te_logic import get_paths_from_bier_tree
from src.XML_constellation.constellation_routing.routing_policy_plugin.BIER_TE.BIER_visualization import plot_bier_te_forwarding_paths


def satellite_id(node):
    return int(str(node).split("_")[-1])


def cell_latlon(cell):
    if hasattr(h3, "cell_to_latlng"):
        lat, lon = h3.cell_to_latlng(cell)
    else:
        lat, lon = h3.h3_to_geo(cell)
    return float(lat), float(lon)


class CellReceiver:
    def __init__(self, cell):
        lat, lon = cell_latlon(cell)
        self.id = f"cell_receiver_{cell}"
        self.latitude = lat
        self.longitude = lon


class TraceNode:
    def __init__(self, addr, branches=None):
        self.addr = addr
        self.branches = branches or []


def output_file(directory, filename):
    if not directory:
        return filename
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    return str(path / filename)


def satellite_trace_tree(G, source_node, destinations):
    children = defaultdict(set)
    for destination in sorted(set(map(str, destinations)), key=satellite_id):
        try:
            path = nx.shortest_path(G, source=source_node, target=destination, weight="weight")
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            continue
        for parent, child in zip(path, path[1:]):
            children[str(parent)].add(str(child))

    def build(node):
        branch_nodes = sorted(children.get(node, set()), key=satellite_id)
        return TraceNode(satellite_id(node), [build(child) for child in branch_nodes])

    return build(str(source_node))


def h3_layer_altitude(sat_map, t):
    altitudes = [float(sat.altitude[t - 1]) for sat in sat_map.values()]
    if not altitudes:
        return 550
    return 550 if sum(altitudes) / len(altitudes) < 1000 else 1200


def plot_yeti_airplanes_exp1(run, G_yeti, forwarding_trace, plan):
    terminal_map = {airplane_endpoint_id(airplane): airplane for airplane in plan.receivers}
    plot_yeti_forwarding_paths(
        sh=run.sh,
        t=run.t,
        sat_map=run.sat_map,
        network_edges=list(G_yeti.edges()),
        all_paths=get_paths_from_yeti_tree(forwarding_trace),
        title=f"YETI Airplane-Aware — Experiment 1, B={len(plan.cells)}, airplanes={plan.num_served_airplanes}",
        output_html=output_file(
            run.settings.visualization_output_dir,
            f"yeti_airplane_aware_experiment1_B{len(plan.cells)}.html",
        ),
        show=run.settings.show_visualization,
        terminal_map=terminal_map,
    )


def plot_yeti_beams_exp1(run, G_yeti, forwarding_trace, plan):
    terminal_map = {f"beam_{cell}": CellReceiver(cell) for cell in plan.cells}
    plot_yeti_forwarding_paths(
        sh=run.sh,
        t=run.t,
        sat_map=run.sat_map,
        network_edges=list(G_yeti.edges()),
        all_paths=get_paths_from_yeti_tree(forwarding_trace),
        title=f"YETI Beam-Aware — Experiment 1, B={len(plan.cells)}",
        output_html=output_file(
            run.settings.visualization_output_dir,
            f"yeti_experiment1_B{len(plan.cells)}.html",
        ),
        show=run.settings.show_visualization,
        terminal_map=terminal_map,
    )


def plot_bier_te_exp1(run, G_te, tree, plan, terminal_id):
    terminal_coords = {
        terminal_id(airplane): (float(airplane.latitude), float(airplane.longitude))
        for airplane in plan.receivers
    }
    plot_bier_te_forwarding_paths(
        sh=run.sh,
        t=run.t,
        sat_map=run.sat_map,
        network_edges=list(G_te.edges()),
        all_paths=get_paths_from_bier_tree(tree),
        terminal_coords=terminal_coords,
        title=f"BIER-TE Airplane Destinations — Experiment 1, B={len(plan.cells)}, airplanes={len(terminal_coords)}",
        output_html=output_file(
            run.settings.visualization_output_dir,
            f"bier_te_experiment1_B{len(plan.cells)}_airplane_destinations.html",
        ),
        show=run.settings.show_visualization,
    )


def plot_bier_star_exp2(run, source_cell, cell_tree, plan):
    H3_BIER_Main.bier_visualizer.plot_h3_bier_trace(
        sh=run.sh,
        t=run.t,
        sat_map=run.sat_map,
        G=run.G,
        source_cell_res1=source_cell,
        destination_cells_res1=set(plan.destination_cells),
        multicast_cell_mst_res0=cell_tree,
        forwarding_trace_tree=satellite_trace_tree(
            run.G,
            run.source_node,
            plan.destination_satellite_nodes,
        ),
        h3_altitude_km=h3_layer_altitude(run.sat_map, run.t),
    )


def plot_yeti_airplanes_exp2(run, G_yeti, forwarding_trace, plan, target_airplanes):
    terminal_map = {airplane_endpoint_id(airplane): airplane for airplane in target_airplanes}
    plot_yeti_forwarding_paths(
        sh=run.sh,
        t=run.t,
        sat_map=run.sat_map,
        network_edges=list(G_yeti.edges()),
        all_paths=get_paths_from_yeti_tree(forwarding_trace),
        title=(
            "YETI Airplane-Aware — Destination Parents, "
            f"K={plan.num_configured_active_parent_cells}, target airplanes={len(target_airplanes)}"
        ),
        output_html=output_file(
            run.settings.visualization_output_dir,
            f"yeti_airplane_aware_destination_regions_K{plan.num_configured_active_parent_cells}.html",
        ),
        show=run.settings.show_visualization,
        terminal_map=terminal_map,
    )


def plot_bier_te_exp2(run, G_te, tree, target_airplanes, terminal_id):
    terminal_coords = {
        terminal_id(airplane): (float(airplane.latitude), float(airplane.longitude))
        for airplane in target_airplanes
    }
    plot_bier_te_forwarding_paths(
        sh=run.sh,
        t=run.t,
        sat_map=run.sat_map,
        network_edges=list(G_te.edges()),
        all_paths=get_paths_from_bier_tree(tree),
        terminal_coords=terminal_coords,
        title=(
            "BIER-TE Airplane Destinations — Experiment 2, "
            f"hop={run.active_parent_count}, target airplanes={len(terminal_coords)}"
        ),
        output_html=output_file(
            run.settings.visualization_output_dir,
            f"bier_te_experiment2_hop{run.active_parent_count}_airplane_destinations.html",
        ),
        show=run.settings.show_visualization,
    )

# Traditional BIER visualization for Experiment 1.
def get_paths_from_traditional_bier_tree(tree):
    if tree is None:
        return []

    paths = []

    def walk(node, current_path):
        path = current_path + [str(node.addr)]
        if not node.branches:
            paths.append(path)
            return

        for branch in node.branches:
            walk(branch, path)

    walk(tree, [])
    return paths


def plot_traditional_bier_exp1(run, G_bier, tree, plan, terminal_id):
    terminal_coords = {
        terminal_id(airplane): (float(airplane.latitude), float(airplane.longitude))
        for airplane in plan.receivers
    }

    plot_bier_te_forwarding_paths(
        sh=run.sh,
        t=run.t,
        sat_map=run.sat_map,
        network_edges=list(G_bier.edges()),
        all_paths=get_paths_from_traditional_bier_tree(tree),
        terminal_coords=terminal_coords,
        title=f"Traditional BIER - Experiment, B={len(plan.cells)}, airplanes={len(terminal_coords)}",
        output_html=output_file(
            run.settings.visualization_output_dir,
            f"bier_traditional_B{len(plan.cells)}_airplane_destinations.html",
        ),
        show=run.settings.show_visualization,
    )

