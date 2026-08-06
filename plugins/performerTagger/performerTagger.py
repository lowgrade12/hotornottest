#!/usr/bin/env python3
"""
PerformerTagger - Batch Tag Performers (Stash task backend)

Applies attribute tags (hair colour, eye colour, ethnicity, body type, height, bust)
to all performers based on their Stash data fields.  For every category where a value
can be derived, the correct tag is always applied and any wrong managed tags in that
category are removed.  Categories for which no value can be derived are left untouched.
Male performers are skipped.

Uses only the Python standard library — no pip dependencies.
"""

import json
import math
import re
import ssl
import sys
import urllib.error
import urllib.request

import log

# ---------------------------------------------------------------------------
# SSL context (accept self-signed certificates for local Stash instances)
# ---------------------------------------------------------------------------

SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

# ---------------------------------------------------------------------------
# Tag groups — mirror the JavaScript DEFAULT_TAG_GROUPS constant exactly
# ---------------------------------------------------------------------------

DEFAULT_TAG_GROUPS = [
    {
        "category": "Hair Color",
        "tags": ["Blonde", "Brunette", "Black Hair", "Red Hair", "Auburn", "Gray Hair"],
    },
    {
        "category": "Eye Color",
        "tags": ["Blue Eyes", "Brown Eyes", "Green Eyes", "Hazel Eyes", "Gray Eyes", "Amber Eyes"],
    },
    {
        "category": "Body Type",
        "tags": ["Skinny", "Slim", "Athletic", "Average", "Curvy", "BBW", "Muscular"],
    },
    {
        "category": "Bust Size",
        "tags": ["Small Bust", "Medium Bust", "Large Bust"],
    },
    {
        "category": "Bust Type",
        "tags": ["Natural Tits", "Enhanced"],
    },
    {
        "category": "Ethnicity",
        "tags": ["Asian", "Latina", "Ebony", "Caucasian", "Mixed"],
    },
    {
        "category": "Height",
        "tags": ["Tall", "Average", "Short", "Tiny"],
    },
    {
        "category": "Genitalia",
        "tags": ["Innie Pussy", "Outie Pussy"],
        "lenient": True,
    },
    {
        "category": "Tattoos",
        "tags": ["Tattoos", "No Tattoos"],
        "auto_tag_only": True,
    },
    {
        "category": "Piercings",
        "tags": ["Piercings", "No Piercings"],
        "auto_tag_only": True,
    },
]

# All tag names that this plugin manages (lowercase for fast lookup)
ALL_MANAGED_TAG_NAMES = {
    t.lower()
    for group in DEFAULT_TAG_GROUPS
    for t in group["tags"]
}

BATCH_PAGE_SIZE = 100

# ---------------------------------------------------------------------------
# Stash connection helpers (same pattern as missing_scenes.py)
# ---------------------------------------------------------------------------

_stash_connection = None
_input_data = None


def get_input_data():
    global _input_data
    if _input_data is None:
        _input_data = json.loads(sys.stdin.read())
    return _input_data


def get_stash_connection():
    global _stash_connection
    if _stash_connection is not None:
        return _stash_connection
    try:
        data = get_input_data()
        sc = data.get("server_connection", {})
        host = sc.get("Host", "localhost")
        if host == "0.0.0.0":
            host = "localhost"
        _stash_connection = {
            "url": (
                sc.get("Scheme", "http")
                + "://"
                + host
                + ":"
                + str(sc.get("Port", 9999))
                + "/graphql"
            ),
            "api_key": sc.get("SessionCookie", {}).get("Value"),
        }
    except Exception as exc:
        log.LogError(f"Failed to read Stash connection from stdin: {exc}")
        _stash_connection = {"url": "http://localhost:9999/graphql", "api_key": None}
    return _stash_connection


def stash_graphql(query, variables=None):
    """Send a GraphQL request to the local Stash instance."""
    conn = get_stash_connection()
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if conn.get("api_key"):
        headers["Cookie"] = f"session={conn['api_key']}"

    payload = json.dumps({"query": query, "variables": variables or {}}).encode("utf-8")
    req = urllib.request.Request(conn["url"], data=payload, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=60, context=SSL_CONTEXT) as resp:
            result = json.loads(resp.read().decode("utf-8"))
            if "errors" in result:
                log.LogWarning(f"GraphQL errors: {result['errors']}")
            return result.get("data")
    except urllib.error.HTTPError as exc:
        log.LogError(f"HTTP {exc.code} from Stash: {exc.reason}")
        raise
    except Exception as exc:
        log.LogError(f"Stash request error: {exc}")
        raise


# ---------------------------------------------------------------------------
# Tag management (in-memory caches to avoid redundant lookups)
# ---------------------------------------------------------------------------

# tag name (lowercase) → tag ID (str)
tag_id_cache: dict[str, str | None] = {}
# category name (lowercase) → tag ID (str)
category_id_cache: dict[str, str | None] = {}


def find_tag_by_name(name: str) -> str | None:
    """Return the Stash tag ID for *name* (exact, case-insensitive), or None.
    Falls back to alias search when not found by primary name, mirroring the
    JS findTagByName() behaviour so the batch process handles the same tags."""
    key = name.lower()
    if key in tag_id_cache:
        return tag_id_cache[key]

    query = """
    query FindTagByName($name: String!) {
      findTags(
        tag_filter: { name: { value: $name, modifier: EQUALS } }
        filter: { per_page: 1 }
      ) {
        tags { id name }
      }
    }
    """
    try:
        data = stash_graphql(query, {"name": name})
        tags = (data or {}).get("findTags", {}).get("tags", [])
        tag = next((t for t in tags if t["name"].lower() == key), None)
        if tag:
            tag_id_cache[key] = tag["id"]
            return tag["id"]
    except Exception as exc:
        log.LogError(f'Error finding tag "{name}": {exc}')
        return None

    # Not found by primary name — check if this name is stored as an alias on
    # another tag.  This mirrors the JS findTagByName() alias fallback so both
    # the one-page and the all-performer batch processes behave identically.
    alias_query = """
    query FindTagByAlias($name: String!) {
      findTags(
        tag_filter: { aliases: { value: $name, modifier: EQUALS } }
        filter: { per_page: -1 }
      ) {
        tags { id name aliases }
      }
    }
    """
    try:
        alias_data = stash_graphql(alias_query, {"name": name})
        alias_tags = (alias_data or {}).get("findTags", {}).get("tags", [])
        # Stash's EQUALS modifier may be case-sensitive depending on the
        # underlying DB collation, so we do a final case-insensitive Python
        # check (mirrors JS findTagByName).  In practice the query returns at
        # most one matching tag, so the nested any() is not expensive.
        alias_tag = next(
            (t for t in alias_tags
             if t.get("aliases") and any(a.lower() == key for a in t["aliases"])),
            None,
        )
        tag_id = alias_tag["id"] if alias_tag else None
        tag_id_cache[key] = tag_id
        return tag_id
    except Exception as exc:
        log.LogError(f'Error finding tag by alias "{name}": {exc}')
        return None


def create_tag(name: str, parent_id: str | None = None) -> str | None:
    """Create a tag in Stash and return its ID, or None on failure."""
    query = """
    mutation TagCreate($input: TagCreateInput!) {
      tagCreate(input: $input) { id name }
    }
    """
    inp: dict = {"name": name}
    if parent_id:
        inp["parent_ids"] = [parent_id]
    try:
        data = stash_graphql(query, {"input": inp})
        tag = (data or {}).get("tagCreate")
        tag_id = tag["id"] if tag else None
        if tag_id:
            tag_id_cache[name.lower()] = tag_id
            log.LogInfo(f'Created tag "{name}" (id: {tag_id})')
        return tag_id
    except Exception as exc:
        log.LogError(f'Error creating tag "{name}": {exc}')
        return None


def get_or_create_tag(name: str, parent_id: str | None = None) -> str | None:
    """Return the ID of *name*, creating it (with *parent_id*) if absent.

    If tag creation fails because the tag already exists (Stash returns a
    GraphQL error for a duplicate name), the function clears any stale cache
    entry and does one more lookup so the existing tag's ID is returned rather
    than None.  This prevents the batch task from logging spurious creation
    errors and then silently failing to apply the tag to performers."""
    existing = find_tag_by_name(name)
    if existing:
        return existing
    created = create_tag(name, parent_id)
    if created:
        return created
    # Creation returned None — most likely because the tag already exists and
    # Stash rejected the duplicate.  Clear any stale cache entry (it may hold
    # None from the earlier failed find) and retry the lookup once.
    tag_id_cache.pop(name.lower(), None)
    return find_tag_by_name(name)


