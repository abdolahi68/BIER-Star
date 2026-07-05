# BIER_visualization.py

import plotly.graph_objects as go
import numpy as np
from . import bier_helpers

# --- Configuration ---
R_EARTH_KM = 6371
OCEAN_COLOR = 'rgba(60, 90, 180, 1.0)'
GRID_COLOR = 'rgba(128, 128, 128, 0.3)'
GRID_WIDTH = 1
LABEL_COLOR = 'rgba(220, 220, 220, 0.8)'
LABEL_SIZE = 8
PATH_COLOR = 'rgba(255, 0, 255, 1.0)' # Magenta for high visibility
PATH_WIDTH = 4
TARGET_COLOR = 'rgba(0, 255, 255, 1.0)' # Cyan
OTHER_COLOR = 'rgba(150, 150, 150, 0.7)' # Gray
# --- MODIFICATION START ---
H3_GRID_COLOR = 'rgba(255, 255, 0, 0.5)' # Yellow, semi-transparent
# --- MODIFICATION END ---


# --- Helper function ---
def latlon_to_xyz(lat, lon, radius):
    """Converts Lat/Lon to 3D Cartesian coordinates."""
    lat_rad, lon_rad = np.radians(lat), np.radians(lon)
    x = radius * np.cos(lat_rad) * np.cos(lon_rad)
    y = radius * np.cos(lat_rad) * np.sin(lon_rad)
    z = radius * np.sin(lat_rad)
    return x, y, z

