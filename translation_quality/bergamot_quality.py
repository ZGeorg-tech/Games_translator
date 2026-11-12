"""Universal production quality layer for Bergamot Block 1 EN -> RU/UK."""

from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping, Pattern


QUALITY_LAYER_SCHEMA_VERSION = 4
PROCESSING_SCHEMA_VERSION = "block1_universal_quality_v2_literal_sentinel"
BASE_MEMORY_KEY = "bergamot_base_memory_beam2"
SUPPORTED_TARGETS = ("ru", "uk")
from resource_registry import resource_path

PACK_ROOT = resource_path("quality.bergamot")

EventLogger = Callable[[str, dict[str, Any]], None]

_SMART_QUOTES = str.maketrans({
    "\u2018": "'",
    "\u2019": "'",
    "\u201a": "'",
    "\u201b": "'",
    "\u201c": '"',
    "\u201d": '"',
    "\u201e": '"',
    "\u201f": '"',
    "`": "'",
})
_TERMINAL_TOKENS = ("...", "\u2026", "?!", "!?", ".", "?", "!")
_CYRILLIC_RE = re.compile(r"[\u0400-\u04ff]")
_LATIN_RE = re.compile(r"[A-Za-z]")
_CONTROL_OUTPUT_RE = re.compile(r"^(?:__[A-Z0-9_:-]+__|<[A-Za-z0-9_:-]+>)$")


def default_pack_paths() -> dict[str, Path]:
    return {
        "ru": PACK_ROOT / "common_en_ru.json",
        "uk": PACK_ROOT / "common_en_uk.json",
    }


def default_validation_paths() -> dict[str, Path]:
    return {
        "ru": PACK_ROOT / "target_validation_ru.json",
        "uk": PACK_ROOT / "target_validation_uk.json",
    }


def _default_normalization() -> dict[str, Any]:
    return {
        "trim": True,
        "unicode_normalization": "NFKC",
        "casefold_for_match": True,
        "normalize_smart_quotes": True,
        "collapse_spaces": True,
        "normalize_ellipsis": True,
        "preserve_question_vs_statement": True,
        "strip_terminal_period_for_lookup": True,
        "strip_terminal_exclamation_for_lookup": False,
        "strip_terminal_question_for_lookup": False,
    }


def _normalize_with_policy(text: str, policy: Mapping[str, Any], *, preserve_terminal: bool = False) -> str:
    value = str(text or "")
    if policy.get("trim", True):
        value = value.strip()
    normalization = str(policy.get("unicode_normalization") or "NFKC")
    try:
        value = unicodedata.normalize(normalization, value)
    except ValueError:
        value = unicodedata.normalize("NFKC", value)
    if policy.get("normalize_smart_quotes", True):
        value = value.translate(_SMART_QUOTES)
    if policy.get("collapse_spaces", True):
        value = re.sub(r"\s+", " ", value)
        value = re.sub(r"\s+([.,!?;:])", r"\1", value)
    if policy.get("normalize_ellipsis", True):
        value = re.sub(r"\.{3,}", "\u2026", value)
    if policy.get("casefold_for_match", True):
        value = value.casefold()
    if not preserve_terminal:
        if policy.get("strip_terminal_period_for_lookup", False) and value.endswith(".") and not value.endswith("..."):
            value = value[:-1].rstrip()
        if policy.get("strip_terminal_exclamation_for_lookup", False) and value.endswith("!"):
            value = value[:-1].rstrip()
        if policy.get("strip_terminal_question_for_lookup", False) and value.endswith("?"):
            value = value[:-1].rstrip()
    return value


def normalize_source(text: str) -> str:
    """Public canonical source normalization used by Block 1 tools."""
    return _normalize_with_policy(text, _default_normalization())


