import io
import os
import re
import subprocess
from PIL import Image, ImageEnhance, ImageOps
import pytesseract
import config
import session_logging
from data_paths import DATA

# Immutable app-local Tesseract resources are resolved by the registry.
os.environ["TESSDATA_PREFIX"] = config.TESSDATA_PATH
pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_PATH


# -----------------------------------------------------------------------------
# OCR CORE v10 — FINAL TESSERACT NORMALIZATION BEFORE BACKENDS
# -----------------------------------------------------------------------------
# Цель этой версии: остановить «перелечивание» OCR.
#
# Что исправляем после v5-v7:
# - многострочные субтитры не должны обрезаться до одной строки;
# - fallback не должен становиться главным режимом и тащить длинные хвосты мусора;
# - троеточия и нормальная пунктуация должны сохраняться;
# - оставляем только осторожные OCR-исправления, без чрезмерного угадывания.
#
# Архитектура остаётся прежней:
# main.py передаёт PIL-картинку -> extract_text_from_pil_image() возвращает строку.
# -----------------------------------------------------------------------------


# Короткие токены, которые часто появляются на краях OCR из-за UI/фона.
# Они удаляются только с краёв строки, не из середины.
LEADING_JUNK_TOKENS = {
    "ee", "eee", "eeee", "eh", "ie", "soe", "mee", "meee", "mme", "ge",
    "wv", "vv", "xk", "te", "emeeen", "pene", "ees", "cell",
    # Из реальных fallback-логов: паразитные префиксы перед нормальным текстом.
    "rs", "nn", "ae", "al", "fee", "fn", "oo", "o0",
}

TRAILING_JUNK_TOKENS = {
    "ee", "eee", "eeee", "aaa", "aaaa", "iii", "iiii", "wv", "vv", "xk",
    "mn", "wtr", "te", "lo", "os", "cel", "cell",
}


# --- АВТООБРЕЗКА ТЕКСТА ВНУТРИ ВЫБРАННОЙ РАМКИ ---
OCR_AUTO_CROP_ENABLED = True
OCR_AUTOCROP_DEBUG = True

# Fallback оставляем, но только как страховку, если основной путь вернул пусто.
OCR_FALLBACK_FULL_FRAME_ENABLED = True

# Tesseract заметно лучше читает мелкий рукописный/курсивный шрифт,
# если перед OCR увеличить уже подготовленную картинку.
# Это не меняет захват, автообрезку или debug_ocr.png; масштабируется только
# временное изображение, которое отправляется в Tesseract.
OCR_TESSERACT_UPSCALE_ENABLED = True
OCR_TESSERACT_UPSCALE_FACTOR = 2
OCR_TESSERACT_UPSCALE_MAX_WIDTH = 2200
OCR_TESSERACT_UPSCALE_MAX_HEIGHT = 700

# Padding вокруг найденного текстового блока. Чуть больше по Y, чтобы не резать
# верх/низ рукописных и курсивных букв.
OCR_AUTOCROP_PADDING_X = 10
OCR_AUTOCROP_PADDING_Y = 8
OCR_AUTOCROP_MIN_WIDTH = 24
OCR_AUTOCROP_MIN_HEIGHT = 9

# Пороги для поиска белых букв. Идём от строгого к мягкому.
OCR_AUTOCROP_TEXT_THRESHOLDS = [215, 205, 195, 185, 175]

# ВАЖНО v8:
# Старый gap=8 часто разделял две строки субтитров на два разных блока,
# после чего выбиралась только более яркая/длинная первая строка.
# Теперь мы сначала находим отдельные полосы, а потом объединяем близкие
# горизонтально совместимые полосы в общий многострочный блок.
OCR_AUTOCROP_ROW_SPLIT_GAP = 8
OCR_AUTOCROP_LINE_GROUP_GAP = 30


COMMON_OCR_REPLACEMENTS = [
    # Сохраняем только осторожные исправления, которые не переписывают смысл.
    (r"\bcombinotion\b", "combination"),
    (r"\bcouse\b", "cause"),
    (r"\bbelween\b", "between"),
    (r"\bSomething\'s\s+rot\s+right\b", "Something's not right"),
    (r"\bSomething\s+rot\s+right\b", "Something's not right"),
    # Реальный OCR-сбой на курсивном шрифте: Wombination/copiation/clobes.
    (r"\bWombination\b", "combination"),
    (r"\bcopiation\b", "combination"),
    (r"\bclobes\b", "clothes"),
    # Узкий шаблон из реального лога: "tm no expert" / "tm ro expert".
    (r"\btm\s+(?:ro|no)\s+expert\b", "I'm no expert"),
]


