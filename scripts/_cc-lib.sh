# Find repository root directory
cc_repo_dir() {
    local dir="$(pwd)"
    while [[ ! -d "$dir/.git" && "$dir" != "/" ]]; do
        dir="$(dirname "$dir")"
    done
    if [[ -d "$dir/.git" ]]; then
        echo "$dir"
    else
        echo "ERROR: Not in a git repository" >&2
        return 1
    fi
}

# Helper function to parse override file and extract service names (single file, kept for compat)
parse_override_services() {
    parse_compose_services "$1"
}

# Parse service names from one or more compose files, returns sorted union
parse_compose_services() {
    local files=("$@")
    python3 - "${files[@]}" <<'PY'
import yaml, sys

services = set()
for fpath in sys.argv[1:]:
    try:
        with open(fpath) as f:
            config = yaml.safe_load(f)
            if config and "services" in config:
                services.update(config["services"].keys())
    except Exception as e:
        print(f"WARNING: Could not parse {fpath}: {e}", file=sys.stderr)

if not services:
    print("ERROR: No services found in provided compose files", file=sys.stderr)
    sys.exit(1)

print("\n".join(sorted(services)))
PY
}
