from __future__ import annotations

import hashlib
import time
from typing import Any

import visual_text_gate


DEFAULT_GAME_FIELD_CONFIG = {
    "sample_interval_ms": 180,
    "persistence_frames": 3,
    "stabilize_ms": 300,
    "pixel_tolerance": 1,
    "sensitivity": 1.0,
}


def normalize_game_field_config(raw: Any) -> dict[str, Any]:
    source = raw if isinstance(raw, dict) else {}

    def as_int(key: str, minimum: int, maximum: int) -> int:
        try:
            value = int(source.get(key, DEFAULT_GAME_FIELD_CONFIG[key]))
        except (TypeError, ValueError):
            value = int(DEFAULT_GAME_FIELD_CONFIG[key])
        return max(minimum, min(maximum, value))

    try:
        sensitivity = float(source.get("sensitivity", 1.0))
    except (TypeError, ValueError):
        sensitivity = 1.0

    return {
        "sample_interval_ms": as_int("sample_interval_ms", 80, 1000),
        "persistence_frames": as_int("persistence_frames", 2, 8),
        "stabilize_ms": as_int("stabilize_ms", 120, 1500),
        "pixel_tolerance": as_int("pixel_tolerance", 0, 2),
        "sensitivity": max(0.5, min(2.0, sensitivity)),
    }


def _mask_signature(mask: bytes) -> str:
    return hashlib.blake2b(mask, digest_size=16).hexdigest() if mask else ""


def _density(mask: bytes | bytearray, active_total: int) -> float:
    return sum(mask) / float(max(1, int(active_total or len(mask) or 1)))


def _dilate(mask: bytes | bytearray, width: int, height: int, radius: int) -> bytearray:
    source = bytes(mask or b"")
    if not source or radius <= 0:
        return bytearray(source)
    result = bytearray(len(source))
    active = [index for index, value in enumerate(source) if value]
    for index in active:
        y, x = divmod(index, width)
        for ny in range(max(0, y - radius), min(height, y + radius + 1)):
            row = ny * width
            for nx in range(max(0, x - radius), min(width, x + radius + 1)):
                result[row + nx] = 1
    return result


def _coverage(source: bytes | bytearray, target: bytes | bytearray) -> float:
    source_count = int(sum(source))
    if source_count <= 0:
        return 1.0 if not any(target) else 0.0
    matched = sum(1 for left, right in zip(source, target) if left and right)
    return matched / float(source_count)


def _update_persistence_counts(
    current: bytes | bytearray,
    previous: bytes | bytearray,
    previous_counts: list[int],
    width: int,
    height: int,
    tolerance: int,
) -> list[int]:
    next_counts = [0] * len(current)
    for index, value in enumerate(current):
        if not value:
            continue
        if not previous:
            next_counts[index] = 1
            continue
        y, x = divmod(index, width)
        best = 0
        for ny in range(max(0, y - tolerance), min(height, y + tolerance + 1)):
            row = ny * width
            for nx in range(max(0, x - tolerance), min(width, x + tolerance + 1)):
                neighbor = row + nx
                if previous[neighbor]:
                    best = max(best, previous_counts[neighbor])
        next_counts[index] = min(255, best + 1) if best else 1
    return next_counts


def _connected_components(mask: bytes | bytearray, width: int, height: int) -> list[dict[str, Any]]:
    source = bytes(mask or b"")
    if not source or width <= 0 or height <= 0:
        return []
    seen = bytearray(len(source))
    components: list[dict[str, Any]] = []
    for start, value in enumerate(source):
        if not value or seen[start]:
            continue
        seen[start] = 1
        stack = [start]
        pixels: list[int] = []
        min_x = max_x = start % width
        min_y = max_y = start // width
        while stack:
            index = stack.pop()
            pixels.append(index)
            y, x = divmod(index, width)
            min_x = min(min_x, x)
            max_x = max(max_x, x)
            min_y = min(min_y, y)
            max_y = max(max_y, y)
            for ny in range(max(0, y - 1), min(height, y + 2)):
                row = ny * width
                for nx in range(max(0, x - 1), min(width, x + 2)):
                    neighbor = row + nx
                    if source[neighbor] and not seen[neighbor]:
                        seen[neighbor] = 1
                        stack.append(neighbor)
        component_width = max_x - min_x + 1
        component_height = max_y - min_y + 1
        box_area = max(1, component_width * component_height)
        components.append(
            {
                "pixels": pixels,
                "count": len(pixels),
                "min_x": min_x,
                "max_x": max_x,
                "min_y": min_y,
                "max_y": max_y,
                "width": component_width,
                "height": component_height,
                "fill_ratio": len(pixels) / float(box_area),
            }
        )
    return components