def _canonical_digest(*payloads: Any) -> str:
    encoded = json.dumps(payloads, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _regex_flags(raw_flags: Any) -> int:
    flags = 0
    for name in raw_flags if isinstance(raw_flags, list) else ():
        normalized = str(name or "").lower().strip()
        if normalized == "ignore_case":
            flags |= re.IGNORECASE
        elif normalized == "multiline":
            flags |= re.MULTILINE
        elif normalized == "dotall":
            flags |= re.DOTALL
    return flags


@dataclass(frozen=True)
class QualityRule:
    rule_id: str
    rule_type: str
    source: str
    translation: str
    category: str
    application: str = "replace"
    source_variants: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()
    punctuation_sensitive: bool = False
    speaker_prefix_safe: bool = True
    note: str = ""


@dataclass(frozen=True)
class SourceAwareRule:
    rule_id: str
    category: str
    priority: int
    mode: str
    confidence: str
    source_pattern: Pattern[str]
    include_source_pattern: Pattern[str] | None
    exclude_source_pattern: Pattern[str] | None
    target_pattern: Pattern[str] | None
    replacement: str
    full_replacement: str


@dataclass(frozen=True)
class ContextHint:
    hint_id: str
    source_pattern: Pattern[str]
    context_terms: tuple[str, ...]
    preferred: str


@dataclass(frozen=True)
class ValidationCheck:
    check_id: str
    check_type: str
    pattern: Pattern[str] | None = None
    pairs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class QualityMatch:
    hit: bool
    translated_text: str
    target_lang: str
    pack_enabled: bool
    pack_status: str
    pack_id: str
    pack_version: str
    pack_digest: str
    rule_id: str = ""
    rule_type: str = ""
    category: str = ""
    action: str = "none"
    reference_target: str = ""
    context_hint_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class QualityPostprocessResult:
    translated_text: str
    applied_rule_ids: tuple[str, ...] = ()
    structure_changes: tuple[str, ...] = ()
    validation_warnings: tuple[str, ...] = ()
    protected_expected_count: int = 0
    protected_restored_count: int = 0
    protected_missing_indexes: tuple[int, ...] = ()
    protected_duplicate_indexes: tuple[int, ...] = ()
    protected_unknown_sentinel_count: int = 0
    protected_restore_failed: bool = False


@dataclass(frozen=True)
class ProtectedLiteral:
    index: int
    original: str
    sentinel: str
    kind: str
    start: int
    end: int


@dataclass(frozen=True)
class ProtectedSourceContext:
    original_source: str
    worker_source: str
    items: tuple[ProtectedLiteral, ...] = ()
    preexisting_sentinels: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProtectedRestoreResult:
    translated_text: str
    expected_count: int = 0
    restored_count: int = 0
    missing_indexes: tuple[int, ...] = ()
    duplicate_indexes: tuple[int, ...] = ()
    unknown_sentinel_count: int = 0

    @property
    def failed(self) -> bool:
        return bool(self.missing_indexes or self.duplicate_indexes or self.unknown_sentinel_count)


@dataclass(frozen=True)
class _PackState:
    target_lang: str
    path: Path
    status: str
    error: str
    pack_id: str
    pack_version: str
    pack_digest: str
    normalization: Mapping[str, Any]
    rules: tuple[QualityRule, ...]
    exact_index: Mapping[tuple[bool, str], QualityRule]
    source_aware_rules: tuple[SourceAwareRule, ...]
    context_hints: tuple[ContextHint, ...]
    validation_checks: tuple[ValidationCheck, ...]
    invalid_rule_count: int = 0
    conflict_count: int = 0

    @property
    def enabled(self) -> bool:
        return self.status == "loaded"


def _empty_state(target_lang: str, path: Path, status: str, error: str) -> _PackState:
    return _PackState(
        target_lang=target_lang,
        path=path,
        status=status,
        error=error,
        pack_id="",
        pack_version="",
        pack_digest="",
        normalization=MappingProxyType(_default_normalization()),
        rules=(),
        exact_index=MappingProxyType({}),
        source_aware_rules=(),
        context_hints=(),
        validation_checks=(),
    )


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("root_must_be_object")
    return payload


def _required_text(mapping: Mapping[str, Any], key: str, context: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}:{key}_must_be_non_empty_string")
    return value.strip()


class BergamotQualityLayer:
    """Load immutable quality assets once and process Bergamot results in memory."""

    def __init__(
        self,
        pack_paths: Mapping[str, Path | str] | None = None,
        event_logger: EventLogger | None = None,
        *,
        structure_path: Path | str | None = None,
        validation_paths: Mapping[str, Path | str] | None = None,
    ) -> None:
        configured = dict(pack_paths or default_pack_paths())
        validations = dict(validation_paths or default_validation_paths())
        self._pack_paths = {target: Path(configured.get(target, default_pack_paths()[target])) for target in SUPPORTED_TARGETS}
        self._validation_paths = {
            target: Path(validations.get(target, default_validation_paths()[target])) for target in SUPPORTED_TARGETS
        }
        self._structure_path = Path(structure_path or (PACK_ROOT / "structure_rules.json"))
        self._event_logger = event_logger
        self._load_lock = threading.RLock()
        self._counter_lock = threading.Lock()
        self._loaded = False
        self._packs: Mapping[str, _PackState] = MappingProxyType({})
        self._structure: Mapping[str, Any] = MappingProxyType({})
        self._speaker_pattern: Pattern[str] | None = None
        self._literal_patterns: tuple[tuple[str, Pattern[str]], ...] = ()
        self._bracket_key_pattern: Pattern[str] | None = None
        self._bare_key_combo_pattern: Pattern[str] | None = None
        self._abbreviation_pattern: Pattern[str] | None = None
        self._sentinel_format = "GTXKEEP{index:04d}XGT"
        self._sentinel_prefix = "GTXKEEP"
        self._sentinel_suffix = "XGT"
        self._sentinel_pattern: Pattern[str] = re.compile(r"GTXKEEP\d+XGT", re.IGNORECASE)
        self._hit_counts = {target: 0 for target in SUPPORTED_TARGETS}
        self._reference_counts = {target: 0 for target in SUPPORTED_TARGETS}
        self._miss_counts = {target: 0 for target in SUPPORTED_TARGETS}

    def _emit(self, event: str, **details: Any) -> None:
        if self._event_logger is None:
            return
        try:
            self._event_logger(event, details)
        except Exception:
            pass

    def _invalid_rule(self, target: str, rule_id: str, rule_type: str, reason: str) -> None:
        self._emit(
            "QUALITY_RULE_INVALID",
            target_lang=target,
            rule_id=rule_id,
            rule_type=rule_type,
            reason=reason,
        )

    def _load_structure(self) -> dict[str, Any]:
        payload = _read_json(self._structure_path)
        if payload.get("schema_id") != "games_translator.quality_structure":
            raise ValueError("structure_schema_id_mismatch")
        speaker = payload.get("speaker_prefix") or {}
        self._speaker_pattern = re.compile(_required_text(speaker, "pattern", "speaker_prefix"), re.UNICODE)
        protection = payload.get("literal_protection") or {}
        compiled: list[tuple[str, Pattern[str]]] = []
        for field, kind in (
            ("technical_placeholder_patterns", "technical_placeholder"),
            ("markup_tag_patterns", "markup_tag"),
            ("escape_sequence_patterns", "escape_sequence"),
        ):
            for index, pattern in enumerate(protection.get(field) or []):
                try:
                    compiled.append((kind, re.compile(str(pattern), re.UNICODE)))
                except re.error as exc:
                    self._invalid_rule("shared", f"{field}_{index}", "literal_protection", str(exc))
        self._literal_patterns = tuple(compiled)

        key_names = tuple(dict.fromkeys(str(value).strip() for value in protection.get("key_names") or [] if str(value).strip()))
        if not key_names:
            raise ValueError("literal_protection_key_names_missing")
        key_union = "(?:" + "|".join(sorted((re.escape(value) for value in key_names), key=len, reverse=True)) + ")"
        self._bracket_key_pattern = re.compile(
            rf"\[{key_union}(?:\+{key_union})*\]",
            re.IGNORECASE | re.UNICODE,
        )
        self._bare_key_combo_pattern = re.compile(
            rf"(?<![A-Za-z0-9_]){key_union}(?:\+{key_union})+(?![A-Za-z0-9_])",
            re.IGNORECASE | re.UNICODE,
        )

        abbreviations = tuple(
            dict.fromkeys(str(value).strip() for value in protection.get("game_abbreviation_whitelist") or [] if str(value).strip())
        )
        if not abbreviations:
            raise ValueError("literal_protection_abbreviation_whitelist_missing")
        abbreviation_union = "(?:" + "|".join(
            sorted((re.escape(value) for value in abbreviations), key=len, reverse=True)
        ) + ")"
        self._abbreviation_pattern = re.compile(
            rf"(?<![A-Za-z0-9_]){abbreviation_union}(?![A-Za-z0-9_])",
            re.UNICODE,
        )

        sentinel = protection.get("sentinel") or {}
        sentinel_format = str(sentinel.get("format") or "")
        format_match = re.fullmatch(r"(?P<prefix>[A-Za-z][A-Za-z0-9_]*)\{index:0(?P<width>\d+)d\}(?P<suffix>[A-Za-z][A-Za-z0-9_]*)", sentinel_format)
        if format_match is None:
            raise ValueError("literal_protection_sentinel_format_invalid")
        width = int(format_match.group("width"))
        if width < 4:
            raise ValueError("literal_protection_sentinel_width_too_small")
        self._sentinel_format = sentinel_format
        self._sentinel_prefix = format_match.group("prefix")
        self._sentinel_suffix = format_match.group("suffix")
        self._sentinel_pattern = re.compile(
            re.escape(self._sentinel_prefix) + r"\d+" + re.escape(self._sentinel_suffix),
            re.IGNORECASE | re.UNICODE,
        )
        return payload

    def _load_validation(self, path: Path, target: str) -> tuple[dict[str, Any], tuple[ValidationCheck, ...], int]:
        payload = _read_json(path)
        if str(payload.get("language") or "").lower() != target:
            raise ValueError(f"validation_language_mismatch:{payload.get('language')!r}")
        checks: list[ValidationCheck] = []
        invalid = 0
        for index, raw in enumerate(payload.get("checks") or []):
            rule_id = str(raw.get("id") or f"validation_{index}") if isinstance(raw, dict) else f"validation_{index}"
            try:
                if not isinstance(raw, dict):
                    raise ValueError("must_be_object")
                kind = _required_text(raw, "type", rule_id)
                if kind == "regex_flag":
                    pattern = re.compile(_required_text(raw, "pattern", rule_id), _regex_flags(raw.get("flags")))
                    checks.append(ValidationCheck(rule_id, kind, pattern=pattern))
                elif kind == "balanced_pairs":
                    pairs = tuple(
                        (str(pair[0]), str(pair[1]))
                        for pair in raw.get("pairs") or []
                        if isinstance(pair, list) and len(pair) == 2 and pair[0] and pair[1]
                    )
                    if not pairs:
                        raise ValueError("pairs_must_not_be_empty")
                    checks.append(ValidationCheck(rule_id, kind, pairs=pairs))
                else:
                    raise ValueError(f"unsupported_validation_type:{kind}")
            except Exception as exc:
                invalid += 1
                self._invalid_rule(target, rule_id, "target_validation", f"{type(exc).__name__}:{exc}")
        return payload, tuple(checks), invalid

    def _load_pack(self, path: Path, target: str, structure: dict[str, Any]) -> _PackState:
        if not path.is_file():
            return _empty_state(target, path, "missing", f"pack_missing:{path}")
        validation_path = self._validation_paths[target]
        if not validation_path.is_file():
            return _empty_state(target, path, "missing", f"validation_missing:{validation_path}")
        try:
            payload = _read_json(path)
            validation_payload, validation_checks, invalid_count = self._load_validation(validation_path, target)
            if payload.get("schema_id") != "games_translator.quality_pack":
                raise ValueError("pack_schema_id_mismatch")
            pack_id = _required_text(payload, "pack_id", "pack")
            source_lang = _required_text(payload, "source_lang", "pack").lower()
            declared_target = _required_text(payload, "target_lang", "pack").lower()
            if source_lang != "en" or declared_target != target:
                raise ValueError(f"language_pair_mismatch:{source_lang}:{declared_target}")
            normalization = payload.get("normalization")
            if not isinstance(normalization, dict):
                raise ValueError("normalization_must_be_object")
            raw_exact = payload.get("exact_phrases")
            raw_source_aware = payload.get("source_aware_rules")
            raw_context = payload.get("context_lexicon")
            if not isinstance(raw_exact, list) or not isinstance(raw_source_aware, list) or not isinstance(raw_context, list):
                raise ValueError("quality_rule_collections_must_be_arrays")

            rules: list[QualityRule] = []
            exact_index: dict[tuple[bool, str], QualityRule] = {}
            seen_ids: set[str] = set()
            conflict_count = 0
            for index, raw in enumerate(raw_exact):
                rule_id = str(raw.get("id") or f"exact_{index}") if isinstance(raw, dict) else f"exact_{index}"
                try:
                    if not isinstance(raw, dict):
                        raise ValueError("must_be_object")
                    if rule_id in seen_ids:
                        raise ValueError("duplicate_rule_id")
                    category = _required_text(raw, "category", rule_id)
                    application = _required_text(raw, "application", rule_id)
                    if application not in {"replace", "reference_only"}:
                        raise ValueError(f"unsupported_application:{application}")
                    if raw.get("match") != "normalized_exact":
                        raise ValueError(f"unsupported_match:{raw.get('match')!r}")
                    variants = tuple(str(value).strip() for value in raw.get("source_variants") or [] if str(value).strip())
                    if not variants:
                        raise ValueError("source_variants_must_not_be_empty")
                    translation = _required_text(raw, "preferred_target", rule_id)
                    punctuation_sensitive = bool(raw.get("punctuation_sensitive", False))
                    rule = QualityRule(
                        rule_id=rule_id,
                        rule_type="normalized_exact",
                        source=variants[0],
                        translation=translation,
                        category=category,
                        application=application,
                        source_variants=variants,
                        risk_flags=tuple(str(flag) for flag in raw.get("risk_flags") or []),
                        punctuation_sensitive=punctuation_sensitive,
                        speaker_prefix_safe=bool(raw.get("speaker_prefix_safe", True)),
                        note=str(raw.get("notes") or "").strip(),
                    )
                    usable = False
                    for variant in variants:
                        key = (punctuation_sensitive, _normalize_with_policy(variant, normalization, preserve_terminal=punctuation_sensitive))
                        if not key[1]:
                            continue
                        previous = exact_index.get(key)
                        if previous is not None:
                            if previous.translation != rule.translation or previous.application != rule.application:
                                conflict_count += 1
                                self._emit(
                                    "QUALITY_RULE_CONFLICT",
                                    target_lang=target,
                                    lookup_key=key[1],
                                    kept_rule_id=previous.rule_id,
                                    skipped_rule_id=rule.rule_id,
                                )
                            continue
                        exact_index[key] = rule
                        usable = True
                    if not usable:
                        raise ValueError("no_unique_source_variant")
                    seen_ids.add(rule_id)
                    rules.append(rule)
                except Exception as exc:
                    invalid_count += 1
                    self._invalid_rule(target, rule_id, "exact_phrase", f"{type(exc).__name__}:{exc}")

            source_rules: list[SourceAwareRule] = []
            source_ids: set[str] = set()
            for index, raw in enumerate(raw_source_aware):
                rule_id = str(raw.get("id") or f"source_aware_{index}") if isinstance(raw, dict) else f"source_aware_{index}"
                try:
                    if not isinstance(raw, dict):
                        raise ValueError("must_be_object")
                    if rule_id in source_ids or rule_id in seen_ids:
                        raise ValueError("duplicate_rule_id")
                    mode = _required_text(raw, "mode", rule_id)
                    if mode not in {"target_substitution", "full_replacement"}:
                        raise ValueError(f"unsupported_mode:{mode}")
                    flags = _regex_flags(raw.get("flags"))
                    source_pattern = re.compile(_required_text(raw, "source_pattern", rule_id), flags)
                    include_raw = str(raw.get("include_source_pattern") or "").strip()
                    exclude_raw = str(raw.get("exclude_source_pattern") or "").strip()
                    target_raw = str(raw.get("target_pattern") or "").strip()
                    replacement = str(raw.get("replacement") or "")
                    full_replacement = str(raw.get("full_replacement") or "")
                    if mode == "target_substitution" and (not target_raw or not replacement):
                        raise ValueError("target_substitution_requires_target_pattern_and_replacement")
                    if mode == "full_replacement" and not full_replacement:
                        raise ValueError("full_replacement_value_missing")
                    source_rules.append(
                        SourceAwareRule(
                            rule_id=rule_id,
                            category=_required_text(raw, "category", rule_id),
                            priority=int(raw.get("priority") or 0),
                            mode=mode,
                            confidence=_required_text(raw, "confidence", rule_id),
                            source_pattern=source_pattern,
                            include_source_pattern=re.compile(include_raw, flags) if include_raw else None,
                            exclude_source_pattern=re.compile(exclude_raw, flags) if exclude_raw else None,
                            target_pattern=re.compile(target_raw, flags) if target_raw else None,
                            replacement=replacement,
                            full_replacement=full_replacement,
                        )
                    )
                    source_ids.add(rule_id)
                except Exception as exc:
                    invalid_count += 1
                    self._invalid_rule(target, rule_id, "source_aware", f"{type(exc).__name__}:{exc}")
            source_rules.sort(key=lambda item: (-item.priority, item.rule_id))

            hints: list[ContextHint] = []
            for index, raw in enumerate(raw_context):
                hint_id = str(raw.get("id") or f"context_{index}") if isinstance(raw, dict) else f"context_{index}"
                try:
                    if not isinstance(raw, dict):
                        raise ValueError("must_be_object")
                    if bool(raw.get("blind_replacement_allowed", False)):
                        raise ValueError("blind_replacement_must_be_false")
                    if str(raw.get("application") or "") != "context_hint":
                        raise ValueError("application_must_be_context_hint")
                    term = _required_text(raw, "source_term", hint_id)
                    preferred = str((raw.get("preferred") or {}).get(target) or "").strip()
                    contexts = tuple(str(value).casefold() for value in raw.get("source_context_any") or [] if str(value).strip())
                    hints.append(
                        ContextHint(
                            hint_id=hint_id,
                            source_pattern=re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE | re.UNICODE),
                            context_terms=contexts,
                            preferred=preferred,
                        )
                    )
                except Exception as exc:
                    invalid_count += 1
                    self._invalid_rule(target, hint_id, "context_lexicon", f"{type(exc).__name__}:{exc}")

            digest = _canonical_digest(
                {"processing_schema": PROCESSING_SCHEMA_VERSION},
                payload,
                structure,
                validation_payload,
            )
            return _PackState(
                target_lang=target,
                path=path,
                status="loaded",
                error="",
                pack_id=pack_id,
                pack_version=str(payload.get("content_revision") or ""),
                pack_digest=digest,
                normalization=MappingProxyType(dict(normalization)),
                rules=tuple(rules),
                exact_index=MappingProxyType(exact_index),
                source_aware_rules=tuple(source_rules),
                context_hints=tuple(hints),
                validation_checks=validation_checks,
                invalid_rule_count=invalid_count,
                conflict_count=conflict_count,
            )
        except Exception as exc:
            return _empty_state(target, path, "invalid", f"{type(exc).__name__}:{exc}")

    def ensure_loaded(self) -> None:
        if self._loaded:
            return
        with self._load_lock:
            if self._loaded:
                return
            try:
                structure = self._load_structure()
                self._structure = MappingProxyType(structure)
                structure_error = ""
            except Exception as exc:
                structure = {}
                structure_error = f"{type(exc).__name__}:{exc}"
            states: dict[str, _PackState] = {}
            for target in SUPPORTED_TARGETS:
                state = (
                    self._load_pack(self._pack_paths[target], target, structure)
                    if not structure_error
                    else _empty_state(target, self._pack_paths[target], "invalid", f"structure_invalid:{structure_error}")
                )
                states[target] = state
                if state.enabled:
                    self._emit(
                        "QUALITY_PACK_LOADED",
                        target_lang=target,
                        pack_path=str(state.path),
                        pack_id=state.pack_id,
                        content_revision=state.pack_version,
                        pack_digest=state.pack_digest,
                        exact_rule_count=len(state.rules),
                        source_aware_rule_count=len(state.source_aware_rules),
                        context_hint_count=len(state.context_hints),
                        invalid_rule_count=state.invalid_rule_count,
                        conflict_count=state.conflict_count,
                        memory_key=self._memory_key_from_state(state),
                    )
                else:
                    self._emit(
                        "QUALITY_PACK_DISABLED",
                        target_lang=target,
                        pack_path=str(state.path),
                        status=state.status,
                        reason=state.error,
                        bergamot_continues=True,
                    )
            self._packs = MappingProxyType(states)
            self._loaded = True

    @staticmethod
    def _memory_key_from_state(state: _PackState | None) -> str:
        if state is not None and state.enabled:
            return f"{BASE_MEMORY_KEY}_ql_{state.pack_digest[:12]}"
        target = state.target_lang if state is not None else "unknown"
        return f"{BASE_MEMORY_KEY}_ql_disabled_{target}"

    def memory_key_for_target(self, target_lang: str) -> str:
        self.ensure_loaded()
        target = str(target_lang or "").lower().strip()
        return self._memory_key_from_state(self._packs.get(target))

    def _default_target(self) -> str:
        target = os.environ.get("GAME_TRANSLATOR_TARGET_LANG", "").lower().strip()
        if target in SUPPORTED_TARGETS:
            return target
        try:
            import config

            target = str(getattr(config, "CURRENT_LANG", "uk") or "uk").lower().strip()
        except Exception:
            target = "uk"
        return target if target in SUPPORTED_TARGETS else "uk"

    @property
    def memory_key(self) -> str:
        return self.memory_key_for_target(self._default_target())

    @property
    def active_digest(self) -> str:
        self.ensure_loaded()
        state = self._packs.get(self._default_target())
        return state.pack_digest if state and state.enabled else ""

    def _extract_speaker(self, text: str) -> tuple[str, str]:
        value = str(text or "").strip()
        if not value or self._speaker_pattern is None:
            return "", value
        speaker_cfg = self._structure.get("speaker_prefix") or {}
        lowered = value.lower()
        if any(str(token).lower() in lowered for token in speaker_cfg.get("reject_url_tokens") or []):
            return "", value
        if re.match(r"^\d{1,2}:\d{2}(?:\s|$)", value):
            return "", value
        match = self._speaker_pattern.match(value)
        if not match:
            return "", value
        prefix = str(match.groupdict().get("prefix") or "").strip()
        body = str(match.groupdict().get("body") or "").strip()
        if not prefix or not body:
            return "", value
        reject_time = str(speaker_cfg.get("reject_time_pattern") or "").strip()
        if reject_time and re.match(reject_time, prefix):
            return "", value
        return f"{prefix}:", body

    def _context_hint_ids(self, source_text: str, state: _PackState) -> tuple[str, ...]:
        folded = source_text.casefold()
        matches = []
        for hint in state.context_hints:
            if not hint.source_pattern.search(source_text):
                continue
            if hint.context_terms and not any(term in folded for term in hint.context_terms):
                continue
            matches.append(hint.hint_id)
        return tuple(matches)

    def _protected_literal_spans(self, source_text: str) -> tuple[tuple[int, int, str, str], ...]:
        source = str(source_text or "")
        candidates: list[tuple[int, int, int, str, str]] = []
        for priority, (kind, pattern) in enumerate(self._literal_patterns):
            for match in pattern.finditer(source):
                if match.start() != match.end():
                    candidates.append((match.start(), match.end(), priority, kind, match.group(0)))
        for priority, (kind, pattern) in enumerate(
            (
                ("bracketed_key", self._bracket_key_pattern),
                ("bare_key_combination", self._bare_key_combo_pattern),
                ("game_abbreviation", self._abbreviation_pattern),
            ),
            start=len(self._literal_patterns),
        ):
            if pattern is None:
                continue
            for match in pattern.finditer(source):
                candidates.append((match.start(), match.end(), priority, kind, match.group(0)))
        candidates.sort(key=lambda item: (item[0], -(item[1] - item[0]), item[2]))
        accepted: list[tuple[int, int, str, str]] = []
        occupied_until = -1
        for start, end, _priority, kind, literal in candidates:
            if start < occupied_until:
                continue
            accepted.append((start, end, kind, literal))
            occupied_until = end
        return tuple(accepted)

    def prepare_protected_source(self, source_text: str) -> ProtectedSourceContext:
        self.ensure_loaded()
        source = str(source_text or "")
        spans = self._protected_literal_spans(source)
        preexisting = tuple(match.group(0) for match in self._sentinel_pattern.finditer(source))
        if not spans:
            return ProtectedSourceContext(source, source, (), preexisting)

        source_folded = source.casefold()
        used: set[str] = set()
        sequence = 0
        items: list[ProtectedLiteral] = []
        chunks: list[str] = []
        cursor = 0
        for index, (start, end, kind, literal) in enumerate(spans):
            while True:
                sentinel = self._sentinel_format.format(index=sequence)
                sequence += 1
                folded = sentinel.casefold()
                if folded not in source_folded and folded not in used:
                    used.add(folded)
                    break
            chunks.append(source[cursor:start])
            chunks.append(sentinel)
            items.append(ProtectedLiteral(index, literal, sentinel, kind, start, end))
            cursor = end
        chunks.append(source[cursor:])
        return ProtectedSourceContext(source, "".join(chunks), tuple(items), preexisting)

    @staticmethod
    def _target_preserves_protected_literals(context: ProtectedSourceContext, target_text: str) -> bool:
        required: dict[str, int] = {}
        for item in context.items:
            required[item.original] = required.get(item.original, 0) + 1
        return all(str(target_text or "").count(literal) >= count for literal, count in required.items())

    def restore_protected_literals(
        self,
        translated_text: str,
        target_lang: str,
        context: ProtectedSourceContext,
    ) -> ProtectedRestoreResult:
        value = str(translated_text or "")
        missing: list[int] = []
        duplicates: list[int] = []
        restored = 0
        for item in context.items:
            digits = item.sentinel[len(self._sentinel_prefix): len(item.sentinel) - len(self._sentinel_suffix)]
            numeric = int(digits)
            numeric_pattern = r"0+" if numeric == 0 else rf"0*{numeric}"
            pattern = re.compile(
                re.escape(self._sentinel_prefix) + numeric_pattern + re.escape(self._sentinel_suffix),
                re.IGNORECASE,
            )
            count = len(pattern.findall(value))
            if count == 0:
                missing.append(item.index)
                continue
            if count > 1:
                duplicates.append(item.index)
            value = pattern.sub(lambda _match, literal=item.original: literal, value, count=1)
            value = pattern.sub("", value)
            restored += 1

        preexisting = {token.casefold() for token in context.preexisting_sentinels}
        unknown_count = 0

        def remove_unknown(match: re.Match[str]) -> str:
            nonlocal unknown_count
            token = match.group(0)
            if token.casefold() in preexisting:
                return token
            unknown_count += 1
            return ""

        value = self._sentinel_pattern.sub(remove_unknown, value)
        result = ProtectedRestoreResult(
            translated_text=value,
            expected_count=len(context.items),
            restored_count=restored,
            missing_indexes=tuple(missing),
            duplicate_indexes=tuple(duplicates),
            unknown_sentinel_count=unknown_count,
        )
        if result.failed:
            self._emit(
                "QUALITY_PROTECTED_LITERAL_RESTORE_FAILED",
                target_lang=str(target_lang or "").lower().strip(),
                expected_count=result.expected_count,
                restored_count=result.restored_count,
                missing_indexes=list(result.missing_indexes),
                duplicate_indexes=list(result.duplicate_indexes),
                unknown_sentinel_count=result.unknown_sentinel_count,
                source_hash=hashlib.sha256(context.original_source.encode("utf-8")).hexdigest()[:16],
            )
        return result

    def lookup(self, source_text: str, target_lang: str) -> QualityMatch:
        self.ensure_loaded()
        target = str(target_lang or "").lower().strip()
        state = self._packs.get(target)
        if state is None or not state.enabled:
            return QualityMatch(
                hit=False,
                translated_text="",
                target_lang=target,
                pack_enabled=False,
                pack_status=state.status if state else "unsupported",
                pack_id=state.pack_id if state else "",
                pack_version=state.pack_version if state else "",
                pack_digest=state.pack_digest if state else "",
            )

        source = str(source_text or "").strip()
        speaker_prefix, body = self._extract_speaker(source)
        candidates = [source]
        if speaker_prefix and body != source:
            candidates.append(body)
        rule: QualityRule | None = None
        for candidate in candidates:
            sensitive = _normalize_with_policy(candidate, state.normalization, preserve_terminal=True)
            normal = _normalize_with_policy(candidate, state.normalization)
            rule = state.exact_index.get((True, sensitive)) or state.exact_index.get((False, normal))
            if rule is not None and (candidate == source or rule.speaker_prefix_safe):
                break
            rule = None
        context_ids = self._context_hint_ids(body, state)
        literal_context = self.prepare_protected_source(source) if rule is not None and rule.application == "replace" else None
        unsafe_exact = bool(
            literal_context
            and literal_context.items
            and not self._target_preserves_protected_literals(literal_context, rule.translation if rule else "")
        )

        with self._counter_lock:
            if rule is None:
                self._miss_counts[target] += 1
            elif rule.application == "replace" and not unsafe_exact:
                self._hit_counts[target] += 1
            else:
                self._reference_counts[target] += 1

        if rule is None:
            return QualityMatch(
                hit=False,
                translated_text="",
                target_lang=target,
                pack_enabled=True,
                pack_status=state.status,
                pack_id=state.pack_id,
                pack_version=state.pack_version,
                pack_digest=state.pack_digest,
                context_hint_ids=context_ids,
            )
        if rule.application == "replace" and unsafe_exact:
            self._emit(
                "QUALITY_REFERENCE_MATCH",
                target_lang=target,
                rule_id=rule.rule_id,
                category=rule.category,
                worker_bypassed=False,
                reason="protected_literal_missing_in_exact_target",
            )
            return QualityMatch(
                hit=False,
                translated_text="",
                target_lang=target,
                pack_enabled=True,
                pack_status=state.status,
                pack_id=state.pack_id,
                pack_version=state.pack_version,
                pack_digest=state.pack_digest,
                rule_id=rule.rule_id,
                rule_type=rule.rule_type,
                category=rule.category,
                action="worker_required_protected_literals",
                reference_target=rule.translation,
                context_hint_ids=context_ids,
            )
        if rule.application == "replace":
            self._emit(
                "QUALITY_EXACT_REPLACE_HIT",
                target_lang=target,
                rule_id=rule.rule_id,
                category=rule.category,
                source_preview=source[:160],
                worker_bypassed=True,
            )
            return QualityMatch(
                hit=True,
                translated_text=rule.translation,
                target_lang=target,
                pack_enabled=True,
                pack_status=state.status,
                pack_id=state.pack_id,
                pack_version=state.pack_version,
                pack_digest=state.pack_digest,
                rule_id=rule.rule_id,
                rule_type=rule.rule_type,
                category=rule.category,
                action="replace",
                context_hint_ids=context_ids,
            )
        self._emit(
            "QUALITY_REFERENCE_MATCH",
            target_lang=target,
            rule_id=rule.rule_id,
            category=rule.category,
            source_preview=source[:160],
            worker_bypassed=False,
        )
        return QualityMatch(
            hit=False,
            translated_text="",
            target_lang=target,
            pack_enabled=True,
            pack_status=state.status,
            pack_id=state.pack_id,
            pack_version=state.pack_version,
            pack_digest=state.pack_digest,
            rule_id=rule.rule_id,
            rule_type=rule.rule_type,
            category=rule.category,
            action="reference_only",
            reference_target=rule.translation,
            context_hint_ids=context_ids,
        )

    @staticmethod
    def _looks_like_target_language(text: str) -> bool:
        cyrillic = len(_CYRILLIC_RE.findall(text))
        latin = len(_LATIN_RE.findall(text))
        return cyrillic > 0 and cyrillic >= latin

    def _apply_source_aware(self, source: str, translated: str, state: _PackState) -> tuple[str, list[str]]:
        if not translated or not self._looks_like_target_language(translated):
            return translated, []
        applicable: list[SourceAwareRule] = []
        for rule in state.source_aware_rules:
            if not rule.source_pattern.search(source):
                continue
            if rule.include_source_pattern is not None and not rule.include_source_pattern.search(source):
                continue
            if rule.exclude_source_pattern is not None and rule.exclude_source_pattern.search(source):
                continue
            if rule.mode == "target_substitution":
                if rule.target_pattern is None or not rule.target_pattern.search(translated):
                    continue
            elif translated.strip() == rule.full_replacement.strip():
                continue
            applicable.append(rule)
        if len(applicable) > 1:
            self._emit(
                "QUALITY_RULE_CONFLICT",
                target_lang=state.target_lang,
                rule_type="source_aware_runtime",
                rule_ids=[rule.rule_id for rule in applicable],
                resolution="priority_then_rule_id",
            )
        result = translated
        applied: list[str] = []
        for rule in applicable:
            before = result
            if rule.mode == "target_substitution" and rule.target_pattern is not None:
                def replace_match(match: re.Match[str]) -> str:
                    replacement = rule.replacement
                    matched = match.group(0)
                    if replacement.endswith(("а", "А")):
                        lower = matched.casefold()
                        root = replacement[:-1]
                        if lower.endswith("и"):
                            ending = "и" if state.target_lang == "uk" else "ы"
                            replacement = root + (ending.upper() if replacement[-1].isupper() else ending)
                        elif lower.endswith("у"):
                            replacement = root + ("У" if replacement[-1].isupper() else "у")
                        elif lower.endswith("е"):
                            replacement = root + ("Е" if replacement[-1].isupper() else "е")
                        elif lower.endswith("ой"):
                            replacement = root + ("ОЙ" if replacement[-1].isupper() else "ой")
                    if matched[:1].isupper() and replacement[:1].islower():
                        replacement = replacement[:1].upper() + replacement[1:]
                    return replacement

                result, count = rule.target_pattern.subn(replace_match, result, count=1)
                if count == 0:
                    continue
            else:
                result = rule.full_replacement
            if result == before:
                continue
            applied.append(rule.rule_id)
            self._emit(
                "QUALITY_SOURCE_AWARE_HIT",
                target_lang=state.target_lang,
                rule_id=rule.rule_id,
                category=rule.category,
                mode=rule.mode,
                confidence=rule.confidence,
                source_preview=source[:160],
            )
        return result, applied

    def _is_fully_quoted(self, text: str) -> bool:
        value = str(text or "").strip()
        for pair in (self._structure.get("outer_quotes") or {}).get("pairs") or []:
            if isinstance(pair, list) and len(pair) == 2 and value.startswith(str(pair[0])) and value.endswith(str(pair[1])):
                return len(value) > len(str(pair[0])) + len(str(pair[1]))
        return False

    def _remove_spurious_outer_quotes(self, source: str, target: str) -> tuple[str, bool]:
        if self._is_fully_quoted(source):
            return target, False
        value = target.strip()
        for pair in (self._structure.get("outer_quotes") or {}).get("pairs") or []:
            if not isinstance(pair, list) or len(pair) != 2:
                continue
            left, right = str(pair[0]), str(pair[1])
            if value.startswith(left) and value.endswith(right):
                inner = value[len(left): len(value) - len(right)].strip()
                if inner:
                    return inner, True
        return target, False

    @staticmethod
    def _terminal_token(text: str) -> str:
        value = str(text or "").rstrip()
        closers = '\"\u201d\u00bb\'\u2019)]}'
        while value and value[-1] in closers:
            value = value[:-1].rstrip()
        for token in _TERMINAL_TOKENS:
            if value.endswith(token):
                return token
        return ""

    def _restore_terminal(self, source: str, target: str) -> tuple[str, bool]:
        source_terminal = self._terminal_token(source)
        if not source_terminal or self._terminal_token(target):
            return target, False
        value = target.rstrip()
        closers = set(str(item) for item in (self._structure.get("terminal_punctuation") or {}).get("insert_before_closers") or [])
        split = len(value)
        while split > 0 and value[split - 1] in closers:
            split -= 1
        return value[:split].rstrip() + source_terminal + value[split:], True

    @staticmethod
    def _finalize_whitespace(text: str) -> str:
        lines = []
        for line in str(text or "").splitlines() or [str(text or "")]:
            value = re.sub(r"[\t ]+", " ", line).strip()
            value = re.sub(r"\s+([,.;:!?])", r"\1", value)
            lines.append(value)
        return "\n".join(lines).strip()

    def _validate_target(self, text: str, state: _PackState) -> list[str]:
        warnings: list[str] = []
        for check in state.validation_checks:
            matched = False
            if check.check_type == "regex_flag" and check.pattern is not None:
                matched = bool(check.pattern.search(text))
            elif check.check_type == "balanced_pairs":
                for left, right in check.pairs:
                    if left == right:
                        if text.count(left) % 2:
                            matched = True
                            break
                    elif text.count(left) != text.count(right):
                        matched = True
                        break
            if matched:
                warnings.append(check.check_id)
                self._emit(
                    "QUALITY_TARGET_VALIDATION_WARNING",
                    target_lang=state.target_lang,
                    check_id=check.check_id,
                    target_preview=text[:160],
                    action="flag_only",
                )
        return warnings

    def process_translation(
        self,
        source_text: str,
        translated_text: str,
        target_lang: str,
        quality_match: QualityMatch | None = None,
        protected_context: ProtectedSourceContext | None = None,
    ) -> QualityPostprocessResult:
        self.ensure_loaded()
        target = str(target_lang or "").lower().strip()
        state = self._packs.get(target)
        raw = str(translated_text or "").strip()
        protected = (
            self.restore_protected_literals(raw, target, protected_context)
            if protected_context is not None and raw
            else ProtectedRestoreResult(raw)
        )
        protected_details = {
            "protected_expected_count": protected.expected_count,
            "protected_restored_count": protected.restored_count,
            "protected_missing_indexes": protected.missing_indexes,
            "protected_duplicate_indexes": protected.duplicate_indexes,
            "protected_unknown_sentinel_count": protected.unknown_sentinel_count,
            "protected_restore_failed": protected.failed,
        }
        if state is None or not state.enabled or not raw:
            return QualityPostprocessResult(protected.translated_text, **protected_details)
        if _CONTROL_OUTPUT_RE.fullmatch(protected.translated_text):
            return QualityPostprocessResult(protected.translated_text, **protected_details)

        result = protected.translated_text
        applied: list[str] = []
        changes: list[str] = []
        if protected.expected_count:
            changes.append("protected_literals_restored" if not protected.failed else "protected_literal_restore_failed")
        if quality_match is None or quality_match.action != "replace":
            result, applied = self._apply_source_aware(str(source_text or ""), result, state)

        result, changed = self._remove_spurious_outer_quotes(source_text, result)
        if changed:
            changes.append("outer_quotes_removed")
            self._emit(
                "QUALITY_STRUCTURE_OUTER_QUOTES_REMOVED",
                target_lang=target,
                source_preview=str(source_text or "")[:160],
            )

        source_prefix, _source_body = self._extract_speaker(source_text)
        target_prefix, _target_body = self._extract_speaker(result)
        if source_prefix and not target_prefix:
            result = f"{source_prefix} {result.lstrip()}"
            changes.append("speaker_prefix_restored")
            self._emit(
                "QUALITY_STRUCTURE_SPEAKER_PREFIX_RESTORED",
                target_lang=target,
                speaker_prefix=source_prefix,
            )

        result, changed = self._restore_terminal(source_text, result)
        if changed:
            changes.append("punctuation_restored")
            self._emit(
                "QUALITY_STRUCTURE_PUNCTUATION_RESTORED",
                target_lang=target,
                punctuation=self._terminal_token(source_text),
            )
        result = self._finalize_whitespace(result)
        warnings = self._validate_target(result, state)
        return QualityPostprocessResult(
            translated_text=result,
            applied_rule_ids=tuple(applied),
            structure_changes=tuple(changes),
            validation_warnings=tuple(warnings),
            **protected_details,
        )

    def diagnostics(self) -> dict[str, Any]:
        self.ensure_loaded()
        with self._counter_lock:
            hit_counts = dict(self._hit_counts)
            reference_counts = dict(self._reference_counts)
            miss_counts = dict(self._miss_counts)
        return {
            "quality_layer_schema_version": QUALITY_LAYER_SCHEMA_VERSION,
            "processing_schema_version": PROCESSING_SCHEMA_VERSION,
            "enabled": all(state.enabled for state in self._packs.values()),
            "partially_enabled": any(state.enabled for state in self._packs.values()),
            "hit_counts": hit_counts,
            "reference_counts": reference_counts,
            "miss_counts": miss_counts,
            "memory_keys": {target: self._memory_key_from_state(state) for target, state in self._packs.items()},
            "packs": {
                target: {
                    "target_lang": target,
                    "path": str(state.path),
                    "status": state.status,
                    "error": state.error,
                    "pack_id": state.pack_id,
                    "pack_version": state.pack_version,
                    "pack_digest": state.pack_digest,
                    "memory_key": self._memory_key_from_state(state),
                    "exact_rule_count": len(state.rules),
                    "source_variant_count": len(state.exact_index),
                    "source_aware_rule_count": len(state.source_aware_rules),
                    "context_hint_count": len(state.context_hints),
                    "validation_check_count": len(state.validation_checks),
                    "invalid_rule_count": state.invalid_rule_count,
                    "conflict_count": state.conflict_count,
                }
                for target, state in self._packs.items()
            },
            "structure_path": str(self._structure_path),
            "validation_paths": {target: str(path) for target, path in self._validation_paths.items()},
        }
