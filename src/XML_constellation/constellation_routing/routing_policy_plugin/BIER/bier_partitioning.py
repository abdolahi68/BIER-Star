# bier_partitioning.py
# ------------------------------------------------------------
"""
Author: Mostafa
This module provides functions to partition the network graph into sub-domains
(partitions) based on different strategies. Each node is assigned a 'set_id'
attribute which identifies the partition it belongs to.

Requires the 'h3-py' library for geographical partitioning.
Install using: pip install h3
"""
import h3
from collections import defaultdict

def assign_partitions(graph, method, node_location_map=None, h3_resolution=1, **kwargs):
    """
    Dispatcher function to partition the network based on the selected method.

    Args:
        graph (nx.Graph): The network graph from networkx.
        method (str): The partitioning method. One of 'geographical',
                      'satellite_footprint', or 'traditional'.
        node_location_map (dict): Map of node IDs to (latitude, longitude) tuples.
                                  Required for 'geographical' partitioning.
        h3_resolution (int): Resolution for H3 partitioning (0-15). Lower is larger area.
        **kwargs: Catches unused keyword arguments.

    Returns:
        nx.Graph: The graph with a 'set_id' attribute assigned to each node.
    """
    print(f"\n[Partitioner] Starting network partitioning using method: '{method}'")
    
    if method == 'geographical':
        if not node_location_map:
            raise ValueError("Node location map ('node_location_map') is required for geographical partitioning.")
        return assign_geographical_partitions(graph, node_location_map, h3_resolution)

    elif method == 'satellite_footprint':
        # ------------------- MODIFICATION START -------------------
        # The logic is now handled by the updated function below.
        return assign_satellite_footprint_partitions(graph)
        # -------------------- MODIFICATION END --------------------

    elif method == 'traditional':
        print("[Partitioner] Using traditional BIER with a single global domain.")
        for node_id in graph.nodes():
            graph.nodes[node_id]['set_id'] = 0
        return graph

    else:
        print(f"[Partitioner] Warning: Unknown method '{method}'. Defaulting to a single global domain (traditional BIER).")
        for node_id in graph.nodes():
            graph.nodes[node_id]['set_id'] = 0
        return graph

def assign_geographical_partitions(graph, node_location_map, h3_resolution):
    """
    Partitions the network based on H3 geographical cells using a provided location map.
    This function is compatible with v3.x and v4.x of the h3-py library.
    """
    print(f"[Partitioner] Assigning geographical partitions with H3 resolution {h3_resolution}.")
    for node_id in graph.nodes():
        if node_id in node_location_map:
            lat, lon = node_location_map[node_id]
            
            # --- VERSION COMPATIBILITY ---
            if hasattr(h3, 'latlng_to_cell'): # v4.x
                set_id = h3.latlng_to_cell(lat, lon, h3_resolution)
            else: # v3.x
                set_id = h3.geo_to_h3(lat, lon, h3_resolution)
            
            graph.nodes[node_id]['set_id'] = set_id
        else:
            print(f"[Partitioner] Warning: Node {node_id} not in location map. Assigning to 'unmapped' partition.")
            graph.nodes[node_id]['set_id'] = 'unmapped'
    return graph

# -------------------  START -------------------
def assign_satellite_footprint_partitions(graph):
    """
    Partitions the network where each satellite's coverage area is a sub-domain.
    Satellites define the partitions. Airplanes are assigned to the partition
    of their nearest satellite, determined by the existing graph edges which
    connect each airplane to its single closest satellite.
    """
    print("[Partitioner] Assigning satellite-based (footprint) partitions.")
    
    # Identify all satellite nodes, which will define the partitions.
    # We assume satellite nodes are prefixed with 'satellite_'.
    satellite_nodes = {n for n in graph.nodes() if str(n).startswith('satellite_')}
    
    # Assign each satellite to be the 'master' of its own partition.
    for sat_id in satellite_nodes:
        graph.nodes[sat_id]['set_id'] = sat_id

    # Assign each non-satellite node (airplane) to the partition of its connected satellite.
    for node_id in graph.nodes():
        if node_id not in satellite_nodes:  # This node is an airplane
            try:
                # The graph topology connects each airplane to its one nearest satellite.
                neighbor_sat = next(graph.neighbors(node_id))
                if neighbor_sat in satellite_nodes:
                    graph.nodes[node_id]['set_id'] = neighbor_sat
                else:
                    # This case should not occur if the graph is built correctly.
                    print(f"[Partitioner] Warning: Airplane '{node_id}' is connected to a non-satellite node '{neighbor_sat}'.")
                    graph.nodes[node_id]['set_id'] = 'unassigned'
            except StopIteration:
                # This airplane has no satellite connection.
                print(f"[Partitioner] Warning: Airplane '{node_id}' is isolated and has no satellite connection.")
                graph.nodes[node_id]['set_id'] = 'unassigned'
    return graph
# -------------------- MODIFICATION END --------------------