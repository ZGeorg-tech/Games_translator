# Third-party licenses and corresponding source

Games_translator source is GPL-3.0-only; the copy of GPLv3 in this directory also
serves recipients of GPLv3 components. Separate packages, weights and libraries
retain their own terms. This notice does not impose additional restrictions on
the GPL-licensed code. For vendor copyrights and third-party notices see the
corresponding `*.dist-info`, source archive and runtime directories.

The shared source archive for this distribution is `GT-R04b-Third-Party-Source.zip`.
It belongs next to the binary downloads; it is not needed to run any block.
Do not distribute these binary packages without making the corresponding source
available to the same recipients. No written offer is made as a substitute.
The Games_translator source, lockfile, native launcher source and build
instructions are in the [Games_translator repository](https://github.com/ZGeorg-tech/Games_translator).

| Files / family | Source supplied or reference |
| --- | --- |
| Riverbank PyQt6 6.11.0, SIP 13.11.1 | Official PyPI sdists and wheel versions; the Windows DLL/PYD bytes match the published wheel RECORDs without Games_translator patches. |
| Qt 6.11.1 (including GPL Qt Quick 3D and LGPL libraries) | Official Qt 6.11.1 source archive including bundled dependency sources and notices. [Qt license terms by module](https://doc.qt.io/qt-6/licensing.html). |
| Qt Multimedia FFmpeg | FFmpeg 7.1.3 and zlib 1.3.1 sources. The actual FFmpeg configuration is recorded in `FFMPEG-BUILD.txt`; the DLL identifies itself as LGPL 2.1 or later. |
| crc32c 2.8 and Shapely's GEOS | crc32c 2.8 sdist; GEOS 3.13.1 (CAPI 1.19.2) source and Shapely's [wheel build project](https://github.com/shapely/shapely/tree/2.1.2). |
| JBIG-KIT | MSYS2 `mingw-w64-x86_64-jbigkit 2.1-5`, binary hash match; original source, patches and recipe in the MSYS2 source archive. |
| libiconv, libintl, libunistring, libidn2 and GCC runtime | Source releases and MSYS2 recipes for libiconv 1.17-4, gettext 0.22.5-2, libunistring 1.2-1, libidn2 2.3.7-2 and GCC 14.2.0-2. The [GCC Runtime Library Exception](https://gcc.gnu.org/onlinedocs/libstdc++/manual/license.html) is preserved for applicable files. |
| Bergamot (Blocks 1/2) | npm `@browsermt/bergamot-translator@0.4.9`, [gitHead 8cc5d04](https://github.com/browsermt/bergamot-translator/tree/8cc5d0495479c9ec56eafafd6bcd7fb5b929ca98), MPL-2.0. The git tree identifies submodule revisions and the npm package identifies Emscripten 3.1.8. |
| llama.cpp | [b10057](https://github.com/ggml-org/llama.cpp/releases/tag/b10057), MIT. The experimental patched `pcache1` runtime is not included. |
| Tesseract and English tessdata | [Tesseract 5.5.0](https://github.com/tesseract-ocr/tesseract/tree/5.5.0) and [tessdata_fast 4.1.0](https://github.com/tesseract-ocr/tessdata_fast/tree/4.1.0), Apache-2.0. English traineddata is byte-identical to the upstream 4.1.0 file. |

## Replace or modify LGPL libraries

There is no requirement to use the bundled copy of an LGPL library, and this
distribution does not prohibit reverse engineering to debug modifications to
such a library. In a copy of the extracted package, close Games_translator,
back up the files, and replace the relevant ABI-compatible Windows x64 DLLs at
their existing paths. Qt and its plugins are under
`resources/shared/packages/core/py313-20260925/Lib/site-packages/PyQt6/Qt6/`
(`bin`, `plugins` and matching QML modules). Preserve the Qt 6.11 ABI,
plugin layout and MSVC architecture. FFmpeg DLLs are in the same `bin` directory.
Tesseract's shared libraries are next to `tesseract.exe` in its runtime
directory. To rebuild bindings rather than replace DLLs, build the supplied
PyQt sdist with the matching Qt development environment and use the same
Python 3.13 x64 ABI. No re-signing by the author is required.

## Proprietary redistributables

The NVIDIA CUDA libraries `cudart64_13.dll`, `cublas64_13.dll` and
`cublasLt64_13.dll` are separately licensed redistributable components,
not GPL. The [CUDA EULA](https://docs.nvidia.com/cuda/eula/index.html) applies to these binaries,
including its distribution and use limitations. NVIDIA driver files are not
included. The VC runtime is likewise separately licensed under the applicable
[Microsoft Redistributable terms](https://learn.microsoft.com/en-us/visualstudio/releases/2022/redistribution).