def _text_like_component_mask(
    mask: bytes | bytearray,
    width: int,
    height: int,
    sensitivity: float,
) -> tuple[bytearray, dict[str, int]]:
    result = bytearray(len(mask))
    all_components = _connected_components(mask, width, height)
    if not all_components:
        return result, {"component_count": 0, "band_count": 0}

    total = max(1, width * height)
    max_component_width = max(4, int(round(width * 0.94)))
    max_component_height = max(3, int(round(height * 0.38)))
    max_component_area = max(12, int(round(total * 0.20)))
    eligible = []
    for component in all_components:
        if component["count"] < 3 or component["height"] < 2:
            continue
        if component["width"] > max_component_width:
            continue
        if component["height"] > max_component_height:
            continue
        if component["width"] * component["height"] > max_component_area:
            continue
        if component["fill_ratio"] > 0.88:
            continue
        eligible.append(component)

    band_gap = max(1, int(round(height * 0.08)))
    bands: list[dict[str, Any]] = []
    for component in sorted(eligible, key=lambda item: (item["min_y"], item["min_x"])):
        band = next(
            (
                item
                for item in bands
                if component["min_y"] <= item["max_y"] + band_gap
                and component["max_y"] >= item["min_y"] - band_gap
            ),
            None,
        )
        if band is None:
            bands.append(
                {
                    "components": [component],
                    "min_x": component["min_x"],
                    "max_x": component["max_x"],
                    "min_y": component["min_y"],
                    "max_y": component["max_y"],
                    "count": component["count"],
                }
            )
            continue
        band["components"].append(component)
        band["min_x"] = min(band["min_x"], component["min_x"])
        band["max_x"] = max(band["max_x"], component["max_x"])
        band["min_y"] = min(band["min_y"], component["min_y"])
        band["max_y"] = max(band["max_y"], component["max_y"])
        band["count"] += component["count"]

    min_span = max(3, int(round(width * (0.085 / max(0.5, sensitivity)))))
    max_band_height = max(3, int(round(height * 0.42)))
    valid_bands = []
    for band in bands:
        span = band["max_x"] - band["min_x"] + 1
        band_height = band["max_y"] - band["min_y"] + 1
        box_area = max(1, span * band_height)
        component_count = len(band["components"])
        single_component = band["components"][0] if component_count == 1 else None
        single_line_like = bool(
            single_component
            and span >= min_span
            and span / float(max(1, band_height)) >= 1.6
            and single_component["fill_ratio"] <= 0.88
        )
        if span < min_span or band_height > max_band_height:
            continue
        if component_count < 2 and not single_line_like:
            continue
        if band["count"] / float(box_area) > 0.82:
            continue
        band["score"] = span * min(3, component_count) + band["count"]
        valid_bands.append(band)

    selected_bands = sorted(valid_bands, key=lambda item: item["score"], reverse=True)[:3]
    for band in selected_bands:
        for component in band["components"]:
            for index in component["pixels"]:
                result[index] = 1
    return result, {
        "component_count": sum(len(band["components"]) for band in selected_bands),
        "band_count": len(selected_bands),
    }


