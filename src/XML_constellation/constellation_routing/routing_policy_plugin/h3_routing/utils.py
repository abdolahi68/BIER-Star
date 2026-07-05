"""
h3_routing/utils.py

This file contains utility functions used across the H3 routing project,
such as geospatial calculations.
"""

import numpy as np
from math import radians, cos, sin, asin, sqrt

def distance_between_satellite_and_user(groundstation, satellite, t):
    """Calculate the ground-to-satellite distance in kilometers."""
    longitude1 = groundstation.longitude
    latitude1 = groundstation.latitude
    # Satellite longitude/latitude are arrays, so we index by t-1
    longitude2 = satellite.longitude[t-1]
    latitude2 = satellite.latitude[t-1]
    
    # Convert latitude and longitude from degrees to radians
    longitude1, latitude1, longitude2, latitude2 = map(radians, [float(longitude1), float(latitude1),
                                                                 float(longitude2), float(latitude2)])
    
    # Haversine formula
    dlon = longitude2 - longitude1
    dlat = latitude2 - latitude1
    a = sin(dlat/2)**2 + cos(latitude1) * cos(latitude2) * sin(dlon/2)**2
    c = 2 * asin(sqrt(a))
    
    # Earth's average radius in kilometers
    r = 6371.0
    distance = c * r
    
    return np.round(distance, 3)

def find_nearest_satellite(user_location, satellites, t):
    """Find the nearest satellite to the given user."""
    nearest_satellite = None
    min_distance = float('inf')

    for sat_id, satellite in satellites.items():
        dist = distance_between_satellite_and_user(user_location, satellite, t)
        if dist < min_distance:
            min_distance = dist
            nearest_satellite = satellite
            
    return nearest_satellite, min_distance