def get_or_create_category_tag(category_name: str) -> str | None:
    """Return the ID of the parent category tag, creating it if absent."""
    key = category_name.lower()
    if key in category_id_cache:
        return category_id_cache[key]
    tag_id = get_or_create_tag(category_name, None)
    category_id_cache[key] = tag_id
    return tag_id


# ---------------------------------------------------------------------------
# Derived-tag logic — mirrors deriveTagsFromPerformerData() in JS
# ---------------------------------------------------------------------------

def derive_tags(performer: dict) -> list[dict]:
    """
    Return a list of {tag_name, category_name} dicts inferred from *performer*'s
    Stash fields.  Mirrors the JS deriveTagsFromPerformerData() function.
    """
    derived = []

    # --- Hair Colour ---
    hc = (performer.get("hair_color") or "").lower()
    if hc:
        tag_name = None
        if "auburn" in hc:
            tag_name = "Auburn"
        elif "blonde" in hc or "blond" in hc:
            tag_name = "Blonde"
        elif "brunette" in hc or "brown" in hc:
            tag_name = "Brunette"
        elif "black" in hc:
            tag_name = "Black Hair"
        elif "red" in hc:
            tag_name = "Red Hair"
        elif "gray" in hc or "grey" in hc or "silver" in hc:
            tag_name = "Gray Hair"
        if tag_name:
            derived.append({"tag_name": tag_name, "category_name": "Hair Color"})

    # --- Eye Colour ---
    ec = (performer.get("eye_color") or "").lower()
    if ec:
        tag_name = None
        if "blue" in ec:
            tag_name = "Blue Eyes"
        elif "brown" in ec or "dark" in ec:
            tag_name = "Brown Eyes"
        elif "green" in ec:
            tag_name = "Green Eyes"
        elif "hazel" in ec:
            tag_name = "Hazel Eyes"
        elif "gray" in ec or "grey" in ec:
            tag_name = "Gray Eyes"
        elif "amber" in ec:
            tag_name = "Amber Eyes"
        if tag_name:
            derived.append({"tag_name": tag_name, "category_name": "Eye Color"})

    # --- Ethnicity (check caucasian before asian to avoid false positive) ---
    eth = (performer.get("ethnicity") or "").lower()
    if eth:
        tag_name = None
        if "caucasian" in eth or "white" in eth:
            tag_name = "Caucasian"
        elif "asian" in eth:
            tag_name = "Asian"
        elif "latina" in eth or "hispanic" in eth or "latin" in eth:
            tag_name = "Latina"
        elif "black" in eth or "african" in eth or "ebony" in eth:
            tag_name = "Ebony"
        elif "mixed" in eth or "biracial" in eth:
            tag_name = "Mixed"
        if tag_name:
            derived.append({"tag_name": tag_name, "category_name": "Ethnicity"})

    # --- Body Type (height) ---
    height_cm = performer.get("height_cm") or 0
    if height_cm > 0 and height_cm <= 160:
        derived.append({"tag_name": "Skinny", "category_name": "Body Type"})

    # --- Height category ---
    # Tall: >= 175 cm (5'9"+), Average: 165–174 cm (5'5"–5'8"),
    # Short: 155–164 cm (5'1"–5'4"), Tiny: < 155 cm (under 5'1")
    if height_cm > 0:
        if height_cm >= 175:
            tag_name = "Tall"
        elif height_cm >= 165:
            tag_name = "Average"
        elif height_cm >= 155:
            tag_name = "Short"
        else:
            tag_name = "Tiny"
        derived.append({"tag_name": tag_name, "category_name": "Height"})

    # --- Bust type (fake_tits field) ---
    ft = performer.get("fake_tits")
    if ft is not None:
        ft_str = str(ft).lower().strip()
        # Empty string means the field was not filled in ("no data") — skip it.
        if ft_str in ("no", "false", "natural"):
            derived.append({"tag_name": "Natural Tits", "category_name": "Bust Type"})
        elif ft_str not in ("", "unknown"):
            derived.append({"tag_name": "Enhanced", "category_name": "Bust Type"})

    # --- Bust size from measurements field (e.g. "34C-24-34") ---
    # Parse the cup letter from the bust portion and map to Small/Medium/Large.
    # Cup A–B → Small Bust, C–D → Medium Bust, DD/E and above → Large Bust.
    measurements = performer.get("measurements")
    if measurements:
        m_str = str(measurements).strip()
        cup_match = re.match(r"^\d*([A-Za-z]+)", m_str)
        if cup_match:
            cup = cup_match.group(1).upper()
            bust_tag = None
            if re.match(r"^(A|B)$", cup):
                bust_tag = "Small Bust"
            elif re.match(r"^(C|D)$", cup):
                bust_tag = "Medium Bust"
            elif re.match(r"^(DD|DDD|E|F|FF|G|GG|H|HH|J|JJ|K)", cup):
                bust_tag = "Large Bust"
            if bust_tag:
                derived.append({"tag_name": bust_tag, "category_name": "Bust Size"})

    # --- Tattoos ---
    tattoos = performer.get("tattoos")
    if tattoos is not None:
        t = str(tattoos).strip()
        derived.append({"tag_name": "No Tattoos" if t == "" else "Tattoos", "category_name": "Tattoos"})

    # --- Piercings ---
    piercings = performer.get("piercings")
    if piercings is not None:
        p = str(piercings).strip()
        derived.append({"tag_name": "No Piercings" if p == "" else "Piercings", "category_name": "Piercings"})

    return derived


