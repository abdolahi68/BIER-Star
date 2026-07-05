"""
h3_routing/visualization.py

This is an enhanced version of the provided visualizer.
It includes functions to:
- Plot specific H3 cells with custom colors (for highlighting).
- Draw the routing path between satellites.
- Integrate these new layers into the main 3D plot.
"""
import plotly.graph_objects as go
import numpy as np
import h3
from collections import defaultdict

print("--- Loading h3_routing/visualization.py module ---")

# --- Configuration (can be adjusted) ---
R_EARTH_KM = 6371
OCEAN_COLOR = 'rgba(60, 90, 180, 1.0)'
SATELLITE_PATH_COLOR = 'rgba(255, 255, 0, 1.0)' # Bright yellow for visibility
SATELLITE_PATH_WIDTH = 4
H3_CELL_HIGHLIGHT_COLOR = 'rgba(0, 255, 255, 0.4)' # Cyan, semi-transparent
# H3_CELL_HIGHLIGHT_ALTITUDE is now passed as a parameter

# --- Core Plotting Functions ---

def latlon_to_xyz(lat_deg, lon_deg, radius):
    """Converts Lat/Lon to 3D Cartesian coordinates."""
    lat_rad, lon_rad = np.radians(lat_deg), np.radians(lon_deg)
    x = radius * np.cos(lat_rad) * np.cos(lon_rad)
    y = radius * np.cos(lat_rad) * np.sin(lon_rad)
    z = radius * np.sin(lat_rad)
    return x, y, z

def get_h3_cell_lines(h3_indices, altitude_km):
    """Generates line coordinates for a set of H3 cells."""
    lx, ly, lz = [], [], []
    radius = R_EARTH_KM + altitude_km
    for h3_idx in h3_indices:
        try:
            # Ensure compatibility with h3 v4+
            b = h3.h3_to_geo_boundary(h3_idx, geo_json=False)
            if not b: continue
            
            lat_coords, lon_coords = zip(*b)
            # Close the loop by repeating the first vertex
            x, y, z = latlon_to_xyz(list(lat_coords) + [lat_coords[0]], 
                                    list(lon_coords) + [lon_coords[0]], radius)
            
            lx.extend(list(x) + [None]) # Use None to break the line between cells
            ly.extend(list(y) + [None])
            lz.extend(list(z) + [None])
        except Exception as e:
            print(f"Warning: Could not draw H3 cell {h3_idx}. Error: {e}")
            continue
    return lx, ly, lz

# MODIFIED: Added 'h3_altitude_km' to the function signature
def plot_constellation_with_h3_route(
    sh, t, source_gs, target_gs,
    all_sats_map, path_sats, traversed_h3_cells, h3_altitude_km
):
    """
    Main function to generate the 3D visualization for the unicast H3 routing.
    """
    print("\n[Visualizer] Generating 3D plot for h3_routing...")
    fig = go.Figure()

    # Layer 1: Earth Sphere
    u, v = np.mgrid[-np.pi:np.pi:150j, 0:np.pi:75j]
    x_e = R_EARTH_KM * np.cos(u) * np.sin(v)
    y_e = R_EARTH_KM * np.sin(u) * np.sin(v)
    z_e = R_EARTH_KM * np.cos(v)
    fig.add_trace(go.Surface(
        x=x_e, y=y_e, z=z_e,
        colorscale=[[0, OCEAN_COLOR], [1, OCEAN_COLOR]],
        showscale=False, name='Earth', hoverinfo='none'
    ))

    # Layer 2: All Satellites in the Shell
    sats_x, sats_y, sats_z = [], [], []
    for sat in all_sats_map.values():
        x, y, z = latlon_to_xyz(sat.latitude[t-1], sat.longitude[t-1], R_EARTH_KM + sat.altitude[t-1])
        sats_x.append(x)
        sats_y.append(y)
        sats_z.append(z)
    
    fig.add_trace(go.Scatter3d(
        x=sats_x, y=sats_y, z=sats_z, mode='markers',
        marker=dict(size=1.8, color='rgba(200, 200, 200, 0.6)'),
        name='Constellation Satellites'
    ))

    # Layer 3: Ground Stations (Source and Target)
    src_x, src_y, src_z = latlon_to_xyz(float(source_gs.latitude), float(source_gs.longitude), R_EARTH_KM)
    tgt_x, tgt_y, tgt_z = latlon_to_xyz(float(target_gs.latitude), float(target_gs.longitude), R_EARTH_KM)
    
    # Safely access .name attribute to prevent errors
    source_name = getattr(source_gs, 'name', 'Source')
    target_name = getattr(target_gs, 'name', 'Target')
    
    fig.add_trace(go.Scatter3d(
        x=[src_x, tgt_x], y=[src_y, tgt_y], z=[src_z, tgt_z], mode='markers',
        marker=dict(size=8, color=['green', 'red'], symbol='diamond'),
        name='Ground Stations', text=[f"Source: {source_name}", f"Target: {target_name}"],
        hoverinfo='text'
    ))

    # Layer 4: Highlighted Traversed H3 Cells
    if traversed_h3_cells:
        # MODIFIED: Use the passed 'h3_altitude_km' parameter
        hx, hy, hz = get_h3_cell_lines(traversed_h3_cells, h3_altitude_km)
        fig.add_trace(go.Scatter3d(
            x=hx, y=hy, z=hz, mode='lines',
            line=dict(width=2, color=H3_CELL_HIGHLIGHT_COLOR),
            name='Traversed H3 Cells (Res 0)'
        ))

    # Layer 5: The Routing Path
    if path_sats:
        path_x, path_y, path_z = [], [], []
        for sat in path_sats:
            x, y, z = latlon_to_xyz(sat.latitude[t-1], sat.longitude[t-1], R_EARTH_KM + sat.altitude[t-1])
            path_x.append(x); path_y.append(y); path_z.append(z)
        
        fig.add_trace(go.Scatter3d(x=path_x, y=path_y, z=path_z, mode='lines', line=dict(width=SATELLITE_PATH_WIDTH, color=SATELLITE_PATH_COLOR), name='Routing Path'))
        fig.add_trace(go.Scatter3d(x=path_x, y=path_y, z=path_z, mode='markers', marker=dict(size=3.5, color=SATELLITE_PATH_COLOR), name='Path Satellites', hoverinfo='none'))

    # Final Layout
    fig.update_layout(
        title_text="H3-Based Unicast Routing Visualization", title_x=0.5,
        showlegend=True,
        legend=dict(yanchor="top", y=0.98, xanchor="left", x=0.01, bgcolor="rgba(255,255,255,0.8)"),
        scene=dict(xaxis=dict(visible=False), yaxis=dict(visible=False), zaxis=dict(visible=False), aspectmode='cube', camera=dict(eye=dict(x=1.5, y=-1.5, z=1.5)), bgcolor='rgb(10,10,20)'),
        margin={"r":0,"t":40,"l":0,"b":0}
    )
    print("[Visualizer] Displaying figure...")
    fig.show()