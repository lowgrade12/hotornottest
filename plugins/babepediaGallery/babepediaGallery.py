import stashapi.log as log
from stashapi.stashapp import StashInterface
import os
import sys
import re
import time
import requests
import json
from pathlib import Path
import base64


per_page = 100
request_s = requests.Session()
request_s.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        )
    }
)

BABEPEDIA_BASE = "https://www.babepedia.com"
BABEPEDIA_SEARCH = "https://www.babepedia.com/ajax-search.php"

# Minimum delay (seconds) between requests to babepedia.com to avoid
# tripping any rate limiting / anti-scraping protections.
REQUEST_DELAY = 1.0
_last_request_time = 0.0


def _lock_path():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         ".babepedia_gallery_running.lock")


def acquire_lock():
    """Create a lock file indicating that a performer/relink task is actively
    running, so the Performer.Update.Post hook (fired by our own performer
    updates) knows to bypass reprocessing instead of looping back into
    "Process Performers"."""
    try:
        with open(_lock_path(), "w") as fh:
            fh.write(str(os.getpid()))
    except OSError as e:
        log.debug(f"Could not create lock file: {e}")


def release_lock():
    """Remove the lock file when the task finishes."""
    try:
        os.unlink(_lock_path())
    except OSError:
        pass


def is_task_running():
    """Return True if the lock file exists and references a still-running
    process. Stale locks (referencing a dead PID) are cleaned up and treated
    as not running."""
    path = _lock_path()
    if not os.path.exists(path):
        return False
    try:
        with open(path) as fh:
            pid_str = fh.read().strip()
        if pid_str:
            pid = int(pid_str)
            os.kill(pid, 0)
        return True
    except (ValueError, ProcessLookupError):
        try:
            os.unlink(path)
        except OSError:
            pass
        return False
    except PermissionError:
        # Process exists but we can't signal it (different user) - still running
        return True
    except OSError:
        return False


FRAGMENT_IMAGE = """
    id
    title
    visual_files {
        ... on ImageFile {
            id
            path
        }
        ... on VideoFile {
            id
            path
        }
    }
    paths {
        image
        thumbnail
    }
    galleries {
        id
    }
    tags {
        id
    }
    performers {
        id
    }
"""


def babepedia_get(url, **kwargs):
    """GET a babepedia.com URL, throttling requests to be a considerate scraper."""
    global _last_request_time
    elapsed = time.monotonic() - _last_request_time
    if elapsed < REQUEST_DELAY:
        time.sleep(REQUEST_DELAY - elapsed)
    resp = request_s.get(url, timeout=30, **kwargs)
    _last_request_time = time.monotonic()
    return resp


