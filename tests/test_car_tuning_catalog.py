from src.core.car_tuning_catalog import format_tuning_block, get_tuning_params


class TestCarTuningCatalog:
    def test_get_tuning_params_gt3rs_verified_controls(self):
        params = get_tuning_params("Porsche 992 GT3 RS")

        assert params is not None
        labels = {param["label"]: param["settings_count"] for param in params}
        assert labels["Front tyre pressure"] == 16
        assert labels["Rear tyre pressure"] == 16
        assert labels["Front ride height"] == 3
        assert labels["Rear ride height"] == 4
        assert labels["Fuel load"] == 90

    def test_get_tuning_params_sf25_partial_name_match(self):
        params = get_tuning_params("Ferrari SF 25")

        assert params is not None
        labels = {param["label"]: param["settings_count"] for param in params}
        assert labels["Front tyre pressure"] == 121
        assert labels["Rear camber"] == 17
        assert labels["Fuel load"] == 120

    def test_get_tuning_params_gt3_cup_generalized_detection(self):
        params = get_tuning_params("Porsche 992 GT3 Cup")

        assert params is not None
        labels = {param["label"]: param["settings_count"] for param in params}
        assert labels["Front tyre pressure"] == 151
        assert labels["Rear tyre pressure"] == 151
        assert labels["Front camber"] == 26
        assert labels["Rear camber"] == 26
        assert labels["Front toe"] == 41
        assert labels["Rear toe"] == 41
        assert labels["Fuel load"] == 111

    def test_get_tuning_params_ferrari_296_gt3_generalized_detection(self):
        params = get_tuning_params("Ferrari 296 GT3")

        assert params is not None
        labels = {param["label"]: param["settings_count"] for param in params}
        assert labels["Front tyre pressure"] == 151
        assert labels["Rear tyre pressure"] == 151
        assert labels["Front camber"] == 21
        assert labels["Rear camber"] == 21
        assert labels["Fuel load"] == 120

    def test_format_tuning_block_rs3_uses_verified_catalog_entries(self):
        block = format_tuning_block("Audi RS 3 Sportback")

        assert "CAR SETUP PARAMETERS (available on this car in AC Evo):" in block
        assert "- Front tyre pressure  [16 selectable settings]" in block
        assert "- Rear tyre pressure  [16 selectable settings]" in block
        assert "- Fuel load  [55 selectable settings]" in block
        assert "Rear camber" not in block

    def test_unknown_car_returns_empty_tuning_block(self):
        assert get_tuning_params("Unknown Car") is None
        assert format_tuning_block("Unknown Car") == ""
