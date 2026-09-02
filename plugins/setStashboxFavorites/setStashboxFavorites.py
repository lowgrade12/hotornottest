# Set Stashbox Favorites Plugin
#
# Inspired by stashSetStashboxFavoritePerformers
# Original: https://github.com/lowgrade12/hotornottest/tree/main/plugins/stashSetStashboxFavoritePerformers
#

import json
import log
import sys
import ssl
import urllib.request
import urllib.error
from favorite_performers_sync import (
    set_stashbox_favorite_performers, set_stashbox_favorite_performer,
    set_stashbox_favorite_studios, set_stashbox_favorite_studio
)

# Create SSL context that doesn't verify certificates (for self-signed certs)
SSL_CONTEXT = ssl.create_default_context()
SSL_CONTEXT.check_hostname = False
SSL_CONTEXT.verify_mode = ssl.CERT_NONE

STASHDB_ENDPOINT = 'https://stashdb.org/graphql'

# Gallery tag configuration - always add this tag when performer is favorited
GALLERY_TAG_ID = "4623"
GALLERY_TAG_NAME = "[Stashbox Performer Gallery]"

json_input = json.loads(sys.stdin.read())
args = json_input.get('args', {})
name = args.get('name')
hook_context = args.get('hookContext')
server_connection = json_input.get("server_connection", {})


def get_stash_url():
    """Build the Stash GraphQL URL from server connection info."""
    host = server_connection.get("Host", "localhost")
    if host == "0.0.0.0":
        host = "localhost"
    return f"{server_connection.get('Scheme', 'http')}://{host}:{server_connection.get('Port', 9999)}/graphql"


def stash_graphql(query, variables=None):
    """Make a GraphQL request to local Stash instance."""
    url = get_stash_url()
    
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    
    # Use session cookie if available
    session_cookie = server_connection.get("SessionCookie", {}).get("Value")
    if session_cookie:
        headers["Cookie"] = f"session={session_cookie}"
    
    data = json.dumps({
        "query": query,
        "variables": variables or {}
    }).encode("utf-8")
    
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    
    try:
        with urllib.request.urlopen(req, timeout=30, context=SSL_CONTEXT) as response:
            result = json.loads(response.read().decode("utf-8"))
            if result.get("errors"):
                log.warning(f"Stash GraphQL errors: {result['errors']}")
            return result.get("data")
    except Exception as e:
        log.error(f"Stash request error: {e}")
        return None

def get_stashboxes():
    """Get configured stashboxes from Stash"""
    result = stash_graphql("""query Configuration { configuration { general { stashBoxes { endpoint api_key } } } }""")
    if not result:
        return []
    return result.get("configuration", {}).get("general", {}).get("stashBoxes", [])


def get_stashdb_credentials(endpoint, api_key):
    """Get endpoint and api_key for stashdb.org, fetching from config if not provided.
    
    Args:
        endpoint: Provided endpoint or None/empty
        api_key: Provided api_key or None/empty
    
    Returns:
        Tuple of (endpoint, api_key) or (None, None) if not configured
    """
    if endpoint and api_key:
        return endpoint, api_key
    
    stashboxes = get_stashboxes()
    stashbox_map = {sb.get('endpoint'): sb.get('api_key') for sb in stashboxes if sb.get('endpoint') and sb.get('api_key')}
    if STASHDB_ENDPOINT in stashbox_map:
        return STASHDB_ENDPOINT, stashbox_map[STASHDB_ENDPOINT]
    
    log.error(f"No stashdb.org endpoint configured in Stash. Please configure a stash-box with endpoint {STASHDB_ENDPOINT}")
    return None, None

def get_performer(performer_id):
    """Get performer details including stash_ids, favorite status, and tags"""
    result = stash_graphql("""
        query FindPerformer($id: ID!) {
            findPerformer(id: $id) {
                id
                name
                favorite
                stash_ids {
                    endpoint
                    stash_id
                }
                tags {
                    id
                    name
                }
            }
        }
    """, {"id": performer_id})
    if not result:
        return None
    return result.get("findPerformer")

def get_studio(studio_id):
    """Get studio details including stash_ids and favorite status"""
    result = stash_graphql("""
        query FindStudio($id: ID!) {
            findStudio(id: $id) {
                id
                name
                favorite
                stash_ids {
                    endpoint
                    stash_id
                }
            }
        }
    """, {"id": studio_id})
    if not result:
        return None
    return result.get("findStudio")

def get_plugin_settings():
    """Get plugin settings from Stash configuration"""
    result = stash_graphql("""query Configuration { configuration { plugins } }""")
    if not result:
        return {}
    return result.get('configuration', {}).get('plugins', {}).get('setStashboxFavorites', {})


def get_or_create_tag(tag_name):
    """Get or create a tag by name.
    
    Args:
        tag_name: Name of the tag to find or create
        
    Returns:
        Tag dict with id and name, or None
    """
    # First try to find the tag
    find_query = """
    query FindTag($name: String!) {
        findTags(tag_filter: { name: { value: $name, modifier: EQUALS } }) {
            tags {
                id
                name
            }
        }
    }
    """
    
    data = stash_graphql(find_query, {"name": tag_name})
    if data and "findTags" in data:
        tags = data["findTags"].get("tags", [])
        if tags:
            return tags[0]
    
    # Create the tag if it doesn't exist
    create_query = """
    mutation TagCreate($input: TagCreateInput!) {
        tagCreate(input: $input) {
            id
            name
        }
    }
    """
    
    log.info(f'Tag "{tag_name}" missing. Creating...')
    data = stash_graphql(create_query, {
        "input": {
            "name": tag_name,
            "description": "Tag created by Set Stashbox Favorites plugin. Marks performers that have been favorited for gallery viewing."
        }
    })
    
    if data and "tagCreate" in data:
        return data["tagCreate"]
    
    log.error(f"Failed to create tag: {tag_name}")
    return None


def get_tag_by_id(tag_id):
    """Get a tag by its ID.
    
    Args:
        tag_id: ID of the tag to find
        
    Returns:
        Tag dict with id and name, or None if not found
    """
    find_query = """
    query FindTag($id: ID!) {
        findTag(id: $id) {
            id
            name
        }
    }
    """
    
    data = stash_graphql(find_query, {"id": tag_id})
    if data and "findTag" in data:
        return data["findTag"]
    return None


def get_gallery_tag():
    """Get the gallery tag, preferring by ID, falling back to name lookup/creation.
    
    Returns:
        Tag dict with id and name, or None if not found/created
    """
    # First try to find by the configured ID
    tag = get_tag_by_id(GALLERY_TAG_ID)
    if tag:
        return tag
    
    # Fall back to finding/creating by name
    log.debug(f'Gallery tag ID {GALLERY_TAG_ID} not found, looking up by name "{GALLERY_TAG_NAME}"')
    return get_or_create_tag(GALLERY_TAG_NAME)


def add_tag_to_performer(performer_id, performer_name, current_tags, tag_id, tag_name):
    """Add a tag to a performer if not already present.
    
    Args:
        performer_id: The local Stash performer ID
        performer_name: The performer's name (for logging)
        current_tags: List of current tag dicts with 'id' keys
        tag_id: The ID of the tag to add
        tag_name: The name of the tag (for logging)
    
    Returns:
        True if tag was added, False if already present or failed
    """
    current_tag_ids = [tag["id"] for tag in current_tags]
    
    # Check if already tagged
    if tag_id in current_tag_ids:
        log.debug(f'Performer {performer_name} already has tag "{tag_name}"')
        return False
    
    # Add the tag using performerUpdate mutation
    update_query = """
    mutation PerformerUpdate($input: PerformerUpdateInput!) {
        performerUpdate(input: $input) {
            id
        }
    }
    """
    
    new_tag_ids = current_tag_ids + [tag_id]
    
    data = stash_graphql(update_query, {
        "input": {
            "id": performer_id,
            "tag_ids": new_tag_ids
        }
    })
    
    if data:
        log.info(f'Added tag "{tag_name}" to performer {performer_name}')
        return True
    else:
        log.warning(f'Failed to add tag "{tag_name}" to performer {performer_name}')
        return False

plugin_settings = get_plugin_settings()
tag_errors = plugin_settings.get('tagErrors', False)
tag_name = plugin_settings.get('tagName')
gallery_tag_enabled = plugin_settings.get('galleryTagEnabled', False)
gallery_tag_name = plugin_settings.get('galleryTagName', '[Stashbox Performer Gallery]')

# Handle hook context (triggered by Performer.Update.Post or Studio.Update.Post)
if hook_context:
    hook_type = hook_context.get('type')
    entity_id = hook_context.get('id')
    
    if hook_type == 'Studio.Update.Post' and entity_id:
        log.debug(f"Hook triggered for studio ID: {entity_id}")
        studio = get_studio(entity_id)
        if studio and studio.get('stash_ids'):
            stashboxes = get_stashboxes()
            stashbox_map = {sb.get('endpoint'): sb.get('api_key') for sb in stashboxes if sb.get('endpoint') and sb.get('api_key')}
            
            for stash_id_entry in studio['stash_ids']:
                endpoint = stash_id_entry.get('endpoint')
                stash_id = stash_id_entry.get('stash_id')
                
                # Only sync with stashdb.org
                if endpoint != STASHDB_ENDPOINT:
                    continue
                
                api_key = stashbox_map.get(endpoint)
                if not api_key:
                    log.warning(f"No API key found for endpoint: {endpoint}")
                    continue
                
                favorite = studio.get('favorite', False)
                log.info(f"Syncing studio {studio.get('name')} (stash_id={stash_id}) favorite={favorite} to {endpoint}")
                set_stashbox_favorite_studio(endpoint, api_key, stash_id, favorite)
        else:
            log.debug(f"Studio {entity_id} has no stash_ids, skipping")
    elif hook_type == 'Performer.Update.Post' and entity_id:
        log.debug(f"Hook triggered for performer ID: {entity_id}")
        performer = get_performer(entity_id)
        favorite = performer.get('favorite', False) if performer else False
        
        # Add gallery tag to performer when favorited. The tag is left in place (not
        # removed once galleries are downloaded) so the stashdb-performer-gallery
        # plugin keeps picking up this performer and downloads any new images added later.
        # Only do this when the galleryTagEnabled setting is turned on.
        if performer and favorite and gallery_tag_enabled:
            gallery_tag = get_gallery_tag()
            if gallery_tag:
                add_tag_to_performer(
                    performer['id'],
                    performer.get('name', 'Unknown'),
                    performer.get('tags', []),
                    gallery_tag['id'],
                    gallery_tag.get('name', GALLERY_TAG_NAME)
                )
        
        if performer and performer.get('stash_ids'):
            stashboxes = get_stashboxes()
            stashbox_map = {sb.get('endpoint'): sb.get('api_key') for sb in stashboxes if sb.get('endpoint') and sb.get('api_key')}
            
            for stash_id_entry in performer['stash_ids']:
                endpoint = stash_id_entry.get('endpoint')
                stash_id = stash_id_entry.get('stash_id')
                
                # Only sync with stashdb.org
                if endpoint != STASHDB_ENDPOINT:
                    continue
                
                api_key = stashbox_map.get(endpoint)
                if not api_key:
                    log.warning(f"No API key found for endpoint: {endpoint}")
                    continue
                
                log.info(f"Syncing performer {performer.get('name')} (stash_id={stash_id}) favorite={favorite} to {endpoint}")
                set_stashbox_favorite_performer(endpoint, api_key, stash_id, favorite)
        else:
            log.debug(f"Performer {entity_id} has no stash_ids, skipping")
    else:
        log.debug(f"Unhandled hook type or no entity ID: type={hook_type}, id={entity_id}")

# Handle task execution (triggered manually)
elif name == 'favorite_performers_sync':
    endpoint, api_key = get_stashdb_credentials(args.get('endpoint'), args.get('api_key'))
    if endpoint and api_key:
        set_stashbox_favorite_performers(server_connection, endpoint, api_key, tag_errors, tag_name)
elif name == 'favorite_studios_sync':
    endpoint, api_key = get_stashdb_credentials(args.get('endpoint'), args.get('api_key'))
    if endpoint and api_key:
        set_stashbox_favorite_studios(server_connection, endpoint, api_key, tag_errors, tag_name)
