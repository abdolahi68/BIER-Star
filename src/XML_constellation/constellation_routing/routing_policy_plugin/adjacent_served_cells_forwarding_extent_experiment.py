from collections import defaultdict
import csv
import os

import h3
import networkx as nx

import src.XML_constellation.constellation_routing.routing_policy_plugin.h3_bier.main as H3_BIER_Main
from src.XML_constellation.constellation_routing.routing_policy_plugin.h3_bier.igmp import schedule_spot_beams_res1_unique
import src.XML_constellation.constellation_routing.routing_policy_plugin.BIER.bier_helpers as bier_helpers

from src.XML_constellation.constellation_routing.routing_policy_plugin.Yeti import (
    add_airplane_access_topology,
    airplane_endpoint_id,
    build_router_tables,
    build_shortest_path_multicast_tree,
    count_yeti_routers,
    encode_tree_to_yeti_labels,
    estimate_label_bits,
    YetiPacket,
)
from src.XML_constellation.constellation_routing.routing_policy_plugin.Yeti.yeti_logic import send_yeti_packet
from src.XML_constellation.constellation_routing.routing_policy_plugin.BIER_TE import build_te_tables, ingress_process_te
from src.XML_constellation.constellation_routing.routing_policy_plugin.BIER.protocol_measures import build_partitioned_bier_tables
from src.XML_constellation.constellation_routing.routing_policy_plugin.BIER.bier_logic_unlimited import (
    ingress_process_for_partition,
    get_paths_from_bier_tree,
)

from src.XML_constellation.constellation_routing.routing_policy_plugin import controlled_experiment_plots as plots
from src.XML_constellation.constellation_routing.routing_policy_plugin.controlled_experiment_definition import (
    ACCESS_ASSIGNMENT_PARENT_RESOLUTION,
    BEAMS_PER_SATELLITE,
    DESTINATION_RESOLUTION,
    EXP1_COLUMNS,
    CellReceiver,
    DestinationPlan,
    YETI_RESERVED_ISLS_PER_SATELLITE,
)


def geo_to_cell(lat, lon, resolution):
    return h3.geo_to_h3(lat, lon, resolution)


def cell_to_latlon(cell):
    lat, lon = h3.h3_to_geo(cell)
    return float(lat), float(lon)


def cell_resolution(cell):
    return int(h3.h3_get_resolution(cell))


def cell_parent_r0(cell):
    return str(h3.h3_to_parent(cell, 0))


def satellite_id(node):
    return int(str(node).split("_")[-1])


def satellite_nodes(G):
    return {str(node) for node in G.nodes() if str(node).startswith("satellite_")}


def receiver_for_cell(cell):
    lat, lon = cell_to_latlon(cell)
    return CellReceiver(f"cell_receiver_{cell}", lat, lon)


def source_user_from_satellite(run, source_node):
    sat = run.sat_map.get(satellite_id(source_node))
    if sat is None:
        raise ValueError(f"{source_node} is not in sat_map")
    return bier_helpers.GroundUser(float(sat.latitude[run.t - 1]), float(sat.longitude[run.t - 1]))


def validate_destination_parent_cells(cells):
    cells = [str(cell) for cell in cells]
    if not cells:
        raise ValueError("At least one destination parent H3 cell is required")
    if len(set(cells)) != len(cells):
        raise ValueError("Destination parent cells must not contain duplicates")

    invalid = [cell for cell in cells if cell_resolution(cell) != 0]
    if invalid:
        raise ValueError("Destination parent cells must be H3 Res-0 cells: " + ", ".join(invalid))


def group_airplanes_in_destination_parents(all_airplanes, destination_parent_cells_r0):
    allowed = {str(cell) for cell in destination_parent_cells_r0}
    grouped = defaultdict(list)
    for airplane in all_airplanes:
        cell = geo_to_cell(float(airplane.latitude), float(airplane.longitude), DESTINATION_RESOLUTION)
        if cell_parent_r0(cell) in allowed:
            grouped[cell].append(airplane)
    return dict(grouped)


def order_occupied_airplane_cells(airplanes_by_cell):
    return sorted(airplanes_by_cell, key=lambda cell: (-len(airplanes_by_cell[cell]), cell))


