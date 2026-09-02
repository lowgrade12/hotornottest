import stashapi.log as log
from stashapi.stashapp import StashInterface
from stashapi.stashbox import StashBoxInterface
import os
import sys
import requests
import json
from pathlib import Path
import base64


per_page = 100
request_s = requests.Session()
stash_boxes = {}
scrapers = {}

# Endpoints to skip when searching for performer images. These TPDB endpoints
# scope results to movies/JAV only and break image search - the base TPDB
# endpoint already covers all images.
SKIPPED_IMAGE_ENDPOINTS = {
    "https://theporndb.net/graphql?type=Movie",
    "https://theporndb.net/graphql?type=JAV",
}

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

    # Validate gallery_ids - filter out galleries that no longer exist
    if "gallery_ids" in validated and validated["gallery_ids"]:
        valid_gallery_ids = []
        for gallery_id in validated["gallery_ids"]:
            try:
                gallery = stash.find_gallery(gallery_id)
                if gallery:
                    valid_gallery_ids.append(gallery_id)
                else:
                    log.debug(f"Gallery {gallery_id} no longer exists, skipping")
            except Exception as e:
                log.debug(f"Error checking gallery {gallery_id}: {e}")
        validated["gallery_ids"] = valid_gallery_ids

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


def processImages(img):
    log.debug("image: %s" % (img,))
    image_data = None
    for file in [x["path"] for x in img["visual_files"]]:
        if settings["path"] in file:
            index_file = Path(Path(file).parent) / (Path(file).stem + ".json")
            log.debug(index_file)
            if index_file.exists():
                log.debug("loading index file %s" % (index_file,))
                with open(index_file) as f:
                    index = json.load(f)
                    index["id"] = img["id"]
                    if image_data:
                        image_data["gallery_ids"].extend(index["gallery_ids"])
                    else:
                        image_data = index
    if image_data:
        # Validate IDs before updating to prevent FOREIGN KEY constraint errors
        validated_data = validate_ids(image_data)
        stash.update_image(validated_data)


