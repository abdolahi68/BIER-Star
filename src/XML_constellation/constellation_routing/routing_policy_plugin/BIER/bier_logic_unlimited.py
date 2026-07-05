# bier_logic_unlimited.py
# ------------------------------------------------------------
import networkx as nx
import matplotlib.pyplot as plt
import copy
import random

# ----------------------------------------------------CLASS--------------------------------------------------------------
class Tree:
    def __init__(self, addr, branches, is_leaf=False, is_rcv=False):
        self.addr = addr
        self.branches = branches
        self.is_leaf = is_leaf # is_leaf implies is_rcv
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

class Packet:
    def __init__(self, bit_string, payload):
        self.bit_string = bit_string
        self.payload = payload

    def copy_payload(self):
        return copy.deepcopy(self.payload)
        
# ------------------------------------------PARTITION-AWARE BIER LOGIC-------------------------------------------------

def get_tree_size(tree):
    if tree is None:
        return 0
    size = 1
    for branch in tree.branches:
        size += get_tree_size(branch)
    return size

def ingress_process_for_partition(graph, ingress_node, pkt, dst, set_id, print_info=False):
    """
    Simulates the BIER process for a set of destinations *within a single partition*.
    This is the new entry point for intra-domain forwarding.
    """
    dst.sort()
    packet = encapsulate_pkt_for_partition(graph, ingress_node, pkt, dst, set_id, print_info)
    entire_tree = process_packet_for_partition(graph, ingress_node, packet, set_id, print_info)
    
    if print_info:
        tree_size = get_tree_size(entire_tree)
        print(f"  [BIER Sub-Process] Tree size in partition '{set_id}': {tree_size}")
    
    return entire_tree

def encapsulate_pkt_for_partition(graph, curr_node, pkt, dst, set_id, print_info=False):
    """
    Encapsulates a packet using the match_table for the given set_id.
    """
    bit_string = 0
    # Use the correct partition-specific match_table
    match_table = graph.nodes[curr_node]['match_tables'].get(set_id, {})
    for node in dst:
        if node in match_table:
            bit_string |= match_table[node]
        elif print_info:
            print(f"Warning: Destination {node} not in match_table for partition {set_id}")

    if print_info:
        print(f"\n  BIT STRING (Partition '{set_id}'): {format(bit_string, 'b')}")
    return Packet(bit_string, pkt)

def process_packet_for_partition(graph, curr_node, packet, set_id, print_info=False):
    """
    Processes and forwards a packet using the BIFT for the given set_id.
    """
    tree = Tree(curr_node, [])
    if packet.bit_string == 0:
        return tree

    bit_string = packet.bit_string
    match_table = graph.nodes[curr_node]['match_tables'].get(set_id, {})
    
    # Check if current node is a destination within this partition
    if curr_node in match_table and (bit_string & match_table.get(curr_node, 0) != 0):
        tree.set_is_rcv(True)
        process_payload(curr_node, packet.payload, print_info)
        bit_string ^= match_table[curr_node]
        if bit_string == 0:
            tree.set_is_leaf(True)
            return tree
            
    # Use the correct partition-specific BIFT
    bift = graph.nodes[curr_node]['bift_tables'].get(set_id, {})
    
    for n_hop in bift:
        new_bit_string = bit_string & bift[n_hop]
        if new_bit_string != 0:
            new_packet = Packet(new_bit_string, packet.copy_payload())
            # Recursive call remains within the same partition
            branch = process_packet_for_partition(graph, n_hop, new_packet, set_id, print_info)
            tree.add_branch(branch)
            bit_string &= ~bift[n_hop]
            if bit_string == 0:
                return tree
    
    if bit_string != 0 and print_info:
         print(f"WARNING !!! -> BitString non-empty for partition {set_id}. Unreached destinations may exist.")

    return tree

# ------------------------------------------ HELPER AND LEGACY FUNCTIONS ------------------------------------------------

def get_paths_from_bier_tree(tree_root):
    """
    Traverses the BIER tree and flattens it into a list of root-to-leaf paths.
    Each path is a list of satellite IDs.
    """
    if not tree_root:
        return []
    
    all_paths = []
    
    def dfs(node, current_path):
        try:
            # Handle node names like 'satellite_X' and plain integers
            node_addr_str = str(node.addr)
            sat_id = int(node_addr_str.split('_')[-1]) if '_' in node_addr_str else int(node_addr_str)
        except (ValueError, IndexError):
            sat_id = node.addr 

        current_path.append(sat_id)
        
        if not node.branches:
            all_paths.append(list(current_path))
        else:
            for branch in node.branches:
                # Pass a copy of the path to the recursive call
                dfs(branch, list(current_path))

    dfs(tree_root, [])
    return all_paths

def process_payload(curr_node, payload, print_info=False):
    if print_info:
        print("The node:", curr_node,"received payload:", payload)

def printTree(root, markerStr="└──", levelMarkers=[]):
    emptyStr = " "*len(markerStr)
    connectionStr = "|" + emptyStr[:-1]
    level = len(levelMarkers)
    mapper = lambda draw: connectionStr if draw else emptyStr
    markers = "".join(map(mapper, levelMarkers[:-1]))
    markers += markerStr if level > 0 else ""
    print(f"{markers}{root.addr}")
    branches = root.branches.copy()
    branches.reverse()
    for i, child in enumerate(branches):
        isLast = i == len(branches) - 1
        printTree(child, markerStr, [*levelMarkers, not isLast])

# NOTE: The 'failure' and 'recover' functions below would need to be
# significantly updated to be partition-aware. They would need to trigger
# BIFT recalculations for the affected partition(s) only. They are
# left here in their original state for completeness.

def failure(graph, node):
    # This function is not partition-aware and should be updated if used.
    pass

def recover(graph, node_info):
    # This function is not partition-aware and should be updated if used.
    pass