def _horizontal_band_fallback(
    mask: bytes | bytearray,
    width: int,
    height: int,
    sensitivity: float,
) -> tuple[bytearray, int]:
    result = bytearray(len(mask))
    if not mask or width <= 0 or height <= 0:
        return result, 0
    min_row_pixels = max(4, int(round(width * (0.16 / max(0.5, sensitivity)))))
    max_row_pixels = max(min_row_pixels, int(round(width * 0.78)))
    row_counts = [sum(mask[y * width:(y + 1) * width]) for y in range(height)]
    dense_rows = [
        min_row_pixels <= count <= max_row_pixels
        for count in row_counts
    ]
    runs = []
    start = None
    for y, active in enumerate(dense_rows + [False]):
        if active and start is None:
            start = y
        elif not active and start is not None:
            runs.append((start, y - 1))
            start = None

    max_band_height = max(3, int(round(height * 0.48)))
    valid = []
    for min_y, max_y in runs:
        band_height = max_y - min_y + 1
        if band_height < 2 or band_height > max_band_height:
            continue
        pixels = [
            index
            for y in range(min_y, max_y + 1)
            for index in range(y * width, (y + 1) * width)
            if mask[index]
        ]
        if not pixels:
            continue
        xs = [index % width for index in pixels]
        span = max(xs) - min(xs) + 1
        if span < max(5, int(round(width * 0.20))):
            continue
        valid.append({"min_y": min_y, "max_y": max_y, "pixels": pixels, "score": span * band_height})

    selected = sorted(valid, key=lambda item: item["score"], reverse=True)[:3]
    selected_pixels = sum(len(item["pixels"]) for item in selected)
    if selected_pixels > int(round(width * height * 0.22)):
        return result, 0
    for band in selected:
        for index in band["pixels"]:
            result[index] = 1
    return result, len(selected)


def _extract_text_like_mask(
    mask: bytes | bytearray,
    width: int,
    height: int,
    sensitivity: float,
) -> tuple[bytearray, dict[str, int]]:
    component_mask, stats = _text_like_component_mask(mask, width, height, sensitivity)
    band_mask, fallback_band_count = _horizontal_band_fallback(
        mask,
        width,
        height,
        sensitivity,
    )
    if fallback_band_count:
        component_mask = bytearray(
            1 if left or right else 0
            for left, right in zip(component_mask, band_mask)
        )
    return component_mask, {
        "component_count": int(stats["component_count"]),
        "band_count": min(3, max(int(stats["band_count"]), fallback_band_count)),
    }


