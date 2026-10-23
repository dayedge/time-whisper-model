"""Qualify local exports, build model frameworks and stage a release. Never push or publish."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import re
import shutil
import stat
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parent.parent
VARIANTS = {"all": ("TimeWhisperModelAll", ["en", "pl"]), "en": ("TimeWhisperModelEnglish", ["en"])}
RUNTIME = ["TimeWhisperTagger.mlpackage", "tokenizer.json", "tokenizer_config.json", "labels.json"]


def run(*command, cwd=None, env=None):
    subprocess.run([str(x) for x in command], cwd=cwd, env=env, check=True)


def output(*command, cwd=None):
    return subprocess.check_output([str(x) for x in command], cwd=cwd, text=True).strip()


def version(value):
    if not re.fullmatch(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?", value):
        raise argparse.ArgumentTypeError("use a semantic version without a v prefix")
    return value


def hashes(folder):
    return {p.relative_to(folder).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(folder.rglob("*")) if p.is_file()}


def archive(folder, destination):
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zipped:
        for p in sorted(folder.rglob("*")):
            if p.is_file() or p.is_symlink():
                info = zipfile.ZipInfo(p.relative_to(folder.parent).as_posix(), (2026, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = p.lstat().st_mode << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                zipped.writestr(info, str(p.readlink()).encode() if p.is_symlink() else p.read_bytes())


def validate(folder, languages):
    for name in [*RUNTIME, "reference.json"]:
        if not (folder / name).exists():
            raise ValueError(f"Missing {name}; use the current time-whisper exporter")
    if any(p.is_symlink() for p in folder.rglob("*")):
        raise ValueError("Export inputs must not contain symlinks")
    export_path = folder / "export.json"
    export = json.loads(export_path.read_text()) if export_path.exists() else None
    labels = json.loads((folder / "labels.json").read_text())
    if export is not None and export.get("base") != labels.get("base"):
        raise ValueError("Export and labels name different base models")
    if export is not None and (export.get("languages") != languages or export.get("max_pieces") != 128):
        raise ValueError("Export languages or maximum sequence length do not match this variant")
    if labels.get("intents") != ["task", "event", "none"] or labels.get("urgencies") != ["none", "L", "M", "H"]:
        raise ValueError("Unexpected intent or urgency contract")
    if not labels.get("labels") or len(labels["labels"]) != len(set(labels["labels"])):
        raise ValueError("Missing or duplicate token labels")
    references = json.loads((folder / "reference.json").read_text())
    if not references:
        raise ValueError("Missing inference references")
    for case in references:
        if not 0 < len(case["input_ids"]) <= 128 or len(case["labels"]) != len(case["input_ids"]):
            raise ValueError("Reference sequence shape mismatch")
        if any(len(row) != len(labels["labels"]) for row in case["labels"]) or len(case["intent"]) != 3 or len(case["urgency"]) != 4:
            raise ValueError("Reference output shape mismatch")
    json.loads((folder / "tokenizer.json").read_text())
    return export, labels


def source_state(library):
    paths = subprocess.check_output(["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=library)
    digest = hashlib.sha256()
    for name in sorted(set(paths.split(b"\0")) - {b""}):
        path = library / os.fsdecode(name)
        digest.update(name + b"\0")
        if path.is_symlink():
            digest.update(b"symlink:" + os.fsencode(path.readlink()))
        elif path.is_file():
            digest.update(str(path.stat().st_mode & 0o111).encode() + b":" + hashlib.sha256(path.read_bytes()).digest())
        else:
            digest.update(b"missing")
    return {"commit": output("git", "rev-parse", "HEAD", cwd=library),
            "clean": not output("git", "status", "--porcelain", "--untracked-files=all", cwd=library),
            "sha256": digest.hexdigest()}


def present_variants(inputs):
    selected = [variant for variant in VARIANTS if (inputs / variant).is_dir()]
    if not selected:
        raise ValueError("No qualified variant folders found")
    return selected


def package_manifest(binaries):
    products = ",\n".join(f'        .library(name: "{module}", targets: ["{module}"])' for module, _ in binaries)
    targets = ",\n".join("        " + target for _, target in binaries)
    return f'''// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "TimeWhisperModel",
    platforms: [.macOS("15.0")],
    products: [
{products}
    ],
    targets: [
{targets}
    ]
)
'''


def qualify(args):
    selected = [variant for variant in VARIANTS if getattr(args, variant, None) is not None]
    if not selected:
        raise ValueError("Supply at least one exported folder: --all PATH and/or --en PATH")
    library = args.library.resolve()
    library_version = args.library_version
    if library_version is None:
        match = re.search(r'^version = "([^"]+)"', (library / "build.gradle.kts").read_text(), re.M)
        if not match:
            raise ValueError("Cannot infer library version; pass --library-version")
        library_version = version(match[1])
    release = args.version or library_version
    state = source_state(library)
    tag = subprocess.run(["git", "rev-parse", "--verify", "--quiet", library_version + "^{commit}"],
                         cwd=library, text=True, capture_output=True)
    release_ready = state["clean"] and tag.returncode == 0 and tag.stdout.strip() == state["commit"]
    if args.training_commit is not None:
        if not re.fullmatch(r"[0-9a-f]{40}", args.training_commit):
            raise ValueError("training-commit must be a full Git commit SHA")
        run("git", "cat-file", "-e", args.training_commit + "^{commit}", cwd=library)
    folders = {variant: getattr(args, variant).resolve() for variant in selected}
    validated = {variant: validate(folder, VARIANTS[variant][1]) for variant, folder in folders.items()}
    print(f"Qualifying {', '.join(selected)} as model {release} against {library} ({library_version})", flush=True)
    if not release_ready:
        print("Local qualification: this library checkout is not a clean version tag. ZIPs cannot be uploaded for release yet.", flush=True)
    (ROOT / "build").mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="qualification-", dir=ROOT / "build") as temporary:
        for variant, folder in folders.items():
            _, languages = VARIANTS[variant]
            export, labels = validated[variant]
            before = hashes(folder)
            run("make", "check", f"MODEL={folder}", "CORPORA=" + " ".join(languages), "SPELLING=1", cwd=library,
                env={**os.environ, "TIME_WHISPER_TAGGER": str(folder)})
            if before != hashes(folder) or source_state(library) != state:
                raise ValueError("Inputs changed during qualification")
            stage = Path(temporary) / variant
            stage.mkdir()
            shutil.copytree(folder, stage / "export")
            if hashes(stage / "export") != before:
                raise ValueError("Model changed while copying qualification inputs")
            metadata = {"version": release, "variant": variant, "languages": languages,
                        "library_version": library_version, "library_source_commit": state["commit"],
                        "library_source_sha256": state["sha256"], "library_source_clean": state["clean"],
                        "release_ready": release_ready, "training_commit": args.training_commit, "export": export,
                        "labels": labels["labels"], "intents": labels["intents"], "urgencies": labels["urgencies"],
                        "files": before, "system_spelling": True, "qualification": "make check passed"}
            (stage / "metadata.json").write_text(json.dumps(metadata, indent=2) + "\n")
        inputs = ROOT / "build/inputs"
        inputs.mkdir(parents=True, exist_ok=True)
        for variant in selected:
            destination = inputs / f"model-{variant}-{release}.zip"
            archive(Path(temporary) / variant, destination)
            print(f"{destination}\nSHA256: {hashlib.sha256(destination.read_bytes()).hexdigest()}")
        # Selection is explicit: do not accidentally upload an older English ZIP with an All-only release.
        for variant in VARIANTS.keys() - set(selected):
            (inputs / f"model-{variant}-{release}.zip").unlink(missing_ok=True)
    return release


def unpack(path, digest, destination):
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise ValueError(f"Input checksum mismatch: {path}")
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    with zipfile.ZipFile(path) as zipped:
        seen = set()
        for item in zipped.infolist():
            target = destination / item.filename
            if (not target.resolve().is_relative_to(destination.resolve()) or "\\" in item.filename
                    or item.filename in seen or stat.S_ISLNK(item.external_attr >> 16)):
                raise ValueError("Unsafe or duplicate archive entry")
            seen.add(item.filename)
        zipped.extractall(destination)
    return destination


def build_framework(module, folder, release_version):
    framework = ROOT / "build/frameworks" / f"{module}.framework"
    if framework.exists():
        shutil.rmtree(framework)
    current = framework / "Versions/A"
    resources = current / "Resources"
    modules = current / "Modules" / f"{module}.swiftmodule"
    resources.mkdir(parents=True)
    modules.mkdir(parents=True)
    (framework / "Versions/Current").symlink_to("A")
    for entry in (module, "Resources", "Modules"):
        (framework / entry).symlink_to(f"Versions/Current/{entry}")
    model = resources / "TimeWhisperTagger"
    model.mkdir()
    for name in RUNTIME:
        source, target = folder / name, model / name
        if source.is_dir():
            shutil.copytree(source, target)
        else:
            shutil.copy2(source, target)
    for name in ("LICENSE", "MODEL_LICENSE.txt", "THIRD_PARTY_NOTICES.txt"):
        shutil.copy2(ROOT / name, resources / name)
    with (resources / "Info.plist").open("wb") as stream:
        plistlib.dump({"CFBundleIdentifier": "org.github.dayedge." + module.lower(), "CFBundleName": module,
                      "CFBundleExecutable": module, "CFBundlePackageType": "FMWK",
                      "CFBundleVersion": release_version.split("-")[0], "CFBundleShortVersionString": release_version.split("-")[0],
                      "MinimumOSVersion": "15.0"}, stream)
    source = ROOT / "build" / f"{module}.swift"
    source.write_text((ROOT / "Sources/Model.swift").read_text().replace("MODEL_ACCESSOR", module.removeprefix("TimeWhisperModel") + "Model"))
    sdk = output("xcrun", "--sdk", "macosx", "--show-sdk-path")
    binaries = []
    for architecture in ("arm64", "x86_64"):
        binary = ROOT / "build" / f"{module}-{architecture}"
        run("xcrun", "swiftc", source, "-sdk", sdk, "-target", f"{architecture}-apple-macos15.0",
            "-swift-version", "5", "-O", "-emit-library", "-emit-module", "-enable-library-evolution",
            "-module-name", module, "-emit-module-path", modules / f"{architecture}-apple-macos.swiftmodule",
            "-emit-module-interface-path", modules / f"{architecture}-apple-macos.swiftinterface",
            "-Xlinker", "-install_name", "-Xlinker", f"@rpath/{module}.framework/Versions/A/{module}", "-o", binary)
        binaries.append(binary)
    run("xcrun", "lipo", "-create", *binaries, "-output", current / module)
    run("codesign", "--force", "--sign", "-", framework)
    destination = ROOT / "build/artifacts" / f"{module}.xcframework"
    if destination.exists():
        shutil.rmtree(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    run("xcodebuild", "-create-xcframework", "-framework", framework, "-output", destination)
    return destination


def build(args):
    inputs = args.inputs.resolve()
    selected = present_variants(inputs)
    validated = []
    for variant in selected:
        module, languages = VARIANTS[variant]
        bundle = inputs / variant
        meta = json.loads((bundle / "metadata.json").read_text())
        folder = bundle / "export"
        export, labels = validate(folder, languages)
        if (meta.get("version") != args.version or meta.get("variant") != variant or meta.get("languages") != languages
                or meta.get("files") != hashes(folder) or meta.get("export") != export
                or meta.get("labels") != labels["labels"] or meta.get("intents") != labels["intents"]
                or meta.get("urgencies") != labels["urgencies"] or meta.get("system_spelling") is not True
                or meta.get("qualification") != "make check passed"
                or meta.get("library_version") != args.library_version):
            raise ValueError("Bundle metadata, qualification or hashes do not match")
        validated.append((variant, module, folder))
    dist = ROOT / "build/distributions"
    local = ROOT / "build/package"
    for directory in (dist, local):
        if directory.exists():
            shutil.rmtree(directory)
        directory.mkdir(parents=True)
    targets, local_targets = [], []
    for variant, module, folder in validated:
        framework = build_framework(module, folder, args.version)
        destination = dist / f"{module}-{args.version}.xcframework.zip"
        archive(framework, destination)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        destination.with_suffix(".zip.sha256").write_text(f"{digest}  {destination.name}\n")
        shutil.copy2(inputs / variant / "metadata.json", dist / f"{variant}-metadata.json")
        url = f"https://github.com/dayedge/time-whisper-model/releases/download/{args.version}/{destination.name}"
        targets.append((module, f'.binaryTarget(name: "{module}", url: "{url}", checksum: "{digest}")'))
        shutil.copytree(framework, local / framework.name, symlinks=True)
        local_targets.append((module, f'.binaryTarget(name: "{module}", path: "{framework.name}")'))
    (dist / "Package.swift").write_text(package_manifest(targets))
    (local / "Package.swift").write_text(package_manifest(local_targets))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    qualify_parser = commands.add_parser("qualify")
    qualify_parser.add_argument("--version", type=version, help="Model version; defaults to the library version")
    qualify_parser.add_argument("--library-version", type=version, help="Defaults to the library checkout\'s Gradle version")
    qualify_parser.add_argument("--library", type=Path, default=ROOT.parent / "time-whisper", help="Library checkout; defaults to ../time-whisper")
    qualify_parser.add_argument("--training-commit", help="Known training source commit; otherwise recorded as unknown")
    qualify_parser.add_argument("--all", type=Path, help="All-language exported model folder")
    qualify_parser.add_argument("--en", type=Path, help="Optional English exported model folder")
    unpack_parser = commands.add_parser("unpack")
    unpack_parser.add_argument("archive", type=Path)
    unpack_parser.add_argument("sha256")
    unpack_parser.add_argument("destination", type=Path)
    build_parser = commands.add_parser("build")
    build_parser.add_argument("--version", required=True, type=version)
    build_parser.add_argument("--library-version", required=True, type=version)
    build_parser.add_argument("--inputs", type=Path, default=ROOT / "build/verified")
    args = parser.parse_args()
    if args.command == "qualify":
        if args.all is None and args.en is None:
            parser.error("supply --all PATH and/or --en PATH")
        qualify(args)
    elif args.command == "unpack":
        unpack(args.archive, args.sha256, args.destination)
    else:
        build(args)


if __name__ == "__main__":
    main()