GIBBERISH_FRAGMENT_PATTERNS = re.compile(
    r"(ragdg|eagdg|atieaget|atieagettren|acecat|arate|tneteeh|neteeh|behe|eeecos|ctiC|idoogd)",
    re.IGNORECASE,
)

COMMON_ENGLISH_HINT_WORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "so", "to", "of", "in",
    "on", "at", "for", "from", "with", "this", "that", "these", "those", "there",
    "here", "is", "are", "was", "were", "be", "been", "being", "am", "im", "i'm",
    "its", "it's", "not", "no", "dont", "don't", "do", "does", "did", "done",
    "can", "could", "should", "would", "will", "might", "must", "have", "has",
    "had", "her", "his", "him", "she", "he", "they", "them", "their", "you",
    "your", "me", "my", "we", "us", "what", "why", "where", "when", "how",
    "about", "into", "out", "up", "down", "middle", "right", "left", "time",
    "face", "death", "blood", "button", "something", "mention", "clothes", "looks",
    "expert", "swelling", "bruising", "severe", "early", "onset",
}


def log_translation(eng_text, translated_text=""):
    """Записывает оригинал и перевод в файл для анализа."""
    if not session_logging.raw_enabled():
        return
    from data_paths import log_file as data_log_file
    log_file = data_log_file("translator_logs.txt")
    try:
        with session_logging.open_log(log_file, "a", encoding="utf-8") as f:
            f.write("=" * 50 + "\n")
            f.write(f"ОРИГИНАЛ (OCR): {eng_text}\n")
            if translated_text:
                f.write(f"ПЕРЕВОД (ИИ):  {translated_text}\n")
            f.write("=" * 50 + "\n\n")
    except Exception as e:
        print(f"[Log Error]: {e}")


def normalize_spaces(text):
    """Приводит пробелы/переносы к одному пробелу, не трогая троеточия."""
    text = re.sub(r'[\r\n]+', ' ', text or '')
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def is_text_garbage(text):
    """
    Первичный фильтр очевидного мусора.
    Не должен быть слишком агрессивным: основная фильтрация зоны остаётся в main.py.
    """
    clean = normalize_spaces(text)
    if not clean:
        return True

    letters = len(re.findall(r'[a-zA-Z]', clean))
    if letters < 3:
        return True

    words = clean.split()
    short_words = [w for w in words if len(w.strip(".,!?\"'()-:;…")) <= 2]
    if len(words) > 0 and (len(short_words) / len(words)) > 0.78 and letters < 14:
        return True

    return False


def _trim_large_column_gaps(candidate_cols, max_gap=45):
    """
    Убирает далёкие одиночные группы колонок, часто являющиеся UI-иконками.
    Возвращает самый широкий горизонтальный блок.
    """
    if not candidate_cols:
        return []

    groups = []
    start = candidate_cols[0]
    prev = candidate_cols[0]

    for x in candidate_cols[1:]:
        if x - prev <= max_gap:
            prev = x
        else:
            groups.append((start, prev))
            start = x
            prev = x

    groups.append((start, prev))
    if not groups:
        return candidate_cols

    best = max(groups, key=lambda g: g[1] - g[0])
    return [x for x in candidate_cols if best[0] <= x <= best[1]]


