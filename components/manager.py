#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Media Manager module for Szurubooru Manager
Handles all media processing operations including upload, tagging, and management
"""

import asyncio
import aiohttp
import aiofiles
import json
import time
import logging
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from tqdm import tqdm

from .api import SzurubooruAPI
from .tagger import WDTaggerManager, WD14_AVAILABLE
from .metrics import PerformanceMetrics
from .utils import is_video_file

logger = logging.getLogger(__name__)

# ANSI color codes for terminal output
RED = '\033[91m'
GREEN = '\033[92m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
CYAN = '\033[96m'
RESET = '\033[0m'


class MediaManager:
    """Media management class with parallel processing"""
    
    def __init__(self, config):
        self.config = config
        self.wd_tagger = WDTaggerManager(config)
        self.metrics = PerformanceMetrics()
        self.upload_semaphore = asyncio.Semaphore(config.max_concurrent_uploads)
        # Track files currently being uploaded to prevent duplicates
        self._uploading_files = set()
        
        # Pre-initialize tagger for better performance
        if WD14_AVAILABLE and config.gpu_enabled:
            try:
                self.wd_tagger.initialize()
            except Exception as e:
                logger.warning(f"Failed to pre-initialize WD14 Tagger: {e}")
        
        # Track processed files to avoid reprocessing (if enabled)
        if config.track_processed_files:
            self.processed_files_log = Path("processed_files.txt")
            self._processed_files_cache = set()
            self._load_processed_files_cache()
        else:
            self.processed_files_log = None
            self._processed_files_cache = set()
            # Clear any existing processed files cache when tracking is disabled
            try:
                processed_files_path = Path("processed_files.txt")
                if processed_files_path.exists():
                    processed_files_path.unlink()
                    logger.info("Cleared processed files cache (tracking disabled)")
            except Exception as e:
                logger.warning(f"Failed to clear processed files cache: {e}")
    
    def _load_processed_files_cache(self):
        """Load processed files from log into memory cache"""
        if self.processed_files_log.exists():
            try:
                with open(self.processed_files_log, 'r', encoding='utf-8') as f:
                    self._processed_files_cache = {line.strip() for line in f if line.strip()}
                print(f"Loaded {len(self._processed_files_cache)} processed files from cache")
            except Exception as e:
                logger.warning(f"Failed to load processed files cache: {e}")
    
    def _mark_file_processed(self, file_path: Path):
        """Mark a file as processed"""
        if self.config.track_processed_files and self.config.skip_processed_files:
            file_str = str(file_path.resolve())
            if file_str not in self._processed_files_cache:
                logger.debug(f"[MARK PROCESSED] {file_path.name} - Adding to processed cache")
                self._processed_files_cache.add(file_str)
                # Append to log file
                try:
                    if self.processed_files_log:
                        with open(self.processed_files_log, 'a', encoding='utf-8') as f:
                            f.write(f"{file_str}\n")
                        logger.debug(f"[MARK PROCESSED] {file_path.name} - Written to processed files log")
                except Exception as e:
                    logger.warning(f"Failed to log processed file: {e}")
            else:
                logger.debug(f"[MARK PROCESSED] {file_path.name} - Already in processed cache")
    
    async def _upload_with_content_type(self, api, file_path: Path, tags: List[str], content_type: str) -> Optional[Dict]:
        """Upload a file with explicit content type"""
        try:
            # Prepare the upload data
            data = aiohttp.FormData()
            data.add_field('metadata', json.dumps({
                'tags': tags,
                'safety': "unsafe"
            }), content_type='application/json')
            
            # Add the file with explicit content type
            async with aiofiles.open(file_path, 'rb') as f:
                content = await f.read()
                data.add_field('content', content, filename=file_path.name, content_type=content_type)
            
            # Upload the post
            async with api.session.post(
                f"{self.config.szurubooru_url}/api/posts/",
                data=data
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result
                else:
                    error_text = await response.text()
                    return {"error": error_text, "status": response.status}
                    
        except Exception as e:
            return {"error": str(e), "status": 0}
    
    async def _upload_as_binary(self, api, file_path: Path, tags: List[str]) -> Optional[Dict]:
        """Upload a file as binary with custom headers"""
        try:
            # Read file as binary
            async with aiofiles.open(file_path, 'rb') as f:
                content = await f.read()
            
            # Create custom headers
            headers = {
                'Content-Type': 'video/mp4',
                'Content-Length': str(len(content)),
                'Accept': 'application/json'
            }
            
            # Prepare metadata
            metadata = {
                'tags': tags,
                'safety': "unsafe"
            }
            
            # Create multipart form data manually
            boundary = f"----WebKitFormBoundary{int(time.time() * 1000)}"
            headers['Content-Type'] = f'multipart/form-data; boundary={boundary}'
            
            # Build multipart body
            body_parts = []
            
            # Add metadata part
            body_parts.append(f'--{boundary}')
            body_parts.append('Content-Disposition: form-data; name="metadata"')
            body_parts.append('Content-Type: application/json')
            body_parts.append('')
            body_parts.append(json.dumps(metadata))
            
            # Add file part
            body_parts.append(f'--{boundary}')
            body_parts.append(f'Content-Disposition: form-data; name="content"; filename="{file_path.name}"')
            body_parts.append('Content-Type: video/mp4')
            body_parts.append('')
            body_parts.append('')
            
            # Join parts and add binary content
            body = '\r\n'.join(body_parts).encode('utf-8') + b'\r\n' + content + f'\r\n--{boundary}--\r\n'.encode('utf-8')
            
            # Upload with custom headers
            async with api.session.post(
                f"{self.config.szurubooru_url}/api/posts/",
                data=body,
                headers=headers
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    return result
                else:
                    error_text = await response.text()
                    return {"error": error_text, "status": response.status}
                    
        except Exception as e:
            return {"error": str(e), "status": 0}
    
    def _is_file_processed(self, file_path: Path) -> bool:
        """Check if file was already processed"""
        if not self.config.skip_processed_files or not self.config.track_processed_files:
            return False
        return str(file_path.resolve()) in self._processed_files_cache
    
    async def scan_upload_directory_batch(self, batch_size: int = None) -> List[Path]:
        """Scan upload directory in batches for better performance with large directories"""
        if batch_size is None:
            batch_size = self.config.batch_discovery_size
            
        upload_path = Path(self.config.upload_directory)
        
        if not upload_path.exists():
            logger.error(f"Upload directory does not exist: {upload_path}")
            return []
        
        loop = asyncio.get_event_loop()
        
        def scan_batch():
            from .utils import is_image_file, is_video_file
            
            found_files = []
            total_scanned = 0
            
            # Scan all files recursively
            for file_path in upload_path.rglob('*'):
                # Skip directories
                if not file_path.is_file():
                    continue
                
                total_scanned += 1
                
                # Skip if already processed
                if self._is_file_processed(file_path):
                    logger.debug(f"[SCAN SKIP] {file_path.name} - Already processed")
                    continue
                
                # Use python-magic to detect actual media files
                if not (is_image_file(file_path) or is_video_file(file_path)):
                    continue
                
                logger.debug(f"[SCAN FOUND] {file_path.name} - Adding to batch")
                found_files.append(file_path)
                
                # Stop when we hit batch limit
                if len(found_files) >= batch_size:
                    break
            
            # Sort by modification time (newest first)
            found_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
            return found_files, total_scanned
        
        files, scanned_count = await loop.run_in_executor(None, scan_batch)
        
        # Check for duplicate file paths in the discovered batch
        unique_files = list(set(files))
        if len(unique_files) != len(files):
            duplicates_found = len(files) - len(unique_files)
            logger.warning(f"[SCAN ERROR] Found {duplicates_found} duplicate file paths in discovered batch!")
            logger.warning(f"[SCAN ERROR] Original count: {len(files)}, Unique count: {len(unique_files)}")
            # Log the duplicate files
            seen = set()
            for f in files:
                if f in seen:
                    logger.warning(f"[SCAN ERROR] Duplicate file: {f.name}")
                seen.add(f)
            files = unique_files
        
        print(f"Discovered batch: {len(files)} new files (scanned {scanned_count} total)")
        return files

    async def scan_upload_directory(self) -> List[Path]:
        """Scan the upload directory for supported files with async I/O"""
        upload_path = Path(self.config.upload_directory)
        
        if not upload_path.exists():
            logger.error(f"Upload directory does not exist: {upload_path}")
            return []
        
        # Use ThreadPoolExecutor for file system operations
        loop = asyncio.get_event_loop()
        
        def scan_files():
            from .utils import is_image_file, is_video_file
            
            files = []
            
            # Scan all files recursively
            for file_path in upload_path.rglob('*'):
                # Skip directories
                if not file_path.is_file():
                    continue
                
                # Use python-magic to detect actual media files (more robust than extension matching)
                if is_image_file(file_path) or is_video_file(file_path):
                    files.append(file_path)
            
            # Sort by modification time (newest first)
            return sorted(files, key=lambda f: f.stat().st_mtime, reverse=True)
        
        files = await loop.run_in_executor(None, scan_files)
        return files
    
    async def upload_single_file_optimized(self, file_path: Path) -> Tuple[bool, str, Optional[Dict]]:
        """Optimized upload with dedicated connection. Returns (success, reason, post_data)"""
        # Check if file is already being uploaded (prevent duplicates)
        file_key = str(file_path.resolve())
        if file_key in self._uploading_files:
            logger.warning(f"[UPLOAD DUPLICATE] {file_path.name} - File already being uploaded, skipping")
            return False, "File already being uploaded", None
        
        # Add diagnostic logging to track upload attempts
        logger.debug(f"[UPLOAD START] {file_path.name} - Semaphore acquired")
        
        async with self.upload_semaphore:
            try:
                # Mark file as being uploaded
                self._uploading_files.add(file_key)
                
                # Check if file still exists
                if not file_path.exists():
                    logger.warning(f"[UPLOAD SKIP] {file_path.name} - File no longer exists")
                    self.metrics.files_failed += 1
                    return False, "File no longer exists", None
                
                # Create dedicated API connection for this upload
                timeout = aiohttp.ClientTimeout(total=self.config.upload_timeout)
                connector = aiohttp.TCPConnector(limit=1)
                
                async with SzurubooruAPI(self.config) as api:
                    api.session._timeout = timeout
                    api.session._connector = connector
                    
                    # Determine initial tags based on file type
                    initial_tags = []
                    if is_video_file(file_path):
                        # For video files, add video tag instead of tagme
                        initial_tags = [self.config.video_tag]
                        print(f"Video file detected: {file_path.name} - adding 'video' tag")
                    else:
                        # For images, add tagme tag for AI processing
                        initial_tags = [self.config.tagme_tag]
                    
                    # Ensure initial tags exist with proper categories
                    for tag in initial_tags:
                        if tag == self.config.video_tag or tag == self.config.tagme_tag:
                            await api.create_tag_with_category(tag, "meta")
                    
                    # Upload with appropriate initial tags
                    logger.debug(f"[UPLOAD API] {file_path.name} - Calling upload_post API")
                    result = await api.upload_post(file_path, tags=initial_tags, safety="unsafe")
                    logger.debug(f"[UPLOAD API] {file_path.name} - API call completed")
                    
                    # Handle MIME type issues for video files
                    if isinstance(result, dict) and "error" in result:
                        error_text = result["error"]
                        if "application/octet-stream" in error_text and is_video_file(file_path):
                            print(f"{YELLOW}[WARNING]{RESET} MIME type issue detected for {file_path.name}")
                            print(f"   Error: {error_text}")
                            print(f"   File size: {file_path.stat().st_size} bytes")
                            
                            # Try to verify if it's actually a video file by checking file header
                            try:
                                with open(file_path, 'rb') as f:
                                    header = f.read(12)
                                    # Check for common video file signatures
                                    if (header.startswith(b'\x00\x00\x00') and header[4:8] in [b'ftyp', b'mdat']) or \
                                       header.startswith(b'RIFF') or \
                                       header.startswith(b'\x1a\x45\xdf\xa3'):
                                        print(f"   {GREEN}[SUCCESS]{RESET} File header appears to be valid video format")
                                        print(f"   {YELLOW}[CONFIG]{RESET} Trying alternative upload method...")
                                        
                                        # Try uploading with explicit content type
                                        print(f"   {YELLOW}[CONFIG]{RESET} Trying upload with explicit video content type...")
                                        fallback_result = await self._upload_with_content_type(api, file_path, [self.config.video_tag], "video/mp4")
                                        if isinstance(fallback_result, dict) and "error" not in fallback_result:
                                            print(f"   {GREEN}[SUCCESS]{RESET} Fallback upload successful!")
                                            result = fallback_result
                                        else:
                                            print(f"   {RED}[ERROR]{RESET} Fallback upload also failed")
                                            if isinstance(fallback_result, dict) and "error" in fallback_result:
                                                print(f"   Fallback error: {fallback_result['error']}")
                                            
                                            # Try alternative approach
                                            print(f"   {YELLOW}[CONFIG]{RESET} Trying alternative binary upload method...")
                                            try:
                                                alt_result = await self._upload_as_binary(api, file_path, [self.config.video_tag])
                                                if isinstance(alt_result, dict) and "error" not in alt_result:
                                                    print(f"   {GREEN}[SUCCESS]{RESET} Alternative upload successful!")
                                                    result = alt_result
                                                else:
                                                    print(f"   {RED}[ERROR]{RESET} Alternative upload also failed")
                                                    if isinstance(alt_result, dict) and "error" in alt_result:
                                                        print(f"   Alternative error: {alt_result['error']}")
                                            except Exception as alt_e:
                                                print(f"   {RED}[ERROR]{RESET} Alternative upload exception: {alt_e}")
                                        
                                        # If all methods failed and skip_problematic_videos is enabled
                                        if self.config.skip_problematic_videos:
                                            print(f"   {YELLOW}[SKIP]{RESET} Skipping problematic video file (skip_problematic_videos enabled)")
                                            self.metrics.files_uploaded += 1
                                            return True, "Skipped problematic video", None
                                    else:
                                        print(f"   {RED}[ERROR]{RESET} File header doesn't match video format")
                                        print(f"   Header: {header.hex()}")
                            except Exception as e:
                                print(f"   {RED}[ERROR]{RESET} Error checking file header: {e}")
                    
                    # Debug logging for API responses if enabled
                    if isinstance(result, dict) and "error" in result:
                        logger.info(f"API Error for {file_path.name}: {result}")
                        # Only show console output if debug is explicitly enabled
                        if self.config.debug_api_errors and self.metrics.files_failed < 10:
                            print(f"Sample API error for {file_path.name}: {result}")
                    
                    if not result:
                        self.metrics.files_failed += 1
                        return False, "Upload failed - No response", None
                    
                    # Check if upload failed with an error
                    if isinstance(result, dict) and "error" in result:
                        error_text = result["error"]
                        status_code = result.get("status", "unknown")
                        
                        # Parse JSON error if it's JSON format
                        detailed_error = error_text
                        try:
                            if error_text.startswith('{'):
                                import json
                                error_data = json.loads(error_text)
                                if isinstance(error_data, dict):
                                    detailed_error = error_data.get('description', error_data.get('message', str(error_data)))
                        except:
                            pass
                        
                        # Check for already uploaded/duplicate error
                        if any(keyword in error_text.lower() for keyword in ['already uploaded', 'duplicate', 'already exists', 'content clash']):
                            # Consider already uploaded as success for metrics
                            self.metrics.files_uploaded += 1
                            return True, f"Duplicate (HTTP {status_code}): {detailed_error}", None
                        else:
                            self.metrics.files_failed += 1
                            return False, f"API Error (HTTP {status_code}): {detailed_error}", None
                    
                    self.metrics.files_uploaded += 1
                    logger.debug(f"[UPLOAD SUCCESS] {file_path.name} - Upload completed successfully")
                    return True, "Success", result
                    
            except Exception as e:
                self.metrics.files_failed += 1
                return False, str(e), None
            finally:
                # Always remove file from uploading set when done
                self._uploading_files.discard(file_key)

    async def parallel_upload_batch(self, files: List[Path]) -> List[Tuple[Path, bool, Optional[Dict]]]:
        """Upload multiple files in parallel with optimized performance"""
        if not files:
            return []
        
        # Deduplicate file list (safety check)
        unique_check = set(files)
        if len(unique_check) != len(files):
            duplicates_found = len(files) - len(unique_check)
            logger.warning(f"Found {duplicates_found} duplicate file paths in batch - deduplicating")
            files = list(unique_check)
        
        print(f"Starting parallel upload of {len(files)} files...")
        
        # Create upload tasks
        tasks = []
        file_names = []
        for file_path in files:
            logger.debug(f"[BATCH TASK] Creating upload task for {file_path.name}")
            # Check for duplicate filenames in the same batch
            if file_path.name in file_names:
                logger.error(f"[BATCH ERROR] Duplicate filename in batch: {file_path.name}")
            file_names.append(file_path.name)
            task = self.upload_single_file_optimized(file_path)
            tasks.append(task)
        
        # Execute all uploads concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results and handle file deletion
        processed_results = []
        failure_reasons = {}
        
        for i, result in enumerate(results):
            file_path = files[i]
            
            if isinstance(result, Exception):
                reason = f"Task exception: {str(result)}"
                logger.error(f"Upload task failed for {file_path}: {result}")
                processed_results.append((file_path, False, None))
                self.metrics.files_failed += 1
                failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
            else:
                success, reason, post_data = result
                processed_results.append((file_path, success, post_data))
                
                if success:
                    # Don't mark as processed here - we'll do it before file deletion to prevent race conditions
                    pass
                else:
                    failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
        
        # Report detailed results summary
        successful_count = len([r for r in processed_results if r[1]])
        duplicate_count = len([r for r in processed_results if r[1] and "Duplicate" in (r[2] if isinstance(r[2], str) else "")])
        
        if failure_reasons:
            # Separate duplicates from real failures
            duplicate_reasons = {k: v for k, v in failure_reasons.items() if "Duplicate" in k}
            real_failures = {k: v for k, v in failure_reasons.items() if "Duplicate" not in k}
            
            print(f"\n{CYAN}[STATS]{RESET} Upload Results Summary:")
            print(f"{GREEN}[SUCCESS]{RESET} New uploads: {successful_count} files")
            if duplicate_reasons:
                duplicate_total = sum(duplicate_reasons.values())
                print(f"{BLUE}[INFO]{RESET} Duplicates (already uploaded): {duplicate_total} files")
            if real_failures:
                real_failure_total = sum(real_failures.values())
                print(f"{RED}[ERROR]{RESET} Real failures: {real_failure_total} files")
            
            if real_failures:
                print(f"\nFailure breakdown:")
                sorted_failures = sorted(real_failures.items(), key=lambda x: x[1], reverse=True)
                for reason, count in sorted_failures:
                    percentage = (count / len(files)) * 100
                    print(f"  • {reason}: {count} files ({percentage:.1f}%)")
            
            if duplicate_reasons and not self.config.debug_api_errors:
                print(f"{BLUE}[NOTE]{RESET} {duplicate_total} files were already uploaded (skipped as duplicates)")
                
        else:
            # Check if there were any duplicates in successful results
            duplicate_count = sum(1 for _, success, post_data in processed_results if success and not post_data)
            new_upload_count = sum(1 for _, success, post_data in processed_results if success and post_data)
            
            if duplicate_count > 0 and new_upload_count > 0:
                print(f"\n{GREEN}[SUCCESS]{RESET} All {len(files)} files processed successfully ({new_upload_count} new uploads, {duplicate_count} duplicates)!")
            elif duplicate_count > 0:
                print(f"\n{GREEN}[SUCCESS]{RESET} All {len(files)} files processed successfully (all duplicates)!")
            else:
                print(f"\n{GREEN}[SUCCESS]{RESET} All {len(files)} files processed successfully (all new uploads)!")
        
        return processed_results
    
    async def update_post_tags_batch(self, posts_data: List[Tuple[Path, Dict]], tag_results: List[Tuple[List[str], List[str], str]]):
        """Update tags for multiple posts concurrently with category assignment"""
        if not posts_data or not tag_results:
            return
        
        print(f"Updating tags for {len(posts_data)} posts...")
        
        # Create update tasks
        tasks = []
        
        for (file_path, post_data), (general_tags, character_tags, safety) in zip(posts_data, tag_results):
            if post_data:
                task = self._update_single_post_tags_with_categories(post_data, general_tags, character_tags, safety)
                tasks.append(task)
        
        # Execute all updates concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Count successful updates
        successful_updates = sum(1 for result in results if result and not isinstance(result, Exception))
        self.metrics.files_tagged += successful_updates
        
        print(f"Successfully updated {successful_updates}/{len(posts_data)} posts")
    
    async def _update_single_post_tags_with_categories(self, post_data: Dict, general_tags: List[str], character_tags: List[str], safety: str) -> bool:
        """Update tags for a single post with category assignment"""
        try:
            post_id = post_data.get('id')
            version = post_data.get('version', 1)
            
            if not post_id:
                logger.warning(f"No post ID found in post data")
                return False
            
            # Get current tags and remove 'tagme' tag
            current_tags = [tag['names'][0] for tag in post_data.get('tags', [])]
            original_tag_count = len(current_tags)
            
            if self.config.tagme_tag in current_tags:
                current_tags.remove(self.config.tagme_tag)
            
            # Combine all tags
            all_tags = list(set(current_tags + (general_tags or []) + (character_tags or [])))
            
            # Debug logging
            logger.info(f"Post {post_id}: {original_tag_count} original tags -> {len(all_tags)} final tags (added {len(general_tags or [])} general + {len(character_tags or [])} character AI tags)")
            
            # Prepare update data
            update_data = {
                'version': version,
                'tags': all_tags
            }
            
            # Add safety if different from default
            if safety != "unsafe":
                update_data['safety'] = safety
            
            # Create dedicated API connection for update
            async with SzurubooruAPI(self.config) as api:
                # First, ensure all tags exist with proper categories
                await self._ensure_tags_with_categories(api, general_tags, character_tags)
                
                # Then update the post
                async with api.session.put(
                    f"{self.config.szurubooru_url}/api/post/{post_id}",
                    json=update_data
                ) as response:
                    if response.status == 200:
                        return True
                    elif response.status == 409:
                        # Retry with incremented version
                        update_data['version'] = version + 1
                        async with api.session.put(
                            f"{self.config.szurubooru_url}/api/post/{post_id}",
                            json=update_data
                        ) as retry_response:
                            success = retry_response.status == 200
                            if not success:
                                error_text = await retry_response.text()
                                logger.warning(f"Post {post_id} update retry failed: {error_text}")
                            return success
                    else:
                        error_text = await response.text()
                        logger.warning(f"Post {post_id} update failed with status {response.status}: {error_text}")
                        return False
        
        except Exception as e:
            logger.warning(f"Failed to update post {post_data.get('id', 'unknown')}: {e}")
            return False
    
    async def _ensure_tags_with_categories(self, api, general_tags: List[str], character_tags: List[str]):
        """Ensure all tags exist with proper categories"""
        try:
            # Create general tags with 'default' category
            for tag in general_tags or []:
                if tag and tag.strip():
                    await api.create_tag_with_category(tag.strip(), "default")
            
            # Create character tags with 'character' category
            for tag in character_tags or []:
                if tag and tag.strip():
                    await api.create_tag_with_category(tag.strip(), "character")
                    
        except Exception as e:
            logger.warning(f"Error ensuring tags with categories: {e}")
    
    async def _update_single_post_tags(self, post_data: Dict, new_tags: List[str], safety: str) -> bool:
        """Update tags for a single post (legacy method for backward compatibility)"""
        try:
            post_id = post_data.get('id')
            version = post_data.get('version', 1)
            
            if not post_id:
                logger.warning(f"No post ID found in post data")
                return False
            
            # Get current tags and remove 'tagme' tag
            current_tags = [tag['names'][0] for tag in post_data.get('tags', [])]
            original_tag_count = len(current_tags)
            
            if self.config.tagme_tag in current_tags:
                current_tags.remove(self.config.tagme_tag)
            
            # Combine tags
            all_tags = list(set(current_tags + (new_tags or [])))
            
            # Debug logging
            logger.info(f"Post {post_id}: {original_tag_count} original tags -> {len(all_tags)} final tags (added {len(new_tags or [])} AI tags)")
            
            # Prepare update data
            update_data = {
                'version': version,
                'tags': all_tags
            }
            
            # Add safety if different from default
            if safety != "unsafe":
                update_data['safety'] = safety
            
            # Create dedicated API connection for update
            async with SzurubooruAPI(self.config) as api:
                async with api.session.put(
                    f"{self.config.szurubooru_url}/api/post/{post_id}",
                    json=update_data
                ) as response:
                    if response.status == 200:
                        return True
                    elif response.status == 409:
                        # Retry with incremented version
                        update_data['version'] = version + 1
                        async with api.session.put(
                            f"{self.config.szurubooru_url}/api/post/{post_id}",
                            json=update_data
                        ) as retry_response:
                            success = retry_response.status == 200
                            if not success:
                                error_text = await retry_response.text()
                                logger.warning(f"Post {post_id} update retry failed: {error_text}")
                            return success
                    else:
                        error_text = await response.text()
                        logger.warning(f"Post {post_id} update failed with status {response.status}: {error_text}")
                        return False
        
        except Exception as e:
            logger.warning(f"Failed to update post {post_data.get('id', 'unknown')}: {e}")
            return False

    async def run_pipeline_processing(self, files: List[Path]) -> Dict[str, int]:
        """Run high-performance pipeline processing with concurrent upload and tagging"""
        if not files:
            return {"uploaded": 0, "tagged": 0, "failed": 0}
        
        self.metrics.start_time = time.time()
        total_files = len(files)
        
        print(f"Starting high-performance pipeline for {total_files} files...")
        print(f"Configuration: {self.config.max_concurrent_uploads} concurrent uploads, {self.config.gpu_batch_size} GPU batch size")
        
        # Phase 1: Parallel Upload
        upload_results = await self.parallel_upload_batch(files)
        
        # Separate results into categories
        successful_uploads = []
        successful_processing = 0
        duplicate_count = 0
        
        for file_path, success, post_data in upload_results:
            if success:
                successful_processing += 1
                if post_data:
                    # New upload - add to AI tagging queue
                    successful_uploads.append((file_path, post_data))
                else:
                    # Duplicate - count separately
                    duplicate_count += 1
        
        new_uploads_count = len(successful_uploads)
        failed_count = total_files - successful_processing
        
        if duplicate_count > 0:
            print(f"Upload phase complete: {new_uploads_count} new uploads, {duplicate_count} duplicates, {failed_count} failed")
        else:
            print(f"Upload phase complete: {new_uploads_count}/{total_files} new uploads")
        
        # Phase 2: Parallel AI Tagging
        tagged_count = 0
        if successful_uploads:
            try:
                # Extract file paths for tagging, filtering out video files
                files_to_tag = []
                video_files = []
                for file_path, _ in successful_uploads:
                    if is_video_file(file_path):
                        video_files.append(file_path)
                    else:
                        files_to_tag.append(file_path)
                
                if video_files:
                    print(f"Skipping AI tagging for {len(video_files)} video files")
                
                if files_to_tag:
                    print(f"Starting AI tagging for {len(files_to_tag)} image files...")
                    
                    # Check if WD14 is available
                    if not WD14_AVAILABLE:
                        print(f"{YELLOW}[WARNING]{RESET} WD14 Tagger not available - skipping AI tagging")
                        # Still need to remove 'tagme' tags from uploaded posts
                        await self._remove_tagme_from_uploads(successful_uploads)
                    elif not self.config.gpu_enabled:
                        print(f"{YELLOW}[WARNING]{RESET} GPU disabled - using CPU for AI tagging")
                        await self._tag_uploaded_files(successful_uploads, files_to_tag)
                        tagged_count = len(files_to_tag)
                    else:
                        # GPU tagging
                        await self._tag_uploaded_files(successful_uploads, files_to_tag)
                        tagged_count = len(files_to_tag)
                else:
                    print("No image files to tag (all were videos)")
                    await self._remove_tagme_from_uploads(successful_uploads)
                
            except Exception as e:
                logger.error(f"Tagging phase failed: {e}")
                import traceback
                logger.error(f"Tagging error traceback: {traceback.format_exc()}")
                print(f"{YELLOW}[WARNING]{RESET} Tagging failed: {e}")
        else:
            print("No successful uploads to tag")
        
        # Handle file deletion for all processed files
        if self.config.delete_after_upload:
            # Delete duplicate files
            if duplicate_count > 0:
                duplicate_files = []
                for file_path, success, post_data in upload_results:
                    if success and not post_data:
                        duplicate_files.append(file_path)
                
                if duplicate_files:
                    print(f"Deleting {len(duplicate_files)} duplicate files...")
                    deleted_count = 0
                    for file_path in duplicate_files:
                        try:
                            if file_path.exists():
                                # Mark as processed BEFORE deletion to prevent race conditions
                                if self.config.track_processed_files:
                                    self._mark_file_processed(file_path)
                                await asyncio.get_event_loop().run_in_executor(None, file_path.unlink)
                                deleted_count += 1
                        except Exception as e:
                            logger.warning(f"Failed to delete duplicate {file_path}: {e}")
                    print(f"Successfully deleted {deleted_count} duplicate files")
            
            # Delete successfully uploaded files
            if successful_uploads:
                files_to_delete = []
                for file_path, _ in successful_uploads:
                    if file_path.exists():
                        files_to_delete.append(file_path)
                
                if files_to_delete:
                    print(f"Deleting {len(files_to_delete)} uploaded files...")
                    deleted_count = 0
                    for file_path in files_to_delete:
                        try:
                            if file_path.exists():
                                # Mark as processed BEFORE deletion to prevent race conditions
                                if self.config.track_processed_files:
                                    logger.debug(f"[DELETE MARK] {file_path.name} - Marking as processed before deletion")
                                    self._mark_file_processed(file_path)
                                logger.debug(f"[DELETE FILE] {file_path.name} - Deleting file")
                                await asyncio.get_event_loop().run_in_executor(None, file_path.unlink)
                                deleted_count += 1
                                logger.debug(f"[DELETE SUCCESS] {file_path.name} - File deleted successfully")
                            else:
                                logger.debug(f"[DELETE SKIP] {file_path.name} - File no longer exists")
                        except Exception as e:
                            logger.warning(f"Failed to delete uploaded file {file_path}: {e}")
                    print(f"Successfully deleted {deleted_count} uploaded files")
        
        # Calculate final metrics
        elapsed_time = time.time() - self.metrics.start_time
        self.metrics.calculate_rates(elapsed_time)
        
        return {
            "uploaded": new_uploads_count,
            "duplicates": duplicate_count,
            "tagged": tagged_count,
            "failed": failed_count,
            "elapsed_time": elapsed_time,
            "upload_rate": self.metrics.upload_rate,
            "tagging_rate": self.metrics.tagging_rate,
            "total_processed": successful_processing
        }
    
    async def _tag_uploaded_files(self, successful_uploads: List[Tuple[Path, Dict]], files_to_tag: List[Path]):
        """Tag uploaded files and update posts"""
        try:
            # Check if files still exist before tagging
            existing_files = [f for f in files_to_tag if f.exists()]
            missing_files = len(files_to_tag) - len(existing_files)
            if missing_files > 0:
                print(f"Warning: {missing_files} files missing for AI tagging")
            
            if existing_files:
                # Batch tag images using GPU
                print(f"Running AI tagger on {len(existing_files)} image files...")
                tag_results = await self.wd_tagger.tag_images_batch(existing_files)
                
                # Debug: Check tag results
                if tag_results:
                    non_empty_general = len([general for general, _, _ in tag_results if general])
                    non_empty_character = len([character for _, character, _ in tag_results if character])
                    print(f"AI tagging results: {non_empty_general}/{len(tag_results)} files got general tags, {non_empty_character}/{len(tag_results)} files got character tags")
                
                # Update posts with tags
                if tag_results:
                    # Need to match files to their post data
                    files_with_posts = []
                    for file_path, post_data in successful_uploads:
                        if file_path in existing_files:
                            files_with_posts.append((file_path, post_data))
                    
                    await self.update_post_tags_batch(files_with_posts, tag_results)
            else:
                print("No files available for AI tagging")
                await self._remove_tagme_from_uploads(successful_uploads)
                
        except Exception as e:
            logger.error(f"Error in _tag_uploaded_files: {e}")
            await self._remove_tagme_from_uploads(successful_uploads)
    
    async def _remove_tagme_from_uploads(self, successful_uploads: List[Tuple[Path, Dict]]):
        """Remove 'tagme' tags from uploaded posts"""
        try:
            print("Removing 'tagme' tags from uploaded posts...")
            
            # Create tasks for removing tagme tags
            tasks = []
            for file_path, post_data in successful_uploads:
                task = self._remove_tagme_from_post(post_data)
                tasks.append(task)
            
            # Execute all tasks concurrently
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Count successful updates
            successful_updates = sum(1 for result in results if result and not isinstance(result, Exception))
            print(f"Successfully removed 'tagme' tags from {successful_updates}/{len(successful_uploads)} posts")
            
        except Exception as e:
            logger.error(f"Error removing tagme tags: {e}")
    
    async def _remove_tagme_from_post(self, post_data: Dict) -> bool:
        """Remove 'tagme' tag from a single post"""
        try:
            post_id = post_data.get('id')
            version = post_data.get('version', 1)
            
            if not post_id:
                return False
            
            # Get current tags and remove 'tagme' tag
            current_tags = [tag['names'][0] for tag in post_data.get('tags', [])]
            if self.config.tagme_tag in current_tags:
                current_tags.remove(self.config.tagme_tag)
            
            # Prepare update data
            update_data = {
                'version': version,
                'tags': current_tags
            }
            
            # Create dedicated API connection for update
            async with SzurubooruAPI(self.config) as api:
                async with api.session.put(
                    f"{self.config.szurubooru_url}/api/post/{post_id}",
                    json=update_data
                ) as response:
                    if response.status == 200:
                        return True
                    elif response.status == 409:
                        # Retry with incremented version
                        update_data['version'] = version + 1
                        async with api.session.put(
                            f"{self.config.szurubooru_url}/api/post/{post_id}",
                            json=update_data
                        ) as retry_response:
                            return retry_response.status == 200
                    else:
                        return False
        
        except Exception as e:
            logger.warning(f"Failed to remove tagme from post {post_data.get('id', 'unknown')}: {e}")
            return False
    
    async def auto_tag_posts(self) -> int:
        """Auto-tag posts that have the 'tagme' tag - continues until no more posts need tagging"""
        if not WD14_AVAILABLE:
            print("WD14 Tagger not available for auto-tagging")
            return 0
        
        try:
            # Initialize WD14 Tagger
            self.wd_tagger.initialize()
            
            async with SzurubooruAPI(self.config) as api:
                # Test connection
                if not await api.test_connection():
                    print("Failed to connect to Szurubooru")
                    return 0
                
                total_processed = 0
                total_failed = 0
                round_num = 1
                
                # Keep processing until no more posts need tagging
                while True:
                    print(f"\n{BLUE}[PROCESS]{RESET} Tagging Round {round_num}")
                    print("=" * 50)
                    
                    # Get posts with 'tagme' tag
                    tagme_posts = await api.get_posts_with_tag(self.config.tagme_tag)
                    
                    # Also get posts with no tags at all
                    untagged_posts = await api.get_untagged_posts()
                    
                    # Combine both lists, avoiding duplicates
                    posts_dict = {}
                    
                    # Add tagme posts
                    if tagme_posts:
                        for post in tagme_posts:
                            posts_dict[post['id']] = post
                    
                    # Add untagged posts
                    if untagged_posts:
                        for post in untagged_posts:
                            if post['id'] not in posts_dict:
                                posts_dict[post['id']] = post
                    
                    posts = list(posts_dict.values())
                    
                    if not posts:
                        print(f"{GREEN}[SUCCESS]{RESET} No more posts found that need tagging")
                        break
                    
                    tagme_count = len(tagme_posts) if tagme_posts else 0
                    untagged_count = len(untagged_posts) if untagged_posts else 0
                    total_unique = len(posts)
                    
                    print(f"Found posts needing tagging in round {round_num}:")
                    print(f"  • {tagme_count} posts with 'tagme' tag")
                    print(f"  • {untagged_count} posts with no tags")
                    print(f"  • {total_unique} total unique posts to process")
                    
                    # Process posts in parallel batches
                    round_processed = 0
                    round_failed = 0
                    batch_size = self.config.tagging_workers or 4
                    
                    # Process in batches for better memory management
                    for i in range(0, len(posts), batch_size):
                        batch = posts[i:i + batch_size]
                        print(f"Processing batch {i//batch_size + 1}/{(len(posts) + batch_size - 1)//batch_size} ({len(batch)} posts)")
                        
                        # Process batch in parallel
                        batch_results = await self._process_tagging_batch(api, batch)
                        
                        # Update counters
                        for success in batch_results:
                            if success:
                                round_processed += 1
                            else:
                                round_failed += 1
                        
                        print(f"Batch complete: {sum(batch_results)}/{len(batch)} successful")
                    
                    total_processed += round_processed
                    total_failed += round_failed
                    
                    print(f"Round {round_num} complete: {round_processed} processed, {round_failed} failed")
                    
                    # If no posts were processed in this round, break to avoid infinite loop
                    if round_processed == 0:
                        print(f"{YELLOW}[WARNING]{RESET} No posts were successfully processed this round. Stopping to avoid infinite loop.")
                        break
                    
                    round_num += 1
                
                print(f"\n{GREEN}[COMPLETE]{RESET} Auto-tagging complete!")
                print(f"Total processed: {total_processed} posts")
                print(f"Total failed: {total_failed} posts")
                print(f"Rounds completed: {round_num - 1}")
                
                return total_processed
                
        except Exception as e:
            print(f"Error in auto-tagging process: {e}")
            return 0
    
    async def _process_tagging_batch(self, api, posts_batch: List[Dict]) -> List[bool]:
        """Process a batch of posts for tagging in parallel"""
        # Create tasks for parallel processing
        tasks = []
        for post in posts_batch:
            task = self._tag_single_post(api, post)
            tasks.append(task)
        
        # Execute all tagging tasks concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        success_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                print(f"Error tagging post {posts_batch[i].get('id', 'unknown')}: {result}")
                success_results.append(False)
            else:
                success_results.append(result)
        
        return success_results
    
    async def _tag_single_post(self, api, post: Dict) -> bool:
        """Tag a single post"""
        try:
            post_id = post['id']
            version = post['version']
            current_tags = [tag['names'][0] for tag in post.get('tags', [])]
            
            # Handle video files
            if post.get('type') == 'video':
                # Remove tagme tag from current tags if present
                if self.config.tagme_tag in current_tags:
                    current_tags.remove(self.config.tagme_tag)
                
                # Add video tag if not already present
                if self.config.video_tag not in current_tags:
                    current_tags.append(self.config.video_tag)
                    
                    # Ensure video tag exists with meta category
                    await api.create_tag_with_category(self.config.video_tag, "meta")
                    
                    # Update post with video tag
                    update_data = {
                        'version': version,
                        'tags': current_tags
                    }
                    
                    async with api.session.put(
                        f"{self.config.szurubooru_url}/api/post/{post_id}",
                        json=update_data
                    ) as response:
                        if response.status == 200:
                            return True
                        else:
                            error_text = await response.text()
                            print(f"Failed to update video post {post_id}: {error_text}")
                            return False
                else:
                    # Video tag already present, just remove tagme if needed
                    if self.config.tagme_tag in [tag['names'][0] for tag in post.get('tags', [])]:
                        update_data = {
                            'version': version,
                            'tags': current_tags
                        }
                        
                        async with api.session.put(
                            f"{self.config.szurubooru_url}/api/post/{post_id}",
                            json=update_data
                        ) as response:
                            return response.status == 200
                    return True
            
            # Remove tagme tag from current tags if present
            if self.config.tagme_tag in current_tags:
                current_tags.remove(self.config.tagme_tag)
            
            # Get content URL and download image for tagging
            content_url = post['contentUrl']
            if not content_url:
                return False
            
            # Construct full URL if it's a relative path
            if not content_url.startswith('http'):
                content_url = f"{self.config.szurubooru_url}/{content_url.lstrip('/')}"
            
            # Download image temporarily
            temp_path = Path(f"temp_{post_id}.jpg")
            try:
                async with api.session.get(content_url) as response:
                    if response.status == 200:
                        async with aiofiles.open(temp_path, 'wb') as f:
                            await f.write(await response.read())
                    else:
                        return False
                
                # Tag the image
                general_tags, character_tags, safety = await self.wd_tagger.tag_image(temp_path)
                
                # Combine existing tags with new tags
                all_tags = list(set(current_tags + (general_tags or []) + (character_tags or [])))
                
                # Ensure tags exist with proper categories
                for tag in general_tags or []:
                    if tag and tag.strip():
                        await api.create_tag_with_category(tag.strip(), "default")
                
                for tag in character_tags or []:
                    if tag and tag.strip():
                        await api.create_tag_with_category(tag.strip(), "character")
                
                # Update both tags and safety in a single request
                update_data = {
                    'version': version,
                    'tags': all_tags
                }
                
                # Add safety if it's different from current
                current_safety = post.get('safety', 'unsafe')
                if safety != current_safety:
                    update_data['safety'] = safety
                
                # Update post
                async with api.session.put(
                    f"{self.config.szurubooru_url}/api/post/{post_id}",
                    json=update_data
                ) as response:
                    if response.status == 200:
                        return True
                    else:
                        error_text = await response.text()
                        print(f"Failed to update post {post_id}: {error_text}")
                        return False
                        
            finally:
                # Clean up temporary file
                if temp_path.exists():
                    temp_path.unlink()
                    
        except Exception as e:
            print(f"Error processing post {post.get('id', 'unknown')}: {e}")
            return False
    
    async def process_untagged_posts(self) -> int:
        """Process posts that have no tags at all"""
        if not WD14_AVAILABLE:
            print("WD14 Tagger not available for processing untagged posts")
            return 0
        
        try:
            # Initialize WD14 Tagger
            self.wd_tagger.initialize()
            
            async with SzurubooruAPI(self.config) as api:
                # Test connection
                if not await api.test_connection():
                    print("Failed to connect to Szurubooru")
                    return 0
                
                # Get posts with no tags
                posts = await api.get_untagged_posts()
                if not posts:
                    print("No untagged posts found")
                    return 0
                
                print(f"Found {len(posts)} untagged posts")
                
                processed_count = 0
                failed_count = 0
                video_count = 0
                
                # Create progress bar for processing
                with tqdm(posts, desc="Processing untagged posts", unit="post") as pbar:
                    for post in pbar:
                        try:
                            post_id = post['id']
                            version = post['version']
                            post_type = post.get('type', 'image')
                            
                            # Check if it's a video
                            if post_type in ['video', 'animation']:
                                # Add video tag to untagged videos
                                await api.create_tag_with_category(self.config.video_tag, "meta")
                                
                                update_data = {
                                    'version': version,
                                    'tags': [self.config.video_tag]
                                }
                                
                                async with api.session.put(
                                    f"{self.config.szurubooru_url}/api/post/{post_id}",
                                    json=update_data
                                ) as response:
                                    if response.status == 200:
                                        processed_count += 1
                                        video_count += 1
                                    elif response.status == 409:
                                        # Try again with incremented version
                                        update_data['version'] = version + 1
                                        async with api.session.put(
                                            f"{self.config.szurubooru_url}/api/post/{post_id}",
                                            json=update_data
                                        ) as retry_response:
                                            if retry_response.status == 200:
                                                processed_count += 1
                                                video_count += 1
                                
                                continue
                            
                            # For images, proceed with AI tagging
                            current_tags = [tag['names'][0] for tag in post['tags']]
                            
                            # Get content URL and download image for tagging
                            content_url = post['contentUrl']
                            if not content_url:
                                continue
                            
                            # Construct full URL if it's a relative path
                            if not content_url.startswith('http'):
                                content_url = f"{self.config.szurubooru_url}/{content_url.lstrip('/')}"
                            
                            # Download image temporarily
                            temp_path = Path(f"temp_{post_id}.jpg")
                            try:
                                async with api.session.get(content_url) as response:
                                    if response.status == 200:
                                        async with aiofiles.open(temp_path, 'wb') as f:
                                            await f.write(await response.read())
                                    else:
                                        continue
                                
                                # Tag the image
                                general_tags, character_tags, safety = await self.wd_tagger.tag_image(temp_path)
                                
                                # Combine existing tags with new tags
                                all_tags = list(set(current_tags + (general_tags or []) + (character_tags or [])))
                                
                                # Ensure tags exist with proper categories
                                for tag in general_tags or []:
                                    if tag and tag.strip():
                                        await api.create_tag_with_category(tag.strip(), "default")
                                
                                for tag in character_tags or []:
                                    if tag and tag.strip():
                                        await api.create_tag_with_category(tag.strip(), "character")
                                
                                # Update both tags and safety in a single request
                                update_data = {
                                    'version': version,
                                    'tags': all_tags
                                }
                                
                                # Add safety if it's different from current
                                current_safety = post.get('safety', 'unsafe')
                                if safety != current_safety:
                                    update_data['safety'] = safety
                                
                                # Make the update request
                                async with api.session.put(
                                    f"{self.config.szurubooru_url}/api/post/{post_id}",
                                    json=update_data
                                ) as response:
                                    if response.status == 200:
                                        processed_count += 1
                                    elif response.status == 409:
                                        # Try again with incremented version
                                        update_data['version'] = version + 1
                                        async with api.session.put(
                                            f"{self.config.szurubooru_url}/api/post/{post_id}",
                                            json=update_data
                                        ) as retry_response:
                                            if retry_response.status == 200:
                                                processed_count += 1
                                
                            finally:
                                # Clean up temporary file
                                if temp_path.exists():
                                    temp_path.unlink()
                            
                            # Small delay to avoid overwhelming the server
                            await asyncio.sleep(0.1)
                            
                        except Exception as e:
                            failed_count += 1
                        
                        # Update progress bar description
                        pbar.set_postfix({
                            'Success': processed_count,
                            'Videos': video_count,
                            'Failed': failed_count
                        })
                
                print(f"Processed {processed_count} untagged posts ({video_count} videos tagged), {failed_count} failed")
                return processed_count
                
        except Exception as e:
            print(f"Error in processing untagged posts: {e}")
            return 0
    
    async def add_characters_to_all_posts(self, start_post_id: Optional[int] = None, end_post_id: Optional[int] = None) -> int:
        """Process posts in the instance and add missing character tags only"""
        if not WD14_AVAILABLE:
            print("WD14 Tagger not available for character tagging")
            return 0
        
        try:
            # Initialize WD14 Tagger
            self.wd_tagger.initialize()
            print("WD14 Tagger initialized for character-only processing")
            
            async with SzurubooruAPI(self.config) as api:
                # Test connection
                if not await api.test_connection():
                    print("Failed to connect to Szurubooru")
                    return 0
                
                # Get total number of posts
                total_posts = await api.get_total_post_count()
                if total_posts == 0:
                    print("No posts found in the instance")
                    return 0
                
                # Determine if using ID range query
                use_id_range = start_post_id is not None or end_post_id is not None
                
                # Set defaults for display
                display_start = start_post_id if start_post_id is not None else 1
                display_end = end_post_id if end_post_id is not None else total_posts
                
                # Validate range if specified
                if use_id_range:
                    if start_post_id is not None and start_post_id < 1:
                        print(f"Invalid start_post_id: {start_post_id}. Must be >= 1")
                        return 0
                    if start_post_id is not None and end_post_id is not None and end_post_id < start_post_id:
                        print(f"Invalid range: start_post_id ({start_post_id}) > end_post_id ({end_post_id})")
                        return 0
                    
                    # Set final range values
                    if start_post_id is None:
                        start_post_id = 1
                    if end_post_id is None:
                        end_post_id = total_posts
                    
                    # Get actual count of posts in the ID range
                    posts_to_process = await api.get_post_count_in_range(start_post_id, end_post_id)
                    
                    if posts_to_process == 0:
                        print(f"No posts found in ID range {start_post_id}..{end_post_id}")
                        return 0
                    
                    print(f"{BLUE}[TARGET]{RESET} Starting character tag addition for posts {start_post_id:,} to {end_post_id:,} ({posts_to_process:,} posts)")
                else:
                    posts_to_process = total_posts
                    print(f"{BLUE}[TARGET]{RESET} Starting character tag addition for ALL {total_posts:,} posts")
                
                print(f"{BLUE}[NOTE]{RESET} This will ONLY add character tags, leaving all existing tags intact")
                print("=" * 70)
                
                # Processing counters
                counters = {
                    'total_processed': 0,
                    'posts_updated': 0,
                    'characters_added': 0,
                    'videos_skipped': 0,
                    'failed_count': 0,
                    'already_had_characters': 0
                }
                
                # Batch processing settings
                configured_batch_size = self.config.batch_discovery_size or 100
                batch_size = min(configured_batch_size, 100)
                
                if configured_batch_size > 100:
                    logger.warning(f"batch_discovery_size ({configured_batch_size}) exceeds API limit of 100, clamping to maximum")
                
                # Create overall progress bar
                with tqdm(total=posts_to_process, desc="Processing posts", unit="post") as overall_pbar:
                    
                    if use_id_range:
                        # Use efficient ID range query
                        print(f"{BLUE}[TARGET]{RESET} Using efficient ID range query: id:{start_post_id}..{end_post_id}")
                        
                        # Process posts in batches using ID range query
                        for offset in range(0, posts_to_process, batch_size):
                            # Get batch of posts within the ID range
                            posts = await api.get_posts_by_id_range(start_post_id, end_post_id, limit=batch_size, offset=offset)
                            
                            if not posts:
                                break
                            
                            print(f"\n{BLUE}[BATCH]{RESET} Processing batch {offset//batch_size + 1}: {len(posts)} posts in range")
                            
                            # Filter out videos and prepare for parallel processing
                            image_posts = []
                            for post in posts:
                                post_type = post.get('type', 'image')
                                if post_type not in ['video', 'animation']:
                                    image_posts.append(post)
                                else:
                                    counters['videos_skipped'] += 1
                            
                            # Update progress for all posts in this batch
                            overall_pbar.update(len(posts))
                            
                            if not image_posts:
                                continue
                            
                            # Process image posts in parallel batches
                            await self._process_character_batch_parallel(api, image_posts, overall_pbar, counters)
                            
                            # Update total processed counter
                            counters['total_processed'] += len(posts)
                            
                            # Small delay between batches
                            await asyncio.sleep(0.2)
                    else:
                        # Use original approach for processing all posts
                        for offset in range(0, total_posts, batch_size):
                            # Get batch of posts
                            posts = await api.get_all_posts(limit=batch_size, offset=offset)
                            
                            if not posts:
                                break
                            
                            print(f"\n{BLUE}[BATCH]{RESET} Processing batch {offset//batch_size + 1}: posts {offset+1} to {offset+len(posts)}")
                            
                            # Filter out videos and prepare for parallel processing
                            image_posts = []
                            for post in posts:
                                post_type = post.get('type', 'image')
                                if post_type not in ['video', 'animation']:
                                    image_posts.append(post)
                                else:
                                    counters['videos_skipped'] += 1
                            
                            # Update progress for all posts in this batch
                            overall_pbar.update(len(posts))
                            
                            if not image_posts:
                                continue
                            
                            # Process image posts in parallel batches
                            await self._process_character_batch_parallel(api, image_posts, overall_pbar, counters)
                            
                            # Update total processed counter
                            counters['total_processed'] += len(posts)
                            
                            # Small delay between batches
                            await asyncio.sleep(0.2)
                
                # Final results
                print(f"\n{GREEN}[COMPLETE]{RESET} Character tagging complete!")
                print(f"{CYAN}[STATS]{RESET} Final Results:")
                print(f"  Total posts processed: {counters['total_processed']:,}")
                print(f"  Posts updated: {counters['posts_updated']:,}")
                print(f"  Character tags added: {counters['characters_added']:,}")
                print(f"  Posts that already had characters: {counters['already_had_characters']:,}")
                print(f"  Videos skipped: {counters['videos_skipped']:,}")
                print(f"  Failed: {counters['failed_count']:,}")
                
                success_rate = (counters['posts_updated'] / (counters['total_processed'] - counters['videos_skipped'])) * 100 if (counters['total_processed'] - counters['videos_skipped']) > 0 else 0
                print(f"  Success rate: {success_rate:.1f}%")
                
                return counters['posts_updated']
                
        except Exception as e:
            print(f"Error in character tagging process: {e}")
            import traceback
            print(f"Full traceback: {traceback.format_exc()}")
            return 0
    
    async def _process_character_batch_parallel(self, api, posts_batch: List[Dict], progress_bar, counters):
        """Process a batch of posts for character tagging in parallel"""
        if not posts_batch:
            return
        
        # Create tasks for parallel processing
        tasks = []
        for post in posts_batch:
            task = self._process_single_post_characters(api, post)
            tasks.append(task)
        
        # Execute all character tagging tasks concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results
        for i, result in enumerate(results):
            post = posts_batch[i]
            post_id = post.get('id', 'unknown')
            
            if isinstance(result, Exception):
                counters['failed_count'] += 1
                print(f"  {RED}[ERROR]{RESET} Error processing post {post_id}: {result}")
            elif result:
                # Result is a tuple: (updated, characters_added_count, already_had_characters)
                updated, chars_added, had_chars = result
                if updated:
                    counters['posts_updated'] += 1
                    counters['characters_added'] += chars_added
                    if chars_added > 0:
                        print(f"  {GREEN}[SUCCESS]{RESET} Post {post_id}: Added {chars_added} character tags")
                if had_chars:
                    counters['already_had_characters'] += 1
            else:
                counters['failed_count'] += 1
            
            # Update progress bar postfix with current stats
            progress_bar.set_postfix({
                'Updated': counters['posts_updated'],
                'Characters Added': counters['characters_added'],
                'Videos Skipped': counters['videos_skipped'],
                'Failed': counters['failed_count']
            })
    
    async def _process_single_post_characters(self, api, post: Dict) -> Tuple[bool, int, bool]:
        """Process a single post for character tagging. Returns (updated, characters_added, already_had_characters)"""
        try:
            post_id = post['id']
            version = post['version']
            current_tags = [tag['names'][0] for tag in post.get('tags', [])]
            
            # Get content URL and download image for tagging
            content_url = post['contentUrl']
            if not content_url:
                return False, 0, False
            
            # Construct full URL if it's a relative path
            if not content_url.startswith('http'):
                content_url = f"{self.config.szurubooru_url}/{content_url.lstrip('/')}"
            
            # Download image temporarily
            temp_path = Path(f"temp_char_{post_id}.jpg")
            try:
                async with api.session.get(content_url) as response:
                    if response.status == 200:
                        async with aiofiles.open(temp_path, 'wb') as f:
                            await f.write(await response.read())
                    else:
                        return False, 0, False
                
                # Run WD14 tagger and extract ONLY character tags
                tagger_result = await asyncio.get_event_loop().run_in_executor(
                    None, 
                    self.wd_tagger.tagger.tag, 
                    str(temp_path)
                )
                
                # Extract only character tags
                new_character_tags = self.wd_tagger._extract_character_tags_only(tagger_result)
                
                # Ensure character tags exist with proper category
                for tag in new_character_tags:
                    if tag and tag.strip():
                        await api.create_tag_with_category(tag.strip(), "character")
                
                if new_character_tags:
                    # Check which character tags are actually new
                    new_tags_to_add = []
                    for char_tag in new_character_tags:
                        if char_tag not in current_tags:
                            new_tags_to_add.append(char_tag)
                    
                    if new_tags_to_add:
                        # Add only the new character tags to existing tags
                        all_tags = current_tags + new_tags_to_add
                        
                        # Update the post
                        update_data = {
                            'version': version,
                            'tags': all_tags
                        }
                        
                        # Make the update request
                        async with api.session.put(
                            f"{self.config.szurubooru_url}/api/post/{post_id}",
                            json=update_data
                        ) as response:
                            if response.status == 200:
                                return True, len(new_tags_to_add), False
                            elif response.status == 409:
                                # Try again with incremented version
                                update_data['version'] = version + 1
                                async with api.session.put(
                                    f"{self.config.szurubooru_url}/api/post/{post_id}",
                                    json=update_data
                                ) as retry_response:
                                    if retry_response.status == 200:
                                        return True, len(new_tags_to_add), False
                                    else:
                                        return False, 0, False
                            else:
                                return False, 0, False
                    else:
                        # Post already has all detected character tags
                        return False, 0, True
                else:
                    # No character tags detected
                    return False, 0, False
                
            finally:
                # Clean up temporary file
                if temp_path.exists():
                    temp_path.unlink()
                    
        except Exception as e:
            return False, 0, False
    
    async def run_batched_optimized_cycle(self):
        """Run batched high-performance processing cycle for large directories"""
        print("Starting batched high-performance processing cycle")
        print(f"Batch size: {self.config.batch_discovery_size} files per batch")
        
        # Test API connection first
        async with SzurubooruAPI(self.config) as api:
            if not await api.test_connection():
                print("Failed to connect to Szurubooru API")
                return
        
        print("API connection verified")
        
        # Initialize metrics
        self.metrics.start_time = time.time()
        total_uploaded = 0
        total_tagged = 0
        total_failed = 0
        total_duplicates = 0
        batch_number = 1
        
        print("\nStarting batched processing...")
        print("="*60)
        
        while True:
            print(f"\nBatch #{batch_number}: Discovering files...")
            
            # Discover next batch of files
            batch_files = await self.scan_upload_directory_batch()
            
            if not batch_files:
                print(f"No more files found. Processing complete!")
                break
            
            print(f"Processing {len(batch_files)} files in batch #{batch_number}")
            
            # Process this batch
            batch_results = await self.run_pipeline_processing(batch_files)
            
            # Accumulate results
            total_uploaded += batch_results['uploaded']
            total_tagged += batch_results['tagged']
            total_failed += batch_results['failed']
            batch_duplicates = batch_results.get('duplicates', 0)
            total_duplicates += batch_duplicates
            
            # Show batch results
            print(f"Batch #{batch_number} complete:")
            print(f"  New uploads: {batch_results['uploaded']}")
            if batch_duplicates > 0:
                print(f"  Duplicates: {batch_duplicates}")
            print(f"  Tagged: {batch_results['tagged']}")
            if batch_results['failed'] > 0:
                print(f"  Failed: {batch_results['failed']}")
            print(f"  Batch rate: {batch_results['upload_rate']:.2f} files/sec")
            
            batch_number += 1
            
            # Small delay between batches
            await asyncio.sleep(0.5)
        
        # Calculate final metrics
        total_time = time.time() - self.metrics.start_time
        total_processed = total_uploaded + total_duplicates + total_failed
        overall_rate = total_processed / total_time if total_time > 0 else 0
        
        # Display final results
        print("\n" + "="*60)
        print("FINAL PERFORMANCE RESULTS")
        print("="*60)
        print(f"Total batches processed: {batch_number - 1}")
        print(f"New uploads:     {total_uploaded}")
        if total_duplicates > 0:
            print(f"Duplicates:      {total_duplicates}")
        print(f"Files tagged:    {total_tagged}")
        if total_failed > 0:
            print(f"Files failed:    {total_failed}")
        print(f"Total processed: {total_processed}")
        print(f"Total time:      {total_time:.2f} seconds")
        print(f"Overall rate:    {overall_rate:.2f} files/sec")
        print("="*60)
    
    async def run_optimized_cycle(self):
        """Run optimized high-performance processing cycle"""
        print("Starting optimized high-performance cycle")
        
        # Use batched processing for better performance
        await self.run_batched_optimized_cycle()
    
    async def run_upload_only_cycle(self):
        """Run batched upload-only cycle without tagging for maximum speed"""
        print("Starting batched upload-only cycle (maximum speed)")
        print(f"Batch size: {self.config.batch_discovery_size} files per batch")
        
        start_time = time.time()
        total_successful = 0
        total_failed = 0
        batch_number = 1
        
        print("="*60)
        
        while True:
            print(f"\nBatch #{batch_number}: Discovering files...")
            
            # Discover next batch of files
            batch_files = await self.scan_upload_directory_batch()
            
            if not batch_files:
                print(f"No more files found. Upload complete!")
                break
            
            print(f"Uploading {len(batch_files)} files in batch #{batch_number}")
            
            # Upload batch without tagging
            upload_results = await self.parallel_upload_batch(batch_files)
            
            # Count batch results
            batch_successful = sum(1 for _, success, _ in upload_results if success)
            batch_failed = len(batch_files) - batch_successful
            total_successful += batch_successful
            total_failed += batch_failed
            
            # Calculate batch rate
            if batch_number == 1:
                batch_time = time.time() - start_time
                batch_start_time = start_time
            else:
                batch_time = time.time() - batch_start_time
            
            batch_rate = batch_successful / batch_time if batch_time > 0 else 0
            
            print(f"Batch #{batch_number} complete:")
            print(f"  Successful: {batch_successful}")
            print(f"  Failed: {batch_failed}")
            print(f"  Batch rate: {batch_rate:.2f} files/sec")
            
            batch_number += 1
            batch_start_time = time.time()
            
            # Small delay between batches
            await asyncio.sleep(0.5)
        
        # Final results
        elapsed_time = time.time() - start_time
        overall_rate = total_successful / elapsed_time if elapsed_time > 0 else 0
        
        print("\n" + "="*60)
        print("UPLOAD RESULTS")
        print("="*60)
        print(f"Total batches: {batch_number - 1}")
        print(f"Upload complete: {total_successful}/{total_successful + total_failed} successful")
        print(f"Total time: {elapsed_time:.2f} seconds")
        print(f"Upload rate: {overall_rate:.2f} files/sec")
        print("="*60)

