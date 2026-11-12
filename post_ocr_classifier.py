from __future__ import annotations

import hashlib
import re
import unicodedata
from collections import Counter
from typing import Any


_FUNCTION_WORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "but",
    "can",
    "do",
    "for",
    "from",
    "got",
    "had",
    "has",
    "have",
    "he",
    "her",
    "him",
    "his",
    "i",
    "if",
    "in",
    "is",
    "it",
    "me",
    "my",
    "no",
    "not",
    "of",
    "on",
    "or",
    "our",
    "she",
    "so",
    "that",
    "the",
    "their",
    "them",
    "they",
    "this",
    "to",
    "up",
    "was",
    "we",
    "were",
    "what",
    "with",
    "you",
    "your",
}
_TERMINAL_RE = re.compile(r"[?.!][\"')\]]*$")
_SPEAKER_RE = re.compile(r"^[A-Z][A-Za-z'-]{1,24}\s*:")

# Эти одиночные CJK-штрихи формально входят в Han-блок Unicode, но в live OCR
# значительно чаще являются краем рамки/глифом шума, чем самостоятельной репликой.
_SUSPICIOUS_SINGLE_HAN_GLYPHS = frozenset({"丨", "丶", "丿", "乀", "乁", "亅"})

LATIN_EN_POLICY = "latin_en"
CHINESE_POLICY = "chinese"
JAPANESE_POLICY = "japanese"


def post_ocr_policy_for_source(source_lang: Any) -> str:
    value = str(source_lang or "en").strip().lower().replace("_", "-")
    if value in {"zh", "zh-hans", "zh-hant", "zho", "chi"}:
        return CHINESE_POLICY
    if value in {"ja", "jpn"}:
        return JAPANESE_POLICY
    return LATIN_EN_POLICY


def _is_han(char: str) -> bool:
    code = ord(char)
    return bool(
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x2FA1F
    )


def _is_hiragana(char: str) -> bool:
    code = ord(char)
    return 0x3040 <= code <= 0x309F


def _is_katakana(char: str) -> bool:
    code = ord(char)
    return bool(
        0x30A0 <= code <= 0x30FF
        or 0x31F0 <= code <= 0x31FF
        or 0xFF66 <= code <= 0xFF9D
    )


def _is_latin(char: str) -> bool:
    return char.isalpha() and unicodedata.name(char, "").startswith("LATIN")