class GameFieldVisualGate:
    """Temporal edge-mask gate for subtitles over a moving game scene."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = normalize_game_field_config(config)
        self.last_reset_reason = "init"
        self.reset("init")

    def configure(self, config: dict[str, Any] | None) -> bool:
        normalized = normalize_game_field_config(config)
        if normalized == self.config:
            return False
        self.config = normalized
        self.reset("config_changed")
        return True

    def reset(self, reason: str = "") -> None:
        self.last_reset_reason = str(reason or "reset")
        self._width = 0
        self._height = 0
        self._active_total = 0
        self._previous_mask = b""
        self._persistence_counts: list[int] = []
        self._baseline_mask = bytearray()
        self._bootstrap_frames = 0
        self._baseline_ready = False
        self._baseline_absence_frames = 0
        self._baseline_refresh_count = 0
        self._candidate_mask = bytearray()
        self._candidate_signature = ""
        self._candidate_stable_since = 0.0
        self._candidate_stable_frames = 0
        self._previous_instant_candidate_mask = bytearray()
        self._active_candidate_mask = bytearray()
        self._active_candidate_signature = ""
        self._last_emitted_state = ""
        self._has_emitted_candidate = False
        self._successful_candidates = 0
        self._pending_emit_state = ""
        self._pending_emit_signature = ""
        self._missing_frames = 0
        self._last_sample_monotonic = 0.0
        self._last_process_monotonic = 0.0
        self._last_state = "no_candidate"
        self._text_like_component_count = 0
        self._text_like_band_count = 0
        self._rejected_candidate_mask = bytearray()
        self._rejected_candidate_signature = ""
        self._rejected_until_monotonic = 0.0

    def _new_result(
        self,
        state: str,
        *,
        reason: str,
        info=None,
        motion_score: float = 0.0,
        persistent_mask: bytes | bytearray = b"",
        candidate_mask: bytes | bytearray = b"",
        stability_age_ms: float = 0.0,
        visual_stable_event: bool = False,
        new_event: bool = False,
        pending_retry: bool = False,
    ) -> dict[str, Any]:
        previous_state = self._last_state
        self._last_state = state
        active_total = int(getattr(info, "active_total", 0) or self._active_total or 1)
        baseline_density = _density(self._baseline_mask, active_total)
        persistent_density = _density(persistent_mask, active_total)
        candidate_density = _density(candidate_mask, active_total)
        candidate_signature = _mask_signature(bytes(candidate_mask))
        if visual_stable_event and self._active_candidate_signature:
            candidate_signature = self._active_candidate_signature
        return {
            "state": state,
            "skip": state not in {"first", "changed"},
            "reason": str(reason or state),
            "gate_mode": "game_background",
            "visual_stable_event": bool(visual_stable_event),
            "new_event": bool(new_event),
            "pending_retry": bool(pending_retry),
            "active_candidate": bool(self._active_candidate_mask),
            "state_changed": previous_state != state,
            "candidate_density": candidate_density,
            "persistent_density": persistent_density,
            "baseline_density": baseline_density,
            "candidate_stability_age_ms": max(0.0, float(stability_age_ms)),
            "motion_score": max(0.0, float(motion_score)),
            "candidate_signature": candidate_signature or self._active_candidate_signature,
            "text_like_component_count": int(self._text_like_component_count),
            "text_like_band_count": int(self._text_like_band_count),
            "baseline_refresh_count": int(self._baseline_refresh_count),
            "rejected_candidate_ttl_ms": max(
                0.0,
                (self._rejected_until_monotonic - self._last_process_monotonic) * 1000.0,
            ),
            "mask_info": info,
        }

    def process(
        self,
        pil_image,
        *,
        gate_config: dict[str, Any] | None = None,
        now_monotonic: float | None = None,
    ) -> dict[str, Any]:
        config = gate_config if isinstance(gate_config, dict) else {}
        self.configure(config.get("game_field", {}))
        info = visual_text_gate.make_text_mask(pil_image, gate_config=config)
        return self.process_mask_info(info, now_monotonic=now_monotonic)

    def _clear_rejected_candidate(self) -> None:
        self._rejected_candidate_mask = bytearray()
        self._rejected_candidate_signature = ""
        self._rejected_until_monotonic = 0.0

    def _refresh_baseline_if_safe(
        self,
        non_text_persistent: bytes | bytearray,
        text_like_candidate: bytes | bytearray,
        persistence_frames: int,
    ) -> bool:
        if self._active_candidate_mask or self._candidate_mask or any(text_like_candidate):
            self._baseline_absence_frames = 0
            return False
        self._baseline_absence_frames += 1
        refresh_after = max(8, int(persistence_frames) * 4)
        if self._baseline_absence_frames < refresh_after:
            return False
        self._baseline_mask = bytearray(non_text_persistent)
        self._baseline_absence_frames = 0
        self._baseline_refresh_count += 1
        return True

    def process_mask_info(
        self,
        info,
        *,
        now_monotonic: float | None = None,
    ) -> dict[str, Any]:
        now = time.monotonic() if now_monotonic is None else float(now_monotonic)
        self._last_process_monotonic = now
        if not info or not getattr(info, "mask", b""):
            return self._new_result("no_candidate", reason="mask_failed", info=info)

        width = int(getattr(info, "width", 0) or 0)
        height = int(getattr(info, "height", 0) or 0)
        current = bytes(1 if value else 0 for value in info.mask)
        if width <= 0 or height <= 0 or len(current) != width * height:
            return self._new_result("no_candidate", reason="invalid_mask_shape", info=info)

        sample_interval = self.config["sample_interval_ms"] / 1000.0
        if (
            self._last_sample_monotonic > 0.0
            and now - self._last_sample_monotonic + 1e-6 < sample_interval
        ):
            state = "same" if self._active_candidate_mask else self._last_state
            if state in {"first", "changed", "cleared"}:
                state = "same" if self._active_candidate_mask else "no_candidate"
            return self._new_result(state, reason="sample_interval", info=info)
        self._last_sample_monotonic = now

        if self._width != width or self._height != height or len(self._previous_mask) not in {0, len(current)}:
            saved_config = dict(self.config)
            self.reset("mask_dimensions_changed")
            self.config = saved_config
            self._width = width
            self._height = height
            self._active_total = int(getattr(info, "active_total", 0) or len(current))
            self._baseline_mask = bytearray(len(current))
            self._persistence_counts = [0] * len(current)
            self._last_sample_monotonic = now

        tolerance = int(self.config["pixel_tolerance"])
        previous = self._previous_mask
        if previous:
            comparison = visual_text_gate.compare_masks(
                visual_text_gate.TextMaskInfo("", previous, width, height, 0.0, sum(previous), self._active_total),
                info,
            )
            motion_score = float(comparison.change_score)
        else:
            motion_score = 1.0 if any(current) else 0.0

        next_counts = _update_persistence_counts(
            current,
            previous,
            self._persistence_counts,
            width,
            height,
            tolerance,
        )

        self._previous_mask = current
        self._persistence_counts = next_counts
        persistence_frames = int(self.config["persistence_frames"])
        persistent = bytearray(1 if count >= persistence_frames else 0 for count in next_counts)
        bootstrap_text_like, bootstrap_stats = _extract_text_like_mask(
            persistent,
            width,
            height,
            float(self.config["sensitivity"]),
        )
        bootstrap_non_text = bytearray(
            1 if value and not bootstrap_text_like[index] else 0
            for index, value in enumerate(persistent)
        )

        if not self._baseline_ready:
            self._bootstrap_frames += 1
            if self._bootstrap_frames >= persistence_frames:
                self._baseline_mask = bytearray(bootstrap_non_text)
                self._baseline_ready = True
            else:
                self._text_like_component_count = int(bootstrap_stats["component_count"])
                self._text_like_band_count = int(bootstrap_stats["band_count"])
                state = "moving" if motion_score >= 0.24 else "no_candidate"
                return self._new_result(
                    state,
                    reason="baseline_bootstrap",
                    info=info,
                    motion_score=motion_score,
                    persistent_mask=persistent,
                    candidate_mask=bootstrap_text_like,
                )

        persistent_residual = bytearray(
            1 if value and not self._baseline_mask[index] else 0
            for index, value in enumerate(persistent)
        )
        instant_residual = bytearray(
            1 if value and not self._baseline_mask[index] else 0
            for index, value in enumerate(current)
        )
        candidate, text_stats = _extract_text_like_mask(
            persistent_residual,
            width,
            height,
            float(self.config["sensitivity"]),
        )
        instant_candidate, _ = _extract_text_like_mask(
            instant_residual,
            width,
            height,
            float(self.config["sensitivity"]),
        )
        previous_instant_candidate = self._previous_instant_candidate_mask
        self._previous_instant_candidate_mask = bytearray(instant_candidate)
        self._text_like_component_count = int(text_stats["component_count"])
        self._text_like_band_count = int(text_stats["band_count"])
        non_text_persistent = bytearray(
            1 if value and not candidate[index] else 0
            for index, value in enumerate(persistent)
        )
        candidate_density = _density(candidate, self._active_total)
        min_density = max(2.0 / max(1, self._active_total), 0.0025 / self.config["sensitivity"])

        if candidate_density < min_density:
            self._candidate_mask = bytearray()
            self._candidate_signature = ""
            self._candidate_stable_since = 0.0
            self._candidate_stable_frames = 0
            if self._active_candidate_mask:
                self._missing_frames += 1
                if self._missing_frames >= persistence_frames:
                    self._active_candidate_mask = bytearray()
                    self._active_candidate_signature = ""
                    self._pending_emit_state = ""
                    self._pending_emit_signature = ""
                    self._refresh_baseline_if_safe(
                        non_text_persistent,
                        candidate,
                        persistence_frames,
                    )
                    self._clear_rejected_candidate()
                    return self._new_result(
                        "cleared",
                        reason="persistent_candidate_disappeared",
                        info=info,
                        motion_score=motion_score,
                        persistent_mask=persistent,
                    )
            else:
                self._refresh_baseline_if_safe(
                    non_text_persistent,
                    candidate,
                    persistence_frames,
                )
                if self._rejected_candidate_mask:
                    self._clear_rejected_candidate()
            state = "moving" if motion_score >= 0.24 else "no_candidate"
            return self._new_result(
                state,
                reason="no_persistent_candidate",
                info=info,
                motion_score=motion_score,
                persistent_mask=persistent,
            )

        self._missing_frames = 0
        self._baseline_absence_frames = 0
        candidate_dilated = _dilate(candidate, width, height, tolerance)

        if self._rejected_candidate_mask:
            if now >= self._rejected_until_monotonic:
                self._clear_rejected_candidate()
            else:
                rejected_dilated = _dilate(
                    self._rejected_candidate_mask,
                    width,
                    height,
                    tolerance,
                )
                rejected_similarity = min(
                    _coverage(candidate, rejected_dilated),
                    _coverage(self._rejected_candidate_mask, candidate_dilated),
                )
                rejected_exact_similarity = min(
                    _coverage(candidate, self._rejected_candidate_mask),
                    _coverage(self._rejected_candidate_mask, candidate),
                )
                if rejected_similarity >= 0.90 and rejected_exact_similarity >= 0.90:
                    self._candidate_mask = bytearray()
                    self._candidate_signature = ""
                    self._candidate_stable_since = 0.0
                    self._candidate_stable_frames = 0
                    return self._new_result(
                        "no_candidate",
                        reason="rejected_candidate_ttl",
                        info=info,
                        motion_score=motion_score,
                        persistent_mask=persistent,
                        candidate_mask=candidate,
                    )
                self._clear_rejected_candidate()

        if self._candidate_mask and not self._active_candidate_mask:
            persistent_growth_ready = bool(
                _coverage(self._candidate_mask, candidate_dilated) >= 0.78
                and sum(candidate) > sum(self._candidate_mask) * 1.04
            )
            nearby_region = _dilate(
                self._candidate_mask,
                width,
                height,
                max(3, tolerance + 2),
            )
            previous_instant_tolerant = _dilate(
                previous_instant_candidate,
                width,
                height,
                tolerance,
            )
            instant_growth = sum(
                1
                for index, value in enumerate(instant_candidate)
                if value and nearby_region[index] and not previous_instant_tolerant[index]
            )
            if (
                not persistent_growth_ready
                and instant_growth >= max(1, int(sum(self._candidate_mask) * 0.03))
            ):
                self._candidate_stable_since = now
                self._candidate_stable_frames = 1
                return self._new_result(
                    "candidate_growing",
                    reason="instant_candidate_growth",
                    info=info,
                    motion_score=motion_score,
                    persistent_mask=persistent,
                    candidate_mask=candidate,
                )
            if persistent_growth_ready:
                self._candidate_mask = bytearray(candidate)
                self._candidate_signature = _mask_signature(bytes(candidate))
                self._candidate_stable_since = now
                self._candidate_stable_frames = 1
                return self._new_result(
                    "candidate_growing",
                    reason="persistent_candidate_growth",
                    info=info,
                    motion_score=motion_score,
                    persistent_mask=persistent,
                    candidate_mask=candidate,
                )

        if (
            self._pending_emit_state
            and self._active_candidate_mask
            and self._pending_emit_signature == self._active_candidate_signature
        ):
            active_dilated = _dilate(self._active_candidate_mask, width, height, tolerance)
            pending_tolerant_similarity = min(
                _coverage(candidate, active_dilated),
                _coverage(self._active_candidate_mask, candidate_dilated),
            )
            pending_exact_similarity = min(
                _coverage(candidate, self._active_candidate_mask),
                _coverage(self._active_candidate_mask, candidate),
            )
            if pending_tolerant_similarity >= 0.90 and pending_exact_similarity >= 0.90:
                return self._new_result(
                    self._pending_emit_state,
                    reason="latest_stable_pending_retry",
                    info=info,
                    motion_score=motion_score,
                    persistent_mask=persistent,
                    candidate_mask=candidate,
                    visual_stable_event=True,
                    pending_retry=True,
                )

        if self._active_candidate_mask:
            active_dilated = _dilate(self._active_candidate_mask, width, height, tolerance)
            active_similarity = min(
                _coverage(candidate, active_dilated),
                _coverage(self._active_candidate_mask, candidate_dilated),
            )
            active_exact_similarity = min(
                _coverage(candidate, self._active_candidate_mask),
                _coverage(self._active_candidate_mask, candidate),
            )
            if active_similarity >= 0.92 and active_exact_similarity >= 0.90:
                self._candidate_mask = bytearray(candidate)
                return self._new_result(
                    "same",
                    reason="stable_candidate_unchanged",
                    info=info,
                    motion_score=motion_score,
                    persistent_mask=persistent,
                    candidate_mask=candidate,
                )

        previous_candidate = self._candidate_mask
        previous_dilated = _dilate(previous_candidate, width, height, tolerance)
        symmetric_similarity = min(
            _coverage(candidate, previous_dilated),
            _coverage(previous_candidate, candidate_dilated),
        ) if previous_candidate else 0.0
        exact_symmetric_similarity = min(
            _coverage(candidate, previous_candidate),
            _coverage(previous_candidate, candidate),
        ) if previous_candidate else 0.0

        if (
            not previous_candidate
            or symmetric_similarity < 0.90
            or exact_symmetric_similarity < 0.90
        ):
            growing = bool(
                previous_candidate
                and _coverage(previous_candidate, candidate_dilated) >= 0.78
                and sum(candidate) > sum(previous_candidate) * 1.04
            )
            self._candidate_mask = bytearray(candidate)
            self._candidate_signature = _mask_signature(bytes(candidate))
            self._candidate_stable_since = now
            self._candidate_stable_frames = 1
            return self._new_result(
                "candidate_growing" if growing else "candidate_stabilizing",
                reason="candidate_shape_changed" if previous_candidate else "candidate_detected",
                info=info,
                motion_score=motion_score,
                persistent_mask=persistent,
                candidate_mask=candidate,
            )

        self._candidate_stable_frames += 1
        stability_age_ms = max(0.0, now - self._candidate_stable_since) * 1000.0
        if stability_age_ms < self.config["stabilize_ms"] or self._candidate_stable_frames < 2:
            return self._new_result(
                "candidate_stabilizing",
                reason="candidate_waiting_stability",
                info=info,
                motion_score=motion_score,
                persistent_mask=persistent,
                candidate_mask=candidate,
                stability_age_ms=stability_age_ms,
            )

        candidate_signature = _mask_signature(bytes(candidate))
        state = "changed" if self._has_emitted_candidate else "first"
        self._candidate_mask = bytearray(candidate)
        self._candidate_signature = candidate_signature
        self._active_candidate_mask = bytearray(candidate)
        self._active_candidate_signature = candidate_signature
        self._last_emitted_state = state
        self._has_emitted_candidate = True
        self._pending_emit_state = ""
        self._pending_emit_signature = ""
        return self._new_result(
            state,
            reason="persistent_candidate_stable",
            info=info,
            motion_score=motion_score,
            persistent_mask=persistent,
            candidate_mask=candidate,
            stability_age_ms=stability_age_ms,
            visual_stable_event=True,
            new_event=True,
        )

    def defer_stable_candidate(self, state: str, signature: str = "") -> bool:
        if not self._active_candidate_mask or state not in {"first", "changed"}:
            return False
        if signature and signature != self._active_candidate_signature:
            return False
        self._pending_emit_state = state
        self._pending_emit_signature = self._active_candidate_signature
        return True

    def mark_ocr_started(self, signature: str = "") -> bool:
        if signature and signature != self._active_candidate_signature:
            return False
        self._pending_emit_state = ""
        self._pending_emit_signature = ""
        return bool(self._active_candidate_mask)

    def note_ocr_result(
        self,
        *,
        text_found: bool,
        signature: str = "",
        now_monotonic: float | None = None,
    ) -> dict[str, Any]:
        if signature and self._active_candidate_signature and signature != self._active_candidate_signature:
            return {"applied": False, "reason": "candidate_signature_mismatch"}
        if text_found:
            if not self._active_candidate_mask:
                return {"applied": False, "reason": "no_active_candidate"}
            self._successful_candidates += 1
            self._clear_rejected_candidate()
            return {"applied": True, "reason": "candidate_confirmed_by_ocr"}
        if not self._active_candidate_mask:
            return {"applied": False, "reason": "no_active_candidate"}
        rejected_at = time.monotonic() if now_monotonic is None else float(now_monotonic)
        self._rejected_candidate_mask = bytearray(self._active_candidate_mask)
        self._rejected_candidate_signature = self._active_candidate_signature
        self._rejected_until_monotonic = rejected_at + 3.0
        if self._successful_candidates <= 0:
            self._has_emitted_candidate = False
        self._active_candidate_mask = bytearray()
        self._active_candidate_signature = ""
        self._candidate_mask = bytearray()
        self._candidate_signature = ""
        self._pending_emit_state = ""
        self._pending_emit_signature = ""
        self._missing_frames = 0
        return {
            "applied": True,
            "reason": "empty_candidate_rejected_ttl_started",
            "baseline_density": _density(self._baseline_mask, self._active_total),
            "baseline_updated": False,
            "rejected_signature": self._rejected_candidate_signature,
            "rejected_ttl_ms": 3000,
        }
