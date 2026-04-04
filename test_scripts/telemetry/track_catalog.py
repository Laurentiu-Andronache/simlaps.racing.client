"""Static track catalog and profile selection helpers for telemetry analysis."""

import os


TRACK_CATALOG = {
    "spa": {
        "name": "Circuit de Spa-Francorchamps",
        "aliases": ["spa", "spa-francorchamps"],
        "default_config": "current",
        "configs": {
            "current": {
                "name": "Current",
                "aliases": ["current", "gp", "full"],
                "corners": [
                    {"id": 1, "name": "La Source", "start": 0.070, "end": 0.100},
                    {"id": 2, "name": "Eau Rouge", "start": 0.100, "end": 0.108},
                    {"id": 3, "name": "Raidillon Left", "start": 0.108, "end": 0.117},
                    {"id": 4, "name": "Raidillon Right", "start": 0.117, "end": 0.126},
                    {"id": 5, "name": "Les Combes 1", "start": 0.128, "end": 0.145},
                    {"id": 6, "name": "Les Combes 2", "start": 0.145, "end": 0.160},
                    {"id": 7, "name": "Les Combes 3", "start": 0.190, "end": 0.208},
                    {"id": 8, "name": "Bruxelles", "start": 0.208, "end": 0.236},
                    {"id": 9, "name": "No Name", "start": 0.330, "end": 0.352},
                    {"id": 10, "name": "Pouhon 1", "start": 0.418, "end": 0.442},
                    {"id": 11, "name": "Pouhon 2", "start": 0.442, "end": 0.472},
                    {"id": 12, "name": "Fagnes 1", "start": 0.488, "end": 0.498},
                    {"id": 13, "name": "Fagnes 2", "start": 0.498, "end": 0.508},
                    {"id": 14, "name": "Campus", "start": 0.710, "end": 0.722},
                    {"id": 15, "name": "Paul Frere", "start": 0.722, "end": 0.752},
                    {"id": 16, "name": "Blanchimont 1", "start": 0.820, "end": 0.832},
                    {"id": 17, "name": "Blanchimont 2", "start": 0.832, "end": 0.842},
                    {"id": 18, "name": "Bus Stop 1", "start": 0.928, "end": 0.942},
                    {"id": 19, "name": "Bus Stop 2", "start": 0.942, "end": 0.955},
                ],
            },
        },
    },
    "laguna_seca": {
        "name": "Laguna Seca",
        "aliases": ["laguna", "laguna-seca", "laguna_seca", "mazda-raceway", "weathertech-raceway"],
        "default_config": "full",
        "configs": {
            "full": {
                "name": "11-Corner Layout",
                "aliases": ["full", "11-corner", "11_corner"],
                "corners": [
                    {"id": 1, "name": "Andretti Hairpin 1", "start": 0.065, "end": 0.090},
                    {"id": 2, "name": "Andretti Hairpin 2", "start": 0.090, "end": 0.120},
                    {"id": 3, "name": "Turn 3", "start": 0.185, "end": 0.225},
                    {"id": 4, "name": "Turn 4", "start": 0.285, "end": 0.325},
                    {"id": 5, "name": "Turn 5", "start": 0.380, "end": 0.425},
                    {"id": 6, "name": "Turn 6", "start": 0.475, "end": 0.520},
                    {"id": 7, "name": "Corkscrew Left", "start": 0.590, "end": 0.615},
                    {"id": 8, "name": "Corkscrew Right", "start": 0.615, "end": 0.645},
                    {"id": 9, "name": "Rainey Curve", "start": 0.700, "end": 0.745},
                    {"id": 10, "name": "Turn 10", "start": 0.810, "end": 0.850},
                    {"id": 11, "name": "Turn 11", "start": 0.900, "end": 0.960},
                ],
            },
        },
    },
    "nordschleife": {
        "name": "Nurburgring Nordschleife",
        "aliases": ["nordschleife", "nurburgring-nordschleife", "nurburg-nordschleife"],
        "default_config": "24h",
        "configs": {
            "24h": {"name": "24H", "aliases": ["24h"], "corners": []},
            "touristenfahrten": {"name": "Touristenfahrten", "aliases": ["touristenfahrten", "tourist"], "corners": []},
        },
    },
    "nurburgring_gp": {
        "name": "Nurburgring Grand Prix",
        "aliases": ["nurburgring-gp", "nurburgring_gp", "gp-strecke"],
        "default_config": "full_gp",
        "configs": {
            "full_gp": {"name": "Full GP", "aliases": ["full", "gp"], "corners": []},
        },
    },
    "brands_hatch": {
        "name": "Brands Hatch",
        "aliases": ["brands", "brands-hatch", "brands_hatch"],
        "default_config": "gp",
        "configs": {
            "gp": {"name": "GP", "aliases": ["gp"], "corners": []},
            "indy": {"name": "Indy", "aliases": ["indy"], "corners": []},
        },
    },
    "cota": {
        "name": "Circuit of the Americas",
        "aliases": ["cota", "circuit-of-the-americas"],
        "default_config": "gp",
        "configs": {
            "gp": {"name": "GP", "aliases": ["gp", "full"], "corners": []},
            "national": {"name": "National", "aliases": ["national"], "corners": []},
        },
    },
    "donington": {
        "name": "Donington Park",
        "aliases": ["donington", "donington-park", "donington_park"],
        "default_config": "gp",
        "configs": {
            "gp": {"name": "Grand Prix", "aliases": ["gp", "grand-prix"], "corners": []},
            "national": {"name": "National", "aliases": ["national"], "corners": []},
        },
    },
    "monza": {
        "name": "Monza",
        "aliases": ["monza"],
        "default_config": "v0_4",
        "configs": {
            "v0_4": {
                "name": "v0.4",
                "aliases": ["v0.4", "v0_4", "full", "gp"],
                "corners": [
                    {"id": 1, "name": "Variante del Rettifilo (T1) Right", "start": 0.060, "end": 0.085},
                    {"id": 2, "name": "Variante del Rettifilo (T2) Left", "start": 0.085, "end": 0.115},
                    {"id": 3, "name": "Curva Grande (T3)", "start": 0.165, "end": 0.230},
                    {"id": 4, "name": "Variante della Roggia (T4) Left", "start": 0.295, "end": 0.320},
                    {"id": 5, "name": "Variante della Roggia (T5) Right", "start": 0.320, "end": 0.350},
                    {"id": 6, "name": "Lesmo 1 (T6)", "start": 0.430, "end": 0.500},
                    {"id": 7, "name": "Lesmo 2 (T7)", "start": 0.535, "end": 0.585},
                    {"id": 8, "name": "Variante Ascari (T8) Left", "start": 0.700, "end": 0.730},
                    {"id": 9, "name": "Variante Ascari (T9) Right", "start": 0.730, "end": 0.755},
                    {"id": 10, "name": "Variante Ascari (T10) Left", "start": 0.755, "end": 0.785},
                    {"id": 11, "name": "Curva Parabolica (T11)", "start": 0.885, "end": 0.970},
                ],
            },
        },
    },
    "bathurst": {
        "name": "Mount Panorama",
        "aliases": ["bathurst", "mount-panorama", "mount_panorama"],
        "default_config": "full",
        "configs": {
            "full": {"name": "Full", "aliases": ["full"], "corners": []},
        },
    },
    "fuji": {
        "name": "Fuji Speedway",
        "aliases": ["fuji", "fuji-speedway", "fuji_speedway"],
        "default_config": "full",
        "configs": {
            "full": {"name": "Full", "aliases": ["full"], "corners": []},
        },
    },
    "imola": {
        "name": "Imola",
        "aliases": ["imola", "autodromo-enzo-e-dino-ferrari"],
        "default_config": "full",
        "configs": {
            "full": {"name": "Full", "aliases": ["full"], "corners": []},
        },
    },
    "oulton_park": {
        "name": "Oulton Park",
        "aliases": ["oulton", "oulton-park", "oulton_park"],
        "default_config": "international",
        "configs": {
            "international": {"name": "International", "aliases": ["international"], "corners": []},
            "foster": {"name": "Foster", "aliases": ["foster"], "corners": []},
        },
    },
    "road_atlanta": {
        "name": "Road Atlanta",
        "aliases": ["road-atlanta", "road_atlanta"],
        "default_config": "full",
        "configs": {
            "full": {"name": "Full", "aliases": ["full"], "corners": []},
        },
    },
    "red_bull_ring": {
        "name": "Red Bull Ring",
        "aliases": ["red-bull-ring", "red_bull_ring", "rbr"],
        "default_config": "full",
        "configs": {
            "full": {"name": "Full", "aliases": ["full", "gp"], "corners": []},
        },
    },
    "suzuka": {
        "name": "Suzuka Circuit",
        "aliases": ["suzuka", "suzuka-circuit", "suzuka_circuit"],
        "default_config": "full",
        "configs": {
            "full": {"name": "Full", "aliases": ["full", "gp"], "corners": []},
        },
    },
}