# ---------------------------------------------------------------------------
# Performer tag update
# ---------------------------------------------------------------------------

def update_performer_tags(performer_id: str, tag_ids: list[str]) -> bool:
    """Replace the full tag list on a performer. Returns True on success."""
    query = """
    mutation UpdatePerformerTags($id: ID!, $tag_ids: [ID!]) {
      performerUpdate(input: { id: $id, tag_ids: $tag_ids }) {
        id
      }
    }
    """
    try:
        stash_graphql(query, {"id": performer_id, "tag_ids": tag_ids})
        return True
    except Exception as exc:
        log.LogError(f"Failed to update tags for performer {performer_id}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Scene tag inference
# ---------------------------------------------------------------------------

def fetch_performer_scene_tags_py(performer_id: str) -> list[dict]:
    """Fetch all scenes for a performer and return their tag arrays."""
    query = """
    query FindPerformerScenes($performer_id: [ID!]) {
      findScenes(
        scene_filter: { performers: { value: $performer_id, modifier: INCLUDES } }
        filter: { per_page: -1 }
      ) {
        scenes {
          tags { id name }
        }
      }
    }
    """
    data = stash_graphql(query, {"performer_id": [performer_id]})
    return (data or {}).get("findScenes", {}).get("scenes", [])


def derive_tags_from_scenes(scenes: list[dict], current_tag_name_to_id: dict) -> list[dict]:
    """
    Derive tag suggestions from scene tags using majority vote.
    Only suggests tags for categories where the performer has no existing managed tag
    (fill-empty-categories-only logic).

    Minimum threshold: when the performer has 4+ scenes a tag must appear in at least
    25% of scenes (minimum 2); with fewer scenes any single occurrence qualifies.

    Returns a list of {tag_name, category_name} dicts.
    """
    if not scenes:
        return []

    # Build lookup: managed tag name (lowercase) -> {tag_name, category_name}
    managed_tag_map: dict[str, dict] = {}
    lenient_categories: set[str] = set()
    for group in DEFAULT_TAG_GROUPS:
        if group.get("auto_tag_only"):
            continue
        for tag_name in group["tags"]:
            managed_tag_map[tag_name.lower()] = {
                "tag_name": tag_name,
                "category_name": group["category"],
            }
        if group.get("lenient"):
            lenient_categories.add(group["category"])

    # Determine which categories already have a managed tag on the performer
    filled_categories: set[str] = set()
    for group in DEFAULT_TAG_GROUPS:
        if group.get("auto_tag_only"):
            continue
        for tag_name in group["tags"]:
            if tag_name.lower() in current_tag_name_to_id:
                filled_categories.add(group["category"])
                break

    # Count occurrences of each managed tag across scenes
    # category_name -> {tag_name -> count}
    category_counts: dict[str, dict[str, int]] = {}
    for scene in scenes:
        for tag in scene.get("tags", []):
            managed = managed_tag_map.get(tag["name"].lower())
            if not managed:
                continue
            cat = managed["category_name"]
            if cat not in category_counts:
                category_counts[cat] = {}
            tn = managed["tag_name"]
            category_counts[cat][tn] = category_counts[cat].get(tn, 0) + 1

    # Minimum threshold: 25% of total scenes (at least 2) when performer has 4+ scenes.
    # Lenient categories always require only 1 occurrence.
    total_scenes = len(scenes)
    default_min_count = max(2, math.ceil(total_scenes * 0.25)) if total_scenes >= 4 else 1

    # Pick majority-vote winner per category
    derived = []
    for category_name, counts in category_counts.items():
        if category_name in filled_categories:
            continue
        min_count = 1 if category_name in lenient_categories else default_min_count
        best_tag = max(counts, key=lambda t: counts[t])
        if counts[best_tag] >= min_count:
            derived.append({"tag_name": best_tag, "category_name": category_name})

    return derived


def process_performer_from_scenes(performer: dict, skip_tagged: bool = False) -> str:
    """
    Apply scene-inferred tags to a single performer.
    Returns 'tagged', 'skipped', or 'error'.
    """
    performer_id = performer["id"]

    # Skip male performers
    gender = (performer.get("gender") or "").upper()
    if gender == "MALE":
        return "skipped"

    # Pre-populate tag ID cache from this performer's existing tags
    current_tags = performer.get("tags", [])
    for t in current_tags:
        tag_id_cache[t["name"].lower()] = t["id"]

    # When skip_tagged is set, skip performers that already have any managed tag
    if skip_tagged:
        existing_managed = {t["name"].lower() for t in current_tags} & ALL_MANAGED_TAG_NAMES
        if existing_managed:
            return "skipped"

    current_ids = {t["id"] for t in current_tags}
    current_tag_name_to_id = {t["name"].lower(): t["id"] for t in current_tags}

    # Fetch scene tags for this performer
    try:
        scenes = fetch_performer_scene_tags_py(performer_id)
    except Exception as exc:
        log.LogError(f"Failed to fetch scenes for performer {performer_id}: {exc}")
        return "error"

    if not scenes:
        return "skipped"

    derived = derive_tags_from_scenes(scenes, current_tag_name_to_id)
    if not derived:
        return "skipped"

    new_ids = set(current_ids)
    log_items = []

    for d in derived:
        category_id = get_or_create_category_tag(d["category_name"])
        tag_id = get_or_create_tag(d["tag_name"], category_id)
        if tag_id:
            new_ids.add(tag_id)
            log_items.append(f"{d['category_name']}: {d['tag_name']}")

    if new_ids == current_ids:
        return "skipped"

    if log_items:
        log.LogDebug(
            f"Performer {performer_id} ({performer.get('name', '?')}) [scenes]: "
            f"[{', '.join(log_items)}]"
        )
    success = update_performer_tags(performer_id, list(new_ids))
    return "tagged" if success else "error"


def task_batch_tag_from_scenes(skip_tagged: bool = False):
    """Main logic for the 'Batch Tag from Scenes' Stash task."""
    log.LogInfo("PerformerTagger: Batch Tag from Scenes starting…")

    first_page = fetch_performer_page_tags_only(1)
    total = first_page.get("count", 0)
    total_pages = math.ceil(total / BATCH_PAGE_SIZE) if total else 0

    log.LogInfo(f"PerformerTagger: {total} performer(s) to process across {total_pages} page(s)")

    processed = 0
    tagged = 0
    skipped = 0
    errors = 0

    def handle_page(performers):
        nonlocal processed, tagged, skipped, errors
        for performer in performers:
            result = process_performer_from_scenes(performer, skip_tagged=skip_tagged)
            processed += 1
            if result == "tagged":
                tagged += 1
            elif result == "error":
                errors += 1
            else:
                skipped += 1

            progress = processed / total if total else 1.0
            log.LogProgress(progress)

    handle_page(first_page.get("performers", []))

    for page in range(2, total_pages + 1):
        page_data = fetch_performer_page_tags_only(page)
        handle_page(page_data.get("performers", []))

    summary = (
        f"PerformerTagger: Done. "
        f"Processed {processed}, tagged {tagged}, skipped {skipped}"
        + (f", errors {errors}" if errors else "")
        + "."
    )
    log.LogInfo(summary)
    return {
        "processed": processed,
        "tagged": tagged,
        "skipped": skipped,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Batch processing
# ---------------------------------------------------------------------------

def fetch_performer_page(page: int) -> dict:
    """Fetch one page of performers with all fields needed for auto-tagging."""
    query = """
    query FindPerformersBatch($filter: FindFilterType) {
      findPerformers(filter: $filter) {
        count
        performers {
          id
          name
          hair_color
          eye_color
          ethnicity
          birthdate
          career_length
          height_cm
          fake_tits
          measurements
          gender
          tattoos
          piercings
          tags { id name }
        }
      }
    }
    """
    data = stash_graphql(query, {
        "filter": {
            "page": page,
            "per_page": BATCH_PAGE_SIZE,
            "sort": "id",
            "direction": "ASC",
        }
    })
    return (data or {}).get("findPerformers", {"count": 0, "performers": []})


def process_performer(performer: dict, skip_tagged: bool = False) -> str:
    """
    Auto-apply derived tags to a single performer.
    Returns 'tagged', 'skipped', or 'error'.
    """
    performer_id = performer["id"]

    # Skip male performers
    gender = (performer.get("gender") or "").upper()
    if gender == "MALE":
        return "skipped"

    # Pre-populate tag ID cache from this performer's existing tags
    current_tags = performer.get("tags", [])
    for t in current_tags:
        tag_id_cache[t["name"].lower()] = t["id"]

    # When skip_tagged is set, skip performers that already have any managed tag
    if skip_tagged:
        existing_managed = {t["name"].lower() for t in current_tags} & ALL_MANAGED_TAG_NAMES
        if existing_managed:
            return "skipped"

    current_ids = {t["id"] for t in current_tags}

    # Build a per-performer name→id map directly from current tags.
    # Used for wrong-tag detection to avoid any stale global-cache issues.
    current_tag_name_to_id = {t["name"].lower(): t["id"] for t in current_tags}

    # Derive tag suggestions from data fields
    derived = derive_tags(performer)
    if not derived:
        return "skipped"

    new_ids = set(current_ids)
    log_items = []

    # --- All categories: always apply the correct tag, replacing any wrong managed tags ---
    # For every category where a value can be derived from the performer's Stash data,
    # ensure the correct tag is present and remove any incorrect managed tags in that
    # category. Categories for which no value can be derived are left untouched.
    derived_by_category = {d["category_name"]: d for d in derived}

    for group in DEFAULT_TAG_GROUPS:
        correct_derived = derived_by_category.get(group["category"])
        if correct_derived is None:
            continue  # no data for this category — leave alone

        correct_name_lower = correct_derived["tag_name"].lower()
        has_correct_tag = False

        for tag_name in group["tags"]:
            # Use the performer's own tag map so we never miss a tag due to a
            # stale None entry in the shared global cache.
            tid = current_tag_name_to_id.get(tag_name.lower())
            if tid and tid in new_ids:
                if tag_name.lower() == correct_name_lower:
                    has_correct_tag = True
                else:
                    new_ids.discard(tid)  # remove wrong tag
                    log_items.append(f"{group['category']}: remove '{tag_name}'")

        if not has_correct_tag:
            category_id = get_or_create_category_tag(group["category"])
            tag_id = get_or_create_tag(correct_derived["tag_name"], category_id)
            if tag_id:
                new_ids.add(tag_id)
                log_items.append(f"{group['category']}: {correct_derived['tag_name']}")

    if new_ids == current_ids:
        return "skipped"

    if log_items:
        log.LogDebug(
            f"Performer {performer_id} ({performer.get('name', '?')}): [{', '.join(log_items)}]"
        )
    success = update_performer_tags(performer_id, list(new_ids))
    return "tagged" if success else "error"


def task_batch_tag_performers(skip_tagged: bool = False):
    """Main logic for the 'Batch Tag Performers' Stash task."""
    log.LogInfo("PerformerTagger: Batch Tag Performers starting…")

    # Fetch first page to get total count
    first_page = fetch_performer_page(1)
    total = first_page.get("count", 0)
    total_pages = math.ceil(total / BATCH_PAGE_SIZE) if total else 0

    log.LogInfo(f"PerformerTagger: {total} performer(s) to process across {total_pages} page(s)")

    processed = 0
    tagged = 0
    skipped = 0
    errors = 0

    def handle_page(performers):
        nonlocal processed, tagged, skipped, errors
        for performer in performers:
            result = process_performer(performer, skip_tagged=skip_tagged)
            processed += 1
            if result == "tagged":
                tagged += 1
            elif result == "error":
                errors += 1
            else:
                skipped += 1

            # Report progress (0.0 – 1.0) so Stash shows the progress bar
            progress = processed / total if total else 1.0
            log.LogProgress(progress)

    handle_page(first_page.get("performers", []))

    for page in range(2, total_pages + 1):
        page_data = fetch_performer_page(page)
        handle_page(page_data.get("performers", []))

    summary = (
        f"PerformerTagger: Done. "
        f"Processed {processed}, tagged {tagged}, skipped {skipped}"
        + (f", errors {errors}" if errors else "")
        + "."
    )
    log.LogInfo(summary)
    return {
        "processed": processed,
        "tagged": tagged,
        "skipped": skipped,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Remove all managed tags
# ---------------------------------------------------------------------------

def fetch_performer_page_tags_only(page: int) -> dict:
    """Fetch one page of performers with only id, name, gender, and tags."""
    query = """
    query FindPerformersTags($filter: FindFilterType) {
      findPerformers(filter: $filter) {
        count
        performers {
          id
          name
          gender
          tags { id name }
        }
      }
    }
    """
    data = stash_graphql(query, {
        "filter": {
            "page": page,
            "per_page": BATCH_PAGE_SIZE,
            "sort": "id",
            "direction": "ASC",
        }
    })
    return (data or {}).get("findPerformers", {"count": 0, "performers": []})


def task_remove_performer_tags():
    """
    Remove ALL tags from every performer so the batch-tag task can re-apply
    correct tags from a clean slate.
    """
    log.LogInfo("PerformerTagger: Remove All Performer Tags starting…")

    first_page = fetch_performer_page_tags_only(1)
    total = first_page.get("count", 0)
    total_pages = math.ceil(total / BATCH_PAGE_SIZE) if total else 0

    log.LogInfo(f"PerformerTagger: {total} performer(s) to scan across {total_pages} page(s)")

    processed = 0
    cleared = 0
    skipped = 0
    errors = 0

    def handle_page(performers):
        nonlocal processed, cleared, skipped, errors
        for performer in performers:
            performer_id = performer["id"]
            current_tags = performer.get("tags", [])

            processed += 1

            if not current_tags:
                skipped += 1
            else:
                # Remove every tag — pass an empty list to wipe the performer clean
                success = update_performer_tags(performer_id, [])
                if success:
                    cleared += 1
                    log.LogDebug(
                        f"Performer {performer_id} ({performer.get('name', '?')}): "
                        f"removed {len(current_tags)} tag(s)"
                    )
                else:
                    errors += 1

            progress = processed / total if total else 1.0
            log.LogProgress(progress)

    handle_page(first_page.get("performers", []))

    for page in range(2, total_pages + 1):
        page_data = fetch_performer_page_tags_only(page)
        handle_page(page_data.get("performers", []))

    summary = (
        f"PerformerTagger: Done. "
        f"Processed {processed}, cleared {cleared}, skipped {skipped}"
        + (f", errors {errors}" if errors else "")
        + "."
    )
    log.LogInfo(summary)
    return {
        "processed": processed,
        "cleared": cleared,
        "skipped": skipped,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    try:
        input_data = get_input_data()
    except Exception as exc:
        print(json.dumps({"error": f"Could not read stdin: {exc}"}))
        return

    args = input_data.get("args", {})
    mode = args.get("mode", "")
    skip_tagged = bool(args.get("skip_tagged", False))

    if mode == "batch_tag":
        output = task_batch_tag_performers(skip_tagged=skip_tagged)
        print(json.dumps({"output": output}))
        return

    if mode == "batch_tag_from_scenes":
        output = task_batch_tag_from_scenes(skip_tagged=skip_tagged)
        print(json.dumps({"output": output}))
        return

    if mode == "remove_all_tags":
        output = task_remove_performer_tags()
        print(json.dumps({"output": output}))
        return

    # Unknown mode — emit a helpful error so Stash logs it
    log.LogError(f"PerformerTagger: unknown task mode '{mode}'")
    print(json.dumps({"error": f"Unknown mode: {mode}"}))


if __name__ == "__main__":
    main()
