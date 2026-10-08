# GT7 reference data

- `cars.csv`, `maker.csv`, `cargrp.csv` come from the community database
  [ddm999/gt7info](https://github.com/ddm999/gt7info) (`_data/db/`, licence MIT-0). The
  relay names the car in each telemetry packet from them (`src/scripts/gt7_cars.py`).
- `index.json` (every GT7 layout) and `signatures.json` (length, bounding box and racing
  line of the layouts that can be recognised) come from
  [jbhoorasingh/gt7-datalogger-track-data](https://github.com/jbhoorasingh/gt7-datalogger-track-data).
  Licences: `LICENSE-track-data`. `src/scripts/gt7_tracks.py` names the track from them.

Do not edit these files by hand. `python3 tools/fetch-gt7-data.py` refreshes the copy
that ships; an install keeps newer copies in `runtime/gt7/` (`racecast gt7-data update`).
