"""
h3_bier/bier_visualizer.py

This module provides visualization functions specifically for the H3-BIER protocol.
"""
import plotly.graph_objects as go
import numpy as np
import h3
import networkx as nx

R_EARTH_KM = 6371
OCEAN_COLOR = 'rgba(60, 90, 180, 1.0)'
SATELLITE_PATH_COLOR = 'rgba(255, 255, 0, 1.0)'
SATELLITE_PATH_WIDTH = 4
ISL_TOPOLOGY_COLOR = 'rgba(128, 128, 128, 0.4)'
ISL_TOPOLOGY_WIDTH = 0.5


def latlon_to_xyz(lat, lon, radius):
    lat_rad, lon_rad = np.radians(lat), np.radians(lon)
    x = radius * np.cos(lat_rad) * np.cos(lon_rad)
    y = radius * np.cos(lat_rad) * np.sin(lon_rad)
    z = radius * np.sin(lat_rad)
    return x, y, z

def get_h3_cell_lines(h3_indices, altitude_km):
    lx, ly, lz = [], [], []
    radius = R_EARTH_KM + altitude_km
    for h3_idx in h3_indices:
        try:
            # Using v3 API: h3.h3_to_geo_boundary
            b = h3.h3_to_geo_boundary(h3_idx, geo_json=False)
            lat_coords, lon_coords = zip(*b)
            x, y, z = latlon_to_xyz(list(lat_coords) + [lat_coords[0]], list(lon_coords) + [lon_coords[0]], radius)
            lx.extend(list(x) + [None]); ly.extend(list(y) + [None]); lz.extend(list(z) + [None])
        except Exception:
            continue
    return lx, ly, lz

def get_path_from_trace(trace_tree):
    if not trace_tree: return []
    paths = []
    def dfs(node, current_path):
        current_path.append(node.addr)
        if not node.branches:
            paths.append(list(current_path))
        else:
            for branch in node.branches:
                dfs(branch, list(current_path))
    dfs(trace_tree, [])
    return paths

def plot_h3_bier_trace(sh, t, sat_map, G, source_cell_res1, destination_cells_res1, multicast_cell_mst_res0, forwarding_trace_tree, h3_altitude_km):
    print("\n[Visualizer] Generating H3-BIER 3D plot...")
    fig = go.Figure()
    u, v = np.mgrid[-np.pi:np.pi:150j, 0:np.pi:75j]
    x_e = R_EARTH_KM * np.cos(u) * np.sin(v)
    y_e = R_EARTH_KM * np.sin(u) * np.sin(v)
    z_e = R_EARTH_KM * np.cos(v)
    fig.add_trace(go.Surface(x=x_e, y=y_e, z=z_e, colorscale=[[0, OCEAN_COLOR], [1, OCEAN_COLOR]], showscale=False, name='Earth', hoverinfo='none'))

    
    # Prepare lists for satellite coordinates AND their IDs for text labels
    sats_x, sats_y, sats_z, sats_text = [], [], [], []
    for sat in sat_map.values():
        x, y, z = latlon_to_xyz(sat.latitude[t-1], sat.longitude[t-1], R_EARTH_KM + sat.altitude[t-1])
        sats_x.append(x)
        sats_y.append(y)
        sats_z.append(z)
        sats_text.append(str(sat.id)) # Add satellite ID as text

    # Add satellite markers and their ID labels to the plot
    fig.add_trace(go.Scatter3d(
        x=sats_x, 
        y=sats_y, 
        z=sats_z, 
        text=sats_text, # Use the IDs as text labels
        mode='markers+text', # Display both the marker and the text
        marker=dict(size=2, color='rgba(200, 200, 200, 0.8)'),
        textfont=dict(size=7, color='rgba(220, 220, 220, 0.7)'),
        name='Constellation'
    ))
    

    isl_x, isl_y, isl_z = [], [], []
    for u_str, v_str in G.edges():
        try:
            u_id = int(u_str.split('_')[1])
            v_id = int(v_str.split('_')[1])
            u_sat = sat_map.get(u_id)
            v_sat = sat_map.get(v_id)

            if u_sat and v_sat:
                u_x, u_y, u_z = latlon_to_xyz(u_sat.latitude[t-1], u_sat.longitude[t-1], R_EARTH_KM + u_sat.altitude[t-1])
                v_x, v_y, v_z = latlon_to_xyz(v_sat.latitude[t-1], v_sat.longitude[t-1], R_EARTH_KM + v_sat.altitude[t-1])
                isl_x.extend([u_x, v_x, None])
                isl_y.extend([u_y, v_y, None])
                isl_z.extend([u_z, v_z, None])
        except (ValueError, IndexError):
            continue
            
    fig.add_trace(go.Scatter3d(
        x=isl_x, y=isl_y, z=isl_z,
        mode='lines',
        line=dict(width=ISL_TOPOLOGY_WIDTH, color=ISL_TOPOLOGY_COLOR),
        name='ISL Topology',
        hoverinfo='none'
    ))

    sc_x, sc_y, sc_z = get_h3_cell_lines([source_cell_res1], 0)
    fig.add_trace(go.Scatter3d(x=sc_x, y=sc_y, z=sc_z, mode='lines', line=dict(width=4, color='rgba(0, 255, 0, 1.0)'), name='Source Cell (Ground - Res 1)'))
    dc_x, dc_y, dc_z = get_h3_cell_lines(destination_cells_res1, 0)
    fig.add_trace(go.Scatter3d(x=dc_x, y=dc_y, z=dc_z, mode='lines', line=dict(width=4, color='rgba(255, 0, 0, 1.0)'), name='Destination Cells (Ground - Res 1)'))
    
    virtual_layer_cells_res0 = set(multicast_cell_mst_res0.nodes())
    ic_x, ic_y, ic_z = get_h3_cell_lines(virtual_layer_cells_res0, h3_altitude_km)
    fig.add_trace(go.Scatter3d(x=ic_x, y=ic_y, z=ic_z, mode='lines', line=dict(width=2, dash='dot', color='rgba(0, 255, 255, 0.7)'), name='Virtual Layer (Res 0)'))

    all_paths = get_path_from_trace(forwarding_trace_tree)
    for i, path_ids in enumerate(all_paths):
        path_x, path_y, path_z = [], [], []
        for sat_id in path_ids:
            sat = sat_map.get(sat_id)
            if sat:
                x, y, z = latlon_to_xyz(sat.latitude[t-1], sat.longitude[t-1], R_EARTH_KM + sat.altitude[t-1])
                path_x.append(x); path_y.append(y); path_z.append(z)
        fig.add_trace(go.Scatter3d(x=path_x, y=path_y, z=path_z, mode='lines+markers', line=dict(width=SATELLITE_PATH_WIDTH, color=SATELLITE_PATH_COLOR), marker=dict(size=3), name=f'Forwarding Path {i+1}'))

    fig.update_layout(title_text=f"H3-BIER Multicast Visualization ({h3_altitude_km} km Virtual Layer)", title_x=0.5, scene=dict(xaxis=dict(visible=False), yaxis=dict(visible=False), zaxis=dict(visible=False), aspectmode='cube', bgcolor='rgb(10,10,20)'), margin={"r":0,"t":40,"l":0,"b":0})
    print("[Visualizer] Displaying H3-BIER figure...")
    fig.show()