# ------------------------------------------------------------
# Main Plotting Function
# ------------------------------------------------------------
# --- MODIFICATION START ---
# Added 'h3_grid_boundaries' parameter
def plot_bier_forwarding_paths(sh, t, sat_map, network_edges, all_paths, title="BIER Multicast Visualization", all_airplanes=None, target_airplanes=None, h3_grid_boundaries=None):
# --- MODIFICATION END ---
    """
    Main function to generate the 3D visualization.
    Shows all airplanes and highlights multicast targets.
    """
    print("[Visualizer] Generating 3D plot...")
    fig = go.Figure()

    # Layer 1: Earth Sphere & Constellation (no changes)
    u, v = np.mgrid[-np.pi:np.pi:150j, 0:np.pi:75j]
    x_e, y_e, z_e = R_EARTH_KM * np.cos(u) * np.sin(v), R_EARTH_KM * np.sin(u) * np.sin(v), R_EARTH_KM * np.cos(v)
    fig.add_trace(go.Surface(x=x_e, y=y_e, z=z_e, colorscale=[[0, OCEAN_COLOR], [1, OCEAN_COLOR]], showscale=False, name='Earth', hoverinfo='none'))

    # --- MODIFICATION START ---
    # Layer 1.5: H3 Geographical Partition Grid
    if h3_grid_boundaries:
        h3_x, h3_y, h3_z = [], [], []
        for boundary in h3_grid_boundaries:
            # Each 'boundary' is a list of (lat, lon) tuples for a single H3 cell
            for i in range(len(boundary) + 1):  # Add an extra point to close the polygon
                lat, lon = boundary[i % len(boundary)]
                x, y, z = latlon_to_xyz(lat, lon, R_EARTH_KM + 0.5) # Elevate slightly above surface
                h3_x.append(x)
                h3_y.append(y)
                h3_z.append(z)
            # Add a 'None' value to create a break in the line trace between cells
            h3_x.append(None)
            h3_y.append(None)
            h3_z.append(None)

        fig.add_trace(go.Scatter3d(
            x=h3_x, y=h3_y, z=h3_z,
            mode='lines',
            line=dict(width=1, color=H3_GRID_COLOR),
            hoverinfo='none',
            name='H3 Grid'
        ))
    # --- MODIFICATION END ---

    sats_x, sats_y, sats_z, sats_labels = [], [], [], []
    for sat_id, sat in sat_map.items():
        x, y, z = latlon_to_xyz(sat.latitude[t-1], sat.longitude[t-1], R_EARTH_KM + sat.altitude[t-1])
        sats_x.append(x); sats_y.append(y); sats_z.append(z); sats_labels.append(str(sat_id))

    grid_x, grid_y, grid_z = [], [], []
    for u_id, v_id in network_edges:
        try:
            u_sat = sat_map.get(int(str(u_id).split('_')[-1]))
            v_sat = sat_map.get(int(str(v_id).split('_')[-1]))
            if u_sat and v_sat:
                x1, y1, z1 = latlon_to_xyz(u_sat.latitude[t-1], u_sat.longitude[t-1], R_EARTH_KM + u_sat.altitude[t-1])
                x2, y2, z2 = latlon_to_xyz(v_sat.latitude[t-1], v_sat.longitude[t-1], R_EARTH_KM + v_sat.altitude[t-1])
                grid_x.extend([x1, x2, None]); grid_y.extend([y1, y2, None]); grid_z.extend([z1, z2, None])
        except (ValueError, IndexError): continue
    fig.add_trace(go.Scatter3d(x=grid_x, y=grid_y, z=grid_z, mode='lines', line=dict(width=GRID_WIDTH, color=GRID_COLOR), hoverinfo='none', name='Grid'))
    fig.add_trace(go.Scatter3d(x=sats_x, y=sats_y, z=sats_z, mode='markers+text', text=sats_labels, textposition='top center', textfont=dict(size=LABEL_SIZE, color=LABEL_COLOR), marker=dict(size=1.5, color='rgba(200, 200, 200, 0.5)'), hoverinfo='text', name='Constellation'))

    # Layer 2: BIER Forwarding Path(s) (no changes)
    for i, path_ids in enumerate(all_paths):
        path_x, path_y, path_z = [], [], []
        for sat_id in path_ids:
            try:
                sat = sat_map.get(int(str(sat_id).split('_')[-1]))
                if sat:
                    x, y, z = latlon_to_xyz(sat.latitude[t-1], sat.longitude[t-1], R_EARTH_KM + sat.altitude[t-1])
                    path_x.append(x); path_y.append(y); path_z.append(z)
            except (ValueError, IndexError): continue
        if len(path_x) > 1:
            fig.add_trace(go.Scatter3d(x=path_x, y=path_y, z=path_z, mode='lines+markers', line=dict(width=PATH_WIDTH, color=PATH_COLOR), marker=dict(size=3.5, color=PATH_COLOR), name=f'Path {i+1}'))

    # (The rest of the function for plotting airplanes remains the same)
    target_ids = {p.id for p in target_airplanes} if target_airplanes else set()

    # Layer 3: Plot Non-Target Airplanes
    if all_airplanes:
        other_x, other_y, other_z, other_text = [], [], [], []
        for plane in all_airplanes:
            if plane.id not in target_ids:
                px, py, pz = latlon_to_xyz(plane.latitude, plane.longitude, R_EARTH_KM + 10) # Elevate slightly
                other_x.append(px); other_y.append(py); other_z.append(pz)
                other_text.append(f"Airplane: {plane.id}")
        fig.add_trace(go.Scatter3d(x=other_x, y=other_y, z=other_z, mode='markers', marker=dict(size=2.5, color=OTHER_COLOR, symbol='circle'), hoverinfo='text', text=other_text, name='Other Airplanes'))

    # Layer 4: Plot Target Airplanes and Access Links
    if target_airplanes:
        egress_sats = {path[-1] for path in all_paths if path}
        access_x, access_y, access_z = [], [], []
        target_x, target_y, target_z, target_text = [], [], [], []
        for plane in target_airplanes:
            px, py, pz = latlon_to_xyz(plane.latitude, plane.longitude, R_EARTH_KM + 10)
            target_x.append(px); target_y.append(py); target_z.append(pz)
            target_text.append(f"DESTINATION: {plane.id}")

            egress_sat_obj = bier_helpers.find_nearest_satellite_at_time(plane, sat_map, t) #
            if egress_sat_obj and (f"satellite_{egress_sat_obj.id}" in egress_sats or egress_sat_obj.id in egress_sats):
                sx, sy, sz = latlon_to_xyz(egress_sat_obj.latitude[t-1], egress_sat_obj.longitude[t-1], R_EARTH_KM + egress_sat_obj.altitude[t-1])
                access_x.extend([sx, px, None]); access_y.extend([sy, py, None]); access_z.extend([sz, pz, None])

        fig.add_trace(go.Scatter3d(x=target_x, y=target_y, z=target_z, mode='markers', marker=dict(size=5, color=TARGET_COLOR, symbol='diamond'), hoverinfo='text', text=target_text, name='Target Airplanes'))
        fig.add_trace(go.Scatter3d(x=access_x, y=access_y, z=access_z, mode='lines', line=dict(width=2, color=TARGET_COLOR, dash='dot'), hoverinfo='none', name='Access Links'))

    # Final Layout
    fig.update_layout(
        title_text=title, title_x=0.5,
        scene=dict(xaxis=dict(visible=False), yaxis=dict(visible=False), zaxis=dict(visible=False), aspectmode='data', bgcolor='rgb(10,10,20)'),
        margin={"r":0,"t":40,"l":0,"b":0},
        legend=dict(yanchor="top", y=0.99, xanchor="left", x=0.01, bgcolor='rgba(0,0,0,0.5)', font=dict(color='white'))
    )
    print("[Visualizer] Displaying figure...")
    fig.show()