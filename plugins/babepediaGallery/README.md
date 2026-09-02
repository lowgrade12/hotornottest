# Babepedia Gallery

Automatically download performer images scraped from [Babepedia](https://www.babepedia.com). Add the `[Stashbox Performer Gallery]` tag to a performer and it will create a gallery of images scraped from that performer's Babepedia page. This is the same tag used by the [stashdb-performer-gallery](../stashdb-performer-gallery) plugin, so tagging a performer once triggers both plugins.

## Features

- **Automatic gallery creation**: Creates a gallery for tagged performers using images scraped from the performer's Babepedia page
- **URL resolution**: Prefers a Babepedia URL already stored on the performer, otherwise guesses the profile URL from the performer's name, falling back to Babepedia's own search endpoint
- **Profile image setting**: Apply the `[Set Profile Image]` tag to an image to set it as the profile image of that performer
- **Tag removal**: Optionally remove the gallery tag from performers after their galleries are downloaded

## Setup

1. Configure the download path in plugin settings
2. Add the configured path as a library path in Stash settings
3. Tag performers with `[Stashbox Performer Gallery]` to trigger gallery creation

## Settings

| Setting | Description |
|---------|-------------|
| **Download parent folder** | Location for downloaded files. Must be a different folder from stash and covered by a library path. |
| **Remove tag after galleries downloaded** | When enabled, removes the `[Stashbox Performer Gallery]` tag from performers after their galleries have been downloaded and linked. |

## Tasks

- **Process Performers**: Fetch performer images from Babepedia for all performers with the `[Stashbox Performer Gallery]` tag
- **relink missing images**: Reprocess images that are missing gallery associations

## Hooks

- **Performer.Update.Post**: Triggers gallery download when a performer is updated with the gallery tag
- **Image.Create.Post**: Processes newly created images
- **Image.Update.Post**: Sets profile image when the `[Set Profile Image]` tag is applied

## How it works

1. For a tagged performer, the plugin resolves a Babepedia profile URL:
   - Uses an existing `babepedia.com/babe/...` URL stored on the performer, if present
   - Otherwise guesses `https://www.babepedia.com/babe/<Name_With_Underscores>` and verifies the page exists
   - Otherwise falls back to Babepedia's `ajax-search.php` endpoint and picks the best name match
2. The performer's Babepedia page is fetched and parsed for full-size image URLs from the main profile gallery and any user-submitted uploads.
3. Images are downloaded to `<download path>/<performer id>/` alongside a small `.json` index file per image (used to link tags/gallery/performer once Stash discovers the file via a library scan).
4. A gallery tagged `[Stashbox Performer Gallery]` and linked to the performer is created (or reused if one already exists) to hold the downloaded images.

## Notes

- Babepedia's HTML structure isn't officially documented and may change over time; if image discovery stops working, the `extract_images`/`extract_name` parsing in `babepediaGallery.py` may need updating to match the current markup.
- Requests to babepedia.com are throttled and sent with a browser-like `User-Agent` to be a considerate scraper.
- Respect Babepedia's terms of service when using this plugin.

## Credits

Structured after the [stashdb-performer-gallery](../stashdb-performer-gallery) plugin in this repository. Babepedia HTML container names (`profbox2`, `useruploads2`) and the `ajax-search.php` search endpoint are referenced from the [Babepedia scraper](https://github.com/stashapp/CommunityScrapers/blob/main/scrapers/Babepedia/Babepedia.py) in stashapp/CommunityScrapers.
