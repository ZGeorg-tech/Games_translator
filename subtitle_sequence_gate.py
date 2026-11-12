from __future__ import annotations

import difflib
import hashlib
import re
import time
import unicodedata
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any


STATIC_PANEL = "static_panel"
GAME_BACKGROUND = "game_background"
FOLLOW_SOURCE = "follow_source"
FAST_SUBTITLES = "fast_subtitles"
BACKGROUND_MODES = {STATIC_PANEL, GAME_BACKGROUND}
DISPLAY_POLICIES = {FOLLOW_SOURCE, FAST_SUBTITLES}

DEFAULT_SUBTITLE_SETTINGS = {
    "background_mode": STATIC_PANEL,
    "display_policy": FOLLOW_SOURCE,
}

EMPTY_CONFIRM_COUNT = 2
EMPTY_CONFIRM_WINDOW_SEC = 0.55
NEAR_DUPLICATE_SIMILARITY = 0.88
FAST_SUBTITLES_STALE_GRACE_MS = 1500


def normalize_subtitle_settings(raw_settings: Any) -> dict[str, str]:
    result = dict(DEFAULT_SUBTITLE_SETTINGS)
    if isinstance(raw_settings, dict):
        result.update(raw_settings)
    result.pop("game_background_strategy", None)

    background_mode = str(result.get("background_mode") or STATIC_PANEL).strip().lower()
    display_policy = str(result.get("display_policy") or FOLLOW_SOURCE).strip().lower()
    result["background_mode"] = background_mode if background_mode in BACKGROUND_MODES else STATIC_PANEL
    result["display_policy"] = display_policy if display_policy in DISPLAY_POLICIES else FOLLOW_SOURCE
    return result


def normalize_ocr_text_for_compare(text: Any) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = value.translate(
        str.maketrans(
            {
                "\u2018": "'",
                "\u2019": "'",
                "\u201b": "'",
                "\u2032": "'",
                "\u201c": '"',
                "\u201d": '"',
            }
        )
    )
    value = value.casefold()
    value = re.sub(r"[^\w\s']", " ", value, flags=re.UNICODE)
    return re.sub(r"\s+", " ", value).strip()


def normalized_text_hash(normalized_text: str) -> str:
    return hashlib.sha256(str(normalized_text or "").encode("utf-8", errors="replace")).hexdigest()[:12]


def _token_overlap(left: str, right: str) -> float:
    left_tokens = Counter(re.findall(r"\w+", left, flags=re.UNICODE))
    right_tokens = Counter(re.findall(r"\w+", right, flags=re.UNICODE))
    denominator = min(sum(left_tokens.values()), sum(right_tokens.values()))
    if denominator <= 0:
        return 0.0
    shared = sum((left_tokens & right_tokens).values())
    return shared / denominator


@dataclass(frozen=True)
class TextComparison:
    classification: str
    similarity_ratio: float
    token_overlap: float
    length_ratio: float
    previous_normalized: str
    candidate_normalized: str

    @property
    def near_duplicate(self) -> bool:
        return self.classification in {"exact", "near_duplicate", "continuation"}


def compare_ocr_text(previous_text: Any, candidate_text: Any) -> TextComparison:
    previous = normalize_ocr_text_for_compare(previous_text)
    candidate = normalize_ocr_text_for_compare(candidate_text)

    if not previous or not candidate:
        classification = "empty"
        similarity = 0.0
        overlap = 0.0
        length_ratio = 0.0
    else:
        similarity = difflib.SequenceMatcher(None, previous, candidate).ratio()
        overlap = _token_overlap(previous, candidate)
        length_ratio = min(len(previous), len(candidate)) / max(len(previous), len(candidate))

        if previous == candidate:
            classification = "exact"
        else:
            prefix_related = previous.startswith(candidate) or candidate.startswith(previous)
            continuation = bool(
                prefix_related
                and length_ratio >= 0.55
                and similarity >= 0.80
                and (overlap >= 0.66 or len(previous.split()) <= 2 or len(candidate.split()) <= 2)
            )
            near_duplicate = bool(
                (
                    similarity >= NEAR_DUPLICATE_SIMILARITY
                    and length_ratio >= 0.58
                    and overlap >= 0.60
                )
                or (
                    similarity >= 0.82
                    and overlap >= 0.80
                    and length_ratio >= 0.55
                )
                or (
                    similarity >= 0.90
                    and length_ratio >= 0.45
                    and overlap >= 0.72
                )
            )
            if continuation:
                classification = "continuation"
            elif near_duplicate:
                classification = "near_duplicate"
            else:
                classification = "new"

    return TextComparison(
        classification=classification,
        similarity_ratio=round(similarity, 6),
        token_overlap=round(overlap, 6),
        length_ratio=round(length_ratio, 6),
        previous_normalized=previous,
        candidate_normalized=candidate,
    )


