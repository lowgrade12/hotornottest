from __future__ import annotations
from dataclasses import dataclass, fields

# Maps restash.yml camelCase setting keys → Settings field names.
_PLUGIN_KEY_MAP = {
    "tasteHalfLifeDays": "taste_half_life_days",
    "cooldownDays": "cooldown_days",
    "freshWeight": "fresh_weight",
    "wildcardPercent": "wildcard_percent",
    "respectManualRatings": "respect_manual_ratings",
    "mirrorToRating100": "mirror_to_rating100",
    "excludeTagName": "exclude_tag_name",
    "dryRun": "dry_run",
    "disablePluginsBeforeRun": "disable_plugins_before_run",
}


@dataclass
class Settings:
    taste_half_life_days: float = 90.0
    cooldown_days: float = 21.0
    fresh_weight: float = 1.0
    wildcard_percent: float = 2.0
    respect_manual_ratings: bool = False
    mirror_to_rating100: bool = False
    exclude_tag_name: str = "[Restash: Exclude]"
    dry_run: bool = True
    disable_plugins_before_run: bool = False
    # --- algorithm constants (spec §4 defaults), exposed for tuning ---
    o_event_value: float = 4.0
    play_event_value: float = 1.0
    abandonment_penalty: float = -0.5
    completion_floor: float = 0.25
    direct_scale: float = 6.0
    confidence_events: float = 5.0
    novelty_strength: float = 0.3
    novelty_half_life_days: float = 30.0
    rediscovery_max_days: float = 180.0
    just_watched_days: float = 2.0
    satiation_threshold: float = 0.25
    satiation_floor: float = 0.3
    satiation_window_days: float = 7.0
    jitter_amplitude: float = 0.06
    wildcard_low_conf_max_events: int = 1
    wildcard_band_low: float = 85.0
    wildcard_band_high: float = 95.0
    wildcard_pool_low: float = 0.40
    wildcard_pool_high: float = 0.70
    favorite_affinity_bonus: float = 1.0
    favorite_percentile_floor: float = 0.75
    ingredient_w_perf: float = 0.45
    ingredient_w_tag: float = 0.35
    ingredient_w_studio: float = 0.10
    ingredient_w_quality: float = 0.10
    perf_w_scenes: float = 0.40
    perf_w_affinity: float = 0.20
    perf_w_fresh: float = 0.15
    perf_w_supply: float = 0.15
    perf_w_novelty: float = 0.10
    scene_rating_weight: float = 0.5
    abandonment_completion_max: float = 0.5
    perf_scenes_shrinkage_k: float = 3.0
    direct_half_life_days: float = 365.0
    rediscovery_bonus: float = 0.40
    # --- write-layer / operational settings (Phase 5) ---
    write_chunk_size: int = 100
    write_max_retries: int = 3
    write_backoff_base: float = 0.5
    write_limit: int = 0
    write_only_scene_ids: tuple[str, ...] = ()

    @classmethod
    def from_plugin_settings(cls, plugin_cfg: dict | None) -> "Settings":
        s = cls()
        if not plugin_cfg:
            return s
        bool_fields = {f.name for f in fields(cls) if f.type == "bool"}
        for plugin_key, field_name in _PLUGIN_KEY_MAP.items():
            if plugin_key not in plugin_cfg:
                continue
            value = plugin_cfg[plugin_key]
            if value is None or (isinstance(value, str) and value.strip() == ""):
                continue
            if field_name in bool_fields:
                value = bool(value)
            elif field_name == "exclude_tag_name":
                value = str(value)
            else:
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    continue
            setattr(s, field_name, value)
        return s
