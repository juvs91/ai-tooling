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

# Helper function to parse override file and extract service names
parse_override_services() {
    local override_file="$1"

    if [[ ! -f "$override_file" ]]; then
        echo "ERROR: Override file not found: $override_file" >&2
        return 1
    fi

    # Use Python for robust YAML parsing
    python3 - <<PY
import yaml
import sys

try:
    with open("$override_file", "r") as f:
        config = yaml.safe_load(f)
        if "services" in config:
            services = sorted(config.get("services", {}).keys())
            print("\n".join(services))
            sys.stdout.flush()
            sys.exit(0)
        else:
            print(f"ERROR: No \"services\" section in $override_file", file=sys.stderr)
            sys.exit(1)
except Exception as e:
    print(f"ERROR: Failed to parse $override_file: {e}", file=sys.stderr)
    sys.exit(1)
PY

}
