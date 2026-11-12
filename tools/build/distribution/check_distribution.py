"""Read-only allowlist gate for a future payload manifest/staging directory."""
from __future__ import annotations
import argparse
import ast
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
from resource_registry import RESOURCES
from language_blocks import ALL_BLOCKS
POLICY=json.loads(Path(__file__).with_name("production_allowlist.json").read_text(encoding="utf-8"))
HELP_FILES = {f"assets/help/{lang}.txt" for lang in ("ru", "uk", "en", "zh", "zh_hant", "ja")}


def installation_template(block=None):
    metadata = dict(POLICY["installation_metadata_template"])
    if block is not None:
        if block not in ALL_BLOCKS:
            raise ValueError(f"Invalid package block: {block}")
        metadata["installed_blocks"] = [block]
    return metadata
UI_FILES = HELP_FILES | {f"assets/icons/{name}" for name in (
    "flag_en.png", "flag_ja.png", "flag_ru.png", "flag_ua.png", "flag_zh.png",
    "ui-check.svg", "ui-chevron-up.svg", "ui-chevron-down.svg")}
PUBLIC_NOTICES = {"licenses/THIRD_PARTY.md", "licenses/Gemma-NOTICE.txt"}
# Exact upstream CA bundles and immutable web assets, not user data.
UPSTREAM_EXCEPTIONS = {
    f'{RESOURCES["app.environment"].relative_path}/Lib/site-packages/certifi/cacert.pem',
    f'{RESOURCES["csanmt.runtime"].relative_path}/Lib/site-packages/certifi/cacert.pem',
    f'{RESOURCES["csanmt.runtime"].relative_path}/Lib/site-packages/grpc/_cython/_credentials/roots.pem',
    f'{RESOURCES["csanmt.runtime"].relative_path}/Lib/site-packages/tensorboard/webfiles.zip',
}
TF_API = f'{RESOURCES["csanmt.runtime"].relative_path}/Lib/site-packages/tensorflow/_api/'


def allowed(relative):
    if not isinstance(relative,str) or "\\" in relative or ":" in relative:
        return False
    path=PurePosixPath(relative)
    if path.is_absolute() or not path.parts or any(p in {".",".."} for p in path.parts):
        return False
    parts=[p.casefold() for p in path.parts]
    if parts[0] in POLICY["forbidden_roots"] or any(
            p in POLICY["forbidden_components"] and not (p == "test" and relative.startswith(TF_API))
            for p in parts):
        return False
    if (path.suffix.casefold() in POLICY["forbidden_suffixes"] and relative not in UPSTREAM_EXCEPTIONS
            or path.name in {"direct_url.json", ".gitignore", "pyvenv.cfg"} and parts[0] == "resources"):
        return False
    # The explicit source files are allowed; tools/test/review artifacts are never collected by a root glob.
    if relative in POLICY["root_files"]:
        return True
    if relative in UI_FILES or relative in PUBLIC_NOTICES:
        return True
    if relative == RESOURCES["bergamot.node"].relative_path.rsplit("/", 1)[0] + "/LICENSE":
        return True
    if parts[0] in POLICY["source_roots"]:
        return path.suffix.lower() in {".py",".json",".yaml",".yml",".txt",".md"}
    for resource_id in POLICY["resource_ids"]:
        if resource_id == "app.assets":
            continue
        prefix=RESOURCES[resource_id].relative_path
        if relative==prefix or relative.startswith(prefix+"/"):
            return True
    return False


