# bier_helpers.py

from math import radians, cos, sin, asin, sqrt
import numpy as np

class GroundUser:
    """A generic class for a user on the ground (can be a source or target)."""
    def __init__(self, lat, lon):
        self.latitude = lat
        self.longitude = lon

def distance_between_satellite_and_user(groundstation, satellite, t):
    """Calculates the distance between a ground entity and a satellite."""
    longitude1 = groundstation.longitude
    latitude1 = groundstation.latitude
    longitude2 = satellite.longitude[t-1]
    latitude2 = satellite.latitude[t-1]
    longitude1, latitude1, longitude2, latitude2 = map(radians, [float(longitude1), float(latitude1), float(longitude2), float(latitude2)])
    dlon = longitude2 - longitude1
    dlat = latitude2 - latitude1
    a = sin(dlat/2)**2 + cos(latitude1) * cos(latitude2) * sin(dlon/2)**2
    distance = 2 * asin(sqrt(a)) * 6371.0 * 1000
    distance = np.round(distance/1000, 3)
    return distance

def find_nearest_satellite_at_time(user, sat_map, t):
    """Finds the single satellite closest to a user."""
    nearest_satellite = None
    min_distance = float('inf')
    for satellite in sat_map.values():
        dist = distance_between_satellite_and_user(user, satellite, t)
        if dist < min_distance:
            min_distance = dist
            nearest_satellite = satellite
    return nearest_satellite