def validate_ids(image_data):
    """Validate that IDs in the image data still exist in Stash.

    This prevents FOREIGN KEY constraint errors when referenced entities
    (tags, galleries, performers) have been deleted from Stash.

    Args:
        image_data: Dictionary containing image update data with tag_ids, gallery_ids, performer_ids

    Returns:
        Dictionary with filtered ID lists (invalid IDs removed)
    """
    validated = image_data.copy()

    # Validate tag_ids - filter out tags that no longer exist
    if "tag_ids" in validated and validated["tag_ids"]:
        valid_tag_ids = []
        for tag_id in validated["tag_ids"]:
            try:
                tag = stash.find_tag(tag_id)
                if tag:
                    valid_tag_ids.append(tag_id)
                else:
                    log.debug(f"Tag {tag_id} no longer exists, skipping")
            except Exception as e:
                log.debug(f"Error checking tag {tag_id}: {e}")
        validated["tag_ids"] = valid_tag_ids

    # Validate gallery_ids - filter out galleries that no longer exist and
    # zip-based galleries whose image membership is managed automatically by
    # Stash (attempting to set these via imageUpdate always fails with
    # "cannot change contents of zip-based gallery")
    if "gallery_ids" in validated and validated["gallery_ids"]:
        valid_gallery_ids = []
        for gallery_id in validated["gallery_ids"]:
            try:
                try:
                    gallery = stash.find_gallery(gallery_id, fragment="id folder { id } files { path }")
                except Exception as e:
                    # The extended fragment (folder/files) may not be supported by
                    # every Stash version. Rather than silently dropping this
                    # (otherwise valid) gallery association - which would leave
                    # newly created images completely un-galleried - fall back to
                    # a plain existence check so the gallery is still linked.
                    log.debug(
                        f"Error checking gallery {gallery_id} with extended fragment, "
                        f"falling back to basic existence check: {e}"
                    )
                    gallery = stash.find_gallery(gallery_id)
                if not gallery:
                    log.debug(f"Gallery {gallery_id} no longer exists, skipping")
                    continue
                if not gallery.get("folder") and gallery.get("files"):
                    log.debug(
                        f"Gallery {gallery_id} is zip/file-based, its image membership "
                        "is managed automatically by Stash, skipping explicit link"
                    )
                    continue
                valid_gallery_ids.append(gallery_id)
            except Exception as e:
                log.debug(f"Error checking gallery {gallery_id}: {e}")
        if valid_gallery_ids:
            validated["gallery_ids"] = valid_gallery_ids
        else:
            # Don't send an empty gallery_ids list - Stash treats an explicit
            # empty list as "remove from all galleries" which would also be
            # rejected (or worse, unlink) for zip-based galleries. Simply omit
            # the field so the image's existing gallery association is untouched.
            validated.pop("gallery_ids", None)

    # Validate performer_ids - filter out performers that no longer exist
    if "performer_ids" in validated and validated["performer_ids"]:
        valid_performer_ids = []
        for performer_id in validated["performer_ids"]:
            try:
                performer = stash.find_performer(performer_id)
                if performer:
                    valid_performer_ids.append(performer_id)
                else:
                    log.debug(f"Performer {performer_id} no longer exists, skipping")
            except Exception as e:
                log.debug(f"Error checking performer {performer_id}: {e}")
        validated["performer_ids"] = valid_performer_ids

    return validated


def slugify_name(name):
    """Turn a performer name into babepedia's underscore-separated slug form."""
    return re.sub(r"\s+", "_", name.strip())


def page_looks_valid(html_text):
    return bool(re.search(r'<h1[^>]*id="babename"[^>]*>', html_text))


def find_babepedia_url(performer):
    """Resolve the babepedia.com profile URL for a performer.

    Preference order:
    1. An existing babepedia.com URL already stored on the performer.
    2. A direct name-to-slug guess (https://www.babepedia.com/babe/<Name>).
    3. Babepedia's own search endpoint, matched against the performer's name
       and any known aliases.
    """
    for u in performer.get("urls") or []:
        if "babepedia.com/babe/" in u:
            return u

    name = performer["name"]
    guess_url = f"{BABEPEDIA_BASE}/babe/{slugify_name(name)}"
    try:
        resp = babepedia_get(guess_url)
        if resp.status_code == 200 and page_looks_valid(resp.text):
            return guess_url
    except requests.RequestException as e:
        log.debug(f"Error checking guessed babepedia url {guess_url}: {e}")

    try:
        resp = babepedia_get(BABEPEDIA_SEARCH, params={"term": name})
        if resp.status_code == 200:
            results = resp.json()
            if results:
                candidates = [name] + [
                    a.strip() for a in (performer.get("alias_list") or [])
                ]
                lowered_candidates = [c.lower() for c in candidates]
                match = None
                for r in results:
                    if r.get("label", "").lower() in lowered_candidates:
                        match = r
                        break
                if match is None:
                    match = results[0]
                slug = match["value"].replace(" ", "_")
                return f"{BABEPEDIA_BASE}/babe/{slug}"
    except (requests.RequestException, ValueError) as e:
        log.debug(f"Error searching babepedia for performer {name}: {e}")

    return None


def extract_images(html_text):
    """Extract full-size image URLs from a babepedia performer page.

    Babepedia performer pages embed the main profile gallery inside a
    `<div id="profbox2">` container and any user-submitted uploads inside a
    `<div ... class="...useruploads2...">` container. Both contain thumbnail
    links of the form `<a class="img" href="...">`, where the href points at
    the full-size image.
    """
    images = []
    seen = set()

    def collect(section_html):
        for href in re.findall(r'<a[^>]+class="[^"]*\bimg\b[^"]*"[^>]+href="([^"]+)"', section_html):
            url = href if href.startswith("http") else f"{BABEPEDIA_BASE}{href}"
            if url not in seen:
                seen.add(url)
                images.append(url)

    # The profbox2/useruploads2 containers can't be reliably bounded with a
    # simple closing-tag regex (nested divs), so just scan a generous window
    # of raw HTML following each container's opening tag for "img" links.
    profbox_start = html_text.find('id="profbox2"')
    if profbox_start != -1:
        section = html_text[profbox_start:profbox_start + 20000]
        collect(section)

    uploads_start = re.search(r'class="[^"]*useruploads2[^"]*"', html_text)
    if uploads_start:
        section = html_text[uploads_start.start():uploads_start.start() + 20000]
        collect(section)

    return images


