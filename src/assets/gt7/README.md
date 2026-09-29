# GT7 car tables

`cars.csv`, `maker.csv` and `cargrp.csv` come from the community database
[ddm999/gt7info](https://github.com/ddm999/gt7info) (`_data/db/`, licence MIT-0).
The relay's telemetry uses them to turn the car id in each GT7 packet into a car name
(`src/scripts/gt7_cars.py`). Do not edit them by hand. Refresh them with
`python3 tools/fetch-gt7-cars.py`, which picks up cars added by a GT7 update.
