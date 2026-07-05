"""
h3_routing/routing.py

Implements the H3-based routing strategies.
- Greedy satellite selection based on H3 cells.
- Route calculation using shortest paths and H3 cell traversal.
"""

import networkx as nx
import h3
from . import utils

def greedy_satellite_selector(satellite_map, user_h3_parents, t):
    """Select satellites located inside the chosen H3 parent cells."""
    selected_sats = {}
    print(f"\n[Selection] Starting greedy satellite selection from {len(satellite_map)} total satellites...")
    
    for sat_id, satellite in satellite_map.items():
        lat = satellite.latitude[t-1]
        lon = satellite.longitude[t-1]
        
        sat_h3_cell = h3.geo_to_h3(lat, lon, 5)
        sat_h3_parent = h3.h3_to_parent(sat_h3_cell, 0)
        
        if sat_h3_parent in user_h3_parents:
            selected_sats[sat_id] = satellite
            
    print(f"[Selection] Selected {len(selected_sats)} satellites covering user regions.")
    return selected_sats


def calculate_route_b(G, satellite_map, source_user, dest_user, t):
    """Find the shortest satellite path and the H3 cells it crosses."""
    print("\n[Routing B] Starting route calculation...")
    
    # 1. Find nearest satellites for source and destination
    start_sat, dist_to_start = utils.find_nearest_satellite(source_user, satellite_map, t)
    end_sat, dist_to_end = utils.find_nearest_satellite(dest_user, satellite_map, t)

    if not start_sat or not end_sat:
        print("[Routing B] ERROR: Could not find a satellite near source or destination.")
        return None, None

    print(f"[Routing B] Source user -> Satellite {start_sat.id} (Distance: {dist_to_start} km)")
    print(f"[Routing B] Destination user -> Satellite {end_sat.id} (Distance: {dist_to_end} km)")

    start_node = f"satellite_{start_sat.id}"
    end_node = f"satellite_{end_sat.id}"

    # 2. Calculate shortest path
    try:
        path_nodes = nx.dijkstra_path(G, source=start_node, target=end_node, weight='weight')
        print(f"[Routing B] Found shortest path with {len(path_nodes)} satellite hops.")
    except nx.NetworkXNoPath:
        print(f"[Routing B] ERROR: No path exists between {start_node} and {end_node}.")
        return None, None
    except nx.NodeNotFound as e:
        print(f"[Routing B] ERROR: Node not in graph: {e}")
        return None, None

    # 3. Determine traversed H3 cells
    traversed_h3_cells = set()
    minimum_path_sats = []
    
    for sat_node_str in path_nodes:
        sat_id = int(sat_node_str.split('_')[1])
        satellite = satellite_map[sat_id]
        minimum_path_sats.append(satellite)
        
        # Get satellite location and find its H3 parent cell
        lat = satellite.latitude[t-1]
        lon = satellite.longitude[t-1]
        h3_index = h3.geo_to_h3(lat, lon, 5) 
        h3_parent = h3.h3_to_parent(h3_index, 0)
        traversed_h3_cells.add(h3_parent)

    print(f"[Routing B] Path traverses {len(traversed_h3_cells)} H3 resolution 0 cells.")
    
    return minimum_path_sats, traversed_h3_cells