# protocol_measures.py

import networkx as nx
from collections import defaultdict
import h3
import math

# ► Import the new partitioning and data fetching modules
from . import bier_partitioning
from . import bier_helpers
from . import airplane_data_fetcher

# ► Import the updated BIER logic and visualization modules
from src.XML_constellation.constellation_routing.routing_policy_plugin.BIER.bier_logic_unlimited import (
    ingress_process_for_partition,
    get_paths_from_bier_tree,
)
from . import BIER_visualization as bier_cv

def build_partitioned_bier_tables(G):
    """
    Builds BIER forwarding tables (BIFT) for each partition (sub-domain)
    in the graph. The tables are stored in the node attributes, keyed by Set ID.
    """
    print("[BIER Engine] Building partitioned BIER Forwarding Tables (BIFTs)...")
    partitions = defaultdict(list)
    for node, data in G.nodes(data=True):
        if 'set_id' in data:
            partitions[data['set_id']].append(node)
        else:
            raise ValueError(f"Node {node} is missing a 'set_id'. Please run partitioning first.")

    for n in G.nodes():
        G.nodes[n]['bift_tables'] = {}
        G.nodes[n]['match_tables'] = {}

    for set_id, nodes_in_partition in partitions.items():
        if not nodes_in_partition:
            continue
        
        sub_graph = G.subgraph(nodes_in_partition).copy()
        sorted_nodes = sorted(list(sub_graph.nodes()))
        
        ############### Mostafa ##########################################
        # Every node in the partition (satellite or airplane) gets its own bit
        # position / BFR-id here, not just nodes that are destinations in the
        # current experiment. Per RFC 8279 §2, BFR-id provisioning is done for
        # the whole sub-domain in advance of any specific flow: any node that
        # can ever act as a BFIR or BFER needs a bit, since any satellite or
        # airplane may become a source or destination later. This is why
        # HeaderBits (len(match_table) == number of nodes in the partition)
        # reflects the full provisioned addressing space, not just the active
        # destination set for a given packet.
        
        
        
        match_table = {node: 2**i for i, node in enumerate(sorted_nodes)}
        
        for n in sub_graph.nodes():
            bift = {nbr: 0 for nbr in sub_graph.adj[n]}
            try:
                paths_in_subgraph = nx.single_source_dijkstra_path(sub_graph, n, weight="weight")
                for dest_node in sub_graph.nodes():
                    if dest_node != n and dest_node in paths_in_subgraph and len(paths_in_subgraph[dest_node]) > 1:
                        next_hop = paths_in_subgraph[dest_node][1]
                        if dest_node in match_table:
                             bift[next_hop] |= match_table[dest_node]
            except nx.NodeNotFound:
                 print(f"Warning: Node {n} not found in subgraph for partition {set_id}. Skipping BIFT generation for it.")

            G.nodes[n]['bift_tables'][set_id] = bift
            G.nodes[n]['match_tables'][set_id] = match_table
    return G

