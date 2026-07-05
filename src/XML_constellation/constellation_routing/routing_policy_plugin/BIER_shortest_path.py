"""
BIER_shortest_path.py

Routing-policy entry point for the controlled Experiment 1 run.
The experiment uses fixed source regions and increasing occupied H3 Res-2
served airplane cells, then returns the usual unicast shortest path expected
by the routing-policy framework.
"""

from pathlib import Path
import json
from pdb import run

import h5py
import networkx as nx
import numpy as np

import src.XML_constellation.constellation_routing.routing_policy_plugin.BIER.bier_helpers as bier_helpers
from src.XML_constellation.constellation_routing.routing_policy_plugin.controlled_experiment_definition import (
    ExperimentRun,
    ExperimentSettings,
)
from src.XML_constellation.constellation_routing.routing_policy_plugin.adjacent_served_cells_forwarding_extent_experiment import (
    Running_ALL_Four_Methods,
)
from src.XML_constellation.constellation_routing.routing_policy_plugin.BIER.airplane_data_fetcher import Airplane


PROJECT_ROOT = Path(__file__).resolve().parents[4]
DATASET_DIR = PROJECT_ROOT / "AirplaneDataset"
CONTROLLED_RESULTS_DIR = PROJECT_ROOT / "ControlledResults"
CONTROLLED_RESULTS_DIR.mkdir(parents=True, exist_ok=True)

SELECTED_DATA_FILE = None
EXCLUDE_ON_GROUND = False
CLEAR_PREVIOUS_CONTROLLED_RESULTS = False
COMBINED_RESULTS_STEM = "all_datasets"
VISUALIZATION_RESULTS_DIR = CONTROLLED_RESULTS_DIR / "Visualizations"


def load_specific_flight_data(filepath):
    print(f"Loading airplane data from {filepath}")
    with open(filepath, "r", encoding="utf-8") as handle:
        records = json.load(handle)

    airplanes = []
    for row_index, record in enumerate(records):
        if EXCLUDE_ON_GROUND and record.get("on_ground", False):
            continue

        latitude = float(record["latitude"])
        longitude = float(record["longitude"])
        if not (-90.0 <= latitude <= 90.0 and -180.0 <= longitude <= 180.0):
            continue

        airplanes.append(
            Airplane(
                id=str(record.get("id", f"airplane_{row_index}")),
                latitude=latitude,
                longitude=longitude,
            )
        )

    print(f"Loaded {len(airplanes)} airplane records.")
    return airplanes


def build_current_shell_graph(constellation_name, sh, t):
    h5_path = f"data/XML_constellation/{constellation_name}.h5"

    with h5py.File(h5_path, "r") as file:
        delay_group = file["delay"][sh.shell_name]
        delay = np.array(delay_group[f"timeslot{t}"]).tolist()

    sat_map = {
        sat.id: sat
        for orbit in sh.orbits
        for sat in orbit.satellites
    }

    graph = nx.Graph()
    graph.add_nodes_from([f"satellite_{i}" for i in range(1, len(delay))])

    for i in range(1, len(delay)):
        for j in range(i + 1, len(delay)):
            if delay[i][j] > 0 and i in sat_map and j in sat_map:
                graph.add_edge(f"satellite_{i}", f"satellite_{j}", weight=delay[i][j])

    return graph, sat_map


def selected_json_files():
    if not DATASET_DIR.exists():
        print(f"Dataset directory not found: {DATASET_DIR}")
        return []

    json_files = sorted(
        path.name
        for path in DATASET_DIR.iterdir()
        if path.is_file() and path.suffix.lower() == ".json"
    )

    if SELECTED_DATA_FILE is not None:
        json_files = [
            filename for filename in json_files
            if filename.lower() == SELECTED_DATA_FILE.lower()
        ]

    if not json_files:
        print(f"No JSON flight snapshots found in {DATASET_DIR}")
    return json_files


def make_experiment_run(graph, sat_map, sh, t, airplanes, dataset_file, output_prefix):
    settings = ExperimentSettings()
    settings.visualization_output_dir = str(VISUALIZATION_RESULTS_DIR)
    
    ## ###########################################
    ### you can use their settings to enable visualization and select the methods to visualize
    # methods_to_visualize = ("BIER-TE-AirplaneDest", "YETI-AirplaneAware", "BIER-Star-R1", "BIER-Traditional")
    # you can enable visualization and select the methods to visualize
    # settings.enable_visualization = True
    # settings.visualization_methods = ("BIER-Traditional",)  # methods to visualize
    

    
    
    
    run = ExperimentRun()
    run.G = graph
    run.sat_map = sat_map
    run.sh = sh
    run.t = t
    run.all_airplanes = airplanes
    run.dataset_file = dataset_file
    run.output_prefix = str(output_prefix)
    run.settings = settings
    return run


