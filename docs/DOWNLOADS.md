# Windows packages: R04b

**Not published.** The checksums below describe the existing local R04b candidates, not files currently available from GitHub Releases. If an archive is rebuilt before publication, update the sizes and checksums before linking it.

Each block is a standalone package. Download all parts for **one** chosen block, not the other blocks. Keep the parts in one folder, open Command Prompt (`cmd.exe`) in that folder and run the corresponding command below. Verify each part with `certutil -hashfile "part filename" SHA256`, then compare the joined ZIP hash before extracting. Use an ASCII-only installation path; spaces are allowed, for example `D:\Games Translator`. Extract the entire ZIP and start `Games_translator.exe` from the extracted folder.

| Package | ZIP bytes | ZIP GiB | Parts | SHA256 (parts and joined ZIP) |
| --- | ---: | ---: | ---: | --- |
| Block 1 | 6,248,781,754 | 5.82 | 4 | [Block1.sha256](downloads/Block1.sha256) |
| Block 2 | 6,698,899,384 | 6.24 | 4 | [Block2.sha256](downloads/Block2.sha256) |
| Block 3 | 9,505,257,174 | 8.85 | 5 | [Block3.sha256](downloads/Block3.sha256) |

### Block 1

```bat
copy /b Games_translator-Block1-Windows-x64.zip.001+Games_translator-Block1-Windows-x64.zip.002+Games_translator-Block1-Windows-x64.zip.003+Games_translator-Block1-Windows-x64.zip.004 Games_translator-Block1-Windows-x64.zip
```

`certutil -hashfile "Games_translator-Block1-Windows-x64.zip" SHA256`

### Block 2

```bat
copy /b Games_translator-Block2-Windows-x64.zip.001+Games_translator-Block2-Windows-x64.zip.002+Games_translator-Block2-Windows-x64.zip.003+Games_translator-Block2-Windows-x64.zip.004 Games_translator-Block2-Windows-x64.zip
```

`certutil -hashfile "Games_translator-Block2-Windows-x64.zip" SHA256`

### Block 3

```bat
copy /b Games_translator-Block3-Windows-x64.zip.001+Games_translator-Block3-Windows-x64.zip.002+Games_translator-Block3-Windows-x64.zip.003+Games_translator-Block3-Windows-x64.zip.004+Games_translator-Block3-Windows-x64.zip.005 Games_translator-Block3-Windows-x64.zip
```

`certutil -hashfile "Games_translator-Block3-Windows-x64.zip" SHA256`
