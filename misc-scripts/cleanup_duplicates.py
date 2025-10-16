#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Szurubooru Duplicate Cleanup Script
Efficiently finds and deletes duplicate posts based on MD5 checksums
Created as a way to cleanup duplicates that were created by the szurubooru_manager.py script due to a bug.


Features:
- Streaming processing (constant memory usage)
- Sliding window approach for optimal performance
- Minimal logging for clean output
- Progress tracking every 200 posts
"""

import asyncio
import aiohttp
import json
import argparse
import logging
from pathlib import Path
from collections import defaultdict
from typing import Dict, List, Tuple

# Import our existing components
from components import load_config, SzurubooruAPI

# Configure minimal logging
logging.basicConfig(
    level=logging.WARNING,  # Only show warnings and errors
    format='%(levelname)s - %(message)s',
    handlers=[logging.FileHandler('duplicate_cleanup.log')]
)
logger = logging.getLogger(__name__)

# ANSI color codes
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
CYAN = '\033[96m'
RESET = '\033[0m'


class DuplicateCleaner:
    """Efficient duplicate cleaner with streaming processing"""
    
    def __init__(self, config):
        self.config = config
        self.api = None
        
    async def __aenter__(self):
        self.api = SzurubooruAPI(self.config)
        await self.api.__aenter__()
        return self
        
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.api:
            await self.api.__aexit__(exc_type, exc_val, exc_tb)
    
    async def cleanup_duplicates(self, dry_run: bool = True, limit: int = None) -> Tuple[int, int]:
        """Streaming duplicate cleanup with sliding window"""
        print(f"{BLUE}[SCAN]{RESET} Starting duplicate cleanup...")
        
        chunk_size = 100
        offset = 0
        total_checked = 0
        total_deleted = 0
        total_errors = 0
        
        # Track MD5s in a sliding window (only last 500 posts for speed)
        seen_md5s = {}  # md5 -> (post_id, version)
        window_size = 500  # Only check duplicates within last 500 posts
        
        print(f"{BLUE}[SCAN]{RESET} Processing in chunks of {chunk_size}...")
        print(f"{BLUE}[INFO]{RESET} API returns posts newest first (highest ID first)")
        
        while True:
            if limit and total_checked >= limit:
                print(f"{BLUE}[SCAN]{RESET} Reached limit of {limit} posts")
                break
                
            # Show initial progress
            if total_checked == 0:
                print(f"{BLUE}[SCAN]{RESET} Starting at offset {offset}")
                
            posts = await self._get_posts_batch(offset, chunk_size)
            if not posts:
                print(f"{BLUE}[DEBUG]{RESET} No more posts found at offset {offset}")
                break
            
            # Process chunk silently with sliding window
            chunk_deleted, chunk_errors = await self._process_chunk_sliding_window(
                posts, seen_md5s, dry_run, window_size
            )
            
            total_deleted += chunk_deleted
            total_errors += chunk_errors
            total_checked += len(posts)
            
            # Progress update every 200 posts for better visibility
            if total_checked % 200 == 0:
                print(f"{BLUE}[SCAN]{RESET} Checked {total_checked:,} posts, deleted {total_deleted} duplicates, tracking {len(seen_md5s)} unique MD5s...")
            
            offset += chunk_size
        
        print(f"\n{BLUE}[SCAN]{RESET} Checked {total_checked:,} total posts")
        print(f"{CYAN}[RESULTS]{RESET} Deleted {total_deleted} duplicates, {total_errors} errors")
        
        return total_deleted, total_errors
    
    async def _process_chunk_sliding_window(self, posts: List[dict], seen_md5s: Dict[str, Tuple[int, int]], dry_run: bool, window_size: int) -> Tuple[int, int]:
        """Process chunk with sliding window approach"""
        deleted_count = 0
        error_count = 0
        
        for post in posts:
            md5 = post.get('checksumMD5')
            if not md5:
                continue
            
            post_id = post['id']
            version = post['version']
            
            if md5 in seen_md5s:
                # Found duplicate within window
                existing_id, existing_version = seen_md5s[md5]
                print(f"{YELLOW}[DUPLICATE]{RESET} Found MD5 {md5[:16]}... in posts {existing_id} and {post_id}")
                
                if post_id > existing_id:
                    # Current is newer, delete old
                    if not dry_run:
                        success = await self._delete_post(existing_id, existing_version)
                        if success:
                            deleted_count += 1
                        else:
                            error_count += 1
                    else:
                        deleted_count += 1
                    seen_md5s[md5] = (post_id, version)
                else:
                    # Existing is newer, delete current
                    if not dry_run:
                        success = await self._delete_post(post_id, version)
                        if success:
                            deleted_count += 1
                        else:
                            error_count += 1
                    else:
                        deleted_count += 1
            else:
                # New MD5 - add to window
                seen_md5s[md5] = (post_id, version)
                
                # Keep window size manageable - remove oldest entries if needed
                if len(seen_md5s) > window_size:
                    # Remove oldest entries to maintain window size
                    # Convert to list, sort by post_id (oldest first), keep only the newest window_size
                    sorted_items = sorted(seen_md5s.items(), key=lambda x: x[1][0])  # Sort by post_id
                    seen_md5s.clear()
                    # Keep only the newest window_size entries
                    for md5, (post_id, version) in sorted_items[-window_size:]:
                        seen_md5s[md5] = (post_id, version)
        
        return deleted_count, error_count
    
    async def _get_posts_batch(self, offset: int, limit: int) -> List[dict]:
        """Get posts batch efficiently"""
        try:
            params = {'limit': limit, 'offset': offset}
            
            async with self.api.session.get(
                f"{self.config.szurubooru_url}/api/posts/",
                params=params
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result.get('results', [])
                else:
                    print(f"{RED}[ERROR]{RESET} API returned status {response.status} at offset {offset}")
                    return []
                    
        except Exception as e:
            print(f"{RED}[ERROR]{RESET} Exception at offset {offset}: {e}")
            return []
    
    async def _delete_post(self, post_id: int, version: int) -> bool:
        """Delete post"""
        try:
            delete_data = {"version": version}
            
            async with self.api.session.delete(
                f"{self.config.szurubooru_url}/api/post/{post_id}",
                json=delete_data
            ) as response:
                return response.status == 200
                    
        except Exception:
            return False


async def main():
    """Main function"""
    parser = argparse.ArgumentParser(description="Duplicate cleanup for Szurubooru instances")
    parser.add_argument("--config", "-c", default="config.json", help="Configuration file path")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without actually doing it")
    parser.add_argument("--auto-confirm", action="store_true", help="Skip confirmation prompt")
    parser.add_argument("--test-limit", type=int, help="Limit scanning to first N posts (for testing)")
    
    args = parser.parse_args()
    
    print("Szurubooru Duplicate Cleanup Tool")
    print("=" * 40)
    print(f"{BLUE}[INFO]{RESET} Efficient cleanup with streaming processing")
    
    # Load configuration
    try:
        config = load_config(args.config)
        print(f"Configuration loaded from: {args.config}")
    except Exception as e:
        print(f"Failed to load configuration: {e}")
        return
    
    # Test API connection
    async with DuplicateCleaner(config) as cleaner:
        if not await cleaner.api.test_connection():
            print(f"{RED}[ERROR]{RESET} Failed to connect to Szurubooru API")
            return
        
        print(f"{GREEN}[SUCCESS]{RESET} Connected to Szurubooru API")
        
        if args.test_limit:
            print(f"\n{YELLOW}[TEST]{RESET} Limiting scan to first {args.test_limit} posts")
        
        if args.dry_run:
            print(f"{YELLOW}[DRY RUN]{RESET} This is a dry run - no changes will be made")
        
        # Confirmation
        if not args.auto_confirm and not args.dry_run:
            print(f"\n{RED}[WARNING]{RESET} This will permanently DELETE duplicate posts!")
            response = input("Continue? (y/N): ")
            if response.lower() != 'y':
                print("Operation cancelled")
                return
        
        # Perform cleanup
        print(f"\n{BLUE}[START]{RESET} Starting duplicate cleanup...")
        deleted_count, error_count = await cleaner.cleanup_duplicates(
            dry_run=args.dry_run, 
            limit=args.test_limit
        )
        
        # Print summary
        print(f"\n{CYAN}[SUMMARY]{RESET} Cleanup Results:")
        print(f"  Successfully deleted: {deleted_count}")
        if error_count > 0:
            print(f"  {RED}Errors: {error_count}{RESET}")
        
        if deleted_count > 0:
            print(f"\n{GREEN}[SUCCESS]{RESET} Cleanup completed!")
            print(f"  Deleted {deleted_count} duplicate posts")
            print(f"  Freed up database space and improved performance")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nOperation cancelled by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise
