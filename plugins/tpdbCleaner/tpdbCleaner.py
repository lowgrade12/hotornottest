"""
tpdbCleaner.py

Stash plugin with two tasks:

  clean            – finds scenes that have both a StashDB and ThePornDB stash
                     ID, then removes the ThePornDB ID, leaving only the
                     StashDB ID.

  scrape_and_clean – finds all scenes with a ThePornDB ID, triggers the
                     StashDB identify task on them, waits for the background
                     job to finish, and then runs the same clean-up step above.

Plugin input is read from stdin as JSON (Stash raw interface).
The server_connection block supplies the host/port/auth so the script works
with any Stash instance without hard-coded URLs.
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

STASHDB_ENDPOINT = "https://stashdb.org/graphql"


# ---------------------------------------------------------------------------
# GraphQL helpers
# ---------------------------------------------------------------------------

def get_stash_url(server_connection: dict) -> str:
    """Build the Stash GraphQL URL from the server_connection block."""
    host = server_connection.get("Host", "localhost")
    if host == "0.0.0.0":
        host = "localhost"
    scheme = server_connection.get("Scheme", "http")
    port = server_connection.get("Port", 9999)
    return f"{scheme}://{host}:{port}/graphql"


def get_headers(server_connection: dict) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    # Use session cookie when available (normal plugin execution)
    session_cookie = server_connection.get("SessionCookie", {})
    if isinstance(session_cookie, dict):
        value = session_cookie.get("Value")
    else:
        value = None
    if value:
        headers["Cookie"] = f"session={value}"
    return headers


def stash_graphql(server_connection: dict, query: str, variables: dict | None = None) -> dict:
    """Execute a GraphQL query/mutation against the local Stash instance."""
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    url = get_stash_url(server_connection)
    req = urllib.request.Request(url, data=payload, headers=get_headers(server_connection), method="POST")
    try:
        with urllib.request.urlopen(req) as resp:
            result = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} from Stash GraphQL endpoint: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach Stash GraphQL endpoint ({url}): {exc.reason}") from exc
    if "errors" in result:
        raise RuntimeError(f"GraphQL error: {result['errors']}")
    return result.get("data", {})


# ---------------------------------------------------------------------------
# Queries / mutations
# ---------------------------------------------------------------------------

FIND_TPDB_SCENES_QUERY = """
query FindTPDBScenes {
  findScenes(
    scene_filter: {
      stash_id_endpoint: {
        endpoint: "https://theporndb.net/graphql"
        modifier: NOT_NULL
      }
    }
    filter: { per_page: -1 }
  ) {
    scenes {
      id
      stash_ids {
        endpoint
        stash_id
      }
    }
  }
}
"""

UPDATE_SCENE_STASH_IDS_MUTATION = """
mutation UpdateSceneStashIDs($id: ID!, $stash_ids: [StashIDInput!]) {
  sceneUpdate(input: { id: $id, stash_ids: $stash_ids }) {
    id
  }
}
"""

TRIGGER_IDENTIFY_MUTATION = """
mutation TriggerIdentify($sceneIds: [ID!]!, $stashdb_url: String!) {
  metadataIdentify(
    input: {
      sceneIDs: $sceneIds
      sources: [
        {
          source: {
            stash_box_endpoint: $stashdb_url
          }
        }
      ]
      options: {
        fieldOptions: [
          {
            field: "stash_ids"
            strategy: MERGE
          }
        ]
      }
    }
  )
}
"""

CHECK_JOB_QUERY = """
query CheckJobStatus($jobId: ID!) {
  findJob(input: { id: $jobId }) {
    status
  }
}
"""


def _endpoint_host(endpoint: str) -> str:
    """Return the lowercase hostname from an endpoint URL."""
    return urllib.parse.urlparse(endpoint).hostname or ""


def _is_stashdb(endpoint: str) -> bool:
    host = _endpoint_host(endpoint)
    return host == "stashdb.org" or host.endswith(".stashdb.org")


def _is_tpdb(endpoint: str) -> bool:
    host = _endpoint_host(endpoint)
    return host == "theporndb.net" or host.endswith(".theporndb.net")


# ---------------------------------------------------------------------------
# Core logic
# ---------------------------------------------------------------------------

def clean_tpdb_ids(server_connection: dict) -> None:
    print("[TPDBCleaner] Fetching scenes with ThePornDB IDs...")
    data = stash_graphql(server_connection, FIND_TPDB_SCENES_QUERY)
    scenes = data.get("findScenes", {}).get("scenes", [])
    print(f"[TPDBCleaner] Found {len(scenes)} scene(s) with a ThePornDB ID. Checking for StashDB duplicates...")

    updated_count = 0

    for scene in scenes:
        scene_id = scene["id"]
        stash_ids = scene.get("stash_ids", [])

        has_stashdb = any(_is_stashdb(sid["endpoint"]) for sid in stash_ids)
        has_tpdb = any(_is_tpdb(sid["endpoint"]) for sid in stash_ids)

        if has_stashdb and has_tpdb:
            # Keep every stash_id that is NOT from ThePornDB
            new_stash_ids = [
                {"endpoint": sid["endpoint"], "stash_id": sid["stash_id"]}
                for sid in stash_ids
                if not _is_tpdb(sid["endpoint"])
            ]
            stash_graphql(
                server_connection,
                UPDATE_SCENE_STASH_IDS_MUTATION,
                {"id": scene_id, "stash_ids": new_stash_ids},
            )
            updated_count += 1
            print(f"[TPDBCleaner] Scene {scene_id}: removed ThePornDB ID.")

    print(f"[TPDBCleaner] Done. Updated {updated_count} scene(s).")


def _wait_for_job(server_connection: dict, job_id: str) -> str:
    """Poll Stash until the background job reaches a terminal state."""
    print(f"[TPDBCleaner] Waiting for identify task (Job ID: {job_id}) to finish...")
    while True:
        data = stash_graphql(server_connection, CHECK_JOB_QUERY, {"jobId": job_id})
        status = data.get("findJob", {}).get("status", "")
        if status in ("FINISHED", "CANCELLED", "FAILED"):
            print(f"[TPDBCleaner] Job {job_id} completed with status: {status}")
            return status
        time.sleep(5)


def scrape_and_clean(server_connection: dict) -> None:
    """Trigger StashDB identify on all TPDB-tagged scenes, wait, then clean."""
    print("[TPDBCleaner] Phase 1: Finding scenes with ThePornDB IDs...")
    data = stash_graphql(server_connection, FIND_TPDB_SCENES_QUERY)
    scenes = data.get("findScenes", {}).get("scenes", [])

    if not scenes:
        print("[TPDBCleaner] No TPDB scenes found. Nothing to do!")
        return

    scene_ids = [scene["id"] for scene in scenes]
    print(f"[TPDBCleaner] Found {len(scene_ids)} scene(s) tagged with ThePornDB.")

    print("[TPDBCleaner] Phase 2: Triggering StashDB identify task...")
    result = stash_graphql(
        server_connection,
        TRIGGER_IDENTIFY_MUTATION,
        {"sceneIds": scene_ids, "stashdb_url": STASHDB_ENDPOINT},
    )
    job_id = result.get("metadataIdentify")
    if not job_id:
        raise RuntimeError("metadataIdentify did not return a job ID.")

    status = _wait_for_job(server_connection, str(job_id))
    if status == "FAILED":
        print("[TPDBCleaner] Warning: identify job failed. Proceeding with cleanup anyway.")

    print("[TPDBCleaner] Phase 3: Cleaning up old ThePornDB IDs...")
    clean_tpdb_ids(server_connection)
    print(
        f"[TPDBCleaner] Scrape & clean complete. "
        f"Processed {len(scene_ids)} scene(s)."
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> int:
    try:
        raw = sys.stdin.read()
        json_input = json.loads(raw) if raw.strip() else {}
        server_connection = json_input.get("server_connection", {})
        mode = json_input.get("args", {}).get("mode", "clean")

        if mode == "scrape_and_clean":
            scrape_and_clean(server_connection)
        else:
            clean_tpdb_ids(server_connection)
        return 0
    except Exception as exc:
        print(f"[TPDBCleaner] ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