def source_closure():
    files=[ROOT/name for name in POLICY["root_files"] if name.endswith(".py")]
    files += [p for folder in POLICY["source_roots"] for p in (ROOT/folder).rglob("*.py") if "__pycache__" not in p.parts]
    missing=[]
    for file in files:
        tree=ast.parse(file.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            modules=[]
            if isinstance(node,ast.Import):
                modules=[alias.name for alias in node.names]
            elif isinstance(node,ast.ImportFrom) and node.module and not node.level:
                modules=[node.module]
            for module in modules:
                if module=="tools" or module.startswith("tools."):
                    missing.append({"file":file.relative_to(ROOT).as_posix(),"module":module,"reason":"production import of tools"})
                local=ROOT/(module.replace(".","/")+".py")
                if local.is_file() and not allowed(local.relative_to(ROOT).as_posix()):
                    missing.append({"file":file.relative_to(ROOT).as_posix(),"module":module,"reason":"local production import outside allowlist"})
    return missing


def check(paths):
    return [{"path":p,"reason":"outside explicit production allowlist / forbidden developer or user data"} for p in paths if not allowed(p)]


def self_test():
    positives=[*POLICY["root_files"],"resources/shared/runtimes/python/3.13.5/python.exe",
        "resources/shared/packages/core/py313-20260925/Lib/site-packages/game_capture_core.pyd",
        "resources/block3/models/fugu-en-ja/fugumt_en_ja/config.json"]
    negatives=['tools/translation/online/targeted_test.py',"tools/experiments/indra_112/models/det.onnx",
        "data/user/profiles/game/profile.json","data/user/tm/game/translation_memory.sqlite",
        "data/user/settings/api_credentials.dpapi","EXP_INDRA02_accuracy_review.zip","M35b_project_cleanup_review.zip",
        "debug_dynamic_capture.png","test_temp/image.png","assets/icons/tests/fixture.png",
        "resources/shared/packages/core/py313-20260925/Lib/site-packages/foo/tests/test.py",
        "resources/shared/packages/core/py313-20260925/Lib/site-packages/foo/__pycache__/foo.pyc",
        "../launcher.py","D:/secret.txt",'tools/translation/online/targeted_test.py']
    positives.extend(sorted(HELP_FILES))
    positives.append(TF_API + "v2/__internal__/test/__init__.py")
    assert all(allowed(p) for p in positives)
    assert all((ROOT / name).read_text(encoding="utf-8").strip() for name in HELP_FILES)
    assert not any(allowed(p) for p in negatives)
    closure=source_closure()
    assert not closure,closure
    return {"passed":True,"positive_cases":len(positives),"negative_cases":len(negatives),"production_source_closure_findings":closure,
        "installer_created":False,"resource_contents_scanned":False,"modularity":"resource_ids must be selected explicitly for installed capabilities; aliases do not duplicate resources"}


def source_check():
    """Evaluate the real ignore rules without creating a repository in the workspace."""
    from data_paths import DATA
    temp = DATA.temp("source-publication-check")
    temp.mkdir(parents=True, exist_ok=True)
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    with tempfile.TemporaryDirectory(dir=temp) as directory:
        subprocess.run(["git", "init", "--bare", "--quiet", "--template=", directory], env=env, check=True, timeout=15)
        command = ["git", "--git-dir="+directory, "--work-tree="+str(ROOT), "-c", "core.excludesFile="+os.devnull]
        listing = subprocess.check_output(command + ["ls-files", "--others", "--exclude-standard", "-z"],
                                          cwd=ROOT, env=env, timeout=30).decode("utf-8")
        paths = listing.strip("\0").split("\0")
        private_examples = (
            "data/user/profiles/example/profile.json", "data/user/tm/example/tm.sqlite",
            "data/user/settings/online-secrets/example.dpapi", "data/user/history/example/history.jsonl",
            "tools/translation/block2/cache/home/.modelscope/credentials/session",
            "data/app/logs/sessions/example/events.jsonl", "config.json", "installation.json", ".env.local",
            "AGENTS.md", "tools/regression/results/test_temp/private/profile.json",
            "resources/shared/models/example.gguf", "tools/build/vulkan_pcache/native-test/probe.exe",
            "logs(example).zip", "R01_release_cleanup_logging_review.zip",
        )
        ignored = subprocess.run(command + ["check-ignore", "--no-index", "-z", "--stdin"],
                                 input=("\0".join(private_examples)+"\0").encode(), capture_output=True,
                                 cwd=ROOT, env=env, timeout=15, check=True).stdout.decode("utf-8").split("\0")
    findings = [{"path": name, "reason": "private example not ignored"} for name in private_examples if name not in ignored]
    root_files = (set(POLICY["root_files"]) - {"installation.json", "Games_translator.exe"}) | {
        ".gitignore", "SOURCE_DISTRIBUTION.md"}
    text_suffixes = {".py", ".rs", ".md", ".txt", ".toml", ".lock", ".cmd", ".bat", ".ps1", ".json",
                     ".jsonl", ".hpp", ".cpp", ".patch", ".comp", ".html", ".yml", ".yaml"}
    developer_path = re.compile(r"(?i)[A-Z]:[\\/](?:Users|WORK|Program Files)[\\/]")
    credentials = re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
                             r"\b(?:AIza[\w-]{35}|sk-[\w-]{24,}|gh[pousr]_[A-Za-z0-9]{36,}|AKIA[A-Z0-9]{16})\b")
    ocr_corpus = "tools/translation/block2/corpus/ocr/"
    corpus_manifest = ROOT / ocr_corpus / "manifest.jsonl"
    synthetic_images = {
        ocr_corpus + row["image_path"] for row in
        map(json.loads, corpus_manifest.read_text(encoding="utf-8").splitlines())
        if row.get("synthetic") is True
    } if corpus_manifest.is_file() else set()
    for name in paths:
        path = PurePosixPath(name)
        root = path.parts[0] if path.parts else ""
        suffix = path.suffix.lower()
        approved_image = suffix == ".png" and (name.startswith("assets/icons/") or name in synthetic_images)
        control_icon = name in {"assets/icons/ui-chevron-up.svg", "assets/icons/ui-chevron-down.svg", "assets/icons/ui-check.svg"}
        if not (name in root_files or name == "run_launcher.bat" or name in HELP_FILES
                or name in PUBLIC_NOTICES or approved_image or control_icon or (
                root in {*POLICY["source_roots"], "tools", "game_capture_core"}
                and (suffix in text_suffixes or path.name == ".gitignore"))):
            findings.append({"path": name, "reason": "outside source publication classes"})
            continue
        file = ROOT / name
        stat = file.lstat()
        if stat.st_file_attributes & 0x400 or stat.st_size > 2 * 1024 * 1024:
            findings.append({"path": name, "reason": "reparse point or oversized source item"})
            continue
        if approved_image:
            continue
        body = file.read_text(encoding="utf-8-sig")
        if developer_path.search(body):
            findings.append({"path": name, "reason": "machine-specific absolute path"})
        if credentials.search(body):
            findings.append({"path": name, "reason": "credential-shaped literal"})
    missing = sorted((root_files | HELP_FILES) - set(paths))
    return {"passed": not findings and not missing, "source_files": len(paths), "ignored_private_examples": len(private_examples),
            "missing_public_sources": missing, "findings": findings, "secret_scan_scope": "path policy plus credential-shaped literal heuristics"}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--self-test",action="store_true")
    parser.add_argument("--source-check", action="store_true")
    parser.add_argument("--manifest",type=Path)
    parser.add_argument("--staging",type=Path)
    args=parser.parse_args()
    if args.self_test:
        print(json.dumps(self_test(),ensure_ascii=False,indent=2))
        return 0
    if args.source_check:
        result = source_check()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return int(not result["passed"])
    paths=[]
    if args.manifest:
        paths=json.loads(args.manifest.read_text(encoding="utf-8"))
    if args.staging:
        staging=args.staging.resolve(strict=True)
        for folder,dirs,files in os.walk(staging,followlinks=False):
            for name in dirs+files:
                path=Path(folder)/name
                if path.lstat().st_file_attributes & 0x400:
                    raise ValueError(f"Payload reparse point forbidden: {path}")
            paths.extend((Path(folder)/name).relative_to(staging).as_posix() for name in files)
        metadata=staging/"installation.json"
        if metadata.exists():
            contents = json.loads(metadata.read_text(encoding="utf-8-sig"))
            if contents not in [installation_template(block) for block in (None, *ALL_BLOCKS)]:
                raise ValueError("Payload installation metadata must be a clean portable template, not a user's configuration")
    if not args.manifest and not args.staging:
        parser.error("Use --manifest, --staging or --self-test; no automatic copy/build")
    findings=check(paths)+source_closure()
    print(json.dumps({"passed":not findings,"checked":len(paths),"findings":findings},ensure_ascii=False,indent=2))
    return int(bool(findings))


if __name__=="__main__":
    raise SystemExit(main())