def build_track_profile(track_key, config_key):
    track = TRACK_CATALOG[track_key]
    config = track["configs"][config_key]
    return {
        "track_key": track_key,
        "track_name": track["name"],
        "config_key": config_key,
        "config_name": config["name"],
        "display_name": f"{track['name']} ({config['name']})",
        "corners": config.get("corners", []),
    }


def select_track_profile(path=None, track_name=None, config_name=None):
    if track_name:
        for track_key, track in TRACK_CATALOG.items():
            labels = [track_key, *track.get("aliases", [])]
            if track_name.lower() in labels:
                if config_name:
                    for config_key, config in track["configs"].items():
                        config_labels = [config_key, *config.get("aliases", [])]
                        if config_name.lower() in config_labels:
                            return track_key, build_track_profile(track_key, config_key)
                    raise ValueError(f"Unknown config '{config_name}' for track '{track_name}'")
                return track_key, build_track_profile(track_key, track["default_config"])
        raise ValueError(f"Unknown track '{track_name}'")

    path_l = os.path.normpath(path).lower() if path else ""
    for track_key, track in TRACK_CATALOG.items():
        if any(alias in path_l for alias in track.get("aliases", [])):
            for config_key, config in track["configs"].items():
                if any(alias in path_l for alias in config.get("aliases", [])):
                    return track_key, build_track_profile(track_key, config_key)
            return track_key, build_track_profile(track_key, track["default_config"])

    return None, None