def _extract_cluster_candidate(mask_img, data, width, row_counts, top, bottom):
    """Преобразует вертикальную полосу ярких пикселей в кандидат текстовой строки."""
    band_h = bottom - top
    if band_h < OCR_AUTOCROP_MIN_HEIGHT:
        return None

    bright_sum = sum(row_counts[top:bottom])
    area = width * band_h
    density = bright_sum / area if area > 0 else 0.0

    # Плотные полосы обычно являются светлой панелью/фоном, а не текстом.
    if density > 0.34:
        return None

    # Слишком высокая полоса сама по себе подозрительна; но в v8 многострочный
    # блок собирается из нескольких строк, поэтому отдельная строка не должна быть огромной.
    if band_h > 75:
        return None

    col_min_pixels = max(1, int(band_h * 0.04))
    candidate_cols = []

    for x in range(width):
        bright_count = 0
        for y in range(top, bottom):
            if data[y * width + x] > 0:
                bright_count += 1
        if bright_count >= col_min_pixels:
            candidate_cols.append(x)

    if not candidate_cols:
        return None

    compact_cols = _trim_large_column_gaps(candidate_cols, max_gap=max(35, int(width * 0.08)))
    if compact_cols:
        candidate_cols = compact_cols

    left = min(candidate_cols)
    right = max(candidate_cols) + 1
    text_w = right - left

    if text_w < OCR_AUTOCROP_MIN_WIDTH:
        return None

    # Очень узкая строка в углу скорее похожа на иконку, если ярких пикселей мало.
    if text_w < max(40, int(width * 0.08)) and bright_sum < 250:
        return None

    center = (left + right) / 2.0
    score = bright_sum + text_w * 2 - abs(center - width / 2.0) * 0.25

    return {
        "top": top,
        "bottom": bottom,
        "left": left,
        "right": right,
        "height": band_h,
        "width": text_w,
        "density": density,
        "bright_sum": bright_sum,
        "center": center,
        "score": score,
    }


def _clusters_are_compatible(a, b, frame_width):
    """Проверяет, похожи ли две строки на части одного субтитрового блока."""
    gap = max(0, max(a["top"], b["top"]) - min(a["bottom"], b["bottom"]))
    if gap > OCR_AUTOCROP_LINE_GROUP_GAP:
        return False

    overlap = min(a["right"], b["right"]) - max(a["left"], b["left"])
    min_w = max(1, min(a["width"], b["width"]))
    center_dist = abs(a["center"] - b["center"])

    if overlap > min_w * 0.12:
        return True

    # Для центрированных субтитров вторая строка может быть существенно короче.
    if center_dist < frame_width * 0.28:
        return True

    return False


def _find_text_bbox_from_binary_mask(mask_img):
    """
    Ищет bbox не отдельной строки, а вероятного текстового блока.

    Главное отличие v8:
    - сначала ищем отдельные горизонтальные полосы текста;
    - потом объединяем близкие совместимые полосы;
    - так двухстрочные субтитры не превращаются в одну верхнюю строку.
    """
    width, height = mask_img.size
    if width <= 0 or height <= 0:
        return None

    data = mask_img.tobytes()

    row_counts = []
    for y in range(height):
        row = data[y * width:(y + 1) * width]
        row_counts.append(sum(1 for px in row if px > 0))

    row_min_pixels = max(2, int(width * 0.002))
    candidate_rows = [y for y, cnt in enumerate(row_counts) if cnt >= row_min_pixels]
    if not candidate_rows:
        return None

    # 1. Сырые вертикальные полосы.
    raw_clusters = []
    cur_start = candidate_rows[0]
    cur_end = candidate_rows[0]

    for y in candidate_rows[1:]:
        if y - cur_end <= OCR_AUTOCROP_ROW_SPLIT_GAP:
            cur_end = y
        else:
            raw_clusters.append((cur_start, cur_end + 1))
            cur_start = y
            cur_end = y
    raw_clusters.append((cur_start, cur_end + 1))

    # 2. Фильтруем полосы и превращаем их в кандидатов строк.
    candidates = []
    for top, bottom in raw_clusters:
        cand = _extract_cluster_candidate(mask_img, data, width, row_counts, top, bottom)
        if cand is not None:
            candidates.append(cand)

    if not candidates:
        return None

    # 3. Берём лучший кандидат как якорь и добираем совместимые строки рядом.
    best = max(candidates, key=lambda c: c["score"])
    selected = [best]

    changed = True
    while changed:
        changed = False
        for cand in candidates:
            if cand in selected:
                continue
            if any(_clusters_are_compatible(cand, s, width) for s in selected):
                tentative_top = min([x["top"] for x in selected] + [cand["top"]])
                tentative_bottom = max([x["bottom"] for x in selected] + [cand["bottom"]])
                tentative_h = tentative_bottom - tentative_top
                # Не даём автообрезке захватить почти всю рамку.
                if tentative_h <= max(125, int(height * 0.78)):
                    selected.append(cand)
                    changed = True

    left = min(c["left"] for c in selected)
    right = max(c["right"] for c in selected)
    top = min(c["top"] for c in selected)
    bottom = max(c["bottom"] for c in selected)

    if (right - left) < OCR_AUTOCROP_MIN_WIDTH or (bottom - top) < OCR_AUTOCROP_MIN_HEIGHT:
        return None

    left = max(0, left - OCR_AUTOCROP_PADDING_X)
    top = max(0, top - OCR_AUTOCROP_PADDING_Y)
    right = min(width, right + OCR_AUTOCROP_PADDING_X)
    bottom = min(height, bottom + OCR_AUTOCROP_PADDING_Y)

    if right <= left or bottom <= top:
        return None

    return (left, top, right, bottom)


def preprocess_image(img, enable_autocrop=True, debug_suffix=""):
    """Очистка картинки перед Tesseract."""
    width, height = img.size

    # Минимальная внешняя обрезка краёв рамки.
    padding_x = 10
    padding_y = 2
    if width > padding_x * 2 and height > padding_y * 2:
        img = img.crop((padding_x, padding_y, width - padding_x, height - padding_y))

    gray_img = img.convert("L")
    contrast_img = ImageEnhance.Contrast(gray_img).enhance(3.0)

    final_thresh = 140
    mask_img = contrast_img.point(lambda x: 255 if x > final_thresh else 0, mode='L')

    if OCR_AUTO_CROP_ENABLED and enable_autocrop:
        bbox = None
        for text_thresh in OCR_AUTOCROP_TEXT_THRESHOLDS:
            bbox_mask = contrast_img.point(lambda x, t=text_thresh: 255 if x > t else 0, mode='L')
            bbox = _find_text_bbox_from_binary_mask(bbox_mask)
            if bbox is not None:
                break

        if bbox is not None:
            img = img.crop(bbox)
            gray_img = gray_img.crop(bbox)
            contrast_img = contrast_img.crop(bbox)
            mask_img = mask_img.crop(bbox)
            if OCR_AUTOCROP_DEBUG and session_logging.raw_enabled():
                contrast_img.save(DATA.ensure("diagnostics") / f"debug_ocr_autocrop{debug_suffix}.png")
        else:
            if OCR_AUTOCROP_DEBUG and session_logging.raw_enabled():
                contrast_img.save(DATA.ensure("diagnostics") / f"debug_ocr_autocrop_failed{debug_suffix}.png")

    binary_img = mask_img.convert('1')
    final_img = ImageOps.invert(binary_img.convert("RGB")).convert("L")
    if session_logging.raw_enabled():
        final_img.save(DATA.ensure("diagnostics") / f"debug_ocr{debug_suffix}.png")
    return final_img


def _clean_token(token):
    return token.strip(".,!?\"'():;[]{}<>-_“”‘’…").lower()


