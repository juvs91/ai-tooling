import os
import sys
import json
import argparse
import requests

DEFAULT_HOST = "https://snr.kronosb.com"
DEFAULT_TOKEN = "sqa_420c860f4dba68ef0386a61c9f2844a1e1879f07"

class SonarClient:
    def __init__(self, host=None, token=None):
        self.host = host or os.environ.get("SONAR_HOST", DEFAULT_HOST)
        self.token = token or os.environ.get("SONAR_TOKEN", DEFAULT_TOKEN)
        if not self.token:
            print("Error: SONAR_TOKEN not found.", file=sys.stderr)
            sys.exit(1)
        self.auth = (self.token, "")

    def _get(self, endpoint, params=None):
        url = f"{self.host.rstrip('/')}/api/{endpoint}"
        try:
            response = requests.get(url, params=params, auth=self.auth)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            print(f"Error fetching from {url}: {e}", file=sys.stderr)
            if response is not None:
                print(f"Response: {response.text}", file=sys.stderr)
            return None

    def search_issues(self, project=None, component=None, statuses="OPEN,CONFIRMED", types=None, severities=None, ps=100):
        params = {
            "statuses": statuses,
            "ps": ps
        }
        if project:
            params["projectKeys"] = project
        if component:
            params["componentKeys"] = component
        if types:
            params["types"] = types
        if severities:
            params["severities"] = severities
            
        return self._get("issues/search", params)

    def get_components(self, project=None):
        params = {}
        if project:
            params["project"] = project
        return self._get("components/search", params)

def main():
    parser = argparse.ArgumentParser(description="SonarQube API Client")
    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # Search issues
    search_parser = subparsers.add_parser("search", help="Search issues")
    search_parser.add_argument("--project", help="Project key")
    search_parser.add_argument("--component", help="Component/File key")
    search_parser.add_argument("--statuses", default="OPEN,CONFIRMED", help="Statuses (comma separated)")
    search_parser.add_argument("--types", help="Types (BUG, VULNERABILITY, CODE_SMELL)")
    search_parser.add_argument("--severities", help="Severities (BLOCKER, CRITICAL, MAJOR, MINOR, INFO)")
    search_parser.add_argument("--ps", type=int, default=100, help="Page size")

    # List projects
    subparsers.add_parser("projects", help="List projects")

    args = parser.parse_args()

    client = SonarClient()

    if args.command == "search":
        result = client.search_issues(
            project=args.project,
            component=args.component,
            statuses=args.statuses,
            types=args.types,
            severities=args.severities,
            ps=args.ps
        )
        if result:
            print(json.dumps(result, indent=2))
    elif args.command == "projects":
        # Note: api/projects/search might require admin or specific perms
        result = client._get("projects/search")
        if result:
            print(json.dumps(result, indent=2))
    else:
        parser.print_help()

if __name__ == "__main__":
    main()
