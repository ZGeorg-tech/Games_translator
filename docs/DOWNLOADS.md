# Windows packages: R04b

[Windows downloads and corresponding source](https://github.com/ZGeorg-tech/Games_translator/releases/tag/r04b). The [third-party source archive](https://github.com/ZGeorg-tech/Games_translator/releases/download/r04b/GT-R04b-Third-Party-Source.zip) has SHA256 `0ece42e51fb771a75bb5be33b7b53b8a672e9bf9406cffc284cf49bb839baf51`.

Each block is a standalone package. Download all parts for **one** chosen block, not the other blocks. Keep the parts in one folder, open Command Prompt (`cmd.exe`) in that folder and run the corresponding command below. Verify each part with `certutil -hashfile "part filename" SHA256`, then compare the joined ZIP hash before extracting. Use an ASCII-only installation path; spaces are allowed, for example `D:\Games Translator`. Extract the entire ZIP and start `Games_translator.exe` from the extracted folder.

| Package | ZIP bytes | ZIP GiB | Parts | SHA256 (parts and joined ZIP) |
| --- | ---: | ---: | ---: | --- |
| Block 1 | 6,249,135,688 | 5.82 | 4 | [Block1.sha256](downloads/Block1.sha256) |
| Block 2 | 6,699,253,183 | 6.24 | 4 | [Block2.sha256](downloads/Block2.sha256) |
| Block 3 | 9,505,646,007 | 8.85 | 5 | [Block3.sha256](downloads/Block3.sha256) |

### Block 1

[001](https://github.com/ZGeorg-tech/Games_translator/releases/download/r04b/Games_translator-Block1-Windows-x64.zip.001) · [002](https://github.com/ZGeorg-tech/Games_translator/releases/download/r04b/Games_translator-Block1-Windows-x64.zip.002) · [003](https://github.com/ZGeorg-tech/Games_translator/releases/download/r04b/Games_translator-Block1-Windows-x64.zip.003) · [004](https://github.com/ZGeorg-tech/Games_translator/releases/download/r04b/Games_translator-Block1-Windows-x64.zip.004) · [SHA256](https://github.com/ZGeorg-tech/Games_translator/releases/download/r04b/Block1.sha256)

```bat
copy /b Games_translator-Block1-Windows-x64.zip.001+Games_translator-Block1-Windows-x64.zip.002+Games_translator-Block1-Windows-x64.zip.003+Games_translator-Block1-Windows-x64.zip.004 Games_translator-Block1-Windows-x64.zip
```

`certutil -hashfile "Games_translator-Block1-Windows-x64.zip" SHA256`

### Block 2

[001](https://github.com/ZGeorg-tech/Games_translator/releases/download/r04b/Games_translator-Block2-Windows-x64.zip.001) · [002](https://github.com/ZGeorg-tech/Games_translator/releases/download/r04b/Games_translator-Block2-Windows-x64.zip.002) · [003](https://github.com/ZGeorg-tech/Games_translator/releases/download/r04b/Games_translator-Block2-Windows-x64.zip.003) · [004](https://github.com/ZGeorg-tech/Games_translator/releases/download/r04b/Games_translator-Block2-Windows-x64.zip.004) · [SHA256](https://github.com/ZGeorg-tech/Games_translator/releases/download/r04b/Block2.sha256)

```bat
copy /b Games_translator-Block2-Windows-x64.zip.001+Games_translator-Block2-Windows-x64.zip.002+Games_translator-Block2-Windows-x64.zip.003+Games_translator-Block2-Windows-x64.zip.004 Games_translator-Block2-Windows-x64.zip
```

`certutil -hashfile "Games_translator-Block2-Windows-x64.zip" SHA256`

### Block 3

[001](https://github.com/ZGeorg-tech/Games_translator/releases/download/r04b/Games_translator-Block3-Windows-x64.zip.001) · [002](https://github.com/ZGeorg-tech/Games_translator/releases/download/r04b/Games_translator-Block3-Windows-x64.zip.002) · [003](https://github.com/ZGeorg-tech/Games_translator/releases/download/r04b/Games_translator-Block3-Windows-x64.zip.003) · [004](https://github.com/ZGeorg-tech/Games_translator/releases/download/r04b/Games_translator-Block3-Windows-x64.zip.004) · [005](https://github.com/ZGeorg-tech/Games_translator/releases/download/r04b/Games_translator-Block3-Windows-x64.zip.005) · [SHA256](https://github.com/ZGeorg-tech/Games_translator/releases/download/r04b/Block3.sha256)

```bat
copy /b Games_translator-Block3-Windows-x64.zip.001+Games_translator-Block3-Windows-x64.zip.002+Games_translator-Block3-Windows-x64.zip.003+Games_translator-Block3-Windows-x64.zip.004+Games_translator-Block3-Windows-x64.zip.005 Games_translator-Block3-Windows-x64.zip
```

`certutil -hashfile "Games_translator-Block3-Windows-x64.zip" SHA256`