def analyze_script_composition(text: Any, source_lang: Any = "en") -> dict[str, Any]:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    policy = post_ocr_policy_for_source(source_lang)
    counts = {
        "han": 0,
        "hiragana": 0,
        "katakana": 0,
        "latin": 0,
        "digits": 0,
        "punctuation": 0,
        "other": 0,
    }
    for char in clean:
        if char.isspace():
            continue
        if _is_han(char):
            counts["han"] += 1
        elif _is_hiragana(char):
            counts["hiragana"] += 1
        elif _is_katakana(char):
            counts["katakana"] += 1
        elif _is_latin(char):
            counts["latin"] += 1
        elif char.isdigit():
            counts["digits"] += 1
        elif unicodedata.category(char).startswith("P"):
            counts["punctuation"] += 1
        else:
            counts["other"] += 1

    effective_units = (
        counts["han"]
        + counts["hiragana"]
        + counts["katakana"]
        + counts["latin"]
        + counts["digits"]
    )
    if policy == CHINESE_POLICY:
        source_script_units = counts["han"]
        short_bypass_min_units = 2
    elif policy == JAPANESE_POLICY:
        source_script_units = counts["han"] + counts["hiragana"] + counts["katakana"]
        short_bypass_min_units = 3
    else:
        source_script_units = counts["latin"]
        short_bypass_min_units = 0
    non_space_units = effective_units + counts["punctuation"] + counts["other"]
    source_script_ratio = _safe_ratio(source_script_units, effective_units)
    other_noise_ratio = _safe_ratio(counts["other"], non_space_units)
    short_bypass_eligible = bool(
        policy in {CHINESE_POLICY, JAPANESE_POLICY}
        and source_script_units >= short_bypass_min_units
        and source_script_ratio >= 0.70
        and other_noise_ratio <= 0.10
        and counts["digits"] <= max(1, source_script_units // 3)
    )
    return {
        "post_ocr_policy": policy,
        **counts,
        "effective_text_units": effective_units,
        "source_script_units": source_script_units,
        "source_script_ratio": source_script_ratio,
        "other_noise_ratio": other_noise_ratio,
        "short_fragment_bypass_eligible": short_bypass_eligible,
    }


def _safe_ratio(value: int | float, total: int | float) -> float:
    if not total:
        return 0.0
    return round(float(value) / float(total), 6)


def _token_core(token: str) -> str:
    return re.sub(r"^[^A-Za-z0-9]+|[^A-Za-z0-9]+$", "", str(token or ""))


def _is_joined_or_camel_token(token: str) -> bool:
    core = _token_core(token)
    if len(core) < 5:
        return False
    if re.search(r"[A-Za-z]\d+[A-Za-z]|\d+[A-Za-z]", core):
        return True
    return bool(re.search(r"[a-z][A-Z]", core))


def _mixed_case_anomaly(token: str) -> bool:
    core = _token_core(token)
    letters = "".join(ch for ch in core if ch.isalpha())
    if len(letters) < 3 or not (any(ch.islower() for ch in letters) and any(ch.isupper() for ch in letters)):
        return False
    if letters[0].isupper() and letters[1:].islower():
        return False
    return True


def analyze_post_ocr_text(text: Any, source_lang: Any = "en") -> dict[str, Any]:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    script_metrics = analyze_script_composition(clean, source_lang)
    chars = list(clean)
    non_space = [ch for ch in chars if not ch.isspace()]
    letters = [ch for ch in chars if ch.isalpha()]
    digits = [ch for ch in chars if ch.isdigit()]
    symbols = [ch for ch in non_space if not ch.isalnum()]
    raw_tokens = [token for token in clean.split() if token]
    token_cores = [_token_core(token) for token in raw_tokens]
    token_cores = [token for token in token_cores if token]
    alpha_tokens = [token for token in token_cores if any(ch.isalpha() for ch in token)]
    lower_tokens = [token.lower() for token in alpha_tokens]
    vowels = [ch for ch in letters if ch in "aeiouyAEIOUY"]
    joined_tokens = [token for token in alpha_tokens if _is_joined_or_camel_token(token)]
    mixed_tokens = [token for token in alpha_tokens if _mixed_case_anomaly(token)]
    isolated_tokens = [token for token in token_cores if len(token) == 1]
    digit_inside_tokens = [
        token
        for token in token_cores
        if any(ch.isalpha() for ch in token) and any(ch.isdigit() for ch in token)
    ]
    titlecase_tokens = [
        token
        for token in alpha_tokens
        if len(token) >= 3 and token[0].isupper() and token[1:].islower()
    ]
    function_word_count = sum(1 for token in lower_tokens if token in _FUNCTION_WORDS)
    repeated_char = False
    compact_alnum = [ch.casefold() for ch in non_space if ch.isalnum()]
    if len(compact_alnum) >= 3:
        repeated_char = max(Counter(compact_alnum).values(), default=0) / len(compact_alnum) >= 0.86

    return {
        **script_metrics,
        "text_len": len(clean),
        "safe_hash": hashlib.sha256(clean.encode("utf-8", errors="replace")).hexdigest()[:12],
        "token_count": len(token_cores),
        "letter_count": len(letters),
        "digit_count": len(digits),
        "symbol_count": len(symbols),
        "alphabetic_ratio": _safe_ratio(len(letters), len(non_space)),
        "digit_ratio": _safe_ratio(len(digits), len(non_space)),
        "symbol_ratio": _safe_ratio(len(symbols), len(non_space)),
        "vowel_ratio": _safe_ratio(len(vowels), len(letters)),
        "mixed_case_anomaly_count": len(mixed_tokens),
        "isolated_one_char_token_count": len(isolated_tokens),
        "joined_token_count": len(joined_tokens),
        "digit_inside_token_count": len(digit_inside_tokens),
        "titlecase_token_count": len(titlecase_tokens),
        "function_word_count": function_word_count,
        "average_alpha_token_len": round(
            sum(len(token) for token in alpha_tokens) / max(1, len(alpha_tokens)),
            6,
        ),
        "has_terminal_punctuation": bool(_TERMINAL_RE.search(clean)),
        "has_question_or_exclamation": bool(re.search(r"[?!][\"')\]]*$", clean)),
        "has_apostrophe": "'" in clean,
        "has_speaker_prefix": bool(_SPEAKER_RE.search(clean)),
        "repeated_character_pattern": repeated_char,
    }


def classify_post_ocr_text(
    text: Any,
    *,
    source_lang: str = "en",
    zone: dict[str, Any] | None = None,
    source_mode: str = "live",
) -> dict[str, Any]:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    metrics = analyze_post_ocr_text(clean, source_lang)
    policy = str(metrics.get("post_ocr_policy") or LATIN_EN_POLICY)
    zone = zone or {}
    source_mode = str(source_mode or "live").strip().lower()
    reasons: list[str] = []
    plausible_reasons: list[str] = []

    try:
        min_text_len = max(1, int(zone.get("min_text_len", 3)))
    except Exception:
        min_text_len = 3
    try:
        max_symbols = float(zone.get("max_garbage_symbols_pct", 0.45))
    except Exception:
        max_symbols = 0.45
    try:
        vowel_min = float(zone.get("vowels_min_pct", 0.24))
        vowel_max = float(zone.get("vowels_max_pct", 0.65))
    except Exception:
        vowel_min, vowel_max = 0.24, 0.65

    cjk_policy = policy in {CHINESE_POLICY, JAPANESE_POLICY}
    source_script_units = int(metrics.get("source_script_units") or 0)
    effective_text_units = int(metrics.get("effective_text_units") or 0)

    if policy == CHINESE_POLICY and source_script_units == 1 and effective_text_units <= 2:
        single_han_clean = bool(
            clean not in _SUSPICIOUS_SINGLE_HAN_GLYPHS
            and int(metrics.get("latin") or 0) == 0
            and int(metrics.get("digits") or 0) == 0
            and float(metrics.get("source_script_ratio") or 0.0) >= 0.80
            and float(metrics.get("other_noise_ratio") or 0.0) <= 0.10
        )
        cautious_reason = "single_han_pending" if single_han_clean else "single_han_rejected_unstable"
        return {
            "accepted": False,
            "classification": "cautious_short_text",
            "reason_code": cautious_reason,
            "reason_codes": [cautious_reason],
            "plausible_natural_language": False,
            "plausible_reason_codes": [],
            "source_mode": source_mode,
            "boxes_available": False,
            **metrics,
        }

    if policy == JAPANESE_POLICY and source_script_units == 1 and effective_text_units <= 2:
        single_kanji_clean = bool(
            int(metrics.get("han") or 0) == 1
            and int(metrics.get("hiragana") or 0) == 0
            and int(metrics.get("katakana") or 0) == 0
            and clean not in _SUSPICIOUS_SINGLE_HAN_GLYPHS
            and int(metrics.get("latin") or 0) == 0
            and int(metrics.get("digits") or 0) == 0
            and float(metrics.get("source_script_ratio") or 0.0) >= 0.80
            and float(metrics.get("other_noise_ratio") or 0.0) <= 0.10
        )
        cautious_reason = (
            "single_kanji_pending"
            if single_kanji_clean
            else "single_japanese_unit_cautious"
        )
        return {
            "accepted": False,
            "classification": "cautious_short_text",
            "reason_code": cautious_reason,
            "reason_codes": [cautious_reason],
            "plausible_natural_language": False,
            "plausible_reason_codes": [],
            "source_mode": source_mode,
            "boxes_available": False,
            **metrics,
        }

    hard_reason = ""
    if not clean or (metrics["text_len"] < min_text_len and not (cjk_policy and source_script_units >= 2)):
        hard_reason = "below_min_text_length"
    elif metrics["letter_count"] == 0 and metrics["digit_count"] == 0:
        hard_reason = "no_letters_or_digits"
    elif metrics["letter_count"] == 0:
        hard_reason = "no_alphabetic_characters"
    elif metrics["repeated_character_pattern"]:
        hard_reason = "repeated_character_noise"
    elif metrics["symbol_ratio"] > max(0.58, max_symbols + 0.12):
        hard_reason = "dominant_symbol_noise"
    elif metrics["alphabetic_ratio"] < 0.34 and metrics["symbol_ratio"] > 0.30:
        hard_reason = "impossible_alphabetic_symbol_ratio"
    elif (
        metrics["token_count"] == 1
        and metrics["letter_count"] <= 4
        and metrics["mixed_case_anomaly_count"] >= 1
        and not metrics["has_terminal_punctuation"]
    ):
        hard_reason = "short_mixed_case_noise"

    if hard_reason:
        return {
            "accepted": False,
            "classification": "hard_garbage",
            "reason_code": hard_reason,
            "reason_codes": [hard_reason],
            "plausible_natural_language": False,
            "plausible_reason_codes": [],
            "source_mode": source_mode,
            "boxes_available": False,
            **metrics,
        }

    hud_reasons: list[str] = []
    if source_mode == "live":
        if (
            metrics["joined_token_count"] >= 2
            and metrics["token_count"] >= 4
            and (
                metrics["isolated_one_char_token_count"] >= 1
                or metrics["digit_inside_token_count"] >= 1
                or metrics["mixed_case_anomaly_count"] >= 2
            )
        ):
            hud_reasons.append("multiple_joined_tokens_with_fragmentation")
        if (
            metrics["digit_inside_token_count"] >= 1
            and metrics["isolated_one_char_token_count"] >= 2
            and metrics["token_count"] >= 4
        ):
            hud_reasons.append("damaged_digit_tokens_with_isolated_labels")
        if (
            metrics["token_count"] >= 4
            and metrics["titlecase_token_count"] >= 3
            and metrics["function_word_count"] == 0
            and not metrics["has_terminal_punctuation"]
            and not metrics["has_speaker_prefix"]
        ):
            hud_reasons.append("titlecase_ui_label_bundle")
    if hud_reasons:
        return {
            "accepted": False,
            "classification": "hud_fragment_bundle",
            "reason_code": hud_reasons[0],
            "reason_codes": hud_reasons,
            "plausible_natural_language": False,
            "plausible_reason_codes": [],
            "source_mode": source_mode,
            "boxes_available": False,
            **metrics,
        }

    fragmented = bool(
        metrics["joined_token_count"] >= 2
        or metrics["isolated_one_char_token_count"] >= max(2, metrics["token_count"] // 2)
        or metrics["mixed_case_anomaly_count"] >= 2
    )
    if (
        policy == CHINESE_POLICY
        and metrics["han"] >= 2
        and metrics["source_script_ratio"] >= 0.65
    ):
        plausible_reasons.append("chinese_han_sequence")
    if (
        policy == JAPANESE_POLICY
        and metrics["source_script_units"] >= 2
        and metrics["source_script_ratio"] >= 0.65
    ):
        plausible_reasons.append("japanese_script_sequence")
    if metrics["has_speaker_prefix"] and metrics["token_count"] >= 3:
        plausible_reasons.append("speaker_prefix")
    if metrics["has_question_or_exclamation"] and metrics["letter_count"] >= 2:
        plausible_reasons.append("terminal_question_or_exclamation")
    if metrics["has_apostrophe"] and metrics["token_count"] >= 2:
        plausible_reasons.append("natural_apostrophe")
    if metrics["function_word_count"] >= 1 and metrics["token_count"] >= 3:
        plausible_reasons.append("function_word_structure")
    if (
        metrics["token_count"] >= 4
        and metrics["average_alpha_token_len"] >= 2.7
        and metrics["alphabetic_ratio"] >= 0.68
    ):
        plausible_reasons.append("multiword_sentence_shape")
    if (
        metrics["has_terminal_punctuation"]
        and metrics["token_count"] <= 2
        and metrics["letter_count"] >= 2
        and not fragmented
    ):
        plausible_reasons.append("short_final_utterance")

    if cjk_policy:
        plausible = bool(
            plausible_reasons
            and source_script_units >= 2
            and metrics["source_script_ratio"] >= 0.65
            and metrics["digit_ratio"] <= 0.18
            and metrics["other_noise_ratio"] <= 0.10
            and not fragmented
        )
    else:
        plausible = bool(
            plausible_reasons
            and metrics["alphabetic_ratio"] >= 0.62
            and metrics["digit_ratio"] <= 0.18
            and metrics["symbol_ratio"] <= 0.24
            and not fragmented
        )
    if plausible:
        return {
            "accepted": True,
            "classification": "plausible_natural_language",
            "reason_code": plausible_reasons[0],
            "reason_codes": plausible_reasons,
            "plausible_natural_language": True,
            "plausible_reason_codes": plausible_reasons,
            "source_mode": source_mode,
            "boxes_available": False,
            **metrics,
        }

    if str(source_lang or "").lower() in {"en", "eng"}:
        vowel_ratio = float(metrics["vowel_ratio"])
        if vowel_ratio < vowel_min or vowel_ratio > vowel_max:
            reasons.append("vowel_ratio_outside_profile")
    if metrics["symbol_ratio"] > max_symbols:
        reasons.append("symbol_ratio_above_profile")
    if metrics["token_count"] >= 4 and metrics["average_alpha_token_len"] < 2.6:
        reasons.append("low_average_word_length")
    if metrics["alphabetic_ratio"] < 0.55:
        reasons.append("low_alphabetic_ratio")
    if metrics["digit_ratio"] > 0.20:
        reasons.append("high_digit_ratio")
    if metrics["mixed_case_anomaly_count"] >= 2:
        reasons.append("multiple_mixed_case_anomalies")
    if (
        metrics["isolated_one_char_token_count"] >= 2
        and metrics["isolated_one_char_token_count"] * 2 >= metrics["token_count"]
    ):
        reasons.append("isolated_token_fragmentation")

    if len(reasons) >= 2:
        return {
            "accepted": False,
            "classification": "soft_garbage",
            "reason_code": reasons[0],
            "reason_codes": reasons,
            "plausible_natural_language": False,
            "plausible_reason_codes": [],
            "source_mode": source_mode,
            "boxes_available": False,
            **metrics,
        }

    return {
        "accepted": True,
        "classification": "accepted",
        "reason_code": "no_strong_garbage_evidence",
        "reason_codes": reasons,
        "plausible_natural_language": False,
        "plausible_reason_codes": [],
        "source_mode": source_mode,
        "boxes_available": False,
        **metrics,
    }


def outcome_log_fields(
    classification: dict[str, Any],
    *,
    final_outcome: str,
    exact_reason_code: str | None = None,
    task_id: int | None = None,
    subtitle_sequence_id: int | None = None,
    background_mode: str = "",
    display_policy: str = "",
) -> dict[str, Any]:
    keys = (
        "source_mode",
        "post_ocr_policy",
        "han",
        "hiragana",
        "katakana",
        "latin",
        "digits",
        "punctuation",
        "other",
        "effective_text_units",
        "source_script_units",
        "source_script_ratio",
        "short_fragment_bypass_eligible",
        "text_len",
        "safe_hash",
        "token_count",
        "alphabetic_ratio",
        "digit_ratio",
        "symbol_ratio",
        "vowel_ratio",
        "mixed_case_anomaly_count",
        "isolated_one_char_token_count",
        "joined_token_count",
        "classification",
        "boxes_available",
    )
    fields = {key: classification.get(key) for key in keys}
    fields.update(
        {
            "ocr_task_id": int(task_id or 0),
            "subtitle_sequence_id": int(subtitle_sequence_id or 0),
            "background_mode": str(background_mode or ""),
            "display_policy": str(display_policy or ""),
            "exact_reason_code": str(
                exact_reason_code
                if exact_reason_code is not None
                else classification.get("reason_code") or ""
            ),
            "reason_codes": list(classification.get("reason_codes") or []),
            "final_outcome": str(final_outcome or ""),
        }
    )
    return fields