def schedule_destination_plan(cells, run, airplanes_by_cell):
    cells = list(cells)
    representatives = [receiver_for_cell(cell) for cell in cells]
    assignment, _ = schedule_spot_beams_res1_unique(
        sat_map=run.sat_map,
        users=representatives,
        t=run.t,
        beams_per_sat=BEAMS_PER_SATELLITE,
        beam_resolution=DESTINATION_RESOLUTION,
        parent_resolution=ACCESS_ASSIGNMENT_PARENT_RESOLUTION,
        attach_to_sat_objects=False,
    )

    missing = set(cells).difference(assignment)
    if missing:
        raise ValueError("The satellite/beam scheduler could not serve: " + ", ".join(sorted(missing)))

    selected_airplanes = {cell: list(airplanes_by_cell[cell]) for cell in cells}
    receivers = [airplane for cell in cells for airplane in selected_airplanes[cell]]
    return DestinationPlan(
        cells=cells,
        receivers=receivers,
        beam_assignment={cell: int(assignment[cell]) for cell in cells},
        airplanes_by_cell=selected_airplanes,
        available_occupied_cells=len(airplanes_by_cell),
    )


def pick_source_satellite_in_parent_cell(run, source_parent_cell_r0):
    candidates = []
    for sat_id, sat in run.sat_map.items():
        parent = geo_to_cell(float(sat.latitude[run.t - 1]), float(sat.longitude[run.t - 1]), 0)
        if parent == str(source_parent_cell_r0):
            candidates.append(f"satellite_{int(sat_id)}")

    if not candidates:
        raise ValueError(f"No satellite is located in source Res-0 cell {source_parent_cell_r0} at timeslot {run.t}")
    return sorted(candidates, key=satellite_id)[0]


def hop_stats_to_egress(run, egress_nodes):
    G_sat = run.G.subgraph(satellite_nodes(run.G))
    distances = [
        int(nx.shortest_path_length(G_sat, source=run.source_node, target=egress))
        for egress in sorted(set(egress_nodes))
    ]
    if not distances:
        raise ValueError("No serving satellite is available for hop statistics")
    return min(distances), sum(distances) / len(distances), max(distances)


# BIER-Star
# Uses the current run state, the selected destination plan, and the H3 resolution.
# Returns a result dictionary with the method name, header bits, and forwarding-cell count.
def run_bier_star(run, plan, resolution, visualize=False):
    packet, _ = H3_BIER_Main.H3_BIER_Function(
        G=run.G.copy(),
        sat_map=run.sat_map,
        source_user=source_user_from_satellite(run, run.source_node),
        destination_users=plan.receivers,
        sh=run.sh,
        t=run.t,
        plan_resolution=resolution,
        dest_resolution=DESTINATION_RESOLUTION,
        beam_assignment_override=plan.beam_assignment,
        forced_ingress_sat_id=satellite_id(run.source_node),
        enable_visualization=visualize,
    )
    if packet is None:
        return {"Method": f"BIER-Star-R{resolution}", "HeaderBits": "N/A", "ForwardingH3Cells": "N/A"}
    return {
        "Method": f"BIER-Star-R{resolution}",
        "HeaderBits": int(packet.header_bit_length()),
        "ForwardingH3Cells": int(packet.forwarding_cell_count()),
    }


# Traditional BIER
# Adds all airplanes as BIER endpoints, builds one global BIER domain,
# then runs BIER forwarding to the active airplane destinations in the current plan.
def run_traditional_bier(run, plan, visualize=False):
    G_bier = run.G.copy()

    # Every airplane in the JSON snapshot is addressable in the BIER bitmap.
    for airplane in run.all_airplanes:
        terminal_id = airplane_terminal_id(airplane)
        if G_bier.has_node(terminal_id):
            continue

        nearest_sat = bier_helpers.find_nearest_satellite_at_time(
            airplane,
            run.sat_map,
            run.t,
        )
        if nearest_sat is None:
            continue

        sat_node = f"satellite_{nearest_sat.id}"
        if not G_bier.has_node(sat_node):
            continue

        distance = bier_helpers.distance_between_satellite_and_user(
            airplane,
            nearest_sat,
            run.t,
        )
        G_bier.add_node(terminal_id)
        G_bier.add_edge(sat_node, terminal_id, weight=distance)

    # Traditional BIER uses one global Set ID/domain.
    bier_domain = 0
    for node in G_bier.nodes():
        G_bier.nodes[node]["set_id"] = bier_domain

    G_bier = build_partitioned_bier_tables(G_bier)

    # Only airplanes in the selected B case are multicast destinations.
    destination_terminals = sorted(
        airplane_terminal_id(airplane)
        for airplane in plan.receivers
        if G_bier.has_node(airplane_terminal_id(airplane))
    )

    tree = ingress_process_for_partition(
        G_bier,
        run.source_node,
        "multicast-payload",
        destination_terminals,
        bier_domain,
        print_info=False,
    )

    if visualize:
        plots.plot_traditional_bier_exp1(run, G_bier, tree, plan, airplane_terminal_id)

    forwarding_paths = get_paths_from_bier_tree(tree)

    return {
        "Method": "BIER-Traditional",
        "HeaderBits": int(G_bier.number_of_nodes()),
        "ForwardingH3Cells": "N/A",
        "NumTargetAirplanes": len(destination_terminals),
        "NumForwardingPaths": len(forwarding_paths),
    }


