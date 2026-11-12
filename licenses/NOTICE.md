# Games_translator licensing and attribution

The author has selected **GPL-3.0-only** for the Games_translator application source; see the [complete license](../LICENSE). The upstream texts below apply only to their respective components. Models, runtimes and artwork are not relicensed under the application's GPL by inclusion or reference.

- [Author license and scope](AUTHOR-LICENSE-STATUS.md)
- [Release licenses and corresponding source](PUBLICATION-GATES.md)
- [Third-party component inventory](THIRD_PARTY.md)
- [R04b package license texts and notices](PACKAGE-NOTICES.md), including CPython 3.13.5, Node.js 24.18.0, PyQt6 6.11.0, Qt 6.11.1, SIP 13.11.1 and OpenCC 1.4.1.
- [Gemma notice](Gemma-NOTICE.txt) for the covered TranslateGemma and MiLMMT distributions. The notice does not substitute for the applicable Gemma agreement.

## Model attribution

| Family | Upstream attribution and terms | R04b evidence and limits |
| --- | --- | --- |
| Marian EN–RU | [Helsinki-NLP/opus-mt-en-ru](https://huggingface.co/Helsinki-NLP/opus-mt-en-ru), Apache-2.0 | Local revision `bb09c99d180016eac6819df3dae68edb1690fdee`; preserve attribution and Apache terms/notices. |
| CSANMT EN–ZH | [iic/nlp_csanmt_translation_en2zh_base](https://modelscope.cn/models/iic/nlp_csanmt_translation_en2zh_base), Apache-2.0 | Staged model card declares Apache-2.0. |
| FuguMT EN–JA | [staka/fugumt-en-ja](https://huggingface.co/staka/fugumt-en-ja), [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/legalcode.en) | Staged model card confirms the declaration. Confirm exact upstream revision and any modifications before distribution. |
| PP-OCRv6 Small | [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR), Apache-2.0 | Both staged Small model cards declare Apache-2.0; verify exact hashes and attribution. This does not cover v4 Mobile or v6 Medium automatically. |
| Bergamot | [Mozilla Firefox Translations model project](https://github.com/mozilla/firefox-translations-models) | Verify exact revisions, provenance and notices of all five staged directions separately from JS/WASM. |
| TranslateGemma 4B and MiLMMT-46-4B v0.1 | [Google TranslateGemma](https://huggingface.co/google/translategemma-4b-it), [Xiaomi Research MiLMMT](https://huggingface.co/xiaomi-research/MiLMMT-46-4B-v0.1), [Gemma terms](https://ai.google.dev/gemma/terms) | The local Q6_K GGUF files must be tied to their exact conversion sources and applicable terms. |
