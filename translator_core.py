import gc  # Сборщик мусора для жесткой очистки RAM/VRAM
from resource_registry import resource_path
from python_runtime import sentencepiece_model_argument
from data_paths import data_path
from transformers import MarianMTModel, MarianTokenizer


class TranslatorCore:
    def __init__(self):
        self.current_offline_pair = None
        self.offline_tokenizer = None
        self.offline_model = None

    def load_model(self):
        """
        Метод предзагрузки для интеграции с новым движком архитектуры.
        Инициализирует базовую модель при старте лаунчера/окна, чтобы избежать фризов.
        """
        import config
        # Берем целевой язык из текущего конфига, по умолчанию ставим 'uk'
        try:
            target_lang = config.CURRENT_LANG
        except Exception:
            target_lang = "uk"

        print(f"[ИИ ПРЕДЗАГРУЗКА] Фоновое развертывание базовой модели (en-{target_lang})...")
        self._init_offline_model(src_lang="en", target_lang=target_lang)

    def _init_offline_model(self, src_lang, target_lang):
        """ Инициализирует оффлайн модель MarianMT только при реальном вызове """
        # Корректировка кодов языков для MarianMT
        src = "en" if src_lang == "en" else src_lang
        tgt = "uk" if target_lang == "uk" else ("ru" if target_lang == "ru" else target_lang)

        pair_key = f"{src}-{tgt}"
        if self.current_offline_pair == pair_key and self.offline_model is not None:
            return True

        # --- КРИТИЧЕСКИЙ ОТРЕЗОК: Сброс старой модели перед загрузкой новой ---
        print(f"[ИИ ОФФЛАЙН] Смена языковой пары. Очистка старой модели {self.current_offline_pair}...")
        self.offline_model = None
        self.offline_tokenizer = None
        self.current_offline_pair = None

        try:
            import torch
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except ImportError:
            pass
        gc.collect()
        # ---------------------------------------------------------------------

        if pair_key == "en-ru":
            model_path = resource_path("marian.enru")
            model_source = str(model_path)
            tokenizer_source = sentencepiece_model_argument(model_path)
            load_kwargs = {"local_files_only": True}
        else:
            model_source = f"Helsinki-NLP/opus-mt-{pair_key}"
            tokenizer_source = model_source
            load_kwargs = {
                "local_files_only": True,
                "cache_dir": str(data_path("hf.home", "hub")),
            }
        print(f"[ИИ ОФФЛАЙН] Загрузка локальной модели для {pair_key}...")

        try:
            self.offline_tokenizer = MarianTokenizer.from_pretrained(tokenizer_source, **load_kwargs)
            self.offline_model = MarianMTModel.from_pretrained(model_source, **load_kwargs)
            self.current_offline_pair = pair_key
            print(f"[ИИ ОФФЛАЙН] Локальная модель {pair_key} успешно загружена!")
            return True
        except Exception as e:
            print(f"[ОШИБКА ОФФЛАЙН МОДЕЛИ]: Не удалось загрузить {model_source}. Ошибка: {e}")
            return False

    def translate_text(self, text, src_lang="en", target_lang="uk", use_online=True):
        """
        Основной метод перевода. Поддерживает динамическое переключение Онлайн/Офлайн.
        """
        if not text or not text.strip():
            return ""

        if use_online:
            from online_translation import ONLINE
            return ONLINE.translate(text, src_lang, target_lang).text

        # --- ОФФЛАЙН РЕЖИМ (MarianMT) ---
        if not self._init_offline_model(src_lang, target_lang):
            return "[Ошибка: Модель перевода недоступна]"

        try:
            inputs = self.offline_tokenizer(text, return_tensors="pt", padding=True)
            translated_tokens = self.offline_model.generate(**inputs)
            result = self.offline_tokenizer.batch_decode(translated_tokens, skip_special_tokens=True)
            return result[0] if result else ""
        except Exception as e:
            print(f"[ИИ ОФФЛАЙН ОШИБКА] Ошибка генерации текста: {e}")
            return ""


_instance = TranslatorCore()

# Сохраняем совместимость с лаунчером (прямые функции)
translate_text = _instance.translate_text
load_model = _instance.load_model

# ИСПРАВЛЕНО: Создаем алиас для оверлея, чтобы работал импорт "from translator_core import translator_core"
translator_core = _instance
