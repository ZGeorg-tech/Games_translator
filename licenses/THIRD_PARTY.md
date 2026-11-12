# Third-party components

This inventory is not a grant of rights. Source and Windows staging are local
release candidates until the outstanding redistribution requirements below are met.
Preserve the upstream license files in each redistributed runtime and package.

| Component | Evidence / action before redistribution |
| --- | --- |
| Python 3.13.5 | [PSF terms](https://docs.python.org/3/license.html); retain the runtime's LICENSE.txt and third-party notices. |
| PyQt6 6.11.0, Qt 6.11.1, SIP | [Riverbank licensing](https://www.riverbankcomputing.com/software/pyqt/intro) and [Qt licensing](https://doc.qt.io/qt-6/licensing.html). Free of charge is not a license. Confirm the application's GPL-compatible licensing and corresponding-source arrangements, or commercial rights for the exact build. Verify Qt module licenses and notices. |
| Installed Python packages | Review the exact package versions and their dist-info license files. This includes the native capture extension and its Rust dependencies, TensorFlow, ModelScope, Paddle, PyTorch, Transformers and transitive packages. |
| Tesseract 5.5.0 / English tessdata | [Engine](https://github.com/tesseract-ocr/tesseract) and [language data](https://github.com/tesseract-ocr/tessdata); verify exact traineddata provenance, distribution notices and bundled DLL licenses. |
| PaddleOCR and OCR models | [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR); the local PP-OCRv6 Small cards declare Apache-2.0. Verify provenance and terms of the exact v4 Mobile and v6 Medium weights separately. |
| Node 24.18.0 and Bergamot JS/WASM | Preserve Node's bundled LICENSE. [Bergamot](https://github.com/browsermt/bergamot-translator) uses MPL-2.0; identify the exact corresponding source of the shipped WASM. |
| Bergamot weights | [Mozilla model project](https://github.com/mozilla/firefox-translations-models); confirm attribution and license per shipped EN-RU, EN-UK, JA-EN, ZH-EN and ZH-Hant-EN artifact. Engine licensing does not cover the models. |
| Marian EN-RU | [Helsinki-NLP/opus-mt-en-ru](https://huggingface.co/Helsinki-NLP/opus-mt-en-ru) declares Apache-2.0. Retain exact revision attribution and Apache notices. |
| TranslateGemma and MiLMMT | [TranslateGemma](https://huggingface.co/google/translategemma-4b-it) and [MiLMMT v0.1](https://huggingface.co/xiaomi-research/MiLMMT-46-4B-v0.1) declare Gemma terms. The [Gemma agreement](https://ai.google.dev/gemma/terms) requires a copy of the applicable agreement, notices, marking modifications and passing through use restrictions. Confirm exact GGUF provenance and satisfy these conditions or leave the weights out. |
| CSANMT | The local [ModelScope model](https://modelscope.cn/models/iic/nlp_csanmt_translation_en2zh_base) card declares Apache-2.0. Retain attribution and notices. |
| FuguMT | [Model card](https://huggingface.co/staka/fugumt-en-ja) declares CC BY-SA 4.0. Include [license](https://creativecommons.org/licenses/by-sa/4.0/) and attribution, and identify any changes. |
| OpenCC | [OpenCC project](https://github.com/BYVoid/OpenCC). Preserve the OpenCC wheel license and authors files. |
| llama.cpp, Vulkan and CUDA | [llama.cpp](https://github.com/ggml-org/llama.cpp) MIT, including notices for dependencies; include exact b10057 sources/patch provenance for modified binaries. Verify [Vulkan loader](https://github.com/KhronosGroup/Vulkan-Loader) and DLL provenance. Compare each shipped NVIDIA DLL to the applicable [CUDA redistribution list and conditions](https://docs.nvidia.com/cuda/eula/index.html). Check MSVC redistributables separately. Do not bundle GPU drivers. |
| UI resources | Verify authorship and publication rights for the exact language flag PNGs and SVG control icons. Local asset ZIPs are not evidence of a redistribution license. |

If an asset's terms or provenance cannot be confirmed, keep it out of any
published package. Optional assets can be obtained directly from their owners
under their terms and installed at the existing ResourceRegistry paths;
this requires no download code or routing changes. This does not resolve the
application's own licensing or the licensing of core GUI/runtime dependencies.