def processPerformers():
    """Process all performers with the [Stashbox Performer Gallery] tag.

    Returns:
        list: List of performer IDs that were processed
    """
    query = {
        "tags": {
            "depth": 0,
            "excludes": [],
            "modifier": "INCLUDES_ALL",
            "value": [tag_stashbox_performer_gallery],
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
    for sid in performer["stash_ids"]:
        log.debug(sid)
        if sid["endpoint"] in SKIPPED_IMAGE_ENDPOINTS:
            log.debug("Skipping image search for endpoint: %s" % (sid["endpoint"],))
            continue
        processPerformerStashid(sid["endpoint"], sid["stash_id"], performer)


def get_stashbox(endpoint):
    for sbx_config in stash.get_configuration()["general"]["stashBoxes"]:
        if sbx_config["endpoint"] == endpoint:
            stashbox = StashBoxInterface(
                {"endpoint": sbx_config["endpoint"], "api_key": sbx_config["api_key"]}
            )
            stash_boxes[endpoint] = stashbox
            return stashbox


def processPerformerStashid(endpoint, stashid, p):
    log.info(
        "processing performer %s, %s  endpoint: %s,  stash id: %s"
        % (
            p["name"],
            p["id"],
            endpoint,
            stashid,
        )
    )

    index_file = os.path.join(settings["path"], p["id"], "index.json")
    if os.path.exists(index_file):
        with open(os.path.join(settings["path"], p["id"], "index.json")) as f:
            index = json.load(f)
    else:
        index = {"files": {}, "galleries": {}, "performer_id": p["id"]}

    modified = False
    stashbox = get_stashbox(endpoint)
    if stashbox:
        query = """id
        name
        images {
          id
          url
        }
        urls{
          url
          type
        }
        """
        perf = stashbox.find_performer(stashid, fragment=query)
        log.debug(perf)
        if endpoint not in index["galleries"]:
            gallery_input = {
                "title": "%s - %s "
                % (
                    p["name"],
                    endpoint[8:-8],
                ),
                "urls": [
                    "%s/performers/%s"
                    % (
                        endpoint[:-8],
                        stashid,
                    )
                ],
                "tag_ids": [tag_stashbox_performer_gallery],
                "performer_ids": [p["id"]],
            }
            gal = stash.create_gallery(gallery_input)
            log.info("Created gallery %s" % (gal,))
            index["galleries"][endpoint] = gal

            modified = True
        # check if the gallery still exists and has not been deleted
        current_gal = stash.find_gallery(index["galleries"][endpoint])
        log.debug("current: %s" % (current_gal,))
        if current_gal is None:
            log.debug("deleted?")
            gallery_input = {
                "title": "%s - %s "
                % (
                    p["name"],
                    endpoint[:-8],
                ),
                "urls": [
                    "%s/performers/%s"
                    % (
                        endpoint[:-8],
                        stashid,
                    )
                ],
                "tag_ids": [tag_stashbox_performer_gallery],
                "performer_ids": [p["id"]],
            }

            gal = stash.create_gallery(gallery_input)
            log.info("Created gallery %s" % (gal,))
            index["galleries"][endpoint] = gal
            modified = True
        if modified:
            with open(index_file, "w") as f:
                json.dump(index, f)

        for img in perf["images"]:
            image_index = Path(settings["path"]) / p["id"] / (img["id"] + ".json")
            if not image_index.exists():
                with open(image_index, "w") as f:
                    image_data = {
                        "title": img["id"],
                        "urls": [img["url"]],
                        "performer_ids": [p["id"]],
                        "tag_ids": [tag_stashbox_performer_gallery],
                        "gallery_ids": [index["galleries"][endpoint]],
                    }
                    json.dump(image_data, f)
            filename = Path(settings["path"]) / p["id"] / (img["id"] + ".jpg")
            if not os.path.exists(filename):
                log.info(
                    "Downloading image %s to %s"
                    % (
                        img["url"],
                        filename,
                    )
                )
                r = requests.get(img["url"])
                with open(filename, "wb") as f:
                    f.write(r.content)
                    f.close()
            #            modified=True
            else:
                log.debug("image already downloaded")

        # scrape urls on the performer using the url scrapers in stash
        if settings["runPerformerScraper"] and len(perf["urls"]) > 0:

            # we need to determine what scrapers we have and what url patterns they accept, query what url patterns are supported, should only need to check once
            if len(scrapers) == 0:
                scrapers_graphql = """query ListPerformerScrapers {
                  listScrapers(types: [PERFORMER]) {
                  id
                  name
                    performer {
                      urls
                      supported_scrapes
                    }
                  }
                }"""
                res = stash.callGQL(scrapers_graphql)
                for r in res["listScrapers"]:
                    if r["performer"]["urls"]:
                        for url in r["performer"]["urls"]:
                            scrapers[url] = r

            for u in perf["urls"]:
                for url in scrapers.keys():
                    if url in u["url"]:
                        log.info(
                            "Running stash scraper on performer url: %s" % (u["url"],)
                        )
                        try:
                            res = stash.scrape_performer_url(u["url"])
                        except Exception as e:
                            log.warning(
                                "Scraper failed for url %s: %s" % (u["url"], e)
                            )
                            continue
                        # Check if the scraper returned a result
                        if res is not None:
                            log.debug(res)
                            # it's possible for multiple images to be returned by a scraper so increment a number each image
                            image_id = 1
                            if res["images"]:
                                for image in res["images"]:
                                    image_index = (
                                        Path(settings["path"])
                                        / p["id"]
                                        / (
                                            "%s-%s.json"
                                            % (
                                                scrapers[url]["id"],
                                                image_id,
                                            )
                                        )
                                    )
                                    if not image_index.exists():
                                        with open(image_index, "w") as f:
                                            image_data = {
                                                "title": "%s - %s "
                                                % (
                                                    scrapers[url]["id"],
                                                    image_id,
                                                ),
                                                "details": "name: %s\ngender: %s\nurl: %s\ntwitter: %s\ninstagram: %s\nbirthdate: %s\nethnicity: %s\ncountry: %s\neye_color: %s\nheight: %s\nmeasurements: %s\nfake tits: %s\npenis_length: %s\n career length: %s\ntattoos: %s\npiercings: %s\nhair_color: %s\nweight: %s\n description: %s\n"
                                                % (
                                                    res["name"],
                                                    res["gender"],
                                                    res["url"],
                                                    res["twitter"],
                                                    res["instagram"],
                                                    res["birthdate"],
                                                    res["ethnicity"],
                                                    res["country"],
                                                    res["eye_color"],
                                                    res["height"],
                                                    res["measurements"],
                                                    res["fake_tits"],
                                                    res["penis_length"],
                                                    res["career_length"],
                                                    res["tattoos"],
                                                    res["piercings"],
                                                    res["hair_color"],
                                                    res["weight"],
                                                    res["details"],
                                                ),
                                                "urls": [
                                                    u["url"],
                                                ],
                                                "performer_ids": [p["id"]],
                                                "tag_ids": [
                                                    tag_stashbox_performer_gallery
                                                ],
                                                "gallery_ids": [
                                                    index["galleries"][endpoint]
                                                ],
                                            }
                                            json.dump(image_data, f)
                                    filename = (
                                        Path(settings["path"])
                                        / p["id"]
                                        / (
                                            "%s-%s.jpg"
                                            % (
                                                scrapers[url]["id"],
                                                image_id,
                                            )
                                        )
                                    )
                                    if not filename.exists():
                                        if image.startswith("data:"):
                                            with open(filename, "wb") as f:
                                                f.write(
                                                    base64.b64decode(
                                                        image.split("base64,")[1]
                                                    )
                                                )
                                                f.close()
                                        else:
                                            with open(image_index, "w") as f:
                                                image_data = {
                                                    "title": "%s - %s "
                                                    % (
                                                        scrapers[url]["id"],
                                                        image_id,
                                                    ),
                                                    "details": "%s" % (res,),
                                                    "urls": [u["url"], image],
                                                    "performer_ids": [p["id"]],
                                                    "tag_ids": [
                                                        tag_stashbox_performer_gallery
                                                    ],
                                                    "gallery_ids": [
                                                        index["galleries"][endpoint]
                                                    ],
                                                }
                                                json.dump(image_data, f)
                                            filename = (
                                                Path(settings["path"])
                                                / p["id"]
                                                / ("%s.jpg" % (image_id,))
                                            )
                                            r = requests.get(img["url"])
                                            if r.status_code == 200:
                                                with open(filename, "wb") as f:
                                                    f.write(r.content)
                                                    f.close()
                                    image_id = image_id + 1

    #                log.debug('%s %s' % (url['url'],url['type'],))
    #                    stash.scraper
    #                    scrape=stash.scrape_performer_url(ur)

    else:
        log.error("endpoint %s not configured, skipping" % (endpoint,))


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


def processQueue():
    for id in settings["queue"].split(","):
        if len(id) > 0:
            p = stash.find_performer(id)
            processPerformer(p)
    # queue has not changed since we started, clear setting
    if (
        stash.get_configuration()["plugins"]["stashdb-performer-gallery"]
        == settings["queue"]
    ):
        stash.configure_plugin("stashdb-performer-gallery", {"queue": ""})
        stash.metadata_scan(paths=[settings["path"]])
        stash.run_plugin_task("stashdb-performer-gallery", "relink missing images")
    else:
        # update remove the completed entries from the queue string leaving the unprocessed and schedule the task again
        log.debug("updating queue")
        stash.configure_plugin(
            "stashdb-performer-gallery",
            {
                "queue": stash.get_configuration()["plugins"][
                    "stashdb-performer-gallery"
                ]["queue"].removeprefix(settings["queue"])
            },
        )
        stash.run_plugin_task(
            "stashdb-performer-gallery", "Process Performers", args={"full": False}
        )


def remove_tag_from_performer(performer_id):
    """Remove the [Stashbox Performer Gallery] tag from a performer after galleries are downloaded.

    This function retrieves the performer's current tags, removes the gallery tag,
    and updates the performer with the remaining tags.

    Args:
        performer_id: The ID of the performer to remove the tag from
    """
    try:
        performer = stash.find_performer(performer_id)
        if not performer:
            log.warning(f"Could not find performer {performer_id} to remove tag")
            return False

        current_tag_ids = [tag["id"] for tag in performer.get("tags", [])]

        # Check if the tag is present
        if tag_stashbox_performer_gallery not in current_tag_ids:
            log.debug(f"Performer {performer.get('name', performer_id)} doesn't have the gallery tag")
            return False

        # Remove the tag
        new_tag_ids = [tid for tid in current_tag_ids if tid != tag_stashbox_performer_gallery]

        stash.update_performer({
            "id": performer_id,
            "tag_ids": new_tag_ids
        })

        log.info(f"Removed [Stashbox Performer Gallery] tag from performer {performer.get('name', performer_id)}")
        return True
    except Exception as e:
        log.error(f"Error removing tag from performer {performer_id}: {e}")
        return False


def relink_images(performer_id=None, processed_performer_ids=None):
    """Relink images that are missing their gallery associations.

    POTENTIAL HANG CAUSES ANALYSIS:
    ================================
    1. INFINITE LOOP RISK: The pagination logic uses a counter `i` that increments
       for each image processed, but the query uses `i` as the page number. If
       `stash.find_images` returns images with pagination starting at page 0/1,
       after processing `per_page` images, `i` would be 100, then `filter={"page": 100, ...}`
       would skip pages 1-99, potentially causing issues or missing images.

       FIX: The pagination should increment page numbers correctly, not use the
       image counter as the page number.

    2. LARGE DATASET ISSUES: If there are many images missing galleries, the function
       fetches them all with no timeout or batch limiting, which could cause hangs
       on large libraries.

    3. NO REQUEST TIMEOUT: The `stash.find_images` calls have no timeout, so if the
       Stash server is slow or unresponsive, the function will hang indefinitely.

    4. FILE I/O BLOCKING: The `processImages` function opens and reads JSON files
       synchronously without timeouts, which could block if files are on slow storage
       or network mounts.

    5. COUNTER VS PAGE MISMATCH: `i` is used both as an image counter AND as a page
       number, but `per_page` is 100. After the first batch, `i=100` but `page` should
       be `1` or `2` (depending on 0/1-based pagination).

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

    # FIX: Use proper pagination with page numbers starting from 1
    page = 1
    processed = 0

    while processed < total:
        log.debug(f"Fetching page {page} (processed {processed}/{total})")
        images = stash.find_images(f=query, filter={"page": page, "per_page": per_page})

        # Safety check: if no images returned, break to avoid infinite loop
        if not images:
            log.warning(f"No images returned for page {page}, breaking loop")
            break

        for img in images:
            log.debug("image: %s" % (img,))
            processImages(img)
            processed += 1
            log.progress((processed / total))

        page += 1

        # Safety check: prevent runaway pagination
        if page > (total // per_page) + 10:
            log.warning(f"Pagination exceeded expected bounds (page {page}), breaking loop")
            break

    log.info(f"Completed relinking {processed} images")

    # FEATURE: Remove the tag from the performer(s) after galleries are downloaded
    if settings.get("removeTagAfterDownload", False):
        if performer_id:
            # Single performer mode - remove tag from the specific performer
            log.info(f"removeTagAfterDownload is enabled, removing tag from performer {performer_id}")
            remove_tag_from_performer(performer_id)
        elif processed_performer_ids:
            # Batch mode with explicit list - remove tag only from processed performers
            log.info(f"removeTagAfterDownload is enabled, removing tag from {len(processed_performer_ids)} processed performers")
            removed_count = 0
            for pid in processed_performer_ids:
                if remove_tag_from_performer(pid):
                    removed_count += 1
            log.info(f"Removed [Stashbox Performer Gallery] tag from {removed_count} performers")


json_input = json.loads(sys.stdin.read())

FRAGMENT_SERVER = json_input["server_connection"]
stash = StashInterface(FRAGMENT_SERVER)

config = stash.get_configuration()["plugins"]
settings = {
    "path": "/download_dir",
    "runPerformerScraper": False,
    "removeTagAfterDownload": False,  # NEW: Option to remove tag after galleries are downloaded
}
if "stashdb-performer-gallery" in config:
    settings.update(config["stashdb-performer-gallery"])
# log.info('config: %s ' % (settings,))


tag_stashbox_performer_gallery = stash.find_tag(
    "[Stashbox Performer Gallery]", create=True
).get("id")
tag_performer_image = stash.find_tag("[Set Profile Image]", create=True).get("id")

if "mode" in json_input["args"]:
    PLUGIN_ARGS = json_input["args"]["mode"]
    if "performer" in json_input["args"]:
        p = stash.find_performer(json_input["args"]["performer"])
        if tag_stashbox_performer_gallery in [x["id"] for x in p["tags"]]:
            processPerformer(p)
            stash.metadata_scan(paths=[settings["path"]])
            stash.run_plugin_task(
                "stashdb-performer-gallery",
                "relink missing images",
                args={"mode": "processImages", "performer_id": p["id"]},
            )
    elif "processPerformers" in PLUGIN_ARGS:
        processed_ids = processPerformers()
        stash.metadata_scan([settings["path"]])
        stash.run_plugin_task(
            "stashdb-performer-gallery", "relink missing images", args={"mode": "processImages", "processed_performer_ids": ",".join(str(pid) for pid in processed_ids)}
        )
    elif "processImages" in PLUGIN_ARGS:
        if "performer_id" in json_input["args"]:
            relink_images(performer_id=json_input["args"]["performer_id"])
        elif "processed_performer_ids" in json_input["args"]:
            # Batch mode - parse comma-separated performer IDs
            ids_str = json_input["args"]["processed_performer_ids"]
            processed_ids = [pid.strip() for pid in ids_str.split(",") if pid.strip()]
            relink_images(processed_performer_ids=processed_ids)
        else:
            relink_images()


elif "hookContext" in json_input["args"]:
    id = json_input["args"]["hookContext"]["id"]
    if json_input["args"]["hookContext"]["type"] == "Image.Create.Post":
        img = stash.find_image(image_in=id, fragment=FRAGMENT_IMAGE)
        processImages(img)
    if json_input["args"]["hookContext"]["type"] == "Image.Update.Post":
        img = stash.find_image(image_in=id, fragment=FRAGMENT_IMAGE)
        if tag_performer_image in [x["id"] for x in img["tags"]]:
            setPerformerPicture(img)
    if json_input["args"]["hookContext"]["type"] == "Performer.Update.Post":
        stash.run_plugin_task(
            "stashdb-performer-gallery", "Process Performers", args={"mode": "processPerformers", "performer": id}
        )
