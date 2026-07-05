# BIER_visualization.py (or constellation_visualizer.py)

"""
Author: Gemini (based on user-provided code)
Date: 2025/06/19
Function: This module provides functions to visualize a satellite constellation,
          specifically for plotting a source, a target, and the shortest path
          between them through the satellite network.
"""

import numpy as np
import plotly.graph_objects as go

# --- Constants and Helper Functions ---
R_EARTH_KM = 6371
OCEAN_COLOR = 'rgba(60, 90, 180, 1.0)'

def latlon_to_xyz(lat_deg, lon_deg, radius):
    """Converts latitude, longitude, and radius into Cartesian X, Y, Z coordinates."""
    lat_rad, lon_rad = np.radians(lat_deg), np.radians(lon_deg)
    x = radius * np.cos(lat_rad) * np.cos(lon_rad)
    y = radius * np.cos(lat_rad) * np.sin(lon_rad)
    z = radius * np.sin(lat_rad)
    return x, y, z

def plot_constellation_with_path(constellation_name, t, sh, source_gs, target_gs, shortest_path, path_sats_map):
    """
    Generates and displays an interactive 3D plot of the constellation, ground stations,
    and the calculated shortest path.
    """
    fig = go.Figure()
    print("--- Generating 3D visualization of the shortest path ---")

    # (Layers 1 and 2 for Earth and Other Satellites are unchanged)
    # --- Layer 1: Draw the Earth ---
    u = np.linspace(0, 2 * np.pi, 100)
    v = np.linspace(0, np.pi, 50)
    x_e = R_EARTH_KM * np.outer(np.cos(u), np.sin(v))
    y_e = R_EARTH_KM * np.outer(np.sin(u), np.sin(v))
    z_e = R_EARTH_KM * np.outer(np.ones(np.size(u)), np.cos(v))
    fig.add_trace(go.Surface(
        x=x_e, y=y_e, z=z_e, colorscale=[[0, OCEAN_COLOR], [1, OCEAN_COLOR]],
        showscale=False, name='Earth', hoverinfo='skip'
    ))

    # --- Layer 2: Plot all satellites in the shell (for context) ---
    other_sats_x, other_sats_y, other_sats_z = [], [], []
    for orbit in sh.orbits:
        for sat in orbit.satellites:
            if f"satellite_{sat.id}" not in shortest_path:
                x, y, z = latlon_to_xyz(sat.latitude[t-1], sat.longitude[t-1], R_EARTH_KM + sat.altitude[t-1])
                other_sats_x.append(x)
                other_sats_y.append(y)
                other_sats_z.append(z)
    fig.add_trace(go.Scatter3d(
        x=other_sats_x, y=other_sats_y, z=other_sats_z,
        mode='markers', marker=dict(size=1.5, color='rgba(128, 128, 128, 0.4)'),
        name='Other Satellites', hoverinfo='skip'
    ))


    # --- Layer 3: Plot Ground Stations and Path Satellites ---
    path_points = {}
    path_points['source_gs'] = latlon_to_xyz(source_gs.latitude, source_gs.longitude, R_EARTH_KM + 2)
    path_points['target_gs'] = latlon_to_xyz(target_gs.latitude, target_gs.longitude, R_EARTH_KM + 2)

    for sat_id_str, sat_obj in path_sats_map.items():
        path_points[sat_id_str] = latlon_to_xyz(sat_obj.latitude[t-1], sat_obj.longitude[t-1], R_EARTH_KM + sat_obj.altitude[t-1])

    gs_path_x = [p[0] for p in path_points.values()]
    gs_path_y = [p[1] for p in path_points.values()]
    gs_path_z = [p[2] for p in path_points.values()]

    # V V V THIS IS THE CORRECTED BLOCK V V V
    # Create descriptive names for hover text, checking if .name attribute exists.
    source_label = f"GS: {source_gs.name}" if hasattr(source_gs, 'name') else "Source Ground Station"
    target_label = f"GS: {target_gs.name}" if hasattr(target_gs, 'name') else "Target Ground Station"
    gs_path_names = [source_label, target_label] + list(path_sats_map.keys())
    # ^ ^ ^ THIS IS THE CORRECTED BLOCK ^ ^ ^

    fig.add_trace(go.Scatter3d(
        x=gs_path_x, y=gs_path_y, z=gs_path_z,
        mode='markers',
        marker=dict(size=4, color='rgba(255, 255, 0, 1.0)', symbol='diamond'),
        name='Path Nodes', text=gs_path_names, hoverinfo='text'
    ))

    # (Layer 4 for Path Links is unchanged)
    # --- Layer 4: Draw the Path Links ---
    start_sat_id = shortest_path[0]
    end_sat_id = shortest_path[-1]
    link_x, link_y, link_z = [], [], []
    link_x.extend([path_points['source_gs'][0], path_points[start_sat_id][0], None])
    link_y.extend([path_points['source_gs'][1], path_points[start_sat_id][1], None])
    link_z.extend([path_points['source_gs'][2], path_points[start_sat_id][2], None])
    link_x.extend([path_points[end_sat_id][0], path_points['target_gs'][0], None])
    link_y.extend([path_points[end_sat_id][1], path_points['target_gs'][1], None])
    link_z.extend([path_points[end_sat_id][2], path_points['target_gs'][2], None])
    for i in range(len(shortest_path) - 1):
        sat1_id = shortest_path[i]
        sat2_id = shortest_path[i+1]
        link_x.extend([path_points[sat1_id][0], path_points[sat2_id][0], None])
        link_y.extend([path_points[sat1_id][1], path_points[sat2_id][1], None])
        link_z.extend([path_points[sat1_id][2], path_points[sat2_id][2], None])
    fig.add_trace(go.Scatter3d(
        x=link_x, y=link_y, z=link_z,
        mode='lines', line=dict(width=3, color='rgba(255, 0, 0, 0.8)'),
        name='Shortest Path', hoverinfo='none'
    ))

    # --- Finalize and Show Plot ---
    # Determine plot title with a fallback for the name attribute
    source_title_name = source_gs.name if hasattr(source_gs, 'name') else "Source"
    target_title_name = target_gs.name if hasattr(target_gs, 'name') else "Target"
    plot_title = f"Shortest Path for '{constellation_name}' at Timeslot {t}<br><sup>{source_title_name} to {target_title_name}</sup>"
    
    fig.update_layout(
        title_text=plot_title, title_x=0.5,
        scene=dict(
            xaxis=dict(visible=False), yaxis=dict(visible=False), zaxis=dict(visible=False),
            aspectmode='cube', camera=dict(eye=dict(x=1.8, y=1.8, z=1.5)), bgcolor='rgb(10,10,20)'
        ),
        margin={"r":0,"t":60,"l":0,"b":0}
    )
    fig.show()