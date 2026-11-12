# Проверка состава дистрибутива

Портативная сборка не требует установщика. `stage_release.py` собирает исходный
комплект и отдельный локальный Windows staging по явному списку. Он не публикует файлы.

Источник состава: `production_allowlist.json`. Не копировать рабочую папку целиком и не использовать
`*.py`/`resources/**` как неограниченный glob без проверки manifest. Root source files перечислены явно;
production packages и immutable resource IDs разрешены отдельно. Root user config не является template.

- `tools/` и `experiments/` полностью исключаются, включая этот validator.
- Исключаются тесты, benchmarks, fixtures, отчёты/review ZIP, developer logs, env/cache/temp, bytecode.
- `data/`, profiles/TM/history, API/DPAPI credentials и локальные installation settings не поставляются.
- `installation.json` генерируется из чистого portable template, не копируется из пользовательской установки.
- Ресурсы выбираются по установочным capabilities из Registry, shared resources включаются один раз.
  Наличие resource ID не требует установки всех блоков. HIP/unqualified variants не активируются этим policy.
- `translation.models`/`bergamot.wasm` — legacy namespace hints, не разрешение копировать legacy model tree.
  Фактическая JS/WASM closure уже находится в `bergamot.runtime`; `llama.runtime` — CUDA alias, не вторая копия.
- Rust/Python native extension находится в app package domain. Rust build tree/другие developer toolchains
  не включаются. Source import closure проверяется, dynamic workers перечислены Registry resource IDs.
- Библиотечные runtime code/data/license files внутри выбранного immutable resource сохраняются, но их
  tests/benchmarks/cache/bytecode отсекаются. Node используется как executable без npm и corepack.

Из корня проекта в x64 Native Tools Command Prompt (MSVC и Windows SDK):

```cmd
cl /nologo /std:c++17 /EHsc /W4 /WX /O2 /MT /Brepro /Fo:windows_launcher.obj tools\build\distribution\windows_launcher.cpp /link /SUBSYSTEM:WINDOWS /OUT:Games_translator.exe user32.lib
```

Загрузчик запускает штатный `python_runtime.py launcher.py` с корнем приложения
как рабочим каталогом. Версия Python в загрузчике должна совпадать с `app.python`
в Registry. Для сборки комплекта требуются отдельно подготовленные, закреплённые
ресурсы из Registry. Вариант `b10057-pcache1` не включён в кандидат: в его DLL
обнаружены пути рабочей копии. Это не меняет выбор runtime в рабочем приложении,
но сборку нельзя считать полной для конфигураций, требующих `pcache1`.

Сборка WGC для поставки: в x64 Developer PowerShell использовать Rust MSVC,
SDK CPython 3.13 x64 (переменная `PYO3_PYTHON`) и локальный Cargo cache для
зависимостей из `game_capture_core/Cargo.lock`. SDK не нужен для запуска.
Не переносить оригинальную dev-сборку с вшитыми личными путями.

```powershell
$root = (Get-Location).Path
$env:CARGO_TARGET_DIR = Join-Path $root 'tools/build/distribution/results/capture'
$env:CARGO_ENCODED_RUSTFLAGS = @("--remap-path-prefix=$root=/app", "--remap-path-prefix=$($root.Replace('\','/'))=/app", "--remap-path-prefix=$env:USERPROFILE=/build-user") -join [char]31
cargo build --offline --locked --release --manifest-path game_capture_core/Cargo.toml --target x86_64-pc-windows-msvc
```

В PowerShell:

```powershell
& resources/shared/runtimes/python/3.13.5/python.exe -I -B -X utf8 tools/build/distribution/stage_release.py stage tools/build/distribution/results/release --capture-extension tools/build/distribution/results/capture/x86_64-pc-windows-msvc/release/game_capture_core.dll
& resources/shared/runtimes/python/3.13.5/python.exe -I -B -X utf8 tools/build/distribution/stage_release.py archives tools/build/distribution/results/release
```

Путь для staging должен быть новым. Запускайте приложение для проверки в копии,
не добавляйте созданный `data/` в staging. Архивы разбиты на части меньше 2 GiB;
манифест и инструкция находятся рядом с архивами. Публикация допустима только
после проверки условий в `licenses/THIRD_PARTY.md`.

### Независимые комплекты

Передайте одинаковый `--block block1`, `--block block2` или `--block block3`
для фаз `stage`, `verify` и `archives`. Для каждого блока нужен свой новый
каталог вывода. `installation.json` задаёт один установленный блок. Рабочая
копия без этого поля по-прежнему показывает все блоки.

`--resource-input` позволяет повторно использовать проверенное дерево Windows
staging с чистым WGC; файлы копируются по путям Registry, а не связываются
с исходным деревом. Код приложения всегда берётся из текущей рабочей копии.
При сборке из рабочих ресурсов вместо этого передайте проверенное расширение
через `--capture-extension`.

Общий Python package domain остаётся целым: в нём зависимости общего Core,
OCR и локальных переводчиков. CSANMT использует отдельный domain только в Block3.
Для Block2 также нужны общие Bergamot EN→RU/UK и Tesseract как аварийный
резерв общего OCR-диспетчера (не как выбор в интерфейсе). Перечень файлов,
размеры, исключённые ресурсы, SHA256 и команды распаковки выдаются для каждого
комплекта. Общая базовая загрузка не требуется.

Автоматический pre-build gate, из корня проекта:

```powershell
& resources/shared/runtimes/python/3.13.5/python.exe -I -B -X utf8 python_runtime.py tools/build/distribution/check_distribution.py --self-test
# Кандидаты для Git по фактическим правилам .gitignore:
& resources/shared/runtimes/python/3.13.5/python.exe -I -B -X utf8 python_runtime.py tools/build/distribution/check_distribution.py --source-check
# Проверка JSON-массива относительных POSIX file paths:
& resources/shared/runtimes/python/3.13.5/python.exe -I -B -X utf8 python_runtime.py tools/build/distribution/check_distribution.py --manifest payload_manifest.json
# Или проверяет уже подготовленный staging, без записи/копирования:
& resources/shared/runtimes/python/3.13.5/python.exe -I -B -X utf8 python_runtime.py tools/build/distribution/check_distribution.py --staging path/to/staging
```

Неизвестные paths, traversal, reparse points и пользовательская installation metadata блокируют staging.
Полный binary/dependency installer не квалифицирован этим gate: его будущая сборка должна отдельно
проверить native closure, лицензии и запуск выбранных capabilities из staging без tools/.
Строковые ссылки Registry на qualification reports не являются runtime file reads; запрет CSANMT на
лабораторный site-packages path также не требует наличия tools. Production consumer tools не найден.
