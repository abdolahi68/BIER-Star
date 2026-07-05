"""
h3_routing_main.py

This module provides the main orchestration function for the original H3-based routing simulation.
It is designed to be imported and called from other scripts.
"""
import networkx as nx
from collections import namedtuple

# Use relative imports to ensure the correct modules are found within the package.
from . import distribution
from . import routing
from . import visualization

# --- Data Structures ---
GroundStation = namedtuple('GroundStation', ['name', 'latitude', 'longitude'])
Satellite = namedtuple('Satellite', ['id', 'latitude', 'longitude', 'altitude'])
Shell = namedtuple('Shell', ['shell_name', 'orbits'])
Orbit = namedtuple('Orbit', ['satellites'])



def h3_routing_function(G, sat_map, source, target, sh, t):
    """
    Orchestrates the original H3-based unicast routing process.
    """
    print("======================================================")
    print("--- Running Original H3 Unicast Routing Strategy ---")
    print("======================================================")

    # Perform H3-based unicast routing
    user_locations = [source, target]
    user_dist = distribution.get_user_h3_distribution(user_locations, resolution=4)
    user_h3_parents = distribution.get_h3_parents(user_dist.keys(), parent_resolution=0)
    
    
    selected_sats = routing.greedy_satellite_selector(sat_map, user_h3_parents, t)
    print(f"Identified {len(selected_sats)} satellites in the region of interest.")
    
    path_sats, traversed_h3_cells = routing.calculate_route_b(G, sat_map, source, target, t)


    print(traversed_h3_cells)
    
    
    if not path_sats:
        print("--- H3 Routing Failed. No path found. ---")
        return None

    altitudes = [sat.altitude[t-1] for sat in sat_map.values()]
    avg_altitude = sum(altitudes) / len(altitudes) if altitudes else 0
    
    if avg_altitude >= 1000:
        h3_altitude_km = 1200 # Oneweb
        topology_name = "OneWeb"
    else:
        h3_altitude_km = 550 # Starlink
        topology_name = "Starlink"

    print(f"[Main] Average satellite altitude is {avg_altitude:.2f} km. Assuming {topology_name} topology.")
    print(f"[Main] Set H3 virtual layer altitude to {h3_altitude_km} km.")

    # Visualize the results using the dedicated visualizer for this module.
    # Pass the new altitude parameter to the visualizer
    visualization.plot_constellation_with_h3_route(
        sh=sh,
        t=t,
        source_gs=source,
        target_gs=target,
        all_sats_map=sat_map,
        path_sats=path_sats,
        traversed_h3_cells=traversed_h3_cells,
        h3_altitude_km=h3_altitude_km
    )

    print("\n--- Original H3 Unicast Routing Strategy Completed Successfully ---")
    return path_sats, traversed_h3_cells