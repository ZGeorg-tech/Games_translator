"""Production Block 2 Fast quality layer promoted from Step 08 v02.

The JSON assets are byte-for-byte copies of the validated laboratory packs.
This module is intentionally independent from ``tools.block2_probe``.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any


from resource_registry import resource_path

PACK_DIR = resource_path("quality.block2_fast")
PACK_PATHS = {
    "common": PACK_DIR / "common_structural_v1.json",
    "ja": PACK_DIR / "ja_fast_v02.json",
    "zh": PACK_DIR / "zh_fast_v02.json",
    "zh_hant": PACK_DIR / "zh_hant_fast_v02.json",
}
SENTINEL_BASE = 9000
SENTINEL_RE = re.compile(r"GTXKEEP\d{4}XGT", re.IGNORECASE)
SPACED_SENTINEL_RE = re.compile(r"GTXKEEP[ \t]+\d{4}XGT", re.IGNORECASE)
PROTECTED_MARKER_RE = re.compile(r"GTXKEEP(?:[ \t]+)?\d{4}XGT", re.IGNORECASE)
CJK_RE = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff]")
UK_RUSSIAN_RE = re.compile(r"\b(?:это|что|как|только|нужно|будет|дверь|сохранить|загрузить|задание)\b", re.IGNORECASE)
PASSTHROUGH_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:HP|MP|XP|EXP|CP|DPS|NPC)(?![A-Za-z0-9_])|"
    r"\[[A-Za-z0-9+_-]+\]|\{[A-Za-z_][A-Za-z0-9_]*\}|%[A-Za-z_][A-Za-z0-9_]*%",
    re.IGNORECASE,
)
OUTPUT_INTEGRITY_VERSION = "block2_fast_output_integrity_v1"
NUMBER_WORDS_EN = {
    0: "zero", 1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven", 8: "eight", 9: "nine",
    10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen", 14: "fourteen", 15: "fifteen", 16: "sixteen",
    17: "seventeen", 18: "eighteen", 19: "nineteen", 20: "twenty", 25: "twenty-five", 30: "thirty", 40: "forty",
    50: "fifty", 60: "sixty", 70: "seventy", 75: "seventy-five", 80: "eighty", 90: "ninety", 100: "one hundred",
    250: "two hundred fifty", 999: "nine hundred ninety nine", 1000: "one thousand", "1.5": "one and a half",
}
NUMBER_WORDS_TARGET = {
    "ru": {
        3: ("три", "трех", "трёх"), 12: ("двенадцать", "двенадцати"), 25: ("двадцать пять", "двадцати пяти"),
        50: ("пятьдесят", "пятидесяти"), 75: ("семьдесят пять", "семидесяти пяти"), 250: ("двести пятьдесят",),
        999: ("девятьсот девяносто девять",), 1000: ("тысяча", "тысячу", "тысячи"), "1.5": ("полтора", "полторы", "полутора"),
    },
    "uk": {
        3: ("три", "трьох"), 12: ("дванадцять", "дванадцяти"),
        25: ("двадцять п'ять", "двадцять п’ять", "двадцяти п'яти", "двадцяти п’яти"),
        50: ("п'ятдесят", "п’ятдесят", "п'ятдесяти", "п’ятдесяти"),
        75: ("сімдесят п'ять", "сімдесят п’ять", "сімдесяти п'яти", "сімдесяти п’яти"),
        250: ("двісті п'ятдесят", "двісті п’ятдесят"),
        999: ("дев'ятсот дев'яносто дев'ять", "дев’ятсот дев’яносто дев’ять"),
        1000: ("тисяча", "тисячу", "тисячі"), "1.5": ("півтора", "півтори"),
    },
}


@dataclass
class ProtectedText:
    worker_source: str
    items: list[dict[str, Any]] = field(default_factory=list)
    elapsed_ms: float = 0.0


@dataclass
class FastLayerResult:
    text: str
    accepted: bool
    used_exact: bool = False
    rule_id: str = ""
    fallback_raw: bool = False
    failures: list[str] = field(default_factory=list)
    elapsed_ms: float = 0.0


@dataclass(frozen=True)
class OutputIntegrityResult:
    accepted: bool
    failures: tuple[str, ...]
    metrics: dict[str, Any]

    @property
    def primary_reason(self) -> str:
        return self.failures[0] if self.failures else ""


def _integrity_tokens(text: str) -> list[str]:
    normalized = unicodedata.normalize("NFKC", str(text or "")).casefold()
    return [token for token in re.findall(r"[^\W_]+", normalized, re.UNICODE) if token]


def _repeated_ngram_metrics(tokens: list[str]) -> dict[str, Any]:
    best_ngram: tuple[str, ...] = ()
    best_size = 0
    best_count = 0
    best_coverage = 0.0
    for size in range(2, 6):
        if len(tokens) < size:
            continue
        ngrams = [tuple(tokens[index:index + size]) for index in range(len(tokens) - size + 1)]
        counts = Counter(ngrams)
        ngram, count = counts.most_common(1)[0]
        covered_positions: set[int] = set()
        for index, candidate in enumerate(ngrams):
            if candidate == ngram:
                covered_positions.update(range(index, index + size))
        coverage = len(covered_positions) / len(tokens) if tokens else 0.0
        candidate_key = (count, coverage, size, " ".join(ngram))
        best_key = (best_count, best_coverage, best_size, " ".join(best_ngram))
        if candidate_key > best_key:
            best_ngram = ngram
            best_size = size
            best_count = count
            best_coverage = coverage
    return {
        "most_repeated_ngram": " ".join(best_ngram),
        "most_repeated_ngram_size": best_size,
        "most_repeated_ngram_count": best_count,
        "repeated_ngram_token_coverage": round(best_coverage, 6),
    }


def validate_output_integrity(source_text: str, output_text: str, target_lang: str) -> OutputIntegrityResult:
    source = str(source_text or "")
    output = str(output_text or "")
    target = str(target_lang or "").strip().lower()
    tokens = _integrity_tokens(output)
    ngram_metrics = _repeated_ngram_metrics(tokens)
    target_letter_count = (
        len(re.findall(r"[A-Za-z]", output))
        if target == "en"
        else len(re.findall(r"[\u0400-\u052f]", output))
    )
    source_length = len(source)
    output_length = len(output)
    unique_token_ratio = len(set(tokens)) / len(tokens) if tokens else 0.0
    replacement_char_count = output.count("\ufffd")
    source_passthrough_detected = bool(PASSTHROUGH_RE.search(source))
    output_passthrough_detected = bool(PASSTHROUGH_RE.search(output))
    source_sentinel_detected = bool(SENTINEL_RE.search(source))
    output_sentinel_detected = bool(SENTINEL_RE.search(output))
    source_language_probe = PASSTHROUGH_RE.sub("", SENTINEL_RE.sub("", source))
    output_language_probe = PASSTHROUGH_RE.sub("", SENTINEL_RE.sub("", output))
    source_has_language_letters = any(char.isalpha() for char in source_language_probe)
    output_has_language_letters = any(char.isalpha() for char in output_language_probe)
    source_requires_target_letters = source_has_language_letters
    output_is_allowed_passthrough = bool(
        (output_passthrough_detected or output_sentinel_detected)
        and not any(char.isalpha() for char in output_language_probe)
    )
    metrics = {
        "source_length": source_length,
        "output_length": output_length,
        "length_ratio": round(output_length / max(1, source_length), 6),
        "target_letter_count": target_letter_count,
        "replacement_char_count": replacement_char_count,
        "source_has_language_letters": source_has_language_letters,
        "source_requires_target_letters": source_requires_target_letters,
        "passthrough_detected": bool(source_passthrough_detected or output_passthrough_detected),
        "internal_sentinel_detected": bool(source_sentinel_detected or output_sentinel_detected),
        "output_is_allowed_passthrough": output_is_allowed_passthrough,
        "token_count": len(tokens),
        "unique_token_ratio": round(unique_token_ratio, 6),
        **ngram_metrics,
    }
    failures: list[str] = []
    if replacement_char_count:
        failures.append("unicode_replacement_character")
    missing_target_letters_allowed = bool(
        not source_requires_target_letters
        and (output_is_allowed_passthrough or not output_has_language_letters)
    )
    if output.strip() and target_letter_count == 0 and not missing_target_letters_allowed:
        failures.append("no_target_letters")
    if (
        len(tokens) >= 12
        and int(ngram_metrics["most_repeated_ngram_count"]) >= 5
        and float(ngram_metrics["repeated_ngram_token_coverage"]) >= 0.50
        and unique_token_ratio <= 0.45
    ):
        failures.append("repetition_degeneration")
    return OutputIntegrityResult(
        accepted=not failures,
        failures=tuple(failures),
        metrics=metrics,
    )


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise RuntimeError(f"block2_fast_pack_invalid:{path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def load_packs() -> dict[str, dict[str, Any]]:
    packs = {key: _read_json(path) for key, path in PACK_PATHS.items()}
    if packs["common"].get("sentinel_format") != "GTXKEEP{index:04d}XGT":
        raise RuntimeError("block2_fast_sentinel_format_unsupported")
    for source_lang in ("ja", "zh", "zh_hant"):
        if packs[source_lang].get("source_lang") != source_lang:
            raise RuntimeError(f"block2_fast_pack_source_mismatch:{source_lang}")
    return packs


@lru_cache(maxsize=1)
def pack_digests() -> dict[str, str]:
    return {key: _sha256(path) for key, path in PACK_PATHS.items()}


@lru_cache(maxsize=1)
def combined_pack_digest() -> str:
    payload = "|".join(f"{key}:{value}" for key, value in sorted(pack_digests().items()))
    return hashlib.sha256(payload.encode("ascii")).hexdigest()


def normalized_exact(text: str) -> str:
    value = unicodedata.normalize("NFKC", str(text or ""))
    value = value.translate(str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'", "…": "..."}))
    value = re.sub(r"\s+", " ", value).strip()
    value = re.sub(r"[\s.!?。！？…]+$", "", value)
    return value.casefold()


def normalized_compare(text: str) -> str:
    return re.sub(r"[^\w\u0400-\u052f]+", "", unicodedata.normalize("NFKC", str(text or "")).casefold())


def exact_rule(source_lang: str, source: str) -> dict[str, Any] | None:
    pack = load_packs()[source_lang]
    key = normalized_exact(source)
    return next(
        (row for row in pack.get("exact") or [] if normalized_exact(row.get("source") or "") == key),
        None,
    )


def _literal_spans(source: str) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for spec in load_packs()["common"].get("literal_patterns") or []:
        for match in re.finditer(str(spec["pattern"]), source, re.UNICODE):
            spans.append({
                "start": match.start(), "end": match.end(), "kind": f"literal:{spec['id']}",
                "source_value": match.group(0), "en": match.group(0), "ru": match.group(0),
                "uk": match.group(0), "priority": 10,
            })
    return spans


def _term_spans(source: str, source_lang: str) -> list[dict[str, Any]]:
    pack = load_packs()[source_lang]
    spans: list[dict[str, Any]] = []
    if source_lang == "ja":
        for name in pack.get("known_names") or []:
            for source_name in name.get("source_forms") or []:
                for honorific in pack.get("honorifics") or []:
                    for suffix in honorific.get("source_forms") or []:
                        token = f"{source_name}{suffix}"
                        for match in re.finditer(re.escape(token), source):
                            spans.append({
                                "start": match.start(), "end": match.end(), "kind": "honorific",
                                "source_value": token, "en": f"{name['en']}{honorific['en']}",
                                "ru": f"{name['ru']}{honorific['ru']}", "uk": f"{name['uk']}{honorific['uk']}",
                                "en_name_aliases": name.get("en_aliases") or [],
                                "priority": 100,
                            })
                for match in re.finditer(re.escape(source_name), source):
                    spans.append({
                        "start": match.start(), "end": match.end(), "kind": "name",
                        "source_value": source_name, "en": name["en"], "ru": name["ru"],
                        "uk": name["uk"], "priority": 70,
                    })
    for term in pack.get("terms") or []:
        for source_form in term.get("source_forms") or []:
            for match in re.finditer(re.escape(source_form), source, re.IGNORECASE):
                spans.append({
                    "start": match.start(), "end": match.end(), "kind": str(term.get("kind") or "term"),
                    "source_value": source_form, "en": term["en"], "ru": term["ru"],
                    "uk": term["uk"], "priority": 80,
                })
    return spans


def _non_overlapping(spans: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []
    for span in sorted(spans, key=lambda row: (-int(row.get("priority") or 0), -(row["end"] - row["start"]), row["start"])):
        if any(span["start"] < end and span["end"] > start for start, end in occupied):
            continue
        selected.append(span)
        occupied.append((span["start"], span["end"]))
    return sorted(selected, key=lambda row: row["start"])


def protect_source(source: str, source_lang: str) -> ProtectedText:
    started = time.perf_counter()
    spans = _non_overlapping(_literal_spans(source) + _term_spans(source, source_lang))
    chunks: list[str] = []
    items: list[dict[str, Any]] = []
    cursor = 0
    for index, span in enumerate(spans):
        if span.get("kind") == "honorific":
            chunks.extend((source[cursor:span["start"]], source[span["start"]:span["end"]]))
            items.append({
                **span, "index": index, "sentinel": "", "postprocess_only": True,
                "postprocess_type": "honorific",
            })
            cursor = span["end"]
            continue
        sentinel = f"GTXKEEP{SENTINEL_BASE + index:04d}XGT"
        chunks.extend((source[cursor:span["start"]], sentinel))
        items.append({**span, "index": index, "sentinel": sentinel})
        cursor = span["end"]
    chunks.append(source[cursor:])
    return ProtectedText("".join(chunks), items, (time.perf_counter() - started) * 1000.0)


def protect_pivot(pivot_en: str, source_items: list[dict[str, Any]], target_lang: str) -> ProtectedText:
    started = time.perf_counter()
    spans = _literal_spans(pivot_en)
    for item in source_items:
        canonical = str(item.get("en") or "")
        target = str(item.get(target_lang) or "")
        if not canonical or not target:
            continue
        for match in re.finditer(re.escape(canonical), pivot_en, re.IGNORECASE):
            spans.append({
                "start": match.start(), "end": match.end(), "kind": item.get("kind"),
                "source_value": canonical, "en": canonical, "ru": item.get("ru") or canonical,
                "uk": item.get("uk") or canonical, "priority": 100,
            })
    chunks: list[str] = []
    items: list[dict[str, Any]] = []
    cursor = 0
    for index, span in enumerate(_non_overlapping(spans)):
        sentinel = f"GTXKEEP{SENTINEL_BASE + index:04d}XGT"
        chunks.extend((pivot_en[cursor:span["start"]], sentinel))
        items.append({**span, "index": index, "sentinel": sentinel, "target_value": span[target_lang]})
        cursor = span["end"]
    chunks.append(pivot_en[cursor:])
    return ProtectedText("".join(chunks), items, (time.perf_counter() - started) * 1000.0)


def restore_markers(text: str, items: list[dict[str, Any]], target_lang: str) -> tuple[str, list[str]]:
    value = str(text or "")
    failures: list[str] = []
    literal_fallback_remaining: dict[str, int] = {}
    for item in items:
        replacement = str(item.get("target_value") or item.get(target_lang) or item.get("source_value") or "")
        if replacement and replacement.casefold() not in literal_fallback_remaining:
            literal_fallback_remaining[replacement.casefold()] = len(
                re.findall(re.escape(replacement), value, re.IGNORECASE)
            )
    for item in items:
        sentinel = str(item.get("sentinel") or "")
        if not sentinel:
            continue
        replacement = str(item.get("target_value") or item.get(target_lang) or item.get("source_value") or "")
        exact_pattern = re.compile(re.escape(sentinel), re.IGNORECASE)
        marker_match = re.fullmatch(r"GTXKEEP(\d{4})XGT", sentinel, re.IGNORECASE)
        spaced_pattern = (
            re.compile(rf"GTXKEEP[ \t]+{re.escape(marker_match.group(1))}XGT", re.IGNORECASE)
            if marker_match else None
        )
        active_pattern = exact_pattern
        count = len(exact_pattern.findall(value))
        if count == 0 and spaced_pattern is not None:
            count = len(spaced_pattern.findall(value))
            active_pattern = spaced_pattern
        if count == 0:
            replacement_key = replacement.casefold()
            if replacement and literal_fallback_remaining.get(replacement_key, 0) > 0:
                literal_fallback_remaining[replacement_key] -= 1
                continue
            failures.append(f"marker_lost:{item.get('index')}")
            continue
        if count > 1:
            failures.append(f"marker_duplicated:{item.get('index')}")
        value = active_pattern.sub(lambda _match, replacement=replacement: replacement, value, count=1)
        value = active_pattern.sub("", value)
    if PROTECTED_MARKER_RE.search(value):
        failures.append("unknown_marker_remaining")
        failures.append("protected_marker_leak")
    return value.strip(), sorted(set(failures))


def _has_fatal_protection_failure(failures: list[str], items: list[dict[str, Any]]) -> bool:
    for failure in failures:
        if failure.startswith(("marker_lost:", "marker_duplicated:")):
            return True
        if failure in {"unknown_marker_remaining", "protected_marker_leak", "marker_remaining"}:
            return True
    return False


def _number_literal_parts(source_value: str) -> tuple[str, int | str, str]:
    normalized = unicodedata.normalize("NFKC", str(source_value or ""))
    match = re.search(r"\d+(?:[.,]\d+)?", normalized)
    if not match:
        return "", "", ""
    digits = match.group(0).replace(",", ".")
    number_key: int | str = int(digits) if digits.isdigit() else digits
    unit_match = re.search(r"(?:%|(?<![A-Za-z0-9_])(?:HP|MP|XP|EXP|CP)(?![A-Za-z0-9_]))", normalized, re.IGNORECASE)
    return digits, number_key, unit_match.group(0).upper() if unit_match else ""


def _contains_numeric_value(text: str, digits: str) -> bool:
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    return any(match.group(0).replace(",", ".") == digits for match in re.finditer(r"(?<!\d)\d+(?:[.,]\d+)?(?!\d)", normalized))


def _normalize_expected_unit(text: str, digits: str, unit: str, target_lang: str) -> tuple[str, list[str]]:
    value = str(text or "")
    failures: list[str] = []
    if not unit:
        return value, failures
    if unit == "%":
        if "%" not in value:
            words = {
                "en": r"(?:per\s*cent|percent)",
                "ru": r"процент(?:а|ов|ы)?",
                "uk": r"відсот(?:ок|ка|ки|ків)",
            }[target_lang]
            value = re.sub(rf"(?<!\d){re.escape(digits)}\s*{words}", f"{digits}%", value, count=1, flags=re.IGNORECASE)
        if "%" not in value:
            failures.append("target_unit_missing:%")
        return value, failures
    unit_pattern = rf"(?<![A-Za-z0-9_]){re.escape(unit)}(?![A-Za-z0-9_])"
    if not re.search(unit_pattern, value, re.IGNORECASE):
        aliases: tuple[str, ...] = ()
        if unit == "HP" and target_lang in {"ru", "uk"}:
            aliases = (r"(?<!\w)(?:к\.?\s*с\.?|л\.?\s*с\.?)(?!\w)",)
        elif unit == "MP" and target_lang in {"ru", "uk"}:
            aliases = (r"\b(?:депутат|депутата|депутату|член\s+парламента)\b",)
        for alias in aliases:
            if re.search(alias, value, re.IGNORECASE):
                value = re.sub(alias, unit, value, count=1, flags=re.IGNORECASE)
                break
    if not re.search(unit_pattern, value, re.IGNORECASE):
        failures.append(f"target_unit_missing:{unit}")
    return value, failures


def apply_target_numbers(text: str, items: list[dict[str, Any]], target_lang: str) -> tuple[str, list[str]]:
    value = str(text or "")
    failures: list[str] = []
    for item in items:
        if item.get("kind") == "literal:game_abbreviation":
            value, unit_failures = _normalize_expected_unit(value, "", str(item.get("source_value") or "").upper(), target_lang)
            failures.extend(unit_failures)
            continue
        if item.get("postprocess_type") != "number":
            continue
        digits, number_key, unit = _number_literal_parts(str(item.get("source_value") or ""))
        if not digits:
            continue
        if not _contains_numeric_value(value, digits):
            replaced = False
            for alias in NUMBER_WORDS_TARGET.get(target_lang, {}).get(number_key, ()):
                if re.search(rf"\b{re.escape(alias)}\b", value, re.IGNORECASE):
                    value = re.sub(rf"\b{re.escape(alias)}\b", digits, value, count=1, flags=re.IGNORECASE)
                    replaced = True
                    break
            if not replaced:
                failures.append(f"target_number_missing:{digits}")
        value, unit_failures = _normalize_expected_unit(value, digits, unit, target_lang)
        failures.extend(unit_failures)
    return value.strip(), failures


def apply_source_aware_en(text: str, items: list[dict[str, Any]]) -> tuple[str, list[str]]:
    value = str(text or "")
    failures: list[str] = []
    title_pattern = r"(?:Mr\.?|Mrs\.?|Ms\.?|Miss|Dr\.?|Sir|Lady|Lord|Master|Senior|Professor|Teacher)"
    suffix_pattern = r"(?:san|kun|chan|sama|senpai|sensei|dono)"
    for item in items:
        if not item.get("postprocess_only"):
            continue
        if item.get("postprocess_type") == "number":
            digits, number_key, unit = _number_literal_parts(str(item.get("source_value") or ""))
            if not digits:
                continue
            if not _contains_numeric_value(value, digits):
                word = NUMBER_WORDS_EN.get(number_key)
                aliases = (word, word.replace("-", " ")) if word and "-" in word else (word,)
                replaced = False
                for alias in aliases:
                    if alias and re.search(rf"\b{re.escape(alias)}\b", value, re.IGNORECASE):
                        value = re.sub(rf"\b{re.escape(alias)}\b", digits, value, count=1, flags=re.IGNORECASE)
                        replaced = True
                        break
                if not replaced:
                    failures.append(f"source_number_missing:{digits}")
            value, unit_failures = _normalize_expected_unit(value, digits, unit, "en")
            failures.extend(unit_failures)
            continue
        if item.get("kind") != "honorific":
            continue
        expected = str(item.get("en") or "")
        base_name = expected.split("-", 1)[0]
        if re.search(re.escape(expected), value, re.IGNORECASE):
            continue
        aliases = [base_name, *(str(alias) for alias in item.get("en_name_aliases") or [])]
        replaced = False
        for alias in aliases:
            for pattern in (
                re.compile(rf"\b{re.escape(alias)}\s+{suffix_pattern}\b", re.IGNORECASE),
                re.compile(rf"\b{title_pattern}\s+{re.escape(alias)}\b", re.IGNORECASE),
                re.compile(rf"\b{re.escape(alias)}\b", re.IGNORECASE),
            ):
                if pattern.search(value):
                    value = pattern.sub(expected, value, count=1)
                    replaced = True
                    break
            if replaced:
                break
        if not replaced:
            failures.append(f"known_name_missing_for_honorific:{base_name}")
    return value.strip(), failures


def validate_output(text: str, target_lang: str, items: list[dict[str, Any]] | None = None) -> list[str]:
    value = str(text or "").strip()
    failures: list[str] = []
    if not value:
        failures.append("empty")
    if CJK_RE.search(value):
        failures.append("source_cjk_remnant")
    if SENTINEL_RE.search(value):
        failures.append("marker_remaining")
    language_probe = PASSTHROUGH_RE.sub("", value)
    letters = [char for char in language_probe if char.isalpha()]
    if target_lang == "en" and letters and not re.search(r"[A-Za-z]", language_probe):
        failures.append("wrong_target_script")
    if target_lang in {"ru", "uk"} and letters and not re.search(r"[\u0400-\u052f]", language_probe):
        failures.append("wrong_target_script")
    if target_lang == "uk" and UK_RUSSIAN_RE.search(value):
        failures.append("russian_contamination_candidate")
    for item in items or []:
        expected = str(item.get("target_value") or item.get(target_lang) or "")
        if expected and normalized_compare(expected) not in normalized_compare(value):
            failures.append(f"known_value_missing:{item.get('kind')}")
    return sorted(set(failures))


class Block2FastLayer:
    def __init__(self) -> None:
        load_packs()

    @property
    def digest(self) -> str:
        return combined_pack_digest()

    def exact(self, source: str, source_lang: str, target_lang: str) -> FastLayerResult | None:
        started = time.perf_counter()
        rule = exact_rule(source_lang, source)
        if not rule:
            return None
        text = str(rule.get(target_lang) or "").strip()
        failures = validate_output(text, target_lang)
        return FastLayerResult(
            text=text, accepted=not failures, used_exact=True,
            rule_id=str(rule.get("id") or normalized_exact(source)), failures=failures,
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )

    def finish(self, raw_output: str, protected: ProtectedText, target_lang: str) -> FastLayerResult:
        started = time.perf_counter()
        restored, restore_failures = restore_markers(raw_output, protected.items, target_lang)
        if target_lang == "en":
            processed, postprocess_failures = apply_source_aware_en(restored, protected.items)
        else:
            processed, postprocess_failures = apply_target_numbers(restored, protected.items, target_lang)
        restored_failures = restore_failures + postprocess_failures + validate_output(processed, target_lang, protected.items)
        if not restored_failures:
            return FastLayerResult(processed, True, failures=[], elapsed_ms=(time.perf_counter() - started) * 1000.0)

        # RAW fallback uses the same single MT inference. It is accepted only when
        # the worker output itself is clean and contains no unresolved sentinels.
        if target_lang == "en":
            raw_processed, raw_postprocess_failures = apply_source_aware_en(raw_output, protected.items)
        else:
            raw_processed, raw_postprocess_failures = apply_target_numbers(raw_output, protected.items, target_lang)
        raw_failures = raw_postprocess_failures + validate_output(raw_processed, target_lang)
        if not raw_failures and not _has_fatal_protection_failure(restored_failures, protected.items):
            return FastLayerResult(
                raw_processed, True, fallback_raw=True,
                failures=sorted(set(restored_failures)), elapsed_ms=(time.perf_counter() - started) * 1000.0,
            )
        return FastLayerResult(
            processed, False, failures=sorted(set(restored_failures + raw_failures)),
            elapsed_ms=(time.perf_counter() - started) * 1000.0,
        )


__all__ = [
    "Block2FastLayer", "FastLayerResult", "OutputIntegrityResult", "ProtectedText", "PACK_PATHS",
    "PASSTHROUGH_RE",
    "OUTPUT_INTEGRITY_VERSION",
    "combined_pack_digest", "pack_digests", "protect_pivot", "protect_source",
    "restore_markers", "validate_output", "validate_output_integrity",
]