# This is the routing-policy entry point. It runs Experiment 1 and calls
# Running_ALL_Four_Methods(), where BIER-Star, Traditional BIER, YETI, and BIER-TE are evaluated.
def BIER_shortest_path(constellation_name, source, target, sh, t):
    # Make sure the folder for the shared Experiment 1 CSV exists before writing results.
    CONTROLLED_RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Mostafa Constellation: {constellation_name}")
    print(f"Mostafa Shell passed to BIER_shortest_path: {sh.shell_name}")

    # Find the JSON flight snapshots to process. If SELECTED_DATA_FILE is set,
    # selected_json_files() returns only that snapshot; otherwise it returns all JSON files.
    json_files = selected_json_files()
    if not json_files:
        return []

    # Print the dataset folder and the JSON snapshots that will be used in this run.
    print(f"Dataset directory: {DATASET_DIR}")
    print(f"JSON snapshots: {len(json_files)}")
    for filename in json_files:
        print(f"  - {filename}")

    # Build the current satellite-shell graph from the delay matrix and keep a satellite-id map.
    graph, sat_map = build_current_shell_graph(constellation_name, sh, t)
    print(f"Satellites in current shell: {len(sat_map)}")

    # Prepare the shared Experiment 1 CSV filename for this constellation timeslot.
    output_prefix = CONTROLLED_RESULTS_DIR / f"{COMBINED_RESULTS_STEM}_t{t}"
    results_csv = (
        f"{output_prefix}_experiment1_combined_destination_sets_"
        "airplanes_in_r0_destination_regions.csv"
    )

    # Either delete the old Experiment 1 CSV or append new rows to the existing file.
    if CLEAR_PREVIOUS_CONTROLLED_RESULTS:
        output_path = Path(results_csv)
        if output_path.exists():
            output_path.unlink()
        print("Previous Experiment 1 CSV file was deleted.")
    else:
        print("Appending new rows to the Experiment 1 CSV file.")

    # Process each JSON snapshot independently, but write all rows into the shared CSV.
    #in this part we called the four-method (BIER, BIER-TE, YETI, and BIER-Star) experiment runner
    processed_dataset_count = 0
    for json_file_name in json_files:
        # Load airplane positions from the current JSON snapshot.
        airplanes = load_specific_flight_data(DATASET_DIR / json_file_name)
        if not airplanes:
            print(f"Skipping {json_file_name}: no airplane records available.")
            continue

        print("\n" + "=" * 68)
        print(f"Running Experiment 1 for {json_file_name}")
        print("=" * 68)

        # Build the run object that passes graph, satellite map, airplane data,
        # dataset name, and output prefix to the four-method experiment runner.
        run = make_experiment_run(
            graph, sat_map, sh, t, airplanes, json_file_name, output_prefix
        )
        # print("Experiment run details:")
        # print(f"  Dataset file: {run.dataset_file}")
        # print(f"  Timeslot: {run.t}")
        # print(f"  Satellites in graph: {len(run.sat_map)}")
        # print(f"  Graph nodes: {run.G.number_of_nodes()}")
        # print(f"  Graph edges: {run.G.number_of_edges()}")
        # print(f"  Airplane records: {len(run.all_airplanes)}")
        # print(f"  Output prefix: {run.output_prefix}")
        # print(f"  Served cell counts: {run.settings.served_cell_counts}")
        # print(f"  Experiment 1 source parent R0: {run.settings.source_parent_cell_r0}")
        # print(f"  Experiment 1 destination parent sets: {run.settings.destination_parent_sets_r0}")
        # print(f"  Visualization enabled: {run.settings.enable_visualization}")
        # print(f"  Visualization methods: {run.settings.visualization_methods}")
        # print(f"  Visualization output dir: {run.settings.visualization_output_dir}")

        # Run the four methods for Experiment 1 and save their results to the CSV.
        Running_ALL_Four_Methods(run, results_csv)
        processed_dataset_count += 1

    # Print a short summary after all selected JSON snapshots have been processed.
    print("\n" + "=" * 68)
    print(f"Saved Experiment 1 results for {processed_dataset_count} JSON dataset(s)")
    print("=" * 68)
    print(f"Experiment 1 results: {results_csv}")

    # After the controlled experiment finishes, return the ordinary unicast shortest path
    # expected by the routing-policy framework.
    ingress = bier_helpers.find_nearest_satellite_at_time(source, sat_map, t)
    egress = bier_helpers.find_nearest_satellite_at_time(target, sat_map, t)
    if ingress is None or egress is None:
        return []

    start_node = f"satellite_{ingress.id}"
    end_node = f"satellite_{egress.id}"

    try:
        return nx.dijkstra_path(graph, start_node, end_node, weight="weight")
    except nx.NetworkXNoPath:
        return []