# ------------------------------------------------------------
# Main Simulation and Forwarding Engine
# ------------------------------------------------------------
# Added satellite_addressing_scope parameter
def BIER_Function(network, src, dests, sh, t, sat_map, partitioning_method, all_airplanes=None, satellite_addressing_scope='all_shells', **kwargs):
    """
    Main entry point for a partitioned BIER simulation.
    This version integrates ALL fetched airplanes into the network topology
    and returns a dictionary of calculated metrics.
    
    Args:
        satellite_addressing_scope (str): For 'satellite_footprint' method,
                                          'all_shells' to address all satellites,
                                          'per_shell' to address satellites within their shell.
    """
    print("\n--- Running Partitioned BIER Simulation ---")
    
    G = network.copy()
    
    node_location_map = {f"satellite_{sat_id}": (sat.latitude[t-1], sat.longitude[t-1]) for sat_id, sat in sat_map.items()}
    
    if all_airplanes:
        for plane in all_airplanes:
            plane_node_id = str(plane.id)
            G.add_node(plane_node_id)
            node_location_map[plane_node_id] = (plane.latitude, plane.longitude)
            
            nearest_sat = bier_helpers.find_nearest_satellite_at_time(plane, sat_map, t)
            if nearest_sat:
                distance = bier_helpers.distance_between_satellite_and_user(plane, nearest_sat, t)
                sat_node_id = f"satellite_{nearest_sat.id}"
                if G.has_node(sat_node_id):
                    G.add_edge(plane_node_id, sat_node_id, weight=distance)
                    G.add_edge(sat_node_id, plane_node_id, weight=distance)

    G = bier_partitioning.assign_partitions(
        G,
        method=partitioning_method,
        node_location_map=node_location_map,
        **{k: v for k, v in kwargs.items() if k in ['h3_resolution']}
    )

    G = build_partitioned_bier_tables(G)
    
    output_metrics = {
        'partitioning_method': partitioning_method,
        'h3_resolution': kwargs.get('h3_resolution') if partitioning_method == 'geographical' else None,
        'bits_for_inter_partition_addressing': 'N/A' # Initialize
    }
    
    partitions = defaultdict(list)
    max_plane_partition_id = None
    total_num_airplanes = len(all_airplanes) if all_airplanes else 0
    output_metrics['total_num_airplanes'] = total_num_airplanes

    if partitioning_method == 'traditional':
        bitstring_length = G.number_of_nodes()
        output_metrics['max_bitstring_length'] = bitstring_length
        output_metrics['average_airplanes_per_partition'] = total_num_airplanes
        print(f"\n[Metrics] Traditional BIER (Single Partition):")
        print(f"  - Max Bitstring Length (all satellites + all airplanes): {bitstring_length}")
        print(f"  - Total number of airplanes: {total_num_airplanes}")
        print(f"  - Average number of airplanes per partition: {total_num_airplanes}")

    elif partitioning_method in ['geographical', 'satellite_footprint']:
        for node, data in G.nodes(data=True):
            if 'set_id' in data:
                partitions[data['set_id']].append(node)

        all_airplane_nodes = {str(p.id) for p in all_airplanes} if all_airplanes else set()
        partition_airplane_count = {
            sid: sum(1 for node in nodes if node in all_airplane_nodes)
            for sid, nodes in partitions.items()
        }
        
        partitions_with_airplanes = {sid: count for sid, count in partition_airplane_count.items() if count > 0}
        num_partitions_with_airplanes = len(partitions_with_airplanes)
        output_metrics['num_partitions_with_airplanes'] = num_partitions_with_airplanes
        
        average_airplanes = 0
        if num_partitions_with_airplanes > 0:
            total_airplanes_in_partitions = sum(partitions_with_airplanes.values())
            average_airplanes = total_airplanes_in_partitions / num_partitions_with_airplanes
        output_metrics['average_airplanes_per_active_partition'] = round(average_airplanes, 2)
        
        print(f"\n[Metrics] Overall Partitioning Stats for '{partitioning_method.capitalize()}':")
        print(f"  - Total number of airplanes found: {total_num_airplanes}")
        print(f"  - Total number of partitions with at least one airplane: {num_partitions_with_airplanes}")
        if num_partitions_with_airplanes > 0:
            print(f"  - Average number of airplanes per active partition: {average_airplanes:.2f}")

        # Calculate bits for inter-partition addressing based on new understanding
        if partitioning_method == 'geographical':
            h3_res = kwargs.get('h3_resolution')
            if h3_res == 0:
                output_metrics['bits_for_inter_partition_addressing'] = 7 # 122 cells for R0
            elif h3_res == 1:
                output_metrics['bits_for_inter_partition_addressing'] = 10 # 842 cells for R1
            else:
                # Fallback to dynamic if resolution not 0 or 1, or not specified
                if num_partitions_with_airplanes > 1:
                    output_metrics['bits_for_inter_partition_addressing'] = math.ceil(math.log2(num_partitions_with_airplanes))
                elif num_partitions_with_airplanes == 1:
                    output_metrics['bits_for_inter_partition_addressing'] = 1
                else:
                    output_metrics['bits_for_inter_partition_addressing'] = 0

        elif partitioning_method == 'satellite_footprint':
            if satellite_addressing_scope == 'all_shells':
                total_satellites_count = len(sat_map)
                if total_satellites_count > 1:
                    output_metrics['bits_for_inter_partition_addressing'] = math.ceil(math.log2(total_satellites_count))
                elif total_satellites_count == 1:
                    output_metrics['bits_for_inter_partition_addressing'] = 1
                else:
                    output_metrics['bits_for_inter_partition_addressing'] = 0
            elif satellite_addressing_scope == 'per_shell':
                # Find the maximum number of satellites in any single shell
                max_sats_in_shell = 0
                if hasattr(sh, 'orbits') and sh.orbits:
                    for orbit in sh.orbits:
                        if hasattr(orbit, 'satellites'):
                            max_sats_in_shell = max(max_sats_in_shell, len(orbit.satellites))
                
                if max_sats_in_shell > 1:
                    output_metrics['bits_for_inter_partition_addressing'] = math.ceil(math.log2(max_sats_in_shell))
                elif max_sats_in_shell == 1:
                    output_metrics['bits_for_inter_partition_addressing'] = 1
                else:
                    output_metrics['bits_for_inter_partition_addressing'] = 0
            else:
                print(f"Warning: Unknown satellite_addressing_scope '{satellite_addressing_scope}'. Setting inter-partition bits to N/A.")
                output_metrics['bits_for_inter_partition_addressing'] = 'N/A'


        if partition_airplane_count and any(count > 0 for count in partition_airplane_count.values()):
            max_plane_partition_id = max(partition_airplane_count, key=partition_airplane_count.get)
        
        if max_plane_partition_id is not None:
            bitstring_length = len(partitions[max_plane_partition_id])
            num_planes = partition_airplane_count[max_plane_partition_id]
            
            output_metrics['busiest_partition_id'] = max_plane_partition_id
            output_metrics['busiest_partition_num_airplanes'] = num_planes
            output_metrics['busiest_partition_bitstring_length'] = bitstring_length
            
            print(f"\n[Metrics] Details for Busiest Partition:")
            print(f"  - Partition with most airplanes: '{max_plane_partition_id}' (found {num_planes} airplanes)")
            print(f"  - Max Bitstring Length (total nodes in this partition): {bitstring_length}")

            if partitioning_method == 'geographical':
                h3_index_str = max_plane_partition_id
                output_metrics['busiest_partition_h3_index'] = h3_index_str
                print(f"  - H3 Cell Index with most airplanes: {h3_index_str}")
            elif partitioning_method == 'satellite_footprint':
                output_metrics['busiest_partition_satellite_id'] = max_plane_partition_id
                print(f"  - Satellite ID with most airplanes in its partition: {max_plane_partition_id}")
        else:
            print("\n[Metrics] Warning: No airplanes were found in any partition.")
            output_metrics['busiest_partition_bitstring_length'] = 0 # Set to 0 if no busy partition

    # (Routing logic is unchanged and omitted for brevity)
    all_forwarding_paths = []
    target_partition_id = None
    dests_in_target_partition = []

    if partitioning_method == 'traditional':
        target_partition_id = G.nodes[src]['set_id'] if G.number_of_nodes() > 0 else 0
        all_nodes_in_graph = list(G.nodes())
        dests_in_target_partition = [d for d in all_nodes_in_graph if d != src]
    elif max_plane_partition_id is not None:
        target_partition_id = max_plane_partition_id
        dests_in_target_partition = partitions[target_partition_id]

    if target_partition_id is not None:
        src_set_id = G.nodes[src]['set_id']
        if src_set_id == target_partition_id:
            multicast_dests = [d for d in dests_in_target_partition if d != src]
            tree = ingress_process_for_partition(G, src, "payload", multicast_dests, target_partition_id)
            all_forwarding_paths = get_paths_from_bier_tree(tree)
        else:
            try:
                shortest_path_lengths = nx.single_source_dijkstra_path_length(G, src, weight='weight')
            except nx.NodeNotFound:
                shortest_path_lengths = {}

            gateway_node, min_dist = None, float('inf')
            for node in partitions[target_partition_id]:
                if node in shortest_path_lengths and shortest_path_lengths[node] < min_dist:
                    min_dist, gateway_node = shortest_path_lengths[node], node
            
            if gateway_node:
                try:
                    unicast_path = nx.shortest_path(G, source=src, target=gateway_node, weight='weight')
                    multicast_dests = [d for d in dests_in_target_partition if d != gateway_node]
                    multicast_tree = ingress_process_for_partition(G, gateway_node, "payload", multicast_dests, target_partition_id)
                    multicast_paths = get_paths_from_bier_tree(multicast_tree)

                    if not multicast_paths:
                        all_forwarding_paths.append(unicast_path)
                    else:
                        for m_path in multicast_paths:
                            all_forwarding_paths.append(unicast_path + m_path[1:])
                except (nx.NetworkXNoPath, nx.NodeNotFound):
                    print(f"Warning: No unicast path found from '{src}' to gateway '{gateway_node}'.")
    
    print("\n--- Launching Visualization ---")
    target_airplanes = kwargs.get('target_airplanes', [])
    
    h3_grid_boundaries = []
    if partitioning_method == 'geographical':
        print("[Visualizer] Preparing H3 grid for visualization...")
        unique_h3_cells = {data['set_id'] for _, data in G.nodes(data=True)
                           if 'set_id' in data and data['set_id'] != 'unmapped'}

        for cell in unique_h3_cells:
            try:
                if hasattr(h3, 'cell_to_boundary'): # v4.x
                    boundary = h3.cell_to_boundary(cell, geo_json=False)
                else: # v3.x
                    boundary = h3.h3_to_geo_boundary(cell, geo_json=False)
                h3_grid_boundaries.append(boundary)
            except h3.H3CellError as e:
                print(f"[H3 Grid] Warning: Could not process H3 cell '{cell}'. Invalid cell. Error: {e}")
            except Exception as e:
                print(f"[H3 Grid] Warning: An unexpected error occurred while processing cell '{cell}': {e}")
    
    # bier_cv.plot_bier_forwarding_paths(
    #     sh=sh, t=t, sat_map=sat_map,
    #     network_edges=network.edges(),
    #     all_paths=[],
    #     title=f"BIER Multicast to Airplanes ({partitioning_method.capitalize()})",
    #     all_airplanes=all_airplanes,
    #     target_airplanes=target_airplanes,
    #     h3_grid_boundaries=h3_grid_boundaries
    # )
    
    return output_metrics