def extract_name(html_text, fallback):
    match = re.search(r'<h1[^>]*id="babename"[^>]*>([^<]+)</h1>', html_text)
    if match:
        return match.group(1).strip()
    return fallback


def get_performer_index(performer_id):
    index_file = Path(settings["path"]) / performer_id / "index.json"
    if index_file.exists():
        try:
            with open(index_file) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            log.debug(f"Could not load performer index {index_file}: {e}")
    return None


def processImages(img):
    log.debug("image: %s" % (img,))
    image_data = None
    for file in [x["path"] for x in img["visual_files"]]:
        if settings["path"] in file:
            performer_id = Path(file).parent.name
            index_file = Path(Path(file).parent) / (Path(file).stem + ".json")
            log.debug(index_file)
            if index_file.exists():
                log.debug("loading index file %s" % (index_file,))
                with open(index_file) as f:
                    index = json.load(f)
                    index["id"] = img["id"]
                    performer_index = get_performer_index(performer_id)
                    if performer_index and performer_index.get("gallery_id"):
                        # Re-resolve the gallery id against the performer's
                        # current gallery mapping in case the gallery stored
                        # in this (possibly older) per-image file has since
                        # been deleted and recreated.
                        index["gallery_ids"] = [performer_index["gallery_id"]]
                    if image_data:
                        image_data["gallery_ids"].extend(index.get("gallery_ids", []))
                    else:
                        image_data = index
    if image_data:
        validated_data = validate_ids(image_data)
        stash.update_image(validated_data)


def processPerformers():
    """Process all performers with the [Babepedia Gallery] tag.

    Returns:
        list: List of performer IDs that were processed
    """
    query = {
        "tags": {
            "depth": 0,
            "excludes": [],
            "modifier": "INCLUDES_ALL",
            "value": [tag_babepedia_gallery],
        }
    }
    performers = stash.find_performers(f=query)
    processed_performer_ids = []

    for performer in performers:
        processPerformer(performer)
        processed_performer_ids.append(performer["id"])

    return processed_performer_ids


def processPerformer(performer):
    dir = Path(settings["path"]) / performer["id"]
    dir.mkdir(parents=True, exist_ok=True)
    nogallery = dir / ".nogallery"
    nogallery.touch()

    url = find_babepedia_url(performer)
    if not url:
        log.error(
            "Could not find a babepedia.com page for performer %s, skipping"
            % (performer["name"],)
        )
        return

    log.info(
        "processing performer %s, %s  babepedia url: %s"
        % (performer["name"], performer["id"], url)
    )

    try:
        resp = babepedia_get(url)
    except requests.RequestException as e:
        log.error(f"Error fetching {url}: {e}")
        return
    if resp.status_code != 200:
        log.error(f"Error fetching {url}: HTTP {resp.status_code}")
        return

    babepedia_name = extract_name(resp.text, performer["name"])
    images = extract_images(resp.text)
    if not images:
        log.warning("No images found on babepedia page %s" % (url,))

    index_file = os.path.join(settings["path"], performer["id"], "index.json")
    if os.path.exists(index_file):
        with open(index_file) as f:
            index = json.load(f)
    else:
        index = {"performer_id": performer["id"], "url": url}

    modified = False
    if "gallery_id" not in index or not stash.find_gallery(index["gallery_id"]):
        gallery_input = {
            "title": "%s - Babepedia" % (babepedia_name,),
            "urls": [url],
            "tag_ids": [tag_babepedia_gallery],
            "performer_ids": [performer["id"]],
        }
        gal = stash.create_gallery(gallery_input)
        log.info("Created gallery %s" % (gal,))
        index["gallery_id"] = gal
        modified = True

    if modified:
        with open(index_file, "w") as f:
            json.dump(index, f)

    for i, image_url in enumerate(images):
        image_index = Path(settings["path"]) / performer["id"] / ("%s.json" % (i,))
        if not image_index.exists():
            with open(image_index, "w") as f:
                image_data = {
                    "title": "%s - %s" % (babepedia_name, i),
                    "urls": [image_url],
                    "performer_ids": [performer["id"]],
                    "tag_ids": [tag_babepedia_gallery],
                    "gallery_ids": [index["gallery_id"]],
                }
                json.dump(image_data, f)
        ext = os.path.splitext(image_url.split("?")[0])[1] or ".jpg"
        filename = Path(settings["path"]) / performer["id"] / ("%s%s" % (i, ext))
        if not filename.exists():
            log.info("Downloading image %s to %s" % (image_url, filename))
            try:
                r = babepedia_get(image_url)
                if r.status_code == 200:
                    with open(filename, "wb") as f:
                        f.write(r.content)
                else:
                    log.warning(f"Failed to download {image_url}: HTTP {r.status_code}")
            except requests.RequestException as e:
                log.warning(f"Failed to download {image_url}: {e}")
        else:
            log.debug("image already downloaded")


