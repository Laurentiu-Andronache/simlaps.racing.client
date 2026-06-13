from src.core.car_tuning_catalog import format_tuning_block, get_tuning_params


class TestCarTuningCatalog:
    def test_get_tuning_params_gt3rs_verified_controls(self):
        params = get_tuning_params("Porsche 992 GT3 RS")

        assert params is not None
        assert len(params) == 19
        labels = {param["label"]: param["settings_count"] for param in params}
        # Original basic params preserved
        assert labels["Front tyre pressure"] == 16
        assert labels["Rear tyre pressure"] == 16
        assert labels["Front ride height"] == 3
        assert labels["Rear ride height"] == 4
        assert labels["Fuel load"] == 90
        # Enhanced params added
        assert labels["Brake bias"] == 21
        assert labels["Front spring rate"] == 12
        assert labels["Rear spring rate"] == 12
        assert labels["Front anti-roll bar"] == 12
        assert labels["Rear anti-roll bar"] == 12
        assert labels["Front bump (compression)"] == 20
        assert labels["Front rebound"] == 20
        assert labels["Rear bump (compression)"] == 20
        assert labels["Rear rebound"] == 20
        assert labels["Rear wing / downforce"] == 10

    def test_get_tuning_params_sf25_partial_name_match(self):
        params = get_tuning_params("Ferrari SF 25")

        assert params is not None
        assert len(params) == 24
        labels = {param["label"]: param["settings_count"] for param in params}
        # Original basic params preserved
        assert labels["Front tyre pressure"] == 121
        assert labels["Rear camber"] == 17
        assert labels["Fuel load"] == 120
        # Enhanced params (prototype — has fast/slow bump/rebound)
        assert labels["Brake bias"] == 41
        assert labels["Front spring rate"] == 30
        assert labels["Rear spring rate"] == 30
        assert labels["Front anti-roll bar"] == 25
        assert labels["Rear anti-roll bar"] == 25
        assert labels["Front bump fast (compression)"] == 30
        assert labels["Front bump slow (compression)"] == 30
        assert labels["Front rebound fast"] == 30
        assert labels["Front rebound slow"] == 30
        assert labels["Rear bump fast (compression)"] == 30
        assert labels["Rear bump slow (compression)"] == 30
        assert labels["Rear rebound fast"] == 30
        assert labels["Rear rebound slow"] == 30
        assert labels["Rear wing / downforce"] == 30
        assert labels["Front wing / splitter"] == 25
        assert labels["Differential power lock"] == 25
        assert labels["Differential coast lock"] == 25

    def test_get_tuning_params_gt3_cup_generalized_detection(self):
        params = get_tuning_params("Porsche 992 GT3 Cup")

        assert params is not None
        assert len(params) == 22
        labels = {param["label"]: param["settings_count"] for param in params}
        # Original basic params preserved
        assert labels["Front tyre pressure"] == 151
        assert labels["Rear tyre pressure"] == 151
        assert labels["Front camber"] == 26
        assert labels["Rear camber"] == 26
        assert labels["Front toe"] == 41
        assert labels["Rear toe"] == 41
        assert labels["Fuel load"] == 111
        # Enhanced params
        assert labels["Brake bias"] == 41
        assert labels["Front spring rate"] == 20
        assert labels["Rear spring rate"] == 20
        assert labels["Front anti-roll bar"] == 20
        assert labels["Rear anti-roll bar"] == 20
        assert labels["Front bump (compression)"] == 30
        assert labels["Front rebound"] == 30
        assert labels["Rear bump (compression)"] == 30
        assert labels["Rear rebound"] == 30
        assert labels["Rear wing / downforce"] == 20
        assert labels["Front splitter / aero balance"] == 15
        assert labels["Differential power lock"] == 20
        assert labels["Differential coast lock"] == 20

    def test_get_tuning_params_ferrari_296_gt3_generalized_detection(self):
        params = get_tuning_params("Ferrari 296 GT3")

        assert params is not None
        assert len(params) == 20
        labels = {param["label"]: param["settings_count"] for param in params}
        # Original basic params preserved
        assert labels["Front tyre pressure"] == 151
        assert labels["Rear tyre pressure"] == 151
        assert labels["Front camber"] == 21
        assert labels["Rear camber"] == 21
        assert labels["Fuel load"] == 120
        # Enhanced params
        assert labels["Brake bias"] == 41
        assert labels["Front spring rate"] == 20
        assert labels["Rear spring rate"] == 20
        assert labels["Front anti-roll bar"] == 20
        assert labels["Rear anti-roll bar"] == 20
        assert labels["Front bump (compression)"] == 30
        assert labels["Front rebound"] == 30
        assert labels["Rear bump (compression)"] == 30
        assert labels["Rear rebound"] == 30
        assert labels["Rear wing / downforce"] == 20
        assert labels["Front splitter / aero balance"] == 15
        assert labels["Differential power lock"] == 20
        assert labels["Differential coast lock"] == 20

    def test_format_tuning_block_rs3_uses_verified_catalog_entries(self):
        block = format_tuning_block("Audi RS 3 Sportback")

        assert "CAR SETUP PARAMETERS (available on this car in AC Evo):" in block
        assert "- Front tyre pressure  [16 selectable settings]" in block
        assert "- Rear tyre pressure  [16 selectable settings]" in block
        assert "- Fuel load  [55 selectable settings]" in block
        # Rear camber is present in the verified catalog with 11 settings
        assert "- Rear camber  [11 selectable settings]" in block
        # Brake bias added as new enhanced param
        assert "- Brake bias  [21 selectable settings]" in block
        # Category header present for non-basic groups
        assert "--- Brakes ---" in block

    def test_format_tuning_block_gt3_cup_shows_category_grouping(self):
        """Verify that category-level grouping headers appear for a GT3 car."""
        block = format_tuning_block("Porsche 992 GT3 Cup")

        assert "CAR SETUP PARAMETERS (available on this car in AC Evo):" in block
        assert "--- Brakes ---" in block
        assert "--- Suspension ---" in block
        assert "--- Dampers ---" in block
        assert "--- Aero ---" in block
        assert "--- Drivetrain ---" in block
        # Basic group has no header
        assert "--- Basic ---" not in block
        # Footer present
        assert "When recommending setup changes, only suggest adjustments from the above list." in block

    def test_unknown_car_returns_empty_tuning_block(self):
        assert get_tuning_params("Unknown Car") is None
        assert format_tuning_block("Unknown Car") == ""
