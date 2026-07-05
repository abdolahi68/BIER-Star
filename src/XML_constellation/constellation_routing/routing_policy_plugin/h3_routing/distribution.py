"""
h3_routing/distribution.py

This file contains functions for determining the distribution of users
based on the H3 geospatial indexing system.
"""
import h3
from collections import defaultdict

def get_user_h3_distribution(user_locations, resolution):
    """Group users by their H3 cell at the selected resolution."""
    distribution = defaultdict(list)
    print(f"\n[Distribution] Calculating user distribution at H3 resolution {resolution}...")
    for user in user_locations:
        lat = float(user.latitude)
        lon = float(user.longitude)
        h3_index = h3.geo_to_h3(lat, lon, resolution)
        distribution[h3_index].append(user)
        
    print(f"[Distribution] Found {len(user_locations)} users across {len(distribution)} H3 cells.")
    return dict(distribution)

def get_h3_parents(h3_indices, parent_resolution=0):
    """Find the unique parent cells for the given H3 cells."""
    parents = {h3.h3_to_parent(idx, parent_resolution) for idx in h3_indices}
    print(f"[Distribution] Identified {len(parents)} unique parent cells at resolution {parent_resolution}.")
    return parents