#!/bin/sh
# Prepare a draft release from the available qualified model archives. Never publish or push.
set -eu

usage() {
    echo "Usage: sh scripts/release.sh [--dry-run] MODEL_VERSION [INPUT_DIR]"
    echo "Reads available model-{all,en}-MODEL_VERSION.zip files; INPUT_DIR defaults to build/inputs."
}

dry_run=false
case "${1:-}" in
    --help|-h) usage; exit 0 ;;
    --dry-run) dry_run=true; shift ;;
esac
if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then usage >&2; exit 2; fi
model_version=$1
repo=dayedge/time-whisper-model
script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
input_dir=${2:-"$script_dir/../build/inputs"}
input_dir=$(CDPATH= cd "$input_dir" && pwd)
cd "$script_dir/.."

# Read the exact archives being uploaded, not potentially stale unpacked metadata.
metadata=$(python3 - "$model_version" "$input_dir" <<'PY'
import hashlib
import json
from pathlib import Path
import sys
import zipfile

sys.path.insert(0, "scripts")
from model import version

release = version(sys.argv[1])
library = None
source = None
checksums = {"all": "none", "en": "none"}
inputs = Path(sys.argv[2])
for variant in ("all", "en"):
    path = inputs / f"model-{variant}-{release}.zip"
    if not path.is_file():
        continue
    with zipfile.ZipFile(path) as archive:
        meta = json.loads(archive.read(f"{variant}/metadata.json"))
    if (meta.get("version") != release or meta.get("variant") != variant
            or meta.get("system_spelling") is not True or meta.get("qualification") != "make check passed"):
        raise SystemExit(f"{path} is not qualified for this release with system spelling.")
    if meta.get("release_ready") is not True:
        raise SystemExit(f"{path} was tested against a working copy. Requalify against a clean published library tag before release.")
    candidate = version(meta["library_version"])
    if library is not None and (candidate != library or meta["library_source_commit"] != source):
        raise SystemExit("Selected variants must be qualified against the same library version and commit.")
    library, source = candidate, meta["library_source_commit"]
    checksums[variant] = hashlib.sha256(path.read_bytes()).hexdigest()
if library is None:
    raise SystemExit(f"No qualified model ZIPs for {release} in {inputs}. Run scripts/model.py qualify first.")
print(library, source, checksums["all"], checksums["en"])
PY
)
read -r library_version library_commit all_sha256 en_sha256 <<EOF
$metadata
EOF
packaging_commit=$(git rev-parse HEAD)
if [ -n "$(git status --porcelain --untracked-files=all)" ]; then
    echo 'Commit the packaging changes before preparing a release.' >&2
    exit 1
fi

run() {
    printf '+'
    printf ' %s' "$@"
    printf '\n'
    if [ "$dry_run" = false ]; then "$@"; fi
}

printf 'Model %s | library %s | packaging commit %s\n' "$model_version" "$library_version" "$packaging_commit"
set --
variants=
if [ "$all_sha256" != none ]; then
    set -- "$@" "$input_dir/model-all-$model_version.zip"
    variants=all
fi
if [ "$en_sha256" != none ]; then
    set -- "$@" "$input_dir/model-en-$model_version.zip"
    variants="${variants:+$variants,}en"
fi
release_state=
if [ "$dry_run" = false ]; then
    # These reads must succeed before any release is created or modified.
    remote_commit=$(gh api "repos/$repo/commits/$packaging_commit" --jq .sha)
    if [ "$remote_commit" != "$packaging_commit" ]; then
        echo 'Push the packaging commit before preparing a release.' >&2
        exit 1
    fi
    remote_library=$(gh api "repos/dayedge/time-whisper/commits/$library_version" --jq .sha)
    if [ "$remote_library" != "$library_commit" ]; then
        echo 'The remote library tag differs from the qualified source. Requalify against the published tag.' >&2
        exit 1
    fi
    release_state=$(gh api "repos/$repo/releases?per_page=100" --paginate \
        --jq ".[] | select(.tag_name == \"$model_version\") | .draft")
    if [ -n "$release_state" ] && [ "$release_state" != true ]; then
        echo 'This version is already published; choose a new model version.' >&2
        exit 1
    fi
    tag=$(gh api "repos/$repo/git/matching-refs/tags/$model_version" \
        --jq ".[] | select(.ref == \"refs/tags/$model_version\") | .object.sha")
    if [ -n "$tag" ]; then
        echo 'This version already has a tag. Review and finish its draft manually; tags are never moved.' >&2
        exit 1
    fi
fi

if [ -z "$release_state" ]; then
    run gh release create "$model_version" --repo "$repo" --draft --target "$packaging_commit" \
        --title "TimeWhisper Models $model_version" \
        --notes "Variants: $variants. Tested with TimeWhisper $library_version."
else
    echo 'Reusing the existing draft release.'
fi
run gh release upload "$model_version" "$@" --repo "$repo" --clobber
set -- -f "source_ref=$packaging_commit" -f "version=$model_version" -f "library_version=$library_version"
if [ "$all_sha256" != none ]; then set -- "$@" -f "all_sha256=$all_sha256"; fi
if [ "$en_sha256" != none ]; then set -- "$@" -f "en_sha256=$en_sha256"; fi
run gh workflow run release.yml --repo "$repo" "$@"
if [ "$dry_run" = true ]; then
    echo 'Dry run: no GitHub calls made. Remote draft/tag checks will run during a real release.'
else
    echo "Follow Prepare model release: https://github.com/$repo/actions/workflows/release.yml"
    echo "The release stays a draft. Review the successful run and assets before publishing."
fi
