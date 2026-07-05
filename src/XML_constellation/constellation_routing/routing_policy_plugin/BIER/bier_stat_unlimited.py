import random
from numpy import mean, std
from src.XML_constellation.constellation_routing.routing_policy_plugin.BIER.bier_logic_unlimited import ingress_process, failure, draw_graph, generate_random_dst
from concurrent.futures import ThreadPoolExecutor
import networkx as nx


#------------------------- GRAPH DEFINITION --------------------------
# from BIER.ISP1_BIER_unlimited import network as network_ISP1 # 650 nodes, 2300 edges
# from BIER.rf1239_BIER_unlimited import network as network_rf1239 # 315 nodes, 1944 edges


# --------------------------------------------------------------------


#------------------- MEASUREMENT FUNCTION DEFINITION -----------------

# Return the number of node in the tree.
def tree_size(tree):
    size = 1
    if tree.n_branch() > 0:
        for i in range(tree.n_branch()):
            size += tree_size(tree.branches[i])
    return size

# Return the number of non-leaf node in the tree.
def tree_size_noleaf(tree):
    size = 0
    if tree.n_branch() > 0:
        size = 1
        for i in range(tree.n_branch()):
            size += tree_size_noleaf(tree.branches[i])
    return size

# Return the number of leaf in the tree.
def tree_n_leaf(tree):
    n_leaf = 0
    if tree.n_branch() > 0:
        for i in range(tree.n_branch()):
            n_leaf += tree_n_leaf(tree.branches[i])
    else:
        n_leaf = 1
    return n_leaf

# Return the height of the tree.
def tree_height(tree):
    height = 0
    if tree.n_branch() > 0:
        for i in range(tree.n_branch()):
            h = tree_height_AUX(tree.branches[i], 1)
            if h > height:
                height = h
    return height
def tree_height_AUX(tree, height):
    sub_height = height
    next_h = height + 1
    if tree.n_branch() > 0:
        for i in range(tree.n_branch()):
            h = tree_height_AUX(tree.branches[i], next_h)
            if h > sub_height:
                sub_height = h
    return sub_height

# Return the average branching factor.
def tree_av_branch_fact(tree):
    size = tree_size_noleaf(tree)
    count = av_branch_fact_AUX(tree)
    return count/size
def av_branch_fact_AUX(tree):
    count = tree.n_branch()
    if count > 0:
        for i in range(tree.n_branch()):
            count += av_branch_fact_AUX(tree.branches[i])
    return count

# Return the average branching factor 
# (taking the number of non-leaf node in argument).
def tree_av_branch_fact2(tree, n_non_leaf):
    size = n_non_leaf
    count = av_branch_fact_AUX(tree)
    return count/size
# --------------------------------------------------------------------
#--------------------------- MEASUREMENTS ----------------------------


# network_m = "Cogentco" or "rf1239" or "ISP1"
def protocol_measurementsV2(mynetwork, src, dst, network_m):
    
    # print("---protocol_measurementsV2 BIER protocol---Start")
  
    tree = ingress_process(mynetwork, src, "payload", dst, return_tree=True)
    # f = open(file_name, "a")
    
    
    # print(tree)
    # print("Size of Dest = ", len(dst) )
    Dest_size = len(dst)
    Tree1_size=tree_size(tree)
    # print("size tree = ", tree_size(tree))
    # f.write(f"BIER {Dest_size} {Tree1_size}\n")

    # print("---Multi connectivity--- End")
    # f.close()
    
    return tree
    





def calculate_shortest_paths_and_assign_sources(network, src1, src2, destinations):
    src1_dests = []
    src2_dests = []
    
    # Calculate the shortest path from src1 and src2 to each destination
    for dest in destinations:
        try:
            path_length_src1 = nx.shortest_path_length(network, source=src1, target=dest, weight='weight')
        except nx.NetworkXNoPath:
            path_length_src1 = float('inf')
        
        try:
            path_length_src2 = nx.shortest_path_length(network, source=src2, target=dest, weight='weight')
        except nx.NetworkXNoPath:
            path_length_src2 = float('inf')
        
        # Assign the destination to the source with the shortest path
        if path_length_src1 <= path_length_src2:
            src1_dests.append(dest)
        else:
            src2_dests.append(dest)
    
    return src1_dests, src2_dests






