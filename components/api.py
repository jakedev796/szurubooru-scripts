#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API client module for Szurubooru
Handles all interactions with the Szurubooru API
"""

import asyncio
import aiohttp
import aiofiles
import json
import logging
from pathlib import Path
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

# ANSI color codes for terminal output
YELLOW = '\033[93m'
RESET = '\033[0m'


class SzurubooruAPI:
    """API client for Szurubooru"""
    
    # Class-level tag cache to avoid recreating existing tags
    _tag_cache = set()
    _cache_file = Path("processed_tags.txt")
    
    def __init__(self, config):
        self.config = config
        self.session = None
        # Use API token if available, otherwise fall back to password
        if config.api_token:
            self.auth = aiohttp.BasicAuth(config.username, config.api_token)
        elif config.password:
            self.auth = aiohttp.BasicAuth(config.username, config.password)
        else:
            raise ValueError("Either api_token or password must be provided")
        
        # Load tag cache on initialization
        self._load_tag_cache()
        
    async def __aenter__(self):
        headers = {'Accept': 'application/json'}
        
        # If using API token, add the Token authorization header
        if self.config.api_token:
            import base64
            token_auth = base64.b64encode(f"{self.config.username}:{self.config.api_token}".encode()).decode()
            headers['Authorization'] = f'Token {token_auth}'
            # Don't use BasicAuth for token authentication
            self.session = aiohttp.ClientSession(headers=headers)
        else:
            # Use BasicAuth for password authentication
            self.session = aiohttp.ClientSession(auth=self.auth, headers=headers)
        
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
        # Save tag cache on exit
        self._save_tag_cache()
    
    def _load_tag_cache(self):
        """Load tag cache from file"""
        try:
            if self._cache_file.exists():
                with open(self._cache_file, 'r', encoding='utf-8') as f:
                    SzurubooruAPI._tag_cache = {line.strip() for line in f if line.strip()}
                logger.info(f"Loaded {len(SzurubooruAPI._tag_cache)} tags from cache")
            else:
                logger.info("No existing tag cache found, starting fresh")
                SzurubooruAPI._tag_cache = set()
        except Exception as e:
            logger.warning(f"Failed to load tag cache: {e}")
            SzurubooruAPI._tag_cache = set()
    
    def _save_tag_cache(self):
        """Save tag cache to file"""
        try:
            with open(self._cache_file, 'w', encoding='utf-8') as f:
                for tag in sorted(SzurubooruAPI._tag_cache):
                    f.write(f"{tag}\n")
            logger.info(f"Saved {len(SzurubooruAPI._tag_cache)} tags to cache")
        except Exception as e:
            logger.warning(f"Failed to save tag cache: {e}")
    
    async def test_connection(self) -> bool:
        """Test connection to Szurubooru with timeout and detailed diagnostics"""
        try:
            # Add timeout to prevent hanging
            timeout = aiohttp.ClientTimeout(total=10.0)
            
            async with self.session.get(
                f"{self.config.szurubooru_url}/api/info",
                timeout=timeout
            ) as response:
                print(f"Response Status: {response.status}")
                
                if response.status == 200:
                    # Try to parse response to verify it's a valid Szurubooru instance
                    try:
                        data = await response.json()
                        if 'serverTime' in data or 'config' in data:
                            print(f"Szurubooru server detected (version info available)")
                            return True
                        else:
                            print("Server responded but doesn't appear to be Szurubooru")
                            return False
                    except:
                        print("Server responded but returned invalid JSON")
                        return False
                elif response.status == 401:
                    print("Authentication failed - check your username/API token")
                    return False
                elif response.status == 403:
                    print("Access forbidden - check API token permissions")
                    return False
                elif response.status == 404:
                    print("API endpoint not found - verify server URL")
                    return False
                else:
                    print(f"Server error: HTTP {response.status}")
                    return False
                    
        except asyncio.TimeoutError:
            print("Connection timeout - server is not responding")
            return False
        except aiohttp.ClientConnectorError as e:
            print(f"Connection failed: {e}")
            print("Check if server is running and URL is correct")
            return False
        except Exception as e:
            print(f"Unexpected error: {e}")
            return False
    
    async def upload_post(self, file_path: Path, tags: List[str] = None, safety: str = "unsafe") -> Optional[Dict]:
        """Upload a post to Szurubooru"""
        if tags is None:
            tags = [self.config.tagme_tag]
        
        try:
            # Prepare the upload data
            data = aiohttp.FormData()
            data.add_field('metadata', json.dumps({
                'tags': tags,
                'safety': safety
            }), content_type='application/json')
            
            # Add the file
            async with aiofiles.open(file_path, 'rb') as f:
                content = await f.read()
                data.add_field('content', content, filename=file_path.name)
            
            # Upload the post
            async with self.session.post(
                f"{self.config.szurubooru_url}/api/posts/",
                data=data
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result
                else:
                    error_text = await response.text()
                    # Return error info for better handling
                    return {"error": error_text, "status": response.status}
                    
        except Exception as e:
            return {"error": str(e), "status": 0}
    
    async def get_posts_with_tag(self, tag: str, limit: int = 100) -> List[Dict]:
        """Get posts that have a specific tag"""
        try:
            params = {
                'query': f'tag:{tag}',
                'limit': limit
            }
            
            async with self.session.get(
                f"{self.config.szurubooru_url}/api/posts/",
                params=params
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get('results', [])
                else:
                    return []
                    
        except Exception:
            return []
    
    async def get_untagged_posts(self, limit: int = 100) -> List[Dict]:
        """Get posts that have no tags (tag-count:0)"""
        try:
            params = {
                'query': 'tag-count:0',
                'limit': limit
            }
            
            async with self.session.get(
                f"{self.config.szurubooru_url}/api/posts/",
                params=params
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get('results', [])
                else:
                    return []
                    
        except Exception:
            return []
    
    async def get_video_posts(self, limit: int = 100) -> List[Dict]:
        """Get posts that are videos (type:video)"""
        try:
            params = {
                'query': 'type:video',
                'limit': limit
            }
            
            async with self.session.get(
                f"{self.config.szurubooru_url}/api/posts/",
                params=params
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get('results', [])
                else:
                    return []
                    
        except Exception:
            return []
    
    async def get_all_posts(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        """Get all posts with pagination"""
        try:
            params = {
                'limit': limit,
                'offset': offset
            }
            
            async with self.session.get(
                f"{self.config.szurubooru_url}/api/posts/",
                params=params
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get('results', [])
                else:
                    text = await response.text()
                    error_msg = f"Failed to get all posts (offset={offset}, limit={limit}): HTTP {response.status} - {text}"
                    logger.error(error_msg)
                    print(f"{YELLOW}[WARNING]{RESET} {error_msg}")
                    return []
                    
        except Exception as e:
            error_msg = f"Exception in get_all_posts(offset={offset}, limit={limit}): {e}"
            logger.error(error_msg)
            print(f"{YELLOW}[WARNING]{RESET} {error_msg}")
            return []
    
    async def get_posts_by_id_range(self, start_id: int, end_id: int, limit: int = 100, offset: int = 0) -> List[Dict]:
        """Get posts within a specific ID range using Szurubooru query syntax"""
        try:
            params = {
                'query': f'id:{start_id}..{end_id}',
                'limit': limit,
                'offset': offset
            }
            
            async with self.session.get(
                f"{self.config.szurubooru_url}/api/posts/",
                params=params
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get('results', [])
                else:
                    text = await response.text()
                    error_msg = f"Failed to get posts by ID range {start_id}..{end_id} (offset={offset}): HTTP {response.status} - {text}"
                    logger.error(error_msg)
                    print(f"{YELLOW}[WARNING]{RESET} {error_msg}")
                    return []
                    
        except Exception as e:
            error_msg = f"Exception in get_posts_by_id_range({start_id}..{end_id}, offset={offset}): {e}"
            logger.error(error_msg)
            print(f"{YELLOW}[WARNING]{RESET} {error_msg}")
            return []
    
    async def get_post_count_in_range(self, start_id: int, end_id: int) -> int:
        """Get the actual count of posts within a specific ID range"""
        try:
            params = {
                'query': f'id:{start_id}..{end_id}',
                'limit': 1,
                'offset': 0
            }
            
            async with self.session.get(
                f"{self.config.szurubooru_url}/api/posts/",
                params=params
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get('total', 0)
                else:
                    text = await response.text()
                    logger.error(f"Failed to get post count in range {start_id}..{end_id}: HTTP {response.status} - {text}")
                    return 0
                    
        except Exception as e:
            logger.error(f"Exception in get_post_count_in_range({start_id}..{end_id}): {e}")
            return 0
    
    async def get_total_post_count(self) -> int:
        """Get total number of posts in the instance"""
        try:
            params = {
                'limit': 1,
                'offset': 0
            }
            
            async with self.session.get(
                f"{self.config.szurubooru_url}/api/posts/",
                params=params
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get('total', 0)
                else:
                    return 0
                    
        except Exception:
            return 0
    
    async def update_post_tags(self, post_id: int, tags: List[str], version: int) -> bool:
        """Update tags for a post"""
        try:
            data = {
                'version': version,
                'tags': tags
            }
            
            async with self.session.put(
                f"{self.config.szurubooru_url}/api/post/{post_id}",
                json=data
            ) as response:
                if response.status == 200:
                    return True
                else:
                    error_text = await response.text()
                    return False
                    
        except Exception:
            return False
    
    async def create_tag_with_category(self, tag_name: str, category: str = "default") -> bool:
        """Create a tag with a specific category"""
        # Check if tag is already in cache
        cache_key = f"{tag_name}:{category}"
        if cache_key in SzurubooruAPI._tag_cache:
            return True
            
        try:
            data = {
                "names": [tag_name],
                "category": category
            }
            
            async with self.session.post(
                f"{self.config.szurubooru_url}/api/tags",
                json=data
            ) as response:
                if response.status == 200:
                    # Add to cache on successful creation
                    SzurubooruAPI._tag_cache.add(cache_key)
                    # Periodically save cache to avoid losing progress
                    if len(SzurubooruAPI._tag_cache) % 50 == 0:
                        self._save_tag_cache()
                    return True
                elif response.status == 409:
                    # Tag already exists - add to cache and return True (no need to update category)
                    SzurubooruAPI._tag_cache.add(cache_key)
                    # Periodically save cache to avoid losing progress
                    if len(SzurubooruAPI._tag_cache) % 50 == 0:
                        self._save_tag_cache()
                    logger.debug(f"Tag {tag_name} already exists, added to cache (total: {len(SzurubooruAPI._tag_cache)})")
                    return True
                else:
                    error_text = await response.text()
                    # Check if it's a TagAlreadyExistsError in the response body
                    if "TagAlreadyExistsError" in error_text:
                        # Tag already exists - add to cache and return True
                        SzurubooruAPI._tag_cache.add(cache_key)
                        # Periodically save cache to avoid losing progress
                        if len(SzurubooruAPI._tag_cache) % 50 == 0:
                            self._save_tag_cache()
                        logger.debug(f"Tag {tag_name} already exists (detected in response body), added to cache (total: {len(SzurubooruAPI._tag_cache)})")
                        return True
                    else:
                        logger.warning(f"Failed to create tag {tag_name} with category {category}: {error_text}")
                        return False
                    
        except Exception as e:
            logger.warning(f"Exception creating tag {tag_name} with category {category}: {e}")
            return False
    
    async def update_tag_category(self, tag_name: str, category: str) -> bool:
        """Update an existing tag's category"""
        try:
            # First get the current tag to get its version
            async with self.session.get(
                f"{self.config.szurubooru_url}/api/tag/{tag_name}"
            ) as response:
                if response.status != 200:
                    return False
                
                tag_data = await response.json()
                version = tag_data.get('version', 1)
            
            # Update the tag with the new category
            data = {
                "version": version,
                "category": category
            }
            
            async with self.session.put(
                f"{self.config.szurubooru_url}/api/tag/{tag_name}",
                json=data
            ) as response:
                return response.status == 200
                
        except Exception as e:
            logger.warning(f"Exception updating tag {tag_name} category to {category}: {e}")
            return False

    def determine_tag_category(self, tag_name: str) -> str:
        """Determine the appropriate category for a tag based on its name and context"""
        tag_lower = tag_name.lower()
        
        # Meta tags
        meta_tags = {
            'tagme', 'video', 'animated', 'animation', 'gif', 'webm', 'mp4', 
            'image', 'photo', 'screenshot', 'artwork', 'fanart', 'original',
            'translated', 'translation', 'english', 'japanese', 'chinese',
            'colored', 'black_and_white', 'monochrome', 'colorized',
            'high_res', 'low_res', 'hd', '4k', '1080p', '720p',
            'nsfw', 'sfw', 'safe', 'unsafe', 'sketchy',
            'meme', 'shitpost', 'quality', 'best', 'worst'
        }
        
        if tag_lower in meta_tags:
            return "meta"
        
        # Default for everything else
        return "default"