def strip_repeated_edge_junk(text):
    """Удаляет короткий OCR-мусор только по краям строки."""
    text = normalize_spaces(text)
    if not text:
        return ""

    tokens = text.split()

    # Левая сторона.
    changed = True
    while tokens and changed:
        changed = False
        first = _clean_token(tokens[0])
        second = _clean_token(tokens[1]) if len(tokens) > 1 else ""

        if first in LEADING_JUNK_TOKENS:
            tokens.pop(0)
            changed = True
            continue

        # "FEE SO I'm..." -> убираем SO только если он остался после FEE/FN и перед I'm/I.
        if first == "so" and len(tokens) > 1 and second in {"i", "im", "i'm"}:
            tokens.pop(0)
            changed = True
            continue

        # Одинокая a перед мусором/заглавным словом чаще является артефактом.
        if first == "a" and len(tokens) > 1:
            if second in LEADING_JUNK_TOKENS or tokens[1][:1].isupper():
                tokens.pop(0)
                changed = True
                continue

        # Лишняя I перед заглавной фразой. Нормальные "I was/I don't" не трогаем.
        if first == "i" and len(tokens) > 1:
            allowed_i_followers = {
                "am", "was", "were", "will", "would", "can", "could", "should",
                "dont", "don't", "guess", "know", "think", "feel", "see", "saw", "have", "had",
            }
            if second not in allowed_i_followers:
                tokens.pop(0)
                changed = True
                continue

    # Правая сторона.
    changed = True
    while tokens and changed:
        changed = False
        last = _clean_token(tokens[-1])

        if last in TRAILING_JUNK_TOKENS:
            tokens.pop()
            changed = True
            continue
        if re.fullmatch(r'i+', last) and len(last) >= 2:
            tokens.pop()
            changed = True
            continue
        if re.fullmatch(r'a{2,}', last):
            tokens.pop()
            changed = True
            continue
        if tokens[-1] == "iN":
            tokens.pop()
            changed = True
            continue

    text = normalize_spaces(" ".join(tokens))

    # Составные хвосты из старых логов.
    text = re.sub(r'\b(?:XK\s+)?i\s+a{2,},?\s*(?:e{2,})?$', '', text).strip()
    text = re.sub(r'\bwv\s+ee$', '', text, flags=re.IGNORECASE).strip()
    text = re.sub(r'\bWv\s+Te\s+WTR$', '', text).strip()
    text = re.sub(r'\ben\s+ee\s+mn$', '', text, flags=re.IGNORECASE).strip()
    text = re.sub(r'\bth\s+en\s+eine\s+i$', '', text, flags=re.IGNORECASE).strip()

    return normalize_spaces(text)


def strip_low_quality_tail_after_sentence(text):
    """
    Убирает явный хвост после нормальной фразы, если Tesseract прицепил мусор.

    Пример:
    "I'm no expert, but this combination of clothes looks. of mmm me a"
    -> "I'm no expert, but this combination of clothes looks off..."

    Это узкая правка под реальный тип OCR-ошибки. Не используется как общий
    литературный редактор.
    """
    text = normalize_spaces(text)
    if not text:
        return ""

    # Частая ошибка Tesseract на рукописном/курсивном "looks... off...":
    # визуальные точки и буквы ff распадаются как:
    #   looks. of
    #   looks. of f
    #   looks. off-
    #   looks. of ow
    #   looks. of Koy
    # Исправляем только в узком контексте "looks ... off", чтобы не превращать
    # произвольное "of" в "off".

    # 1) Самый безопасный вариант: OCR уже увидел off, но вставил лишнюю точку
    #    между looks и off. Для переводчика это критично, потому что "off"
    #    становится отдельным обрывком и часто теряется.
    text = re.sub(
        r"\blooks\s*(?:\.{1,3}|,)\s+off-?\.?(?:\s*)$",
        "looks off...",
        text,
        flags=re.IGNORECASE,
    )

    # 2) Вариант, где ff развалились на of/f/ow/Koy/m...
    text = re.sub(
        r"\blooks\s*(?:\.{1,3}|,)\s+of(?:\s+(?:f-?|ow|koy|m+|me|a))*\b\.?(?:\s*)$",
        "looks off...",
        text,
        flags=re.IGNORECASE,
    )

    # Более общий, но всё ещё безопасный случай: слово off раскололось как "of f".
    text = re.sub(
        r"\bof\s+f-?\b",
        "off",
        text,
        flags=re.IGNORECASE,
    )

    # Общий срез: после точки идёт короткий набор явно мусорных токенов.
    text = re.sub(
        r"\.\s+(?:Vv\s+)?(?:Lo|oS|os|cel|cell|m+|me|a)(?:\s+(?:Lo|oS|os|cel|cell|m+|me|a))*\s*$",
        ".",
        text,
        flags=re.IGNORECASE,
    )

    # Длинные хвосты со скобками/повторами после нормального предложения.
    text = re.sub(
        r"\s+(?:Vv\s+)?a\s+e{2,}[A-Za-z0-9()'\";:]+$",
        "",
        text,
        flags=re.IGNORECASE,
    ).strip()

    # Финальная страховка: если после нормализации осталось "off-" в конце,
    # считаем это рукописным многоточием/затуханием фразы, а не дефисом.
    text = re.sub(r"\boff-\s*$", "off...", text, flags=re.IGNORECASE)

    # Хвосты UI/иконок, которые часто появляются после завершённой строки.
    text = re.sub(r"\s+WwW\s*$", "", text, flags=re.IGNORECASE)

    return normalize_spaces(text)


def looks_like_long_ocr_gibberish(text):
    """Защита от длинных псевдоанглийских строк из текстур."""
    text = normalize_spaces(text)
    if not text:
        return True

    if GIBBERISH_FRAGMENT_PATTERNS.search(text):
        return True

    words = re.findall(r"[A-Za-z']+", text)
    if len(words) < 8:
        return False

    lowered = [w.lower().strip("'") for w in words]
    common_count = sum(1 for w in lowered if w in COMMON_ENGLISH_HINT_WORDS)

    suspicious = 0
    for w in lowered:
        clean = re.sub(r"[^a-z]", "", w)
        if len(clean) < 5:
            continue
        if re.search(r"([a-z])\1\1", clean):
            suspicious += 1
            continue
        if re.search(r"[bcdfghjklmnpqrstvwxyz]{4,}", clean):
            suspicious += 1
            continue
        if clean.endswith(("gdg", "gdt", "ttr", "eeh", "ehe")):
            suspicious += 1
            continue

    if common_count <= 2 and suspicious >= 3:
        return True

    return False


def apply_common_ocr_corrections(text):
    """Осторожные OCR-исправления."""
    text = text or ""

    for pattern, replacement in COMMON_OCR_REPLACEMENTS:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    text = re.sub(r'\bNo\.\.', 'No.', text)
    text = re.sub(r"\bdont\b", "don't", text, flags=re.IGNORECASE)
    text = re.sub(r"\bdortt\b", "don't", text, flags=re.IGNORECASE)
    text = re.sub(r"\bYoudl\b", "You'll", text, flags=re.IGNORECASE)
    text = re.sub(r"\bYoull\b", "You'll", text, flags=re.IGNORECASE)

    return normalize_spaces(text)


def clean_ocr_text(raw_text, lang="eng"):
    """Главная постобработка OCR-строки."""
    text = normalize_spaces(raw_text)
    if not text:
        return ""

    tesseract_lang = "eng" if lang == "en" else lang

    if tesseract_lang == "eng":
        text = text.replace('|', 'I')
        text = re.sub(r'\bl\b', 'I', text)

        # Сохраняем троеточия и базовую пунктуацию.
        # Символ … тоже сохраняем, но затем нормализуем в "...".
        text = re.sub(r'[^a-zA-Z0-9\s.,!?…\'"\-:;()]', '', text)
        text = text.replace('…', '...')
        text = normalize_spaces(text)

        text = strip_repeated_edge_junk(text)
        text = strip_low_quality_tail_after_sentence(text)
        text = apply_common_ocr_corrections(text)
        text = strip_repeated_edge_junk(text)
        text = strip_low_quality_tail_after_sentence(text)

        words = text.split()
        clean_words = []
        for w in words:
            stripped = w.strip(".,!?\"'()-:;…")
            if len(stripped) > 1 or stripped.lower() in ['i', 'a']:
                clean_words.append(w)
        text = normalize_spaces(" ".join(clean_words))

    else:
        text = normalize_spaces(text)

    return text


def _ocr_quality_score(text):
    """Грубая оценка OCR-кандидата для выбора между основным и fallback-проходом."""
    text = normalize_spaces(text)
    if not text:
        return -999

    letters = len(re.findall(r"[a-zA-Z]", text))
    words = re.findall(r"[A-Za-z']+", text)
    weird = len(re.findall(r"[(){}\[\]<>_;]", text))
    non_basic = len(re.findall(r"[^a-zA-Z0-9\s.,!?\-:'\"]", text))

    score = letters
    score -= weird * 5
    score -= non_basic * 3

    if looks_like_long_ocr_gibberish(text):
        score -= 100

    if words:
        common_count = sum(1 for w in words if w.lower().strip("'") in COMMON_ENGLISH_HINT_WORDS)
        score += common_count * 2
        short_count = sum(1 for w in words if len(w.strip("'")) <= 2)
        score -= int((short_count / len(words)) * 12)

    return score


def _prepare_image_for_tesseract(clean_img):
    """Мягкое увеличение перед Tesseract для мелкого курсивного игрового шрифта."""
    if not OCR_TESSERACT_UPSCALE_ENABLED:
        return clean_img

    width, height = clean_img.size
    if width <= 0 or height <= 0:
        return clean_img

    factor = OCR_TESSERACT_UPSCALE_FACTOR
    new_w = width * factor
    new_h = height * factor

    if new_w > OCR_TESSERACT_UPSCALE_MAX_WIDTH or new_h > OCR_TESSERACT_UPSCALE_MAX_HEIGHT:
        return clean_img

    # LANCZOS для уже бинаризованной картинки обычно даёт Tesseract более гладкие
    # контуры букв, не меняя сам debug_ocr.png.
    return clean_img.resize((new_w, new_h), Image.Resampling.LANCZOS)


def _run_tesseract_on_processed_image(clean_img, tesseract_lang="eng", psm=6):
    """Один OCR-проход по уже подготовленной картинке."""
    ocr_img = _prepare_image_for_tesseract(clean_img)

    if os.name == "nt" and not (config.TESSDATA_PATH + str(DATA.temp())).isascii():
        # The bundled native CLI can fail to open absolute non-ASCII paths.
        # Use Win32 cwd for tessdata and pass the same prepared image via stdin.
        encoded = io.BytesIO()
        ocr_img.save(encoded, format="PNG")
        proc = subprocess.run(
            [config.TESSERACT_PATH, "stdin", "stdout", "-l", tesseract_lang,
             "--psm", str(psm), "--tessdata-dir", "."],
            cwd=config.TESSDATA_PATH, input=encoded.getvalue(), capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if proc.returncode:
            raise pytesseract.TesseractError(proc.returncode, proc.stderr.decode("utf-8", errors="replace").strip())
        raw_text = proc.stdout.decode("utf-8").strip()
    else:
        raw_text = pytesseract.image_to_string(
            ocr_img,
            lang=tesseract_lang,
            config=f"--psm {psm}",
        ).strip()

    text = clean_ocr_text(raw_text, lang=tesseract_lang)

    if tesseract_lang == "eng":
        if is_text_garbage(text):
            return ""
        if looks_like_long_ocr_gibberish(text):
            return ""
        if _ocr_quality_score(text) < 5:
            return ""

    return text


def _log_fallback(kind, text):
    try:
        from data_paths import log_file
        with session_logging.open_log(log_file("ocr_fallback.log"), "a", encoding="utf-8") as f:
            f.write(f"{kind}: {text}\n")
    except Exception:
        pass


def extract_text_from_pil_image(img, lang="eng"):
    """
    Извлекает текст из PIL-картинки.

    v8 recovery:
    - основной путь: многострочная автообрезка + psm 6;
    - fallback: только если основной путь пустой;
    - fallback принимается только если не выглядит хуже очевидного мусора;
    - debug_ocr.png теперь должен сохранять весь найденный текстовый блок, а не одну строку.
    """
    try:
        tesseract_lang = "eng" if lang == "en" else lang

        # 1. Основной путь: block crop + psm 6.
        clean_img = preprocess_image(img, enable_autocrop=True, debug_suffix="")
        text = _run_tesseract_on_processed_image(clean_img, tesseract_lang=tesseract_lang, psm=6)
        if text:
            return text

        if not OCR_FALLBACK_FULL_FRAME_ENABLED:
            return ""

        # 2. Осторожный fallback: полный кроп без внутренней автообрезки.
        clean_img_full = preprocess_image(img, enable_autocrop=False, debug_suffix="_fallback_full")

        fallback_candidates = []
        for psm in (6, 7):
            candidate = _run_tesseract_on_processed_image(clean_img_full, tesseract_lang=tesseract_lang, psm=psm)
            if candidate:
                fallback_candidates.append((psm, candidate, _ocr_quality_score(candidate)))

        if not fallback_candidates:
            return ""

        fallback_candidates.sort(key=lambda item: item[2], reverse=True)
        best_psm, best_text, best_score = fallback_candidates[0]

        # Не пускаем fallback, если он едва прошёл и выглядит сомнительно.
        if best_score < 12:
            return ""

        _log_fallback(f"FALLBACK_FULL_FRAME_PSM{best_psm}_USED", best_text)
        return best_text

    except Exception as e:
        print(f"[OCR Error]: {e}")
        return ""
