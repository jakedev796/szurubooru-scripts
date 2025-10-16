#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Szurubooru Tag Synchronization Manager
A script to sync tags from a CSV file with Szurubooru, updating categories and aliases.

Features:
- Read tag data from CSV file
- Fetch existing tags from Szurubooru
- Update tag categories based on CSV mapping
- Create missing tags with proper categories
- Associate aliases for tags
- Handle category mapping (0=default, 3=copyright, 4=character)
"""

import os
import sys
import json
import csv
import logging
import argparse
import asyncio
import aiohttp
from pathlib import Path
from typing import List, Dict, Optional, Set, Tuple
from dataclasses import dataclass
from tqdm import tqdm

# Configure file logging (console handler added later in main when args are known)
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
if not logger.handlers:
    file_handler = logging.FileHandler('tag_sync_manager.log', encoding='utf-8')
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(file_handler)

@dataclass
class Config:
    """Configuration class for tag sync manager"""
    szurubooru_url: str
    username: str
    api_token: str = None
    password: str = None  # Fallback for backward compatibility
    csv_file: str = "danbooru_tags.csv"
    batch_size: int = 100
    dry_run: bool = False
    sample_mode: bool = False  # Process only one batch for testing
    create_missing_tags: bool = True
    update_categories: bool = True
    update_aliases: bool = True
    filter_foreign_aliases: bool = True  # Filter out non-Latin aliases
    cleanup_suggestions: bool = True  # Remove any existing suggestions (incorrectly added aliases)
    cleanup_unused_tags: bool = False  # Delete tags with 0 usage (orphaned from incorrect suggestions)

@dataclass
class CSVTag:
    """Represents a tag from the CSV file"""
    name: str
    category: int
    count: int
    aliases: List[str]

@dataclass
class SzurubooruTag:
    """Represents a tag from Szurubooru"""
    name: str
    category: str
    version: int
    aliases: List[str]

class SzurubooruAPI:
    """API client for Szurubooru tag operations"""
    
    def __init__(self, config: Config):
        self.config = config
        self.session = None
        # Use API token if available, otherwise fall back to password
        if config.api_token:
            self.auth = aiohttp.BasicAuth(config.username, config.api_token)
        elif config.password:
            self.auth = aiohttp.BasicAuth(config.username, config.password)
        else:
            raise ValueError("Either api_token or password must be provided")
        
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
    
    async def test_connection(self) -> bool:
        """Test connection to Szurubooru"""
        try:
            timeout = aiohttp.ClientTimeout(total=10.0)
            
            async with self.session.get(
                f"{self.config.szurubooru_url}/api/info",
                timeout=timeout
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    if 'serverTime' in data or 'config' in data:
                        print(f"[SUCCESS] Connected to Szurubooru server")
                        return True
                    else:
                        print("[ERROR] Server responded but doesn't appear to be Szurubooru")
                        return False
                else:
                    print(f"[ERROR] Server error: HTTP {response.status}")
                    return False
                    
        except Exception as e:
            print(f"[ERROR] Connection failed: {e}")
            return False
    
    async def get_all_tags(self, limit: int = 100, offset: int = 0) -> List[Dict]:
        """Get all tags with pagination"""
        try:
            params = {
                'limit': limit,
                'offset': offset
            }
            
            async with self.session.get(
                f"{self.config.szurubooru_url}/api/tags/",
                params=params
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get('results', [])
                else:
                    text = await response.text()
                    logger.error(f"Failed to get tags: HTTP {response.status} - {text}")
                    return []
                    
        except Exception as e:
            logger.error(f"Failed to get tags: {e}")
            return []
    
    async def get_tag(self, tag_name: str) -> Optional[Dict]:
        """Get a specific tag by name"""
        try:
            # URL encode the tag name to handle special characters
            import urllib.parse
            encoded_tag_name = urllib.parse.quote(tag_name, safe='')
            
            async with self.session.get(
                f"{self.config.szurubooru_url}/api/tag/{encoded_tag_name}"
            ) as response:
                if response.status == 200:
                    return await response.json()
                elif response.status == 404:
                    # Don't log 404s as errors for invalid tag names
                    logger.debug(f"Tag '{tag_name}' not found (404)")
                    return None
                else:
                    text = await response.text()
                    logger.error(f"Failed to get tag '{tag_name}': HTTP {response.status} - {text}")
                    return None
                    
        except Exception as e:
            logger.error(f"Failed to get tag {tag_name}: {e}")
            return None
    
    async def create_tag(self, tag_name: str, category: str = "default", aliases: List[str] = None) -> bool:
        """Create a new tag"""
        try:
            # Combine tag name with aliases in names array
            names = [tag_name]
            if aliases:
                names.extend(aliases)
            
            data = {
                "names": names,
                "category": category
            }
            
            async with self.session.post(
                f"{self.config.szurubooru_url}/api/tags",
                json=data
            ) as response:
                if response.status == 200:
                    return True
                elif response.status == 409:  # Tag already exists
                    logger.info(f"Tag {tag_name} already exists")
                    return True
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to create tag '{tag_name}' (category={category}, aliases={len(aliases or [])}): HTTP {response.status} - {error_text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Exception creating tag {tag_name}: {e}")
            return False
    
    async def update_tag(self, tag_name: str, category: str = None, aliases: List[str] = None, cleanup_suggestions: bool = False) -> bool:
        """Update an existing tag"""
        try:
            # First get the current tag to get its version
            tag_data = await self.get_tag(tag_name)
            if not tag_data:
                logger.warning(f"Tag {tag_name} not found for update")
                return False
            
            version = tag_data.get('version', 1)
            current_category = tag_data.get('category', 'default')
            current_names = tag_data.get('names', [])
            current_suggestions = tag_data.get('suggestions', [])
            
            # Prepare update data
            update_data = {
                "version": version
            }
            
            # Update category if different
            if category and category != current_category:
                update_data["category"] = category
            
            # Update aliases if different
            if aliases is not None:
                # Combine tag name with new aliases
                new_names = [tag_name]
                if aliases:
                    new_names.extend(aliases)
                
                # Convert to sets for comparison (excluding the main tag name)
                current_aliases_set = set(current_names[1:]) if len(current_names) > 1 else set()
                new_aliases_set = set(aliases)
                
                if current_aliases_set != new_aliases_set:
                    update_data["names"] = new_names
            
            # Clean up suggestions if requested and they exist
            if cleanup_suggestions and current_suggestions:
                update_data["suggestions"] = []
            
            # Only update if there are changes
            if len(update_data) <= 1:  # Only version
                logger.info(f"Tag {tag_name} already up to date")
                return True
            
            # URL encode the tag name for the API call
            import urllib.parse
            encoded_tag_name = urllib.parse.quote(tag_name, safe='')
            
            async with self.session.put(
                f"{self.config.szurubooru_url}/api/tag/{encoded_tag_name}",
                json=update_data
            ) as response:
                if response.status == 200:
                    return True
                elif response.status == 409:  # Version conflict
                    # Retry with incremented version
                    update_data["version"] = version + 1
                    async with self.session.put(
                        f"{self.config.szurubooru_url}/api/tag/{tag_name}",
                        json=update_data
                    ) as retry_response:
                        if retry_response.status == 200:
                            return True
                        retry_text = await retry_response.text()
                        logger.error(f"Failed to update tag '{tag_name}' after retry. Payload={update_data} HTTP {retry_response.status} - {retry_text}")
                        return False
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to update tag '{tag_name}'. Payload={update_data} HTTP {response.status} - {error_text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Exception updating tag {tag_name}: {e}")
            return False
    
    async def delete_tag(self, tag_name: str) -> bool:
        """Delete a tag"""
        try:
            # First get the current tag to get its version
            tag_data = await self.get_tag(tag_name)
            if not tag_data:
                logger.warning(f"Tag {tag_name} not found for deletion")
                return False
            
            version = tag_data.get('version', 1)
            
            # URL encode the tag name for the API call
            import urllib.parse
            encoded_tag_name = urllib.parse.quote(tag_name, safe='')
            
            async with self.session.delete(
                f"{self.config.szurubooru_url}/api/tag/{encoded_tag_name}",
                json={"version": version}
            ) as response:
                if response.status == 200:
                    return True
                elif response.status == 409:  # Version conflict
                    # Retry with incremented version
                    async with self.session.delete(
                        f"{self.config.szurubooru_url}/api/tag/{encoded_tag_name}",
                        json={"version": version + 1}
                    ) as retry_response:
                        if retry_response.status == 200:
                            return True
                        retry_text = await retry_response.text()
                        logger.error(f"Failed to delete tag '{tag_name}' after retry: HTTP {retry_response.status} - {retry_text}")
                        return False
                else:
                    error_text = await response.text()
                    logger.error(f"Failed to delete tag '{tag_name}': HTTP {response.status} - {error_text}")
                    return False
                    
        except Exception as e:
            logger.error(f"Exception deleting tag {tag_name}: {e}")
            return False

class TagSyncManager:
    """Manager for synchronizing tags between CSV and Szurubooru"""
    
    def __init__(self, config: Config):
        self.config = config
        self.csv_tags: Dict[str, CSVTag] = {}
        self.szurubooru_tags: Dict[str, SzurubooruTag] = {}
        self.stats = {
            'total_csv_tags': 0,
            'total_szurubooru_tags': 0,
            'tags_created': 0,
            'tags_updated': 0,
            'tags_skipped': 0,
            'suggestions_cleaned': 0,
            'unused_tags_found': 0,
            'unused_tags_deleted': 0,
            'aliases_deferred': 0,
            'aliases_applied_after_cleanup': 0,
            'errors': 0
        }
        self.skipped_reasons = []  # Track why tags were skipped
        self.created_tags = []  # Track created tags
        self.updated_tags = []  # Track updated tags
        self.cleaned_suggestions = []  # Track tags with suggestions cleaned
        self.unused_tags = []  # Track unused tags found
        self.deleted_tags = []  # Track deleted tags
        self.deferred_aliases: List[str] = []  # Track aliases we skipped due to conflicts
    
    def map_category(self, csv_category: int) -> str:
        """Map CSV category numbers to Szurubooru category names"""
        category_map = {
            0: "default",      # General tags
            3: "copyright",    # Copyright tags
            4: "character"     # Character tags
        }
        return category_map.get(csv_category, "default")
    
    def filter_foreign_aliases(self, aliases: List[str]) -> List[str]:
        """Filter out foreign language aliases (Japanese, Chinese, Korean, etc.)"""
        if not aliases:
            return []
        
        filtered_aliases = []
        for alias in aliases:
            # Check if alias contains mostly Latin characters
            if self._is_latin_alias(alias):
                filtered_aliases.append(alias)
        
        return filtered_aliases
    
    def _is_valid_tag_name(self, tag_name: str) -> bool:
        """Check if a tag name is valid for API operations"""
        if not tag_name or not tag_name.strip():
            return False
        
        # Skip tags that are just punctuation or special characters
        if tag_name in ['?', '??', '!', '!!', '.', '..', '...']:
            return False
        
        # Skip tags that start with invalid characters
        if tag_name.startswith(('?', '!', '.', '/', '\\')):
            return False
        
        # Skip tags that are too short or too long
        if len(tag_name) < 1 or len(tag_name) > 100:
            return False
        
        return True
    
    def _is_latin_alias(self, alias: str) -> bool:
        """Check if an alias contains mostly Latin characters"""
        if not alias or not alias.strip():
            return False
        
        # Count Latin characters (basic Latin, extended Latin, and common symbols)
        latin_count = 0
        total_chars = 0
        
        for char in alias:
            if char.isspace() or char in '_-()[]{}.,!?@#$%^&*+=<>/~`|\\':
                # Skip whitespace and common symbols
                continue
            
            # Check if character is in Latin ranges
            code_point = ord(char)
            is_latin = (
                # Basic Latin (A-Z, a-z, 0-9)
                (0x0041 <= code_point <= 0x005A) or  # A-Z
                (0x0061 <= code_point <= 0x007A) or  # a-z
                (0x0030 <= code_point <= 0x0039) or  # 0-9
                # Extended Latin (accents, etc.)
                (0x00C0 <= code_point <= 0x017F) or  # Latin Extended
                (0x0180 <= code_point <= 0x024F) or  # Latin Extended-B
                (0x1E00 <= code_point <= 0x1EFF) or  # Latin Extended Additional
                (0x2C60 <= code_point <= 0x2C7F) or  # Latin Extended-C
                (0xA720 <= code_point <= 0xA7FF) or  # Latin Extended-D
                (0xAB30 <= code_point <= 0xAB6F) or  # Latin Extended-E
                # Common symbols and punctuation
                (0x0020 <= code_point <= 0x007F)     # Basic Latin block
            )
            
            if is_latin:
                latin_count += 1
            total_chars += 1
        
        # If no meaningful characters, skip it
        if total_chars == 0:
            return False
        
        # Require at least 80% Latin characters
        latin_ratio = latin_count / total_chars
        return latin_ratio >= 0.8
    
    def load_csv_tags(self) -> int:
        """Load tags from CSV file"""
        try:
            csv_path = Path(self.config.csv_file)
            if not csv_path.exists():
                logger.error(f"CSV file not found: {csv_path}")
                return 0
            
            print(f"Loading tags from {csv_path}...")
            
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                
                for row in reader:
                    tag_name = row['tag'].strip()
                    if not tag_name:
                        continue
                    
                    try:
                        category = int(row['category'])
                        count = int(row['count'])
                        aliases_str = row.get('alias', '').strip()
                        
                        # Parse aliases
                        aliases = []
                        if aliases_str:
                            raw_aliases = [alias.strip() for alias in aliases_str.split(',') if alias.strip()]
                            # Filter out foreign language aliases if enabled
                            if self.config.filter_foreign_aliases:
                                aliases = self.filter_foreign_aliases(raw_aliases)
                            else:
                                aliases = raw_aliases
                        
                        self.csv_tags[tag_name] = CSVTag(
                            name=tag_name,
                            category=category,
                            count=count,
                            aliases=aliases
                        )
                        
                    except (ValueError, KeyError) as e:
                        logger.warning(f"Invalid row in CSV: {row} - {e}")
                        continue
            
            self.stats['total_csv_tags'] = len(self.csv_tags)
            print(f"[SUCCESS] Loaded {self.stats['total_csv_tags']} tags from CSV")
            return self.stats['total_csv_tags']
            
        except Exception as e:
            logger.error(f"Failed to load CSV tags: {e}")
            return 0
    
    async def load_szurubooru_tags(self) -> int:
        """Load all tags from Szurubooru"""
        try:
            print("Loading tags from Szurubooru...")
            
            async with SzurubooruAPI(self.config) as api:
                if not await api.test_connection():
                    return 0
                
                offset = 0
                batch_size = self.config.batch_size
                
                with tqdm(desc="Fetching tags", unit="batch") as pbar:
                    while True:
                        tags_batch = await api.get_all_tags(limit=batch_size, offset=offset)
                        
                        if not tags_batch:
                            break
                        
                        for tag_data in tags_batch:
                             tag_name = tag_data.get('names', [''])[0]
                             if tag_name:
                                 self.szurubooru_tags[tag_name] = SzurubooruTag(
                                     name=tag_name,
                                     category=tag_data.get('category', 'default'),
                                     version=tag_data.get('version', 1),
                                     aliases=tag_data.get('names', [])[1:] if len(tag_data.get('names', [])) > 1 else []
                                 )
                                 
                                 # Check if this tag has suggestions that need cleaning
                                 if self.config.cleanup_suggestions and tag_data.get('suggestions'):
                                     self.stats['suggestions_cleaned'] += 1
                                     self.cleaned_suggestions.append(f"Tag '{tag_name}' has {len(tag_data.get('suggestions', []))} suggestions to clean")
                                 
                                 # Check if this tag is unused (0 usage)
                                 if self.config.cleanup_unused_tags and tag_data.get('usages', 0) == 0:
                                     self.stats['unused_tags_found'] += 1
                                     self.unused_tags.append(f"Tag '{tag_name}' (category: {tag_data.get('category', 'default')}) - 0 usages")
                        
                        offset += len(tags_batch)
                        pbar.update(1)
                        
                        if len(tags_batch) < batch_size:
                            break
            
            self.stats['total_szurubooru_tags'] = len(self.szurubooru_tags)
            print(f"[SUCCESS] Loaded {self.stats['total_szurubooru_tags']} tags from Szurubooru")
            return self.stats['total_szurubooru_tags']
            
        except Exception as e:
            logger.error(f"Failed to load Szurubooru tags: {e}")
            return 0
    
    async def sync_tags(self):
        """Synchronize tags between CSV and Szurubooru in the correct order:
        1. Clean up all suggestions first
        2. Delete unused tags (orphaned from suggestions)
        3. Sync CSV data (create/update tags with proper aliases)
        """
        if not self.csv_tags:
            logger.error("No CSV tags loaded")
            return
        
        print(f"\n[START] Starting tag synchronization in phases...")
        print(f"[INFO] CSV tags: {self.stats['total_csv_tags']}")
        print(f"[INFO] Szurubooru tags: {self.stats['total_szurubooru_tags']}")
        print(f"[INFO] Dry run: {self.config.dry_run}")
        if self.config.sample_mode:
            print(f"[INFO] Sample mode: Processing only first {self.config.batch_size} tags")
        print("=" * 60)
        
        async with SzurubooruAPI(self.config) as api:
            # PHASE 1: Clean up all suggestions first
            if self.config.cleanup_suggestions:
                await self._cleanup_all_suggestions(api)

            # PHASE 2: Delete unused tags (orphaned from suggestions)
            if self.config.cleanup_unused_tags:
                await self._cleanup_unused_tags(api)

            # PHASE 3: Sync CSV data (create/update tags with proper aliases)
            await self._sync_csv_tags(api)
        
        # Print final statistics
        print(f"\n[COMPLETE] Tag synchronization complete!")
        print(f"[STATS] Final Statistics:")
        print(f"  CSV tags processed: {self.stats['total_csv_tags']}")
        print(f"  Tags created: {self.stats['tags_created']}")
        print(f"  Tags updated: {self.stats['tags_updated']}")
        print(f"  Tags skipped: {self.stats['tags_skipped']}")
        print(f"  Suggestions cleaned: {self.stats['suggestions_cleaned']}")
        print(f"  Unused tags found: {self.stats['unused_tags_found']}")
        print(f"  Unused tags deleted: {self.stats['unused_tags_deleted']}")
        print(f"  Aliases deferred: {self.stats['aliases_deferred']}")
        print(f"  Aliases applied after cleanup: {self.stats['aliases_applied_after_cleanup']}")
        print(f"  Errors: {self.stats['errors']}")
        
        # Print created tags summary
        if self.created_tags:
            print(f"\n[CREATED] Tags that were created:")
            for tag in self.created_tags[:10]:  # Show first 10
                print(f"  - {tag}")
            if len(self.created_tags) > 10:
                print(f"  ... and {len(self.created_tags) - 10} more (see log file for details)")
        
        # Print updated tags summary
        if self.updated_tags:
            print(f"\n[UPDATED] Tags that were updated:")
            for tag in self.updated_tags[:10]:  # Show first 10
                print(f"  - {tag}")
            if len(self.updated_tags) > 10:
                print(f"  ... and {len(self.updated_tags) - 10} more (see log file for details)")
        
        # Print skipped tags summary
        if self.skipped_reasons:
            print(f"\n[SKIPPED] Tags that were skipped:")
            for reason in self.skipped_reasons[:10]:  # Show first 10
                print(f"  - {reason}")
            if len(self.skipped_reasons) > 10:
                print(f"  ... and {len(self.skipped_reasons) - 10} more (see log file for details)")
        
        # Print suggestions cleaned summary
        if self.cleaned_suggestions:
            print(f"\n[CLEANED] Tags with suggestions that were cleaned:")
            for tag in self.cleaned_suggestions[:10]:  # Show first 10
                print(f"  - {tag}")
            if len(self.cleaned_suggestions) > 10:
                print(f"  ... and {len(self.cleaned_suggestions) - 10} more (see log file for details)")

        # Print deferred aliases note
        if self.deferred_aliases:
            print(f"\n[DEFERRED] Deferred alias updates for {len(self.deferred_aliases)} tags (due to conflicts before cleanup):")
            for name in self.deferred_aliases[:10]:
                print(f"  - {name}")
            if len(self.deferred_aliases) > 10:
                print(f"  ... and {len(self.deferred_aliases) - 10} more (see log file for details)")
        
        # Clean up unused tags at the very end (after all updates and suggestion cleanup)
        # This avoids conflicts when alias updates would rename into an existing tag created from suggestions
        if self.config.cleanup_unused_tags and self.unused_tags:
            await self._cleanup_unused_tags(api)

        # After cleanup, try to apply deferred aliases once more (optional best-effort)
        if self.deferred_aliases and self.config.update_aliases and not self.config.dry_run:
            print(f"\n[POST] Applying deferred alias updates after cleanup...")
            with tqdm(self.deferred_aliases, desc="Applying deferred aliases", unit="tag") as pbar:
                for name in pbar:
                    try:
                        csv_tag = self.csv_tags.get(name)
                        if not csv_tag:
                            continue
                        success = await api.update_tag(
                            name,
                            aliases=csv_tag.aliases,
                            cleanup_suggestions=False
                        )
                        if success:
                            self.stats['aliases_applied_after_cleanup'] += 1
                    except Exception as e:
                        logger.error(f"[ERROR] Failed to apply deferred aliases for '{name}': {e}")
        
        if self.config.sample_mode:
            print(f"\n[INFO] This was a sample run. To process all tags, remove --sample flag.")
    
    async def _cleanup_all_suggestions(self, api: SzurubooruAPI):
        """PHASE 1: Clean up all suggestions from all tags"""
        print(f"\n[PHASE 1] Cleaning up all suggestions...")
        
        # Get all tags that have suggestions
        tags_with_suggestions = []
        for tag_name, tag_data in self.szurubooru_tags.items():
            # Skip invalid tag names that would cause URL issues
            if not self._is_valid_tag_name(tag_name):
                logger.debug(f"Skipping invalid tag name: '{tag_name}'")
                continue
                
            # We need to get the full tag data to check for suggestions
            full_tag_data = await api.get_tag(tag_name)
            if full_tag_data and full_tag_data.get('suggestions'):
                tags_with_suggestions.append(tag_name)
        
        if not tags_with_suggestions:
            print("[PHASE 1] No tags with suggestions found.")
            return
        
        print(f"[PHASE 1] Found {len(tags_with_suggestions)} tags with suggestions to clean")
        
        # Limit to one batch if in sample mode
        if self.config.sample_mode:
            tags_with_suggestions = tags_with_suggestions[:self.config.batch_size]
            print(f"[PHASE 1] Sample mode: Processing only first {len(tags_with_suggestions)} tags")
        
        with tqdm(tags_with_suggestions, desc="Cleaning suggestions", unit="tag") as pbar:
            for tag_name in pbar:
                try:
                    if not self.config.dry_run:
                        success = await api.update_tag(
                            tag_name,
                            cleanup_suggestions=True
                        )
                        if success:
                            self.stats['suggestions_cleaned'] += 1
                            self.cleaned_suggestions.append(f"Tag '{tag_name}' - suggestions cleaned")
                            logger.info(f"[CLEAN] Tag '{tag_name}' - suggestions cleaned")
                        else:
                            self.stats['errors'] += 1
                            logger.error(f"[ERROR] Failed to clean suggestions for tag '{tag_name}'")
                    else:
                        self.stats['suggestions_cleaned'] += 1
                        self.cleaned_suggestions.append(f"Tag '{tag_name}' - would clean suggestions")
                        logger.info(f"[DRY-RUN] Would clean suggestions for tag '{tag_name}'")
                    
                    pbar.set_postfix({
                        'Cleaned': self.stats['suggestions_cleaned'],
                        'Errors': self.stats['errors']
                    })
                    
                except Exception as e:
                    logger.error(f"Error cleaning suggestions for tag {tag_name}: {e}")
                    self.stats['errors'] += 1
        
        print(f"[PHASE 1] Suggestion cleanup complete!")
    
    async def _sync_csv_tags(self, api: SzurubooruAPI):
        """PHASE 3: Sync CSV data (create/update tags with proper aliases)"""
        print(f"\n[PHASE 3] Syncing CSV data...")
        
        # Convert to list for slicing
        csv_tags_list = list(self.csv_tags.values())
        
        # Limit to one batch if in sample mode
        if self.config.sample_mode:
            csv_tags_list = csv_tags_list[:self.config.batch_size]
            print(f"[PHASE 3] Sample mode: Processing {len(csv_tags_list)} tags for preview")
        
        # Process each CSV tag
        with tqdm(csv_tags_list, desc="Syncing tags", unit="tag") as pbar:
            for csv_tag in pbar:
                try:
                    # Skip invalid tag names
                    if not self._is_valid_tag_name(csv_tag.name):
                        logger.debug(f"Skipping invalid CSV tag name: '{csv_tag.name}'")
                        self.stats['tags_skipped'] += 1
                        self.skipped_reasons.append(f"Tag '{csv_tag.name}' - invalid tag name")
                        continue
                        
                    await self._process_tag(api, csv_tag)
                    pbar.set_postfix({
                        'Created': self.stats['tags_created'],
                        'Updated': self.stats['tags_updated'],
                        'Skipped': self.stats['tags_skipped'],
                        'Errors': self.stats['errors']
                    })
                except Exception as e:
                    logger.error(f"Error processing tag {csv_tag.name}: {e}")
                    self.stats['errors'] += 1
        
        print(f"[PHASE 3] CSV sync complete!")
    
    async def _process_tag(self, api: SzurubooruAPI, csv_tag: CSVTag):
        """Process a single CSV tag"""
        tag_name = csv_tag.name
        target_category = self.map_category(csv_tag.category)
        
        # Check if tag exists in Szurubooru
        szurubooru_tag = self.szurubooru_tags.get(tag_name)
        
        if szurubooru_tag:
            # Tag exists - check if it needs updates
            needs_update = False
            update_reasons = []
            
            # Check category
            if self.config.update_categories and szurubooru_tag.category != target_category:
                needs_update = True
                update_reasons.append(f"category: {szurubooru_tag.category} → {target_category}")
            
            # Check aliases
            if self.config.update_aliases:
                current_aliases_set = set(szurubooru_tag.aliases)
                target_aliases_set = set(csv_tag.aliases)
                
                if current_aliases_set != target_aliases_set:
                    needs_update = True
                    added_aliases = target_aliases_set - current_aliases_set
                    removed_aliases = current_aliases_set - target_aliases_set
                    if added_aliases:
                        update_reasons.append(f"add aliases: {list(added_aliases)}")
                    if removed_aliases:
                        update_reasons.append(f"remove aliases: {list(removed_aliases)}")
            
            if needs_update:
                if not self.config.dry_run:
                    success = await api.update_tag(
                        tag_name,
                        category=target_category if self.config.update_categories else None,
                        aliases=csv_tag.aliases if self.config.update_aliases else None
                    )
                    if success:
                        self.stats['tags_updated'] += 1
                        update_summary = f"Tag '{tag_name}' - {', '.join(update_reasons)}"
                        self.updated_tags.append(update_summary)
                        logger.info(f"[UPDATE] {update_summary}")
                    else:
                        self.stats['errors'] += 1
                        logger.error(f"[ERROR] Failed to update tag '{tag_name}'")
                else:
                    self.stats['tags_updated'] += 1
                    update_summary = f"Tag '{tag_name}' - {', '.join(update_reasons)}"
                    self.updated_tags.append(update_summary)
                    logger.info(f"[DRY-RUN] Would update {update_summary}")
            else:
                self.stats['tags_skipped'] += 1
                skip_reason = f"Tag '{tag_name}' - no changes needed (category: {szurubooru_tag.category}, aliases: {len(szurubooru_tag.aliases)})"
                self.skipped_reasons.append(skip_reason)
                logger.debug(f"[SKIP] {skip_reason}")
        
        else:
            # Tag doesn't exist - create it
            if self.config.create_missing_tags:
                if not self.config.dry_run:
                    success = await api.create_tag(
                        tag_name,
                        category=target_category,
                        aliases=csv_tag.aliases
                    )
                    if success:
                        self.stats['tags_created'] += 1
                        create_summary = f"Tag '{tag_name}' (category: {target_category}, aliases: {len(csv_tag.aliases)})"
                        self.created_tags.append(create_summary)
                        logger.info(f"[CREATE] {create_summary}")
                    else:
                        self.stats['errors'] += 1
                        logger.error(f"[ERROR] Failed to create tag '{tag_name}'")
                else:
                    self.stats['tags_created'] += 1
                    create_summary = f"Tag '{tag_name}' (category: {target_category}, aliases: {len(csv_tag.aliases)})"
                    self.created_tags.append(create_summary)
                    logger.info(f"[DRY-RUN] Would create {create_summary}")
            else:
                self.stats['tags_skipped'] += 1
                skip_reason = f"Tag '{tag_name}' - not created (create_missing_tags disabled)"
                self.skipped_reasons.append(skip_reason)
                logger.debug(f"[SKIP] {skip_reason}")
    
    async def _cleanup_unused_tags(self, api: SzurubooruAPI):
        """Clean up unused tags (0 usage)"""
        if not self.unused_tags:
            return
        
        print(f"\n[CLEANUP] Starting cleanup of {len(self.unused_tags)} unused tags...")
        
        # Extract tag names from the unused tags list
        unused_tag_names = []
        for unused_tag_info in self.unused_tags:
            # Extract tag name from the info string (format: "Tag 'name' (category: x) - 0 usages")
            tag_name = unused_tag_info.split("'")[1]
            # Skip invalid tag names
            if not self._is_valid_tag_name(tag_name):
                logger.debug(f"Skipping invalid unused tag name: '{tag_name}'")
                continue
            unused_tag_names.append(tag_name)
        
        # Limit to one batch if in sample mode
        if self.config.sample_mode:
            unused_tag_names = unused_tag_names[:self.config.batch_size]
            print(f"[INFO] Sample mode: Processing only first {len(unused_tag_names)} unused tags")
        
        with tqdm(unused_tag_names, desc="Deleting unused tags", unit="tag") as pbar:
            for tag_name in pbar:
                try:
                    if not self.config.dry_run:
                        success = await api.delete_tag(tag_name)
                        if success:
                            self.stats['unused_tags_deleted'] += 1
                            self.deleted_tags.append(f"Tag '{tag_name}' - deleted (0 usages)")
                            logger.info(f"[DELETE] Tag '{tag_name}' - deleted (0 usages)")
                        else:
                            self.stats['errors'] += 1
                            logger.error(f"[ERROR] Failed to delete unused tag '{tag_name}'")
                    else:
                        self.stats['unused_tags_deleted'] += 1
                        self.deleted_tags.append(f"Tag '{tag_name}' - would delete (0 usages)")
                        logger.info(f"[DRY-RUN] Would delete unused tag '{tag_name}' (0 usages)")
                    
                    pbar.set_postfix({
                        'Deleted': self.stats['unused_tags_deleted'],
                        'Errors': self.stats['errors']
                    })
                    
                except Exception as e:
                    logger.error(f"Error deleting unused tag {tag_name}: {e}")
                    self.stats['errors'] += 1
        
        print(f"[CLEANUP] Unused tag cleanup complete!")
        print(f"  Unused tags found: {self.stats['unused_tags_found']}")
        print(f"  Unused tags deleted: {self.stats['unused_tags_deleted']}")
        
        # Print deleted tags summary
        if self.deleted_tags:
            print(f"\n[DELETED] Unused tags that were deleted:")
            for tag in self.deleted_tags[:10]:  # Show first 10
                print(f"  - {tag}")
            if len(self.deleted_tags) > 10:
                print(f"  ... and {len(self.deleted_tags) - 10} more (see log file for details)")

def load_config(config_path: str) -> Config:
    """Load configuration from JSON file"""
    try:
        with open(config_path, 'r') as f:
            config_data = json.load(f)
        
        # Only extract the fields we need for tag sync
        required_fields = {
            'szurubooru_url': config_data.get('szurubooru_url'),
            'username': config_data.get('username'),
            'api_token': config_data.get('api_token'),
            'password': config_data.get('password')
        }
        
        # Check for required fields
        if not required_fields['szurubooru_url']:
            raise ValueError("szurubooru_url is required in config")
        if not required_fields['username']:
            raise ValueError("username is required in config")
        if not required_fields['api_token'] and not required_fields['password']:
            raise ValueError("Either api_token or password is required in config")
        
        return Config(**required_fields)
    except Exception as e:
        logger.error(f"Failed to load config from {config_path}: {e}")
        raise

async def main():
    """Main function"""
    parser = argparse.ArgumentParser(description="Szurubooru Tag Synchronization Manager")
    parser.add_argument("--config", "-c", default="config.json", help="Configuration file path")
    parser.add_argument("--csv", default="danbooru_tags.csv", help="CSV file path")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without making changes")
    parser.add_argument("--sample", action="store_true", help="Process only one batch for testing (combine with --dry-run)")
    parser.add_argument("--no-create", action="store_true", help="Don't create missing tags")
    parser.add_argument("--no-categories", action="store_true", help="Don't update categories")
    parser.add_argument("--no-aliases", action="store_true", help="Don't update aliases")
    parser.add_argument("--no-filter-foreign", action="store_true", help="Don't filter out foreign language aliases")
    parser.add_argument("--no-cleanup-suggestions", action="store_true", help="Don't clean up existing suggestions")
    parser.add_argument("--cleanup-unused", action="store_true", help="Delete tags with 0 usage (orphaned from incorrect suggestions)")
    parser.add_argument("--batch-size", type=int, default=100, help="Batch size for API calls")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print verbose errors to console")
    
    args = parser.parse_args()
    
    # Print header
    print("Szurubooru Tag Synchronization Manager")
    print("=" * 50)
    
    # Load configuration
    try:
        config = load_config(args.config)
    except Exception as e:
        print(f"Failed to load configuration: {e}")
        return
    
    # Override config with command line arguments
    config.csv_file = args.csv
    config.dry_run = args.dry_run
    config.sample_mode = args.sample
    config.create_missing_tags = not args.no_create
    config.update_categories = not args.no_categories
    config.update_aliases = not args.no_aliases
    config.filter_foreign_aliases = not args.no_filter_foreign
    config.cleanup_suggestions = not args.no_cleanup_suggestions
    config.cleanup_unused_tags = args.cleanup_unused
    config.batch_size = args.batch_size
    
    # Optional console logging based on verbosity
    if args.verbose:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(console_handler)

    # Create tag sync manager
    manager = TagSyncManager(config)
    
    # Load CSV tags
    csv_count = manager.load_csv_tags()
    if csv_count == 0:
        print("[ERROR] No CSV tags loaded. Exiting.")
        return
    
    # Load Szurubooru tags
    szurubooru_count = await manager.load_szurubooru_tags()
    if szurubooru_count == 0:
        print("[ERROR] No Szurubooru tags loaded. Exiting.")
        return
    
    # Perform synchronization
    await manager.sync_tags()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)
