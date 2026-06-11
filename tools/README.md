# Tools

Utility scripts for sim-laps-client.

## Files

- `generate_car_tuning_catalog.py`
  - Parses protobuf car setup and limits files from `extracted/content/cars/`.
  - Generates `src/core/data/car_tuning_catalog.json` with per-car parameter counts.
  - Used for AI prompt generation when asking about car tuning.

- `parse_all_logs.py`
  - Parses all ACE log files from a configured directory.
  - Prints session, lap, sector, and tyre compound summaries to stdout.
  - Useful for quick inspection of raw log data outside the GUI.

## Notes

- `generate_car_tuning_catalog.py` requires the extracted car data to exist under `extracted/content/cars/`.
- `parse_all_logs.py` points to a hardcoded `LOG_DIR` — update as needed for your environment.