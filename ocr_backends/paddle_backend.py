"""
Production PaddleOCR backend.

This backend is lazy-loaded: importing the application does not import PaddleOCR.
The heavy OCR model is loaded only when this backend is actually used, usually
when the user selects the quality OCR mode.

It supports both newer PaddleOCR 3.x style results and older 2.x style results
as far as practical, because PaddleOCR public result objects have changed across
versions.
"""

from __future__ import annotations

import os
import re
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Callable, List

import startup_block3_diagnostics
import responsiveness_diagnostics as responsiveness


from resource_registry import resource_path

from data_paths import DATA, data_path
PADDLE_MODEL_ROOT = data_path("paddle.models")
PADDLE_HOME_DIR = data_path("paddle.home")
PADDLEX_CACHE_DIR = data_path("paddlex")
MODELSCOPE_CACHE_DIR = data_path("modelscope")
HF_CACHE_DIR = data_path("hf.home")
PADDLE_TMP_DIR = DATA.temp("paddle")


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y", "enabled"}


class PaddleBackend:
    """Adapter for PaddleOCR quality and speed presets.

    The same class is used by two live OCR modes:
    - preset="quality": the current stable PaddleOCR behaviour;
    - preset="fast": PP-OCRv4 mobile, measured as the faster live subtitle
      candidate in step32.

    The modes are separate backend instances in ocr_engine.py, so choosing the
    fast preset never removes or changes the existing quality preset.
    """

    def __init__(self, preset: str = "quality", text_det_limit_side_len: int | None = None) -> None:
        self.preset = (preset or "quality").strip().lower()
        self.text_det_limit_side_len = text_det_limit_side_len
        self.name = (
            "paddle_block2_fast"
            if self.preset == "block2_fast"
            else "paddle_fast"
            if self.preset == "fast"
            else "paddle"
        )
        self.display_name = (
            "PaddleOCR v6 Small"
            if self.preset == "block2_fast"
            else "PaddleOCR fast"
            if self.preset == "fast"
            else "PaddleOCR quality"
        )
        self._ocr = None
        self._loaded_lang = ""
        self._load_error: Exception | None = None
        self._startup_diag_model_load_started = False
        self._startup_diag_model_ready = False
        self._startup_diag_import_started = False
        self._startup_diag_import_ended = False
        self._startup_diag_backend_create_started = False
        self._startup_diag_backend_create_ended = False
        self._startup_diag_warmup_started = False
        self._startup_diag_warmup_ended = False

    def _prepare_project_local_storage(self) -> None:
        """Keep Paddle/PaddleX/model caches and fallback temp inside the project."""
        for path in (
            PADDLE_MODEL_ROOT,
            PADDLE_HOME_DIR,
            PADDLEX_CACHE_DIR,
            MODELSCOPE_CACHE_DIR,
            HF_CACHE_DIR,
            HF_CACHE_DIR / "hub",
            PADDLE_TMP_DIR,
        ):
            path.mkdir(parents=True, exist_ok=True)

        forced_env = {key: str(path) for key, path in DATA.environment_paths("paddle").items()}
        forced_env.update({
            "PADDLE_HOME": str(PADDLE_HOME_DIR),
            "PADDLE_PDX_CACHE_HOME": str(PADDLEX_CACHE_DIR),
            "MODELSCOPE_CACHE": str(MODELSCOPE_CACHE_DIR),
            "HF_HOME": str(HF_CACHE_DIR),
            "HUGGINGFACE_HUB_CACHE": str(HF_CACHE_DIR / "hub"),
            "TRANSFORMERS_CACHE": str(HF_CACHE_DIR / "hub"),
            "GAME_TRANSLATOR_PADDLE_MODEL_ROOT": str(PADDLE_MODEL_ROOT),
            "GAME_TRANSLATOR_PADDLE_TMP": str(PADDLE_TMP_DIR),
        })
        for key, value in forced_env.items():
            os.environ[key] = value

    def _downloads_allowed(self) -> bool:
        return _truthy(os.environ.get("GAME_TRANSLATOR_ALLOW_PADDLE_MODEL_DOWNLOADS"))

    def _install_download_guard(self) -> Callable[[], None]:
        """Block accidental Paddle model downloads unless explicitly enabled."""
        if self._downloads_allowed():
            return lambda: None

        patches: List[tuple[Any, str, Any]] = []

        def blocked(*_args: Any, **_kwargs: Any) -> Any:
            raise RuntimeError(
                "PADDLE_OCR_MODEL_DOWNLOAD_REQUIRED: PaddleOCR attempted to download a model, "
                f"but downloads are disabled. Put required Paddle models/cache under {PADDLE_MODEL_ROOT} "
                "or rerun only after explicit controlled-download approval."
            )

        def patch_attr(obj: Any, attr: str) -> None:
            try:
                original = getattr(obj, attr)
                setattr(obj, attr, blocked)
                patches.append((obj, attr, original))
            except Exception:
                pass

        try:
            import requests  # type: ignore

            for attr in ("get", "post", "request"):
                patch_attr(requests, attr)
        except Exception:
            pass

        try:
            import httpx  # type: ignore

            for attr in ("get", "post", "request", "stream"):
                patch_attr(httpx, attr)
        except Exception:
            pass

        for attr in ("urlopen", "urlretrieve"):
            patch_attr(urllib.request, attr)

        def restore() -> None:
            for obj, attr, original in reversed(patches):
                try:
                    setattr(obj, attr, original)
                except Exception:
                    pass

        return restore

    def _format_load_error(self, exc: Exception) -> str:
        message = str(exc)
        if "PADDLE_OCR_MODEL_DOWNLOAD_REQUIRED" in message:
            return message
        lower = message.lower()
        if "download" in lower or "not found" in lower or "no such file" in lower or "missing" in lower:
            return (
                f"PADDLE_OCR_MODEL_MISSING: PaddleOCR model/cache is missing or unavailable for "
                f"{self.name}. Expected project-local storage: {PADDLE_MODEL_ROOT}. "
                f"Original error: {message}"
            )
        return message

    def _map_lang(self, lang: str) -> str:
        """Map app/Tesseract language names to PaddleOCR language names."""
        value = (lang or "en").lower().strip()
        if value in {"eng", "en", "english"}:
            return "en"
        if value in {"rus", "ru", "russian"}:
            return "ru"
        if value in {"ukr", "uk", "ukrainian"}:
            # Paddle may not support Ukrainian in all builds; keep for future.
            return "uk"
        if value in {"jpn", "ja", "japanese"}:
            return "japan"
        if value in {"chi_sim", "zh", "ch", "chinese"}:
            return "ch"
        if value in {"kor", "ko", "korean"}:
            return "korean"
        return value

    def _prepare_paddle_runtime_flags(self) -> None:
        """
        Prepare safer Paddle runtime flags for Windows CPU compare mode.

        The first compare test can fail inside Paddle oneDNN/PIR executor on some
        Windows/Paddle combinations. We do not need oneDNN acceleration for this
        diagnostic mode, so we explicitly prefer the safer generic CPU path.
        Unknown flags are ignored by Paddle.
        """
        # Must be set before importing paddle/paddleocr whenever possible.
        # Step19: force the safe generic CPU path. Do NOT use setdefault here:
        # a previous run/terminal may have already set oneDNN/MKLDNN flags.
        # The user's logs showed Paddle falling into oneDNN/PIR and then falling
        # back to Tesseract. We need PaddleOCR quality, not a fast fallback.
        thread_count = os.environ.get("GAME_TRANSLATOR_PADDLE_CPU_THREADS", "2").strip() or "2"

        forced_env = {
            "FLAGS_use_mkldnn": "0",
            "FLAGS_use_onednn": "0",
            "FLAGS_enable_pir_api": "0",
            "FLAGS_enable_pir_in_executor": "0",
            "FLAGS_use_pir_api": "0",
            "OMP_NUM_THREADS": thread_count,
            "MKL_NUM_THREADS": thread_count,
            "OPENBLAS_NUM_THREADS": thread_count,
            "NUMEXPR_NUM_THREADS": thread_count,
            "FLAGS_cpu_math_library_num_threads": thread_count,
        }
        for key, value in forced_env.items():
            os.environ[key] = value

        try:
            # PaddleOCR 3.x may import ModelScope, which imports torch. On the
            # user's Windows/Python 3.13 environment, diagnostics showed that
            # importing paddle first and torch later can fail on torch\lib\shm.dll,
            # while torch -> paddle -> paddleocr works. This import is intentionally
            # best-effort: it improves stability when torch is present and does
            # nothing if torch is absent.
            try:
                import torch  # noqa: F401
            except Exception:
                pass

            import paddle

            for flags in (
                {"FLAGS_use_mkldnn": False},
                {"FLAGS_enable_pir_api": False},
                {"FLAGS_cpu_math_library_num_threads": int(thread_count)},
            ):
                try:
                    paddle.set_flags(flags)
                except Exception:
                    pass
        except Exception:
            # Paddle may not be importable yet. The later import will report the real error.
            pass

    def _constructor_extra_kwargs(self) -> dict[str, Any]:
        """Return PaddleOCR constructor kwargs for the selected preset."""
        kwargs: dict[str, Any] = {}
        if self.preset == "fast":
            kwargs.update(
                {
                    "ocr_version": "PP-OCRv4",
                    "text_detection_model_name": "PP-OCRv4_mobile_det",
                    "text_detection_model_dir": str(resource_path("paddle.fast_det")),
                    "text_recognition_model_name": "en_PP-OCRv4_mobile_rec",
                    "text_recognition_model_dir": str(resource_path("paddle.fast_rec")),
                }
            )
        elif self.preset == "block2_fast":
            kwargs.update(
                {
                    "ocr_version": "PP-OCRv6",
                    "text_detection_model_name": "PP-OCRv6_small_det",
                    "text_detection_model_dir": str(resource_path("paddle.block2_det")),
                    "text_recognition_model_name": "PP-OCRv6_small_rec",
                    "text_recognition_model_dir": str(resource_path("paddle.block2_rec")),
                }
            )
        else:
            kwargs.update(
                {
                    "text_detection_model_name": "PP-OCRv6_medium_det",
                    "text_detection_model_dir": str(resource_path("paddle.full_det")),
                    "text_recognition_model_name": "PP-OCRv6_medium_rec",
                    "text_recognition_model_dir": str(resource_path("paddle.full_rec")),
                }
            )
            if self.text_det_limit_side_len is not None:
                kwargs["text_det_limit_side_len"] = int(self.text_det_limit_side_len)
        return kwargs

    def _predict_extra_kwargs(self) -> dict[str, Any]:
        """Return PaddleOCR predict kwargs for the selected preset.

        PaddleOCR 3.x accepts text_det_limit_side_len both at construction and
        predict time. Passing it at predict time keeps the preset active even if
        a constructor fallback had to drop unsupported kwargs for compatibility.
        Older API variants may reject the kwarg; _run_ocr_on_input catches that
        and retries without it.
        """
        if self.preset in {"fast", "block2_fast"}:
            return {}
        if self.text_det_limit_side_len is None:
            return {}
        return {"text_det_limit_side_len": int(self.text_det_limit_side_len)}

    def _ensure_loaded(self, lang: str = "en") -> Any:
        """Load PaddleOCR lazily, with compatibility for 3.x and 2.x params."""
        paddle_lang = self._map_lang(lang)
        if self._ocr is not None and (
            self.preset == "block2_fast" or self._loaded_lang == paddle_lang
        ):
            return self._ocr
        if self._ocr is not None and self._loaded_lang != paddle_lang:
            self._ocr = None
            self._load_error = None
        if self._load_error is not None:
            raise RuntimeError(f"PaddleOCR is unavailable: {self._load_error}")

        restore_download_guard = lambda: None
        try:
            if not self._startup_diag_model_load_started:
                self._startup_diag_model_load_started = True
                startup_block3_diagnostics.emit_startup_diagnostic(
                    "PADDLE_MODEL_LOAD_STARTED",
                    backend=self.name,
                    lang=paddle_lang,
                )
            self._prepare_project_local_storage()
            if not self._startup_diag_import_started:
                self._startup_diag_import_started = True
                startup_block3_diagnostics.emit_startup_diagnostic(
                    "PADDLE_IMPORT_BEGIN",
                    backend=self.name,
                    lang=paddle_lang,
                )
            try:
                self._prepare_paddle_runtime_flags()
                restore_download_guard = self._install_download_guard()
                from paddleocr import PaddleOCR
            except Exception as import_exc:
                if not self._startup_diag_import_ended:
                    self._startup_diag_import_ended = True
                    startup_block3_diagnostics.emit_startup_diagnostic(
                        "PADDLE_IMPORT_END",
                        backend=self.name,
                        lang=paddle_lang,
                        ok=False,
                        error=f"{type(import_exc).__name__}:{import_exc}"[:500],
                    )
                raise
            if not self._startup_diag_import_ended:
                self._startup_diag_import_ended = True
                startup_block3_diagnostics.emit_startup_diagnostic(
                    "PADDLE_IMPORT_END",
                    backend=self.name,
                    lang=paddle_lang,
                    ok=True,
                )

            preset_kwargs = self._constructor_extra_kwargs()

            # PaddleOCR 3.x / 2.x constructor compatibility.
            #
            # Step17 showed an important compatibility problem: some installed
            # PaddleOCR builds reject certain documented/older parameter names by
            # raising a non-TypeError exception such as "Unknown argument: ...".
            # If we stop on that first exception, Paddle becomes unavailable and
            # the app silently falls back to Tesseract.
            #
            # Step18 therefore uses safer constructor attempts first and treats
            # "unknown argument" style failures as a signal to try the next,
            # simpler constructor variant. CPU threading is controlled through
            # environment/Paddle flags above rather than constructor kwargs.
            constructor_attempts = [
                # PaddleOCR 3.x / 2.x compatibility. Try oneDNN/MKLDNN-off
                # variants first; they worked in earlier stable builds and avoid
                # the Windows PIR/oneDNN runtime crash seen in step18.
                dict(
                    lang=paddle_lang,
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                    enable_mkldnn=False,
                    **preset_kwargs,
                ),
                dict(
                    lang=paddle_lang,
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    enable_mkldnn=False,
                    **preset_kwargs,
                ),
                dict(
                    lang=paddle_lang,
                    use_angle_cls=False,
                    show_log=False,
                    enable_mkldnn=False,
                    **preset_kwargs,
                ),
                dict(
                    lang=paddle_lang,
                    use_angle_cls=False,
                    enable_mkldnn=False,
                    **preset_kwargs,
                ),
                # Minimal variants for builds that reject enable_mkldnn or the
                # new document-pipeline arguments. These are fallback constructor
                # variants only; runtime flags above still force oneDNN off.
                dict(
                    lang=paddle_lang,
                    use_doc_orientation_classify=False,
                    use_doc_unwarping=False,
                    use_textline_orientation=False,
                    **preset_kwargs,
                ),
                dict(
                    lang=paddle_lang,
                    use_angle_cls=False,
                    show_log=False,
                    **preset_kwargs,
                ),
                dict(
                    lang=paddle_lang,
                    use_angle_cls=False,
                    **preset_kwargs,
                ),
                dict(
                    lang=paddle_lang,
                    **preset_kwargs,
                ),
            ]

            last_constructor_error: Exception | None = None

            def is_argument_compat_error(exc: Exception) -> bool:
                msg = str(exc).lower()
                return (
                    isinstance(exc, TypeError)
                    or "unknown argument" in msg
                    or "unexpected keyword" in msg
                    or "got an unexpected" in msg
                    or "invalid keyword" in msg
                    or "unrecognized" in msg
                )

            if not self._startup_diag_backend_create_started:
                self._startup_diag_backend_create_started = True
                startup_block3_diagnostics.emit_startup_diagnostic(
                    "PADDLE_BACKEND_CREATE_BEGIN",
                    backend=self.name,
                    lang=paddle_lang,
                    phase_note="PaddleOCR_constructor_includes_model_load",
                )
                startup_block3_diagnostics.emit_startup_diagnostic(
                    "PADDLE_MODEL_LOAD_BEGIN",
                    backend=self.name,
                    lang=paddle_lang,
                    combined_with="PADDLE_BACKEND_CREATE",
                )

            # First try the preset-aware constructors. If this PaddleOCR build
            # rejects text_det_limit_side_len at construction time, retry the same
            # compatibility matrix without preset kwargs; the fast preset can still
            # pass text_det_limit_side_len at predict time.
            for allow_preset_kwargs in (True, False):
                for base_kwargs in constructor_attempts:
                    kwargs = dict(base_kwargs)
                    if not allow_preset_kwargs:
                        kwargs.pop("text_det_limit_side_len", None)
                    try:
                        self._ocr = PaddleOCR(**kwargs)
                        self._loaded_lang = paddle_lang
                        if not self._startup_diag_model_ready:
                            self._startup_diag_model_ready = True
                            startup_block3_diagnostics.emit_startup_diagnostic(
                                "PADDLE_MODEL_LOAD_END",
                                backend=self.name,
                                lang=paddle_lang,
                                ok=True,
                                combined_with="PADDLE_BACKEND_CREATE",
                            )
                            if not self._startup_diag_backend_create_ended:
                                self._startup_diag_backend_create_ended = True
                                startup_block3_diagnostics.emit_startup_diagnostic(
                                    "PADDLE_BACKEND_CREATE_END",
                                    backend=self.name,
                                    lang=paddle_lang,
                                    ok=True,
                                    phase_note="PaddleOCR_constructor_includes_model_load",
                                )
                            startup_block3_diagnostics.emit_startup_diagnostic(
                                "PADDLE_MODEL_READY",
                                backend=self.name,
                                lang=paddle_lang,
                                constructor_kwargs=sorted(kwargs.keys()),
                            )
                        return self._ocr
                    except Exception as e:
                        last_constructor_error = e
                        if is_argument_compat_error(e):
                            continue
                        raise
                if not preset_kwargs:
                    break

            if last_constructor_error is not None:
                raise last_constructor_error
            raise RuntimeError("PaddleOCR constructor failed without a detailed error.")
        except Exception as e:
            if self._startup_diag_backend_create_started and not self._startup_diag_backend_create_ended:
                startup_block3_diagnostics.emit_startup_diagnostic(
                    "PADDLE_MODEL_LOAD_END",
                    backend=self.name,
                    lang=paddle_lang,
                    ok=False,
                    error=f"{type(e).__name__}:{e}"[:500],
                    combined_with="PADDLE_BACKEND_CREATE",
                )
                self._startup_diag_backend_create_ended = True
                startup_block3_diagnostics.emit_startup_diagnostic(
                    "PADDLE_BACKEND_CREATE_END",
                    backend=self.name,
                    lang=paddle_lang,
                    ok=False,
                    error=f"{type(e).__name__}:{e}"[:500],
                    phase_note="PaddleOCR_constructor_includes_model_load",
                )
            message = self._format_load_error(e)
            self._load_error = RuntimeError(message)
            raise RuntimeError(f"PaddleOCR load failed: {message}") from e
        finally:
            restore_download_guard()

    def extract_text_from_pil_image(self, img: Any, lang: str = "eng") -> str:
        """Extract text from a PIL image using PaddleOCR."""
        with responsiveness.phase("model_load_or_lookup", backend=self.name, lang=lang):
            ocr = self._ensure_loaded(lang=lang)

        # Сначала пробуем прямой ndarray-ввод. На части версий PaddleOCR это
        # работает быстрее, чем постоянная запись временного PNG на диск.
        # Если API этой версии PaddleOCR принимает только путь к файлу — мягко
        # откатываемся к старому стабильному пути через временный PNG.
        try:
            import numpy as np
            with responsiveness.phase("image_preprocess", backend=self.name, lang=lang):
                arr = np.array(img.convert("RGB"))
            result = self._run_ocr_on_input(ocr, arr)
            with responsiveness.phase("result_postprocess", backend=self.name, lang=lang):
                texts = self._extract_texts(result)
                text = self._postprocess(" ".join(texts))
            if text or self._result_looks_valid_empty(result):
                return text
        except Exception:
            pass

        PADDLE_TMP_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix="game_translator_paddle_",
            suffix=".png",
            delete=False,
            dir=str(PADDLE_TMP_DIR),
        ) as tmp:
            tmp_path = Path(tmp.name)
        try:
            img.convert("RGB").save(tmp_path)
            result = self._run_ocr_on_input(ocr, str(tmp_path))
            texts = self._extract_texts(result)
            return self._postprocess(" ".join(texts))
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _result_looks_valid_empty(self, result: Any) -> bool:
        """True, если OCR успешно отработал, но текста реально нет."""
        return result is not None

    def _run_ocr_on_input(self, ocr: Any, image_input: Any) -> Any:
        """Run OCR with compatibility for PaddleOCR API variants."""

        startup_warmup_call = not self._startup_diag_warmup_started
        if startup_warmup_call:
            self._startup_diag_warmup_started = True
            startup_block3_diagnostics.emit_startup_diagnostic(
                "PADDLE_WARMUP_BEGIN",
                backend=self.name,
                input_kind="path" if isinstance(image_input, (str, Path)) else "ndarray",
            )
            startup_block3_diagnostics.emit_startup_diagnostic(
                "PADDLE_WARMUP_STARTED",
                backend=self.name,
                input_kind="path" if isinstance(image_input, (str, Path)) else "ndarray",
            )

        try:
            with responsiveness.phase("paddle_api", backend=self.name):
                return self._run_ocr_api(ocr, image_input)
        finally:
            if startup_warmup_call and not self._startup_diag_warmup_ended:
                self._startup_diag_warmup_ended = True
                startup_block3_diagnostics.emit_startup_diagnostic(
                    "PADDLE_WARMUP_END",
                    backend=self.name,
                    input_kind="path" if isinstance(image_input, (str, Path)) else "ndarray",
                )

    def _run_ocr_api(self, ocr: Any, image_input: Any) -> Any:
        """Invoke the available PaddleOCR API after startup timing begins."""

        def run_ocr_method() -> Any:
            try:
                return ocr.ocr(image_input, cls=False)
            except TypeError:
                return ocr.ocr(image_input)

        # PaddleOCR 3.x pipeline API often exposes predict(...).
        # Some Windows/Paddle builds can fail at runtime inside the new
        # PIR/oneDNN executor. If the object also exposes the old ocr(...) API,
        # try that before giving up and forcing a Tesseract fallback.
        if hasattr(ocr, "predict"):
            predict_kwargs = self._predict_extra_kwargs()
            try:
                return ocr.predict(image_input, **predict_kwargs)
            except TypeError:
                try:
                    return ocr.predict(input=image_input, **predict_kwargs)
                except TypeError:
                    # Older/newer builds may reject text_det_limit_side_len at
                    # predict time. Retry without preset kwargs before falling
                    # back to the old ocr(...) API.
                    try:
                        return ocr.predict(image_input)
                    except TypeError:
                        return ocr.predict(input=image_input)
            except Exception as e:
                msg = str(e).lower()
                if hasattr(ocr, "ocr") and ("onednn" in msg or "mkldnn" in msg or "pir" in msg):
                    return run_ocr_method()
                raise

        # PaddleOCR 2.x common API.
        if hasattr(ocr, "ocr"):
            return run_ocr_method()

        raise RuntimeError("Unknown PaddleOCR object API: no predict() or ocr().")

    def _extract_texts(self, result: Any) -> List[str]:
        """
        Extract text strings from multiple PaddleOCR result formats.

        Known formats include:
        - old list format: [[[box], (text, score)], ...]
        - new result objects with .json / .res / .to_dict()
        - dicts with rec_texts / text / rec_text fields
        """
        texts: List[str] = []

        def add_text(value: Any) -> None:
            if not isinstance(value, str):
                return
            value = value.strip()
            if value and not self._looks_like_path_or_number(value):
                texts.append(value)

        def walk(obj: Any) -> None:
            if obj is None:
                return

            # Result object conversions used by PaddleOCR 3.x.
            for attr in ("json", "res"):
                try:
                    value = getattr(obj, attr)
                    if value is not None and value is not obj:
                        walk(value)
                        return
                except Exception:
                    pass

            try:
                to_dict = getattr(obj, "to_dict", None)
                if callable(to_dict):
                    walk(to_dict())
                    return
            except Exception:
                pass

            if isinstance(obj, dict):
                # Most useful explicit keys first.
                for key in ("rec_texts", "texts", "text", "rec_text", "label", "transcription"):
                    if key in obj:
                        value = obj[key]
                        if isinstance(value, list):
                            for item in value:
                                add_text(item)
                        else:
                            add_text(value)
                # Recurse into values to support nested page structures.
                for value in obj.values():
                    if isinstance(value, (dict, list, tuple)) or hasattr(value, "to_dict"):
                        walk(value)
                return

            if isinstance(obj, (list, tuple)):
                # Old PaddleOCR line format: [box, (text, score)]
                if len(obj) == 2 and isinstance(obj[1], (list, tuple)) and obj[1]:
                    if isinstance(obj[1][0], str):
                        add_text(obj[1][0])
                        return
                # Sometimes line is (text, score)
                if len(obj) >= 1 and isinstance(obj[0], str):
                    add_text(obj[0])
                    return
                for item in obj:
                    walk(item)
                return

            # Avoid blindly converting arbitrary objects to str; it often yields
            # object reprs instead of OCR text.

        walk(result)

        # Deduplicate while preserving order.
        seen = set()
        unique: List[str] = []
        for text in texts:
            key = text.strip()
            if key and key not in seen:
                seen.add(key)
                unique.append(key)
        return unique

    def _looks_like_path_or_number(self, text: str) -> bool:
        if len(text) > 260:
            return True
        if re.fullmatch(r"[\d.\-+eE]+", text):
            return True
        if "game_translator_paddle_" in text:
            return True
        return False

    def _postprocess(self, text: str) -> str:
        """Light cleanup for PaddleOCR game subtitles."""
        text = (text or "").replace("\r", " ").replace("\n", " ")
        text = re.sub(r"\s+", " ", text).strip()

        # Paddle часто видит курсивное off как отдельный кусок после точки.
        # Для переводчика важно получить нормальную английскую связку looks off.
        text = re.sub(r"\blooks\s*[.·]{1,3}\s*off\b", "looks off", text, flags=re.IGNORECASE)
        text = re.sub(r"\blooks\s*[.·]{1,3}\s*o\b", "looks off", text, flags=re.IGNORECASE)
        text = re.sub(r"\blookoff\b", "looks off", text, flags=re.IGNORECASE)

        # Частые склейки/разрывы Paddle на игровых субтитрах.
        text = re.sub(r"\btotell\b", "to tell", text, flags=re.IGNORECASE)
        text = re.sub(r"\bfromthis\b", "from this", text, flags=re.IGNORECASE)
        text = re.sub(r"\bisnit\s+it\b", "isn't it", text, flags=re.IGNORECASE)
        text = re.sub(r"\bmiddle\s+(Something's)\b", r"middle. \1", text)

        # Нормализуем повторные точки без уничтожения настоящего троеточия.
        text = re.sub(r"\.\.\s+", "... ", text)
        text = re.sub(r"\.\.([A-Za-z])", r"... \1", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text