# YETI

def airplane_to_satellite_from_plan(plan):
    mapping = {}
    for cell in plan.cells:
        sat_node = f"satellite_{int(plan.beam_assignment[cell])}"
        for airplane in plan.airplanes_by_cell.get(cell, []):
            mapping[airplane_endpoint_id(airplane)] = sat_node
    return mapping


# Uses the run state and selected destination plan to build the YETI access topology.
# Returns a result dictionary with the method name and estimated label/header bits.
def run_yeti_airplane_aware(run, plan, visualize=False):
    G_access, destination_nodes = add_airplane_access_topology(
        graph=run.G.copy(),
        airplane_to_satellite=airplane_to_satellite_from_plan(plan),
        destination_airplanes=plan.receivers,
        strict=True,
    )
    G_yeti = build_router_tables(
        G_access,
        reserved_beam_interfaces_per_satellite=0,
        reserved_isl_interfaces_per_satellite=YETI_RESERVED_ISLS_PER_SATELLITE,
    )
    tree = build_shortest_path_multicast_tree(G_yeti, run.source_node, destination_nodes)
    if tree.get("missing_destinations"):
        return {"Method": "YETI-AirplaneAware", "HeaderBits": "N/A", "ForwardingH3Cells": "N/A"}

    labels = encode_tree_to_yeti_labels(G_yeti, tree)
    max_interfaces = max((len(G_yeti.nodes[node].get("interface_id_map", {})) for node in G_yeti.nodes()), default=0)
    bits = estimate_label_bits(labels=labels, num_nodes=count_yeti_routers(G_yeti), max_interfaces=max_interfaces)

    if visualize:
        trace = send_yeti_packet(
            graph=G_yeti,
            dest_node=run.source_node,
            packet=YetiPacket(labels=labels, payload="multicast-payload"),
            destinations=set(destination_nodes),
            print_info=False,
        )
        plots.plot_yeti_airplanes_exp1(run, G_yeti, trace, plan)

    return {"Method": "YETI-AirplaneAware", "HeaderBits": int(bits), "ForwardingH3Cells": "N/A"}


# BIER-TE

def airplane_terminal_id(airplane):
    return f"airplane_{getattr(airplane, 'id', id(airplane))}"


# Uses the run state and selected destination plan to build BIER-TE terminal mappings.
# Returns a result dictionary with the method name, header bits, and target-airplane count.
def run_bier_te_airplane_destinations(run, plan, visualize=False):
    terminal_to_satellite = {}
    for cell in plan.cells:
        sat_node = f"satellite_{plan.beam_assignment[cell]}"
        for airplane in plan.airplanes_by_cell.get(cell, []):
            terminal_to_satellite[airplane_terminal_id(airplane)] = sat_node

    destination_terminals = sorted(terminal_to_satellite)
    G_te = build_te_tables(
        run.G.copy(),
        terminal_to_satellite=terminal_to_satellite,
        satellites_are_destinations=False,
        unique_terminal_decap=True,
    )
    tree, packet = ingress_process_te(
        G_te,
        run.source_node,
        "multicast-payload",
        [],
        destination_terminals,
        print_info=False,
        return_tree=True,
        return_packet=True,
    )

    if visualize:
        plots.plot_bier_te_exp1(run, G_te, tree, plan, airplane_terminal_id)

    return {
        "Method": "BIER-TE-AirplaneDest",
        "HeaderBits": int(packet.header_bitstring_length),
        "ForwardingH3Cells": "N/A",
        "NumTargetAirplanes": len(destination_terminals),
    }


# CSV output

def save_results(path, rows):
    if not rows:
        return

    write_header = not os.path.exists(path) or os.path.getsize(path) == 0
    with open(path, "a", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EXP1_COLUMNS)
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


def add_method_result(rows, run, plan, result):
    rows.append({
        "DatasetFile": run.dataset_file,
        "Experiment": "E1_IncreasingOccupiedRes2AirplaneCells_InConfiguredR0Parents",
        "DestinationRegionSet": run.destination_set_label,
        "Case": run.case,
        "Method": result["Method"],
        "SourceParentCell_R0": run.source_parent,
        "DestinationParentCells_R0": "|".join(run.destination_parents),
        "SourceSatellite": run.source_node,
        "NumAvailableOccupiedDestinationCells": plan.available_occupied_cells,
        "NumServedDestinationCells_B": len(plan.cells),
        "NumServedAirplanes": plan.num_served_airplanes,
        "NumServingSatellites": plan.num_serving_satellites,
        "HeaderBits": result.get("HeaderBits", "N/A"),
    })