@dataclass
class SubtitleSequence:
    sequence_id: int
    source_text: str
    normalized_text: str
    normalized_hash: str
    state: str
    created_at: float
    updated_at: float
    source_present: bool = True
    empty_count: int = 0
    first_empty_at: float = 0.0
    translation_request_id: str = ""
    translation_pending: bool = False
    translation_visible: bool = False
    source_gone: bool = False
    published_at: float = 0.0
    source_gone_event_emitted: bool = False
    cancel_reason: str = ""


@dataclass
class StaleVisibleTranslation:
    sequence_id: int
    stale_reason: str
    stale_since: float
    stale_grace_ms: int
    stale_deadline: float
    replacement_sequence_id: int = 0


class SubtitleSequenceGate:
    """OCR-oriented sequence tracker independent from the visual-mask gate."""

    def __init__(
        self,
        *,
        background_mode: str = STATIC_PANEL,
        display_policy: str = FOLLOW_SOURCE,
        empty_confirm_count: int = EMPTY_CONFIRM_COUNT,
        empty_confirm_window_sec: float = EMPTY_CONFIRM_WINDOW_SEC,
    ) -> None:
        self.background_mode = STATIC_PANEL
        self.display_policy = FOLLOW_SOURCE
        self.empty_confirm_count = max(1, int(empty_confirm_count))
        self.empty_confirm_window_sec = max(0.0, float(empty_confirm_window_sec))
        self.sequence_generation = 0
        self._sequence_counter = 0
        self.active: SubtitleSequence | None = None
        self.stale_visible: StaleVisibleTranslation | None = None
        self.configure(background_mode=background_mode, display_policy=display_policy)

    def configure(self, *, background_mode: str, display_policy: str) -> bool:
        settings = normalize_subtitle_settings(
            {
                "background_mode": background_mode,
                "display_policy": display_policy,
            }
        )
        changed = (
            settings["background_mode"] != self.background_mode
            or settings["display_policy"] != self.display_policy
        )
        self.background_mode = settings["background_mode"]
        self.display_policy = settings["display_policy"]
        return changed

    def _base_decision(self, event: str, *, now: float | None = None) -> dict[str, Any]:
        sequence = self.active
        return {
            "event": str(event),
            "timestamp": float(time.time() if now is None else now),
            "subtitle_background_mode": self.background_mode,
            "subtitle_display_policy": self.display_policy,
            "subtitle_sequence_id": int(sequence.sequence_id if sequence else 0),
            "normalized_text_hash": str(sequence.normalized_hash if sequence else ""),
            "source_length": int(len(sequence.source_text) if sequence else 0),
            "candidate_length": 0,
            "similarity_ratio": 0.0,
            "token_overlap": 0.0,
            "length_ratio": 0.0,
            "classification": "",
            "empty_count": int(sequence.empty_count if sequence else 0),
            "source_present": bool(sequence.source_present if sequence else False),
            "source_gone": bool(sequence.source_gone if sequence else False),
            "translation_pending": bool(sequence.translation_pending if sequence else False),
            "translation_visible": bool(sequence.translation_visible if sequence else False),
            "sequence_created": False,
            "sequence_reused": False,
            "sequence_cancelled": False,
            "source_change_detected": False,
            "cancel_reason": "",
            "publish_allowed": False,
            "publish_rejected_reason": "",
            "stale_sequence_id": int(self.stale_visible.sequence_id if self.stale_visible else 0),
            "stale_reason": str(self.stale_visible.stale_reason if self.stale_visible else ""),
            "stale_since": float(self.stale_visible.stale_since if self.stale_visible else 0.0),
            "stale_grace_ms": int(self.stale_visible.stale_grace_ms if self.stale_visible else 0),
            "stale_deadline": float(self.stale_visible.stale_deadline if self.stale_visible else 0.0),
            "replacement_sequence_id": int(
                self.stale_visible.replacement_sequence_id if self.stale_visible else 0
            ),
            "stale_grace_started": False,
            "stale_grace_cancelled": False,
            "preserve_visible_during_stale_grace": bool(self.stale_visible is not None),
            "clear_reason": "",
            "new_sequence_replaced_old": False,
            "remaining_split_parts_cancelled": 0,
            "clear_visible": False,
            "cancel_translation": False,
        }

    def _arm_stale_visible(
        self,
        sequence: SubtitleSequence,
        *,
        reason: str,
        now: float,
        replacement_sequence_id: int = 0,
    ) -> bool:
        if self.display_policy != FAST_SUBTITLES or not sequence.translation_visible:
            return False
        if self.stale_visible is not None:
            return False
        grace_ms = FAST_SUBTITLES_STALE_GRACE_MS
        self.stale_visible = StaleVisibleTranslation(
            sequence_id=int(sequence.sequence_id),
            stale_reason=str(reason or "stale"),
            stale_since=float(now),
            stale_grace_ms=grace_ms,
            stale_deadline=float(now) + (grace_ms / 1000.0),
            replacement_sequence_id=int(replacement_sequence_id or 0),
        )
        sequence.translation_visible = False
        return True

    def _new_sequence(self, text: str, normalized: str, now: float) -> SubtitleSequence:
        self._sequence_counter += 1
        self.active = SubtitleSequence(
            sequence_id=self._sequence_counter,
            source_text=text,
            normalized_text=normalized,
            normalized_hash=normalized_text_hash(normalized),
            state="active_source",
            created_at=now,
            updated_at=now,
        )
        return self.active

    def process_ocr_text(
        self,
        text: Any,
        *,
        now: float | None = None,
        force_new: bool = False,
    ) -> dict[str, Any]:
        now = float(time.time() if now is None else now)
        source_text = str(text or "").strip()
        normalized = normalize_ocr_text_for_compare(source_text)
        if not normalized:
            return self.process_empty(now=now)

        previous = self.active
        if previous is None or previous.state == "cancelled":
            sequence = self._new_sequence(source_text, normalized, now)
            if self.stale_visible is not None and not self.stale_visible.replacement_sequence_id:
                self.stale_visible.replacement_sequence_id = int(sequence.sequence_id)
            decision = self._base_decision("SUBTITLE_SEQUENCE_CREATED", now=now)
            decision.update(
                {
                    "subtitle_sequence_id": sequence.sequence_id,
                    "normalized_text_hash": sequence.normalized_hash,
                    "source_length": len(source_text),
                    "candidate_length": len(source_text),
                    "classification": "new",
                    "sequence_created": True,
                    "publish_allowed": True,
                }
            )
            return decision

        comparison = compare_ocr_text(previous.source_text, source_text)
        if comparison.near_duplicate and not force_new:
            previous.updated_at = now
            previous.source_present = True
            previous.source_gone = False
            previous.empty_count = 0
            previous.first_empty_at = 0.0
            if len(normalized) > len(previous.normalized_text):
                previous.source_text = source_text
                previous.normalized_text = normalized
                previous.normalized_hash = normalized_text_hash(normalized)
            if previous.translation_visible:
                previous.state = "translation_visible"
            elif previous.translation_pending:
                previous.state = "translation_pending"
            else:
                previous.state = "active_source"
            decision = self._base_decision("SUBTITLE_SEQUENCE_NEAR_DUPLICATE", now=now)
            decision.update(
                {
                    "candidate_length": len(source_text),
                    "similarity_ratio": comparison.similarity_ratio,
                    "token_overlap": comparison.token_overlap,
                    "length_ratio": comparison.length_ratio,
                    "classification": comparison.classification,
                    "sequence_reused": True,
                }
            )
            return decision

        replaced_id = previous.sequence_id
        was_visible = bool(previous.translation_visible)
        was_pending = bool(previous.translation_pending)
        replacement_sequence_id = self._sequence_counter + 1
        stale_grace_started = self._arm_stale_visible(
            previous,
            reason="new_subtitle",
            now=now,
            replacement_sequence_id=replacement_sequence_id,
        )
        previous.state = "cancelled"
        previous.cancel_reason = "new_subtitle"
        previous.translation_pending = False
        sequence = self._new_sequence(source_text, normalized, now)
        decision = self._base_decision("SUBTITLE_SEQUENCE_REPLACED", now=now)
        decision.update(
            {
                "subtitle_sequence_id": sequence.sequence_id,
                "replaced_sequence_id": replaced_id,
                "normalized_text_hash": sequence.normalized_hash,
                "source_length": len(source_text),
                "candidate_length": len(source_text),
                "similarity_ratio": comparison.similarity_ratio,
                "token_overlap": comparison.token_overlap,
                "length_ratio": comparison.length_ratio,
                "classification": "new",
                "sequence_created": True,
                "sequence_cancelled": True,
                "cancel_reason": "new_subtitle",
                "publish_allowed": True,
                "new_sequence_replaced_old": True,
                "clear_visible": bool(was_visible and self.stale_visible is None),
                "cancel_translation": was_pending,
                "stale_grace_started": stale_grace_started,
                "preserve_visible_during_stale_grace": bool(self.stale_visible is not None),
            }
        )
        return decision

    def note_source_change_candidate(
        self,
        text: Any,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Revoke old publication rights before a credible new source is fully accepted."""
        now = float(time.time() if now is None else now)
        source_text = str(text or "").strip()
        normalized = normalize_ocr_text_for_compare(source_text)
        previous = self.active
        decision = self._base_decision("SUBTITLE_SOURCE_CHANGE_CANDIDATE", now=now)
        decision.update(
            {
                "candidate_length": len(source_text),
                "candidate_normalized_hash": normalized_text_hash(normalized) if normalized else "",
            }
        )
        if not normalized or previous is None or previous.state == "cancelled":
            decision["publish_rejected_reason"] = "no_active_source_to_replace"
            return decision

        comparison = compare_ocr_text(previous.source_text, source_text)
        decision.update(
            {
                "similarity_ratio": comparison.similarity_ratio,
                "token_overlap": comparison.token_overlap,
                "length_ratio": comparison.length_ratio,
                "classification": comparison.classification,
            }
        )
        if comparison.near_duplicate:
            decision["publish_rejected_reason"] = "candidate_belongs_to_active_source"
            return decision

        was_visible = bool(previous.translation_visible)
        was_pending = bool(previous.translation_pending)
        stale_grace_started = self._arm_stale_visible(
            previous,
            reason="new_subtitle_pending_confirmation",
            now=now,
        )
        previous.state = "cancelled"
        previous.cancel_reason = "new_subtitle_pending_confirmation"
        previous.translation_pending = False
        decision = self._base_decision("SUBTITLE_SOURCE_CHANGE_DETECTED", now=now)
        decision.update(
            {
                "subtitle_sequence_id": int(previous.sequence_id),
                "candidate_length": len(source_text),
                "candidate_normalized_hash": normalized_text_hash(normalized),
                "similarity_ratio": comparison.similarity_ratio,
                "token_overlap": comparison.token_overlap,
                "length_ratio": comparison.length_ratio,
                "classification": "new",
                "source_change_detected": True,
                "sequence_cancelled": True,
                "cancel_reason": previous.cancel_reason,
                "publish_allowed": False,
                "publish_rejected_reason": previous.cancel_reason,
                "clear_visible": bool(was_visible and self.stale_visible is None),
                "cancel_translation": was_pending,
                "stale_grace_started": stale_grace_started,
                "preserve_visible_during_stale_grace": bool(self.stale_visible is not None),
            }
        )
        return decision

    def process_empty(self, *, now: float | None = None) -> dict[str, Any]:
        now = float(time.time() if now is None else now)
        sequence = self.active
        if sequence is None or sequence.state == "cancelled":
            return self._base_decision("SUBTITLE_SEQUENCE_EMPTY_IDLE", now=now)
        if sequence.source_gone:
            decision = self._base_decision("SUBTITLE_EMPTY_AFTER_SOURCE_GONE", now=now)
            decision.update(
                {
                    "classification": "empty_after_source_gone",
                    "suppress_log": True,
                }
            )
            return decision

        sequence.empty_count += 1
        if sequence.first_empty_at <= 0.0:
            sequence.first_empty_at = now
        sequence.updated_at = now
        empty_age = max(0.0, now - sequence.first_empty_at)
        confirmed = bool(
            sequence.empty_count >= self.empty_confirm_count
            or (
                sequence.empty_count > 1
                and self.empty_confirm_window_sec > 0.0
                and empty_age >= self.empty_confirm_window_sec
            )
        )
        if not confirmed:
            decision = self._base_decision("SUBTITLE_EMPTY_HOLD", now=now)
            decision.update(
                {
                    "classification": "empty_hold",
                    "empty_count": sequence.empty_count,
                    "empty_age_sec": round(empty_age, 6),
                }
            )
            return decision

        first_source_gone_transition = not sequence.source_gone_event_emitted
        sequence.source_present = False
        sequence.source_gone = True
        sequence.source_gone_event_emitted = True
        if self.display_policy == FAST_SUBTITLES:
            was_visible = bool(sequence.translation_visible)
            was_pending = bool(sequence.translation_pending)
            stale_grace_started = self._arm_stale_visible(
                sequence,
                reason="source_gone",
                now=now,
            )
            sequence.state = "cancelled"
            sequence.cancel_reason = "source_gone"
            sequence.translation_visible = False
            sequence.translation_pending = False
            event_name = (
                "SUBTITLE_SEQUENCE_SOURCE_GONE"
                if first_source_gone_transition
                else "SUBTITLE_SEQUENCE_SOURCE_GONE_RECONFIRMED"
            )
            decision = self._base_decision(event_name, now=now)
            decision.update(
                {
                    "classification": "empty_confirmed",
                    "empty_count": sequence.empty_count,
                    "publish_allowed": False,
                    "publish_rejected_reason": "source_gone",
                    "sequence_cancelled": True,
                    "cancel_reason": sequence.cancel_reason,
                    "clear_visible": bool(was_visible and self.stale_visible is None),
                    "cancel_translation": was_pending,
                    "stale_grace_started": stale_grace_started,
                    "preserve_visible_during_stale_grace": bool(self.stale_visible is not None),
                    "suppress_log": bool(
                        not first_source_gone_transition
                        and not stale_grace_started
                        and not was_pending
                    ),
                }
            )
            return decision

        sequence.state = "cancelled"
        sequence.cancel_reason = "source_gone_follow_source"
        was_visible = bool(sequence.translation_visible)
        was_pending = bool(sequence.translation_pending)
        sequence.translation_visible = False
        sequence.translation_pending = False
        decision = self._base_decision("SUBTITLE_SEQUENCE_CANCELLED", now=now)
        decision.update(
            {
                "classification": "empty_confirmed",
                "empty_count": sequence.empty_count,
                "sequence_cancelled": True,
                "cancel_reason": sequence.cancel_reason,
                "clear_reason": sequence.cancel_reason if was_visible else "",
                "clear_visible": was_visible,
                "cancel_translation": was_pending,
                "publish_rejected_reason": sequence.cancel_reason,
            }
        )
        return decision

    def mark_translation_started(self, sequence_id: int, request_id: str = "") -> dict[str, Any]:
        decision = self._base_decision("SUBTITLE_TRANSLATION_STARTED")
        if not self.is_current(sequence_id):
            decision["publish_rejected_reason"] = "sequence_not_current"
            return decision
        sequence = self.active
        assert sequence is not None
        sequence.translation_request_id = str(request_id or "")
        sequence.translation_pending = True
        sequence.state = "translation_pending" if sequence.source_present else "source_gone"
        decision = self._base_decision("SUBTITLE_TRANSLATION_STARTED")
        decision["translation_request_id"] = sequence.translation_request_id
        return decision

    def mark_translation_completed(self, sequence_id: int) -> dict[str, Any]:
        decision = self._base_decision("SUBTITLE_TRANSLATION_REJECTED")
        if not self.is_current(sequence_id):
            decision["publish_rejected_reason"] = "sequence_not_current"
            return decision
        sequence = self.active
        assert sequence is not None
        sequence.translation_pending = False
        allowed = bool(
            sequence.state != "cancelled"
            and sequence.source_present
        )
        if not allowed:
            decision["publish_rejected_reason"] = sequence.cancel_reason or "source_not_current"
            return decision
        decision = self._base_decision("SUBTITLE_TRANSLATION_COMPLETED")
        decision["publish_allowed"] = True
        return decision

    def mark_translation_published(
        self,
        sequence_id: int,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        now = float(time.time() if now is None else now)
        decision = self.mark_translation_completed(sequence_id)
        if not decision.get("publish_allowed"):
            return decision
        sequence = self.active
        assert sequence is not None
        sequence.translation_visible = True
        sequence.translation_pending = False
        sequence.state = "translation_visible"
        sequence.published_at = now
        replaced_stale = self.stale_visible
        if replaced_stale is not None:
            self.stale_visible = None
        decision = self._base_decision("SUBTITLE_TRANSLATION_PUBLISHED", now=now)
        decision.update(
            {
                "publish_allowed": True,
                "stale_sequence_id": int(replaced_stale.sequence_id if replaced_stale else 0),
                "stale_reason": str(replaced_stale.stale_reason if replaced_stale else ""),
                "stale_since": float(replaced_stale.stale_since if replaced_stale else 0.0),
                "stale_grace_ms": int(replaced_stale.stale_grace_ms if replaced_stale else 0),
                "stale_deadline": float(replaced_stale.stale_deadline if replaced_stale else 0.0),
                "replacement_sequence_id": int(sequence.sequence_id if replaced_stale else 0),
                "stale_grace_cancelled": bool(replaced_stale is not None),
                "clear_reason": "replacement_ready" if replaced_stale is not None else "",
            }
        )
        return decision

    def expire_stale_grace(self, sequence_id: int, *, now: float | None = None) -> dict[str, Any]:
        now = float(time.time() if now is None else now)
        decision = self._base_decision("SUBTITLE_STALE_GRACE_EXPIRED", now=now)
        stale = self.stale_visible
        if self.display_policy != FAST_SUBTITLES or stale is None:
            decision["publish_rejected_reason"] = "stale_grace_not_active"
            return decision
        if int(stale.sequence_id) != int(sequence_id):
            decision["publish_rejected_reason"] = "stale_sequence_not_owner"
            return decision
        if now < stale.stale_deadline:
            remaining_ms = max(1, int(round((stale.stale_deadline - now) * 1000.0)))
            decision.update(
                {
                    "publish_rejected_reason": "stale_grace_not_elapsed",
                    "stale_grace_remaining_ms": remaining_ms,
                    "stale_grace_reschedule": True,
                }
            )
            return decision

        self.stale_visible = None
        decision.update(
            {
                "clear_visible": True,
                "stale_sequence_id": int(stale.sequence_id),
                "stale_reason": str(stale.stale_reason),
                "stale_since": float(stale.stale_since),
                "stale_grace_ms": int(stale.stale_grace_ms),
                "stale_deadline": float(stale.stale_deadline),
                "replacement_sequence_id": int(stale.replacement_sequence_id),
                "clear_reason": "stale_grace_expired",
            }
        )
        return decision

    def cancel_current(self, reason: str) -> dict[str, Any]:
        sequence = self.active
        stale = self.stale_visible
        decision = self._base_decision("SUBTITLE_SEQUENCE_CANCELLED")
        if sequence is not None:
            was_visible = bool(sequence.translation_visible)
            was_pending = bool(sequence.translation_pending)
            sequence.state = "cancelled"
            sequence.cancel_reason = str(reason or "cancelled")
            sequence.translation_visible = False
            sequence.translation_pending = False
            decision = self._base_decision("SUBTITLE_SEQUENCE_CANCELLED")
            decision.update(
                {
                    "sequence_cancelled": True,
                    "cancel_reason": sequence.cancel_reason,
                    "clear_visible": was_visible,
                    "cancel_translation": was_pending,
                }
            )
        if stale is not None:
            self.stale_visible = None
            decision.update(
                {
                    "stale_sequence_id": int(stale.sequence_id),
                    "stale_reason": str(stale.stale_reason),
                    "stale_since": float(stale.stale_since),
                    "stale_grace_ms": int(stale.stale_grace_ms),
                    "stale_deadline": float(stale.stale_deadline),
                    "replacement_sequence_id": int(stale.replacement_sequence_id),
                    "clear_visible": True,
                    "clear_reason": str(reason or "cancelled"),
                }
            )
        return decision

    def reset(self, reason: str) -> dict[str, Any]:
        previous = self.active
        decision = self.cancel_current(reason)
        self.sequence_generation += 1
        self.active = None
        decision.update(
            {
                "event": "SUBTITLE_GATE_RESET",
                "subtitle_sequence_id": int(previous.sequence_id if previous else 0),
                "cancel_reason": str(reason or "reset"),
                "sequence_cancelled": bool(previous is not None),
                "sequence_generation": self.sequence_generation,
            }
        )
        return decision

    def is_current(self, sequence_id: int) -> bool:
        return bool(
            self.active is not None
            and self.active.state != "cancelled"
            and int(self.active.sequence_id) == int(sequence_id)
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "subtitle_background_mode": self.background_mode,
            "subtitle_display_policy": self.display_policy,
            "sequence_generation": self.sequence_generation,
            "sequence_counter": self._sequence_counter,
            "active": asdict(self.active) if self.active is not None else None,
            "stale_visible": asdict(self.stale_visible) if self.stale_visible is not None else None,
        }