def setPerformerPicture(img):
    if len(img["performers"]) == 1:
        log.debug(img["paths"]["image"])
        res = request_s.get(img["paths"]["image"])
        log.debug(res.headers["Content-Type"])
        if res.status_code == 200:
            encoded = base64.b64encode(res.content).decode()
            new_performer = {
                "id": img["performers"][0]["id"],
                "image": "data:{0};base64,{1}".format(
                    res.headers["Content-Type"], encoded
                ),
            }
            log.info("updating performer with tagged image %s" % (new_performer["id"],))
            stash.update_performer(new_performer)


def remove_tag_from_performer(performer_id):
    """Remove the [Babepedia Gallery] tag from a performer after its gallery is downloaded."""
    try:
        performer = stash.find_performer(performer_id)
        if not performer:
            log.warning(f"Could not find performer {performer_id} to remove tag")
            return False

        current_tag_ids = [tag["id"] for tag in performer.get("tags", [])]

        if tag_babepedia_gallery not in current_tag_ids:
            log.debug(f"Performer {performer.get('name', performer_id)} doesn't have the gallery tag")
            return False

        new_tag_ids = [tid for tid in current_tag_ids if tid != tag_babepedia_gallery]

        stash.update_performer({
            "id": performer_id,
            "tag_ids": new_tag_ids
        })

        log.info(f"Removed [Babepedia Gallery] tag from performer {performer.get('name', performer_id)}")
        return True
    except Exception as e:
        log.error(f"Error removing tag from performer {performer_id}: {e}")
        return False