def Running_ALL_Four_Methods(run, results_csv=None):
    results_csv = results_csv or (
        f"{run.output_prefix}_experiment1_combined_destination_sets_airplanes_in_r0_destination_regions.csv"
    )

    # Read the experiment settings prepared in BIER_shortest_path.py.
    settings = run.settings
    source_parent = settings.source_parent_cell_r0

    # Run Experiment 1 once for each configured destination-region set. in this experiment, satellites are fixed, but airplanes 
    # are varied because we use different JSON snapshots of airplane positions. Each snapshot is processed independently, but all results are written to the same CSV file.
    for set_label, destination_parents in settings.destination_parent_sets_r0:
         # Convert destination parent cells to strings and validate that they are H3 Res-0 cells.
        destination_parents = [str(cell) for cell in destination_parents]
        validate_destination_parent_cells(destination_parents)
        if str(source_parent) in set(destination_parents):
            raise ValueError("The source parent cell must not also be a destination parent")

        # Store the current experiment-set information in run so all methods and CSV rows use the same context.
        run.source_parent = source_parent
        run.destination_parents = destination_parents
        run.destination_set_label = str(set_label)


        # Select only airplanes inside the current destination parent cells, then order occupied Res-2 cells (each satellite beam assign to a cell R2).
        airplanes_by_cell = group_airplanes_in_destination_parents(run.all_airplanes, destination_parents)
        ordered_cells = order_occupied_airplane_cells(airplanes_by_cell)
        available = len(ordered_cells)
        
        # Example: if we request B=(4, 8, 12, 16, 20, 24, 28, 32)
        # but this JSON file has only 18 occupied destination cells,
        # then we can only run B=(4, 8, 12, 16).
        requested = sorted(set(int(value) for value in settings.served_cell_counts if int(value) > 0))
        valid_counts = [value for value in requested if value <= available]

        print(f"[Experiment] Destination-region set: {set_label}")
        print(f"[Experiment] Occupied Res-2 destination cells available: {available}")
        if not valid_counts:
            raise ValueError("No requested B case can run for this JSON snapshot")

        # Select the maximum number of destination cells needed by this experiment.
        # The beam/satellite assignment is computed once for this largest case.
        # Later, each smaller B case uses only the first B cells from this plan.
        max_cells = ordered_cells[:max(valid_counts)]
        full_plan = schedule_destination_plan(max_cells, run, airplanes_by_cell)
        run.source_node = settings.fixed_source_node or pick_source_satellite_in_parent_cell(run, source_parent)
        print(f"[Experiment] Fixed source satellite: {run.source_node}")

        # Run all four methods for every valid B value.
        for B in valid_counts:
            plan = full_plan.subset(max_cells[:B])
            run.case = f"B={B}"
            
            # Compute hop statistics to make sure serving satellites are reachable from the source.
            hop_stats_to_egress(run, plan.serving_satellite_nodes)

            print(
                f"[Experiment] B={B}, "
                f"served airplanes={plan.num_served_airplanes}, "
                f"serving satellites={plan.num_serving_satellites}"
            )
            
            # Collect one CSV row per method for the current B case.
            rows = []

            # BIER-Star with H3 forwarding resolution 0.
            result = run_bier_star(run, plan, 0, run.wants_plot("BIER-Star-R0", B))
            add_method_result(rows, run, plan, result)

            # BIER-Star with H3 forwarding resolution 1.
            result = run_bier_star(run, plan, 1, run.wants_plot("BIER-Star-R1", B))
            add_method_result(rows, run, plan, result)
            
            # YETI airplane-aware multicast baseline.
            result = run_yeti_airplane_aware(run, plan, run.wants_plot("YETI-AirplaneAware", B))
            add_method_result(rows, run, plan, result)

            # Traditional BIER header-size baseline.
            result = run_traditional_bier(run, plan, run.wants_plot("BIER-Traditional", B))
            add_method_result(rows, run, plan, result)
            
            # BIER-TE with airplane destinations as terminal nodes.
            result = run_bier_te_airplane_destinations(run, plan, run.wants_plot("BIER-TE-AirplaneDest", B))
            add_method_result(rows, run, plan, result)

            # Append the current B case results to the shared Experiment 1 CSV.
            save_results(results_csv, rows)

    print(f"[ControlledExperiment] Experiment results: {results_csv}")
    return results_csv
