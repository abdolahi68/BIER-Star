BEAMS_PER_SATELLITE = 32   ### for starlink the number of beams are 48, for onweb is 32
YETI_RESERVED_ISLS_PER_SATELLITE = None
DESTINATION_RESOLUTION = 2
ACCESS_ASSIGNMENT_PARENT_RESOLUTION = 0

DEFAULT_SOURCE_PARENT_CELL_R0 = "801bfffffffffff"
DEFAULT_DESTINATION_PARENT_CELLS_SET1 = (
    "8029fffffffffff", "8027fffffffffff", "802bfffffffffff", "8049fffffffffff", "8045fffffffffff",
)
DEFAULT_DESTINATION_PARENT_CELLS_SET2 = (
    "8019fffffffffff", "801ffffffffffff", "803ffffffffffff", "802dfffffffffff", "8039fffffffffff",
)
DEFAULT_DESTINATION_PARENT_SETS = (
    ("set1", DEFAULT_DESTINATION_PARENT_CELLS_SET1),
    ("set2", DEFAULT_DESTINATION_PARENT_CELLS_SET2),
)
DEFAULT_SERVED_CELL_COUNTS = (4, 8, 12, 16, 20, 24, 28, 32)


class ExperimentSettings:
    def __init__(self):
        self.served_cell_counts = DEFAULT_SERVED_CELL_COUNTS
        self.source_parent_cell_r0 = DEFAULT_SOURCE_PARENT_CELL_R0
        self.destination_parent_sets_r0 = DEFAULT_DESTINATION_PARENT_SETS
        self.fixed_source_node = None

        self.enable_visualization = False
        self.visualize_cases = (32,)
        self.visualization_methods = ()
        self.visualization_output_dir = None
        self.show_visualization = True

    def update(self, values):
        for name, value in values.items():
            if not hasattr(self, name):
                raise TypeError(f"Unknown experiment setting: {name}")
            setattr(self, name, value)
        return self


class ExperimentRun:
    def __init__(self):
        self.G = None
        self.sat_map = None
        self.sh = None
        self.t = None
        self.all_airplanes = None
        self.dataset_file = None
        self.output_prefix = None
        self.settings = ExperimentSettings()

        self.source_node = None
        self.source_parent = None
        self.destination_parents = None
        self.destination_set_label = None
        self.case = None

    def wants_plot(self, method, case_value=None):
        settings = self.settings
        if not settings.enable_visualization:
            return False
        if method not in set(settings.visualization_methods or ()):
            return False
        return settings.visualize_cases is None or case_value in set(map(int, settings.visualize_cases))



class CellReceiver:
    def __init__(self, id, latitude, longitude):
        self.id = id
        self.latitude = latitude
        self.longitude = longitude


class DestinationPlan:
    def __init__(self, cells, receivers, beam_assignment, airplanes_by_cell, available_occupied_cells):
        self.cells = cells
        self.receivers = receivers
        self.beam_assignment = beam_assignment
        self.airplanes_by_cell = airplanes_by_cell
        self.available_occupied_cells = available_occupied_cells
        self.serving_satellite_nodes = {f"satellite_{int(sid)}" for sid in self.beam_assignment.values()}
        self.num_serving_satellites = len(self.serving_satellite_nodes)
        self.num_served_airplanes = sum(
            len(self.airplanes_by_cell.get(cell, [])) for cell in self.cells
        )

    def subset(self, selected_cells):
        selected = list(selected_cells)
        airplanes_by_cell = {cell: list(self.airplanes_by_cell.get(cell, [])) for cell in selected}
        receivers = [airplane for cell in selected for airplane in airplanes_by_cell[cell]]
        return DestinationPlan(
            cells=selected,
            receivers=receivers,
            beam_assignment={cell: self.beam_assignment[cell] for cell in selected},
            airplanes_by_cell=airplanes_by_cell,
            available_occupied_cells=self.available_occupied_cells,
        )


EXP1_COLUMNS = [
    "DatasetFile", "Experiment", "DestinationRegionSet", "Case", "Method",
    "SourceParentCell_R0", "DestinationParentCells_R0", "SourceSatellite",
    "NumAvailableOccupiedDestinationCells", "NumServedDestinationCells_B",
    "NumServedAirplanes", "NumServingSatellites", "HeaderBits",
]