def relink_images(performer_id=None, processed_performer_ids=None):
    """Relink images that are missing their gallery associations.

    Args:
        performer_id: Optional performer ID to limit relinking to a specific performer
        processed_performer_ids: Optional list of performer IDs that were processed in batch mode
    """
    query = {
        "path": {"modifier": "INCLUDES", "value": settings["path"]},
    }
    if performer_id is None:
        query["is_missing"] = "galleries"
        query["path"] = {"modifier": "INCLUDES", "value": settings["path"]}
    else:
        query["path"] = {
            "modifier": "INCLUDES",
            "value": str(Path(settings["path"]) / performer_id / ""),
        }

    total = stash.find_images(f=query, get_count=True)[0]
    log.info(f"Found {total} images to process for relinking")

    page = 1
    processed = 0

    while processed < total:
        log.debug(f"Fetching page {page} (processed {processed}/{total})")
        images = stash.find_images(f=query, filter={"page": page, "per_page": per_page})

        if not images:
            log.warning(f"No images returned for page {page}, breaking loop")
            break

        for img in images:
            log.debug("image: %s" % (img,))
            processImages(img)
            processed += 1
            log.progress((processed / total))

        page += 1

        if page > (total // per_page) + 10:
            log.warning(f"Pagination exceeded expected bounds (page {page}), breaking loop")
            break

    log.info(f"Completed relinking {processed} images")

    if settings.get("removeTagAfterDownload", False):
        if performer_id:
            log.info(f"removeTagAfterDownload is enabled, removing tag from performer {performer_id}")
            remove_tag_from_performer(performer_id)
        elif processed_performer_ids:
            log.info(f"removeTagAfterDownload is enabled, removing tag from {len(processed_performer_ids)} processed performers")
            removed_count = 0
            for pid in processed_performer_ids:
                if remove_tag_from_performer(pid):
                    removed_count += 1
            log.info(f"Removed [Babepedia Gallery] tag from {removed_count} performers")


json_input = json.loads(sys.stdin.read())

FRAGMENT_SERVER = json_input["server_connection"]
stash = StashInterface(FRAGMENT_SERVER)

config = stash.get_configuration()["plugins"]
settings = {
    "path": "/download_dir",
    "removeTagAfterDownload": False,
}
if "babepediaGallery" in config:
    settings.update(config["babepediaGallery"])

tag_babepedia_gallery = stash.find_tag("[Babepedia Gallery]", create=True).get("id")
tag_performer_image = stash.find_tag("[Set Profile Image]", create=True).get("id")

if "mode" in json_input["args"]:
    PLUGIN_ARGS = json_input["args"]["mode"]
    # These tasks touch performers/images ourselves (downloading images,
    # scanning metadata, linking galleries, setting profile pictures), which
    # would otherwise re-trigger the Performer.Update.Post hook and process
    # the same performer again. Hold the lock for the duration of the task so
    # the hook knows to bypass reprocessing while we're already working.
    acquire_lock()
    try:
        if "performer" in json_input["args"]:
            p = stash.find_performer(json_input["args"]["performer"])
            if tag_babepedia_gallery in [x["id"] for x in p["tags"]]:
                processPerformer(p)
                stash.metadata_scan(paths=[settings["path"]])
                stash.run_plugin_task(
                    "babepediaGallery",
                    "relink missing images",
                    args={"mode": "processImages", "performer_id": p["id"]},
                )
        elif "processPerformers" in PLUGIN_ARGS:
            processed_ids = processPerformers()
            stash.metadata_scan([settings["path"]])
            stash.run_plugin_task(
                "babepediaGallery", "relink missing images", args={"mode": "processImages", "processed_performer_ids": ",".join(str(pid) for pid in processed_ids)}
            )
        elif "processImages" in PLUGIN_ARGS:
            if "performer_id" in json_input["args"]:
                relink_images(performer_id=json_input["args"]["performer_id"])
            elif "processed_performer_ids" in json_input["args"]:
                ids_str = json_input["args"]["processed_performer_ids"]
                processed_ids = [pid.strip() for pid in ids_str.split(",") if pid.strip()]
                relink_images(processed_performer_ids=processed_ids)
            else:
                relink_images()
    finally:
        release_lock()


elif "hookContext" in json_input["args"]:
    id = json_input["args"]["hookContext"]["id"]
    if json_input["args"]["hookContext"]["type"] == "Image.Create.Post":
        # Hold the lock while handling this hook - processImages() links the
        # image to its gallery, which would otherwise trigger the
        # Performer.Update.Post hook indirectly and cause a reprocessing loop.
        acquire_lock()
        try:
            img = stash.find_image(image_in=id, fragment=FRAGMENT_IMAGE)
            processImages(img)
        finally:
            release_lock()
    if json_input["args"]["hookContext"]["type"] == "Image.Update.Post":
        img = stash.find_image(image_in=id, fragment=FRAGMENT_IMAGE)
        if tag_performer_image in [x["id"] for x in img["tags"]]:
            # setPerformerPicture() updates the performer, which fires
            # Performer.Update.Post - hold the lock so that hook bypasses
            # reprocessing instead of looping back here.
            acquire_lock()
            try:
                setPerformerPicture(img)
            finally:
                release_lock()
    if json_input["args"]["hookContext"]["type"] == "Performer.Update.Post":
        # Bypass reprocessing if one of our own tasks (which updates
        # performers/images as part of downloading and linking galleries) is
        # already running - otherwise that update re-fires this hook and
        # reprocesses the same performer in an endless loop.
        if is_task_running():
            log.debug(
                "Performer.Update.Post: a babepediaGallery task is already "
                "running, bypassing hook to avoid reprocessing loop."
            )
        else:
            stash.run_plugin_task(
                "babepediaGallery", "Process Performers", args={"mode": "processPerformers", "performer": id}
            )
