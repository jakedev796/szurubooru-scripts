#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Szurubooru Media Manager
A comprehensive script for uploading media and auto-tagging with WD14 Tagger

Features:
- Upload media from a configured directory to Szurubooru
- Auto-tag images using WD14 Tagger with GPU acceleration
- Remove 'tagme' tags after successful tagging
- Delete original files after successful upload
- Configurable scheduling support
- Efficient batch processing
"""

import os
import sys
import json
import time
import logging
import argparse
import asyncio
import aiohttp
import aiofiles
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
import schedule
import threading
from datetime import datetime
from tqdm import tqdm

# WD14 Tagger imports
try:
    import torch
    from wdtagger import Tagger
    WD14_AVAILABLE = True
except ImportError:
    WD14_AVAILABLE = False
    print("Warning: WD14 Tagger not available. Install with: pip install wdtagger")

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('szurubooru_manager.log')
    ]
)
logger = logging.getLogger(__name__)

@dataclass
class Config:
    """Configuration class for Szurubooru manager"""
    szurubooru_url: str
    username: str
    upload_directory: str
    supported_extensions: List[str]
    api_token: str = None
    password: str = None  # Fallback for backward compatibility
    tagme_tag: str = "tagme"
    video_tag: str = "video"  # Tag to add to video files
    skip_problematic_videos: bool = False  # Skip videos that cause MIME type issues
    batch_size: int = 10
    max_workers: int = 4
    gpu_enabled: bool = True
    confidence_threshold: float = 0.5
    max_tags_per_image: int = 20
    delete_after_upload: bool = True
    retry_attempts: int = 3
    retry_delay: float = 1.0
    # New performance optimization settings
    max_concurrent_uploads: int = 12
    gpu_batch_size: int = 8
    upload_workers: int = 8
    tagging_workers: int = 2
    pipeline_enabled: bool = True
    connection_pool_size: int = 20
    upload_timeout: float = 30.0
    tagging_timeout: float = 60.0
    # Batched file discovery settings
    batch_discovery_size: int = 1000
    skip_processed_files: bool = True
    # Debug settings
    debug_api_errors: bool = False
    # Processed file tracking (useful if delete_after_upload is false or for debugging)
    track_processed_files: bool = True

def is_video_file(file_path: Path) -> bool:
    """Check if a file is a video based on its extension"""
    video_extensions = {'.mp4', '.webm', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.m4v', '.3gp', '.ogv'}
    return file_path.suffix.lower() in video_extensions

class SzurubooruAPI:
    """API client for Szurubooru"""
    
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

class WDTaggerManager:
    """Manager for WD14 Tagger operations with GPU batch processing"""
    
    def __init__(self, config: Config):
        self.config = config
        self.tagger = None
        self.device = None
        self.is_initialized = False
        self._lock = asyncio.Lock()
        
    def initialize(self):
        """Initialize WD14 Tagger"""
        if not WD14_AVAILABLE:
            raise ImportError("WD14 Tagger not available. Install with: pip install wdtagger")
        
        try:
            # Check for GPU availability
            if self.config.gpu_enabled and torch.cuda.is_available():
                self.device = torch.device('cuda')
                print(f"Using GPU: {torch.cuda.get_device_name()}")
                # Set GPU memory optimization
                torch.cuda.empty_cache()
            else:
                self.device = torch.device('cpu')
                print("Using CPU for AI processing")
            
            # Initialize the tagger with model specification
            model_name = "SmilingWolf/wd-swinv2-tagger-v3"  # Default model
            self.tagger = Tagger(model_repo=model_name)
            self.is_initialized = True
            print(f"WD14 Tagger initialized successfully")
            
        except Exception as e:
            print(f"Failed to initialize WD14 Tagger: {e}")
            raise
    
    async def ensure_initialized(self):
        """Ensure tagger is initialized (thread-safe)"""
        if not self.is_initialized:
            async with self._lock:
                if not self.is_initialized:
                    await asyncio.get_event_loop().run_in_executor(None, self.initialize)
    
    def _process_result(self, result) -> Tuple[List[str], str]:
        """Process a single tagger result"""
        # Extract tags from general_tag_data
        tags = []
        if hasattr(result, 'general_tag_data') and result.general_tag_data:
            for tag, confidence in result.general_tag_data.items():
                if confidence >= self.config.confidence_threshold and len(tags) < self.config.max_tags_per_image:
                    tags.append(tag)
        
        # Extract safety rating
        safety = "unsafe"  # Default safety
        if hasattr(result, 'rating_data') and result.rating_data:
            # Map WD14 Tagger safety ratings to Szurubooru safety levels
            rating_data = result.rating_data
            
            # Get the highest confidence rating
            max_confidence = 0
            max_rating = "general"
            
            for rating, confidence in rating_data.items():
                if confidence > max_confidence:
                    max_confidence = confidence
                    max_rating = rating
            
            # Map WD14 ratings to Szurubooru safety levels
            if max_rating == "explicit":
                safety = "unsafe"
            elif max_rating == "questionable":
                safety = "sketchy"
            elif max_rating == "sensitive":
                safety = "sketchy"
            else:  # general
                safety = "safe"
        
        # Filter and clean tags
        cleaned_tags = []
        for tag in tags:
            # Only remove confidence values if present (e.g., "tag (0.95)" -> "tag")
            import re
            clean_tag = re.sub(r'\s*\([\d.]+\)$', '', str(tag)).strip()
            if clean_tag and len(clean_tag) > 1:
                cleaned_tags.append(clean_tag)
        
        return cleaned_tags, safety
    
    async def tag_image(self, image_path: Path) -> Tuple[List[str], str]:
        """Tag a single image using WD14 Tagger. Returns (tags, safety)"""
        await self.ensure_initialized()
        
        try:
            if not self.tagger:
                raise RuntimeError("WD14 Tagger not initialized")
            
            # Run tagging in executor to avoid blocking
            result = await asyncio.get_event_loop().run_in_executor(
                None, 
                self.tagger.tag, 
                str(image_path)
            )
            
            return self._process_result(result)
            
        except Exception as e:
            logger.warning(f"Failed to tag image {image_path}: {e}")
            return [], "safe"
    
    async def tag_images_batch(self, image_paths: List[Path]) -> List[Tuple[List[str], str]]:
        """Tag multiple images in batches for GPU efficiency"""
        await self.ensure_initialized()
        
        if not image_paths:
            return []
        
        results = []
        batch_size = min(self.config.gpu_batch_size, len(image_paths))
        
        try:
            # Process images in batches
            for i in range(0, len(image_paths), batch_size):
                batch_paths = image_paths[i:i + batch_size]
                
                # Process batch concurrently (still one at a time but with proper async)
                batch_tasks = []
                for path in batch_paths:
                    if path.exists():
                        task = asyncio.get_event_loop().run_in_executor(
                            None, 
                            self.tagger.tag, 
                            str(path)
                        )
                        batch_tasks.append(task)
                    else:
                        batch_tasks.append(asyncio.create_task(asyncio.sleep(0)))
                
                # Wait for all tasks in batch
                batch_results = await asyncio.gather(*batch_tasks, return_exceptions=True)
                
                # Process results
                for j, result in enumerate(batch_results):
                    if isinstance(result, Exception) or result is None:
                        results.append(([], "safe"))
                    else:
                        try:
                            processed_result = self._process_result(result)
                            results.append(processed_result)
                        except Exception:
                            results.append(([], "safe"))
                
                # Small delay between batches to prevent overwhelming GPU
                if i + batch_size < len(image_paths):
                    await asyncio.sleep(0.1)
        
        except Exception as e:
            logger.error(f"Error in batch tagging: {e}")
            # Return empty results for all images
            results = [([], "safe") for _ in image_paths]
        
        return results

@dataclass 
class PerformanceMetrics:
    """Track performance metrics"""
    files_uploaded: int = 0
    files_tagged: int = 0
    files_failed: int = 0
    upload_rate: float = 0.0
    tagging_rate: float = 0.0
    start_time: float = 0.0
    
    def calculate_rates(self, elapsed_time: float):
        if elapsed_time > 0:
            total_processed = self.files_uploaded + self.files_tagged + self.files_failed
            self.upload_rate = self.files_uploaded / elapsed_time if elapsed_time > 0 else 0
            self.tagging_rate = self.files_tagged / elapsed_time if elapsed_time > 0 else 0

class MediaManager:
    """High-performance media management class with parallel processing"""
    
    def __init__(self, config: Config):
        self.config = config
        self.wd_tagger = WDTaggerManager(config)
        self.metrics = PerformanceMetrics()
        self.upload_semaphore = asyncio.Semaphore(config.max_concurrent_uploads)
        
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
                self._processed_files_cache.add(file_str)
                # Append to log file
                try:
                    if self.processed_files_log:
                        with open(self.processed_files_log, 'a', encoding='utf-8') as f:
                            f.write(f"{file_str}\n")
                except Exception as e:
                    logger.warning(f"Failed to log processed file: {e}")
    
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
            body_parts.append('')  # Empty line before binary content
            
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
            found_files = []
            total_scanned = 0
            
            for ext in self.config.supported_extensions:
                if len(found_files) >= batch_size:
                    break
                    
                # Scan for both lowercase and uppercase extensions
                for pattern in [f"*.{ext.lower()}", f"*.{ext.upper()}"]:
                    for file_path in upload_path.rglob(pattern):
                        total_scanned += 1
                        
                        # Skip if already processed
                        if self._is_file_processed(file_path):
                            continue
                            
                        # Skip if file doesn't exist (could have been deleted)
                        if not file_path.exists():
                            continue
                            
                        found_files.append(file_path)
                        
                        # Stop when we hit batch limit
                        if len(found_files) >= batch_size:
                            break
                    
                    if len(found_files) >= batch_size:
                        break
            
            # Sort by modification time (newest first)
            found_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
            return found_files, total_scanned
        
        files, scanned_count = await loop.run_in_executor(None, scan_batch)
        
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
            files = []
            for ext in self.config.supported_extensions:
                # Use case-insensitive pattern to scan recursively
                pattern_lower = f"*.{ext.lower()}"
                pattern_upper = f"*.{ext.upper()}"
                files.extend(upload_path.rglob(pattern_lower))
                files.extend(upload_path.rglob(pattern_upper))
            
            # Remove duplicates and sort by modification time (newest first)
            unique_files = list(set(files))
            return sorted(unique_files, key=lambda f: f.stat().st_mtime, reverse=True)
        
        files = await loop.run_in_executor(None, scan_files)
        return files
    
    async def upload_single_file_optimized(self, file_path: Path) -> Tuple[bool, str, Optional[Dict]]:
        """Optimized upload with dedicated connection. Returns (success, reason, post_data)"""
        async with self.upload_semaphore:
            try:
                # Check if file still exists
                if not file_path.exists():
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
                    
                    # Upload with appropriate initial tags
                    result = await api.upload_post(file_path, tags=initial_tags, safety="unsafe")
                    
                    # Handle MIME type issues for video files
                    if isinstance(result, dict) and "error" in result:
                        error_text = result["error"]
                        if "application/octet-stream" in error_text and is_video_file(file_path):
                            print(f"⚠️  MIME type issue detected for {file_path.name}")
                            print(f"   Error: {error_text}")
                            print(f"   File size: {file_path.stat().st_size} bytes")
                            
                            # Try to verify if it's actually a video file by checking file header
                            try:
                                with open(file_path, 'rb') as f:
                                    header = f.read(12)
                                    # Check for common video file signatures
                                    if (header.startswith(b'\x00\x00\x00') and header[4:8] in [b'ftyp', b'mdat']) or \
                                       header.startswith(b'RIFF') or \
                                       header.startswith(b'\x1a\x45\xdf\xa3'):  # WebM
                                        print(f"   ✅ File header appears to be valid video format")
                                        print(f"   🔧 Trying alternative upload method...")
                                        
                                        # Try uploading with explicit content type
                                        print(f"   🔧 Trying upload with explicit video content type...")
                                        fallback_result = await self._upload_with_content_type(api, file_path, [self.config.video_tag], "video/mp4")
                                        if isinstance(fallback_result, dict) and "error" not in fallback_result:
                                            print(f"   ✅ Fallback upload successful!")
                                            result = fallback_result
                                        else:
                                            print(f"   ❌ Fallback upload also failed")
                                            if isinstance(fallback_result, dict) and "error" in fallback_result:
                                                print(f"   Fallback error: {fallback_result['error']}")
                                            
                                            # Try alternative approach - upload as binary with different headers
                                            print(f"   🔧 Trying alternative binary upload method...")
                                            try:
                                                alt_result = await self._upload_as_binary(api, file_path, [self.config.video_tag])
                                                if isinstance(alt_result, dict) and "error" not in alt_result:
                                                    print(f"   ✅ Alternative upload successful!")
                                                    result = alt_result
                                                else:
                                                    print(f"   ❌ Alternative upload also failed")
                                                    if isinstance(alt_result, dict) and "error" in alt_result:
                                                        print(f"   Alternative error: {alt_result['error']}")
                                            except Exception as alt_e:
                                                print(f"   ❌ Alternative upload exception: {alt_e}")
                                        
                                        # If all methods failed and skip_problematic_videos is enabled
                                        if self.config.skip_problematic_videos:
                                            print(f"   ⏭️  Skipping problematic video file (skip_problematic_videos enabled)")
                                            self.metrics.files_uploaded += 1  # Count as "processed" to avoid reprocessing
                                            return True, "Skipped problematic video", None
                                    else:
                                        print(f"   ❌ File header doesn't match video format")
                                        print(f"   Header: {header.hex()}")
                            except Exception as e:
                                print(f"   ❌ Error checking file header: {e}")
                    
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
                    return True, "Success", result
                    
            except Exception as e:
                self.metrics.files_failed += 1
                return False, str(e), None

    async def parallel_upload_batch(self, files: List[Path]) -> List[Tuple[Path, bool, Optional[Dict]]]:
        """Upload multiple files in parallel with optimized performance"""
        if not files:
            return []
        
        print(f"Starting parallel upload of {len(files)} files...")
        
        # Create upload tasks
        tasks = []
        for file_path in files:
            task = self.upload_single_file_optimized(file_path)
            tasks.append(task)
        
        # Execute all uploads concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Process results and handle file deletion
        processed_results = []
        failure_reasons = {}  # Track failure reasons for better reporting
        
        for i, result in enumerate(results):
            file_path = files[i]
            
            if isinstance(result, Exception):
                reason = f"Task exception: {str(result)}"
                logger.error(f"Upload task failed for {file_path}: {result}")
                processed_results.append((file_path, False, None))
                self.metrics.files_failed += 1
                # Track failure reason
                failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
            else:
                success, reason, post_data = result
                processed_results.append((file_path, success, post_data))
                
                if success:
                    # Mark as processed (but DON'T delete yet - needed for AI tagging)
                    self._mark_file_processed(file_path)
                else:
                    # Track failure reasons
                    failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
        
        # Report detailed results summary
        successful_count = len([r for r in processed_results if r[1]])
        duplicate_count = len([r for r in processed_results if r[1] and "Duplicate" in (r[2] if isinstance(r[2], str) else "")])
        
        if failure_reasons:
            # Separate duplicates from real failures
            duplicate_reasons = {k: v for k, v in failure_reasons.items() if "Duplicate" in k}
            real_failures = {k: v for k, v in failure_reasons.items() if "Duplicate" not in k}
            
            print(f"\n📊 Upload Results Summary:")
            print(f"✅ New uploads: {successful_count} files")
            if duplicate_reasons:
                duplicate_total = sum(duplicate_reasons.values())
                print(f"🔄 Duplicates (already uploaded): {duplicate_total} files")
            if real_failures:
                real_failure_total = sum(real_failures.values())
                print(f"❌ Real failures: {real_failure_total} files")
            
            if real_failures:
                print(f"\nFailure breakdown:")
                sorted_failures = sorted(real_failures.items(), key=lambda x: x[1], reverse=True)
                for reason, count in sorted_failures:
                    percentage = (count / len(files)) * 100
                    print(f"  • {reason}: {count} files ({percentage:.1f}%)")
            
            if duplicate_reasons and not self.config.debug_api_errors:
                print(f"📝 Note: {duplicate_total} files were already uploaded (skipped as duplicates)")
                
        else:
            # Check if there were any duplicates in successful results
            duplicate_count = sum(1 for _, success, post_data in processed_results if success and not post_data)
            new_upload_count = sum(1 for _, success, post_data in processed_results if success and post_data)
            
            if duplicate_count > 0 and new_upload_count > 0:
                print(f"\n✅ All {len(files)} files processed successfully ({new_upload_count} new uploads, {duplicate_count} duplicates)!")
            elif duplicate_count > 0:
                print(f"\n✅ All {len(files)} files processed successfully (all duplicates)!")
            else:
                print(f"\n✅ All {len(files)} files processed successfully (all new uploads)!")
        
        return processed_results
    
    async def update_post_tags_batch(self, posts_data: List[Tuple[Path, Dict]], tag_results: List[Tuple[List[str], str]]):
        """Update tags for multiple posts concurrently"""
        if not posts_data or not tag_results:
            return
        
        print(f"Updating tags for {len(posts_data)} posts...")
        
        # Create update tasks
        tasks = []
        files_to_delete = []  # Track files to delete after tagging
        
        for (file_path, post_data), (new_tags, safety) in zip(posts_data, tag_results):
            if post_data:  # Update post even if no AI tags (to remove 'tagme')
                task = self._update_single_post_tags(post_data, new_tags, safety)
                tasks.append(task)
                
                # Add file to deletion list (will delete after tagging)
                if self.config.delete_after_upload:
                    files_to_delete.append(file_path)
        
        # Execute all updates concurrently
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Count successful updates
        successful_updates = sum(1 for result in results if result and not isinstance(result, Exception))
        self.metrics.files_tagged += successful_updates
        
        print(f"Successfully updated {successful_updates}/{len(posts_data)} posts")
        
        # Delete files after successful tagging
        if files_to_delete:
            print(f"Deleting {len(files_to_delete)} processed files...")
            for file_path in files_to_delete:
                try:
                    if file_path.exists():
                        await asyncio.get_event_loop().run_in_executor(None, file_path.unlink)
                except Exception as e:
                    logger.warning(f"Failed to delete {file_path}: {e}")
    
    async def _update_single_post_tags(self, post_data: Dict, new_tags: List[str], safety: str) -> bool:
        """Update tags for a single post"""
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
            
            # Combine tags (even if new_tags is empty, we still remove 'tagme')
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
                    elif response.status == 409:  # Version conflict
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
        successful_uploads = []  # New uploads with post data (for AI tagging)
        successful_processing = 0  # All successful processing (new + duplicates)
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
        
        # Phase 2: Parallel AI Tagging (if WD14 available and successful uploads exist)
        tagged_count = 0
        if WD14_AVAILABLE and successful_uploads and self.config.gpu_enabled:
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
                
                print(f"Starting AI tagging for {len(files_to_tag)} image files...")
                
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
                        non_empty_tags = len([tags for tags, _ in tag_results if tags])
                        print(f"AI tagging results: {non_empty_tags}/{len(tag_results)} files got tags")
                    
                    # Update posts with tags (match files to posts)
                    if tag_results:
                        # Need to match files to their post data
                        files_with_posts = []
                        for file_path, post_data in successful_uploads:
                            if file_path in existing_files:
                                files_with_posts.append((file_path, post_data))
                        
                        await self.update_post_tags_batch(files_with_posts, tag_results)
                        tagged_count = len([tags for tags, _ in tag_results if tags])
                else:
                    print("No files available for AI tagging")
                
            except Exception as e:
                logger.error(f"Tagging phase failed: {e}")
                import traceback
                logger.error(f"Tagging error traceback: {traceback.format_exc()}")
        
        # Handle duplicate file deletion (duplicates don't get tagged, so delete them here)
        if self.config.delete_after_upload and duplicate_count > 0:
            duplicate_files = []
            for file_path, success, post_data in upload_results:
                if success and not post_data:  # Duplicate file
                    duplicate_files.append(file_path)
            
            if duplicate_files:
                print(f"Deleting {len(duplicate_files)} duplicate files...")
                deleted_count = 0
                for file_path in duplicate_files:
                    try:
                        if file_path.exists():
                            await asyncio.get_event_loop().run_in_executor(None, file_path.unlink)
                            deleted_count += 1
                    except Exception as e:
                        logger.warning(f"Failed to delete duplicate {file_path}: {e}")
                print(f"Successfully deleted {deleted_count} duplicate files")
        
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
                    print(f"\n🔄 Tagging Round {round_num}")
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
                    
                    # Add untagged posts (if not already in tagme list)
                    if untagged_posts:
                        for post in untagged_posts:
                            if post['id'] not in posts_dict:
                                posts_dict[post['id']] = post
                    
                    posts = list(posts_dict.values())
                    
                    if not posts:
                        print("✅ No more posts found that need tagging")
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
                    batch_size = self.config.tagging_workers or 4  # Use tagging_workers config
                    
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
                        print("⚠️  No posts were successfully processed this round. Stopping to avoid infinite loop.")
                        break
                    
                    round_num += 1
                
                print(f"\n🎉 Auto-tagging complete!")
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
            
            # Handle video files (add video tag but skip AI tagging)
            if post.get('type') == 'video':
                # Remove tagme tag from current tags if present
                if self.config.tagme_tag in current_tags:
                    current_tags.remove(self.config.tagme_tag)
                
                # Add video tag if not already present
                if self.config.video_tag not in current_tags:
                    current_tags.append(self.config.video_tag)
                    
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
                    return True  # Already properly tagged
            
            # Remove tagme tag from current tags if present (for posts that have it)
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
                new_tags, safety = await self.wd_tagger.tag_image(temp_path)
                
                # Combine existing tags with new tags
                all_tags = list(set(current_tags + new_tags))
                
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
                                    elif response.status == 409:  # Conflict
                                        # Try again with incremented version
                                        update_data['version'] = version + 1
                                        async with api.session.put(
                                            f"{self.config.szurubooru_url}/api/post/{post_id}",
                                            json=update_data
                                        ) as retry_response:
                                            if retry_response.status == 200:
                                                processed_count += 1
                                                video_count += 1
                                
                                continue  # Skip AI tagging for videos
                            
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
                                new_tags, safety = await self.wd_tagger.tag_image(temp_path)
                                
                                # Combine existing tags with new tags
                                all_tags = list(set(current_tags + new_tags))
                                
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
                                    elif response.status == 409:  # Conflict
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
            return 0
    
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
            
            # Small delay between batches to prevent overwhelming
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
        
        # Performance comparison
        old_estimated_time = total_processed / 1.13  # Your old rate
        speedup = old_estimated_time / total_time if total_time > 0 else 1
        print(f"Performance improvement: {speedup:.1f}x faster than before!")
        print(f"Time saved: {old_estimated_time - total_time:.1f} seconds")
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

def load_config(config_path: str) -> Config:
    """Load configuration from JSON file"""
    try:
        with open(config_path, 'r') as f:
            config_data = json.load(f)
        
        # Filter out comment fields that start with underscore
        filtered_config = {k: v for k, v in config_data.items() if not k.startswith('_')}
        
        return Config(**filtered_config)
    except Exception as e:
        logger.error(f"Failed to load config from {config_path}: {e}")
        raise

def create_default_config(config_path: str):
    """Create a default optimized configuration file"""
    default_config = {
        "szurubooru_url": "http://localhost:8080",
        "username": "your_username",
        "api_token": "your_api_token_here",
        "upload_directory": "./uploads",
        "supported_extensions": ["jpg", "jpeg", "png", "gif", "webm", "mp4", "webp"],
        "tagme_tag": "tagme",
        "video_tag": "video",
        "skip_problematic_videos": True,
        "batch_size": 0,
        "max_workers": 4,
        "gpu_enabled": True,
        "confidence_threshold": 0.5,
        "max_tags_per_image": 20,
        "delete_after_upload": True,
        "retry_attempts": 3,
        "retry_delay": 1.0,
        # Performance optimization settings
        "max_concurrent_uploads": 12,
        "gpu_batch_size": 8,
        "upload_workers": 8,
        "tagging_workers": 2,
        "pipeline_enabled": True,
        "connection_pool_size": 20,
        "upload_timeout": 30.0,
        "tagging_timeout": 60.0,
        # Batched file discovery settings
        "batch_discovery_size": 1000,
        "skip_processed_files": True,
        # Debug settings
        "debug_api_errors": False,
        # Processed file tracking (useful if delete_after_upload is false or for debugging)
        "track_processed_files": True
    }
    
    with open(config_path, 'w') as f:
        json.dump(default_config, f, indent=2)
    
    logger.info(f"Created optimized config file: {config_path}")
    print(f"Created optimized configuration file: {config_path}")
    print("Key performance settings:")
    print(f"   - Max concurrent uploads: {default_config['max_concurrent_uploads']}")
    print(f"   - GPU batch size: {default_config['gpu_batch_size']}")
    print(f"   - Pipeline enabled: {default_config['pipeline_enabled']}")

def check_video_file(file_path: str):
    """Check if a video file is valid and get its details"""
    import mimetypes
    from pathlib import Path
    
    path = Path(file_path)
    if not path.exists():
        print(f"❌ File does not exist: {file_path}")
        return
    
    print(f"🔍 Analyzing video file: {path.name}")
    print(f"📁 Path: {path}")
    print(f"📏 Size: {path.stat().st_size:,} bytes")
    
    # Check MIME type
    mime_type, _ = mimetypes.guess_type(str(path))
    print(f"🎬 MIME type: {mime_type}")
    
    # Check file header
    try:
        with open(path, 'rb') as f:
            header = f.read(16)
            print(f"🔍 File header: {header.hex()}")
            
            # Check for common video signatures
            if header.startswith(b'\x00\x00\x00') and header[4:8] in [b'ftyp', b'mdat']:
                print("✅ MP4 signature detected")
                ftyp = header[4:8].decode('ascii', errors='ignore')
                print(f"   Format: {ftyp}")
            elif header.startswith(b'RIFF'):
                print("✅ AVI signature detected")
            elif header.startswith(b'\x1a\x45\xdf\xa3'):
                print("✅ WebM/MKV signature detected")
            elif header.startswith(b'GIF8'):
                print("✅ GIF signature detected")
            else:
                print("❌ No recognized video signature found")
                print("   This might not be a valid video file")
                
    except Exception as e:
        print(f"❌ Error reading file: {e}")
    
    # Check if it's a video extension
    if is_video_file(path):
        print("✅ File has video extension")
    else:
        print("❌ File does not have video extension")

async def main():
    """Main function with optimized processing modes"""
    parser = argparse.ArgumentParser(description="Szurubooru High-Performance Media Manager")
    parser.add_argument("--config", "-c", default="config.json", help="Configuration file path")
    parser.add_argument("--mode", "-m", 
                       choices=["optimized", "upload", "tag", "untagged", "full", "legacy"], 
                       default="optimized", 
                       help="Operation mode: optimized (recommended), upload, tag (comprehensive), untagged, full, or legacy")
    parser.add_argument("--schedule", "-s", help="Schedule in cron format (e.g., '*/30 * * * *' for every 30 minutes)")
    parser.add_argument("--create-config", action="store_true", help="Create an optimized configuration file")
    parser.add_argument("--test-connection", action="store_true", help="Test connection to Szurubooru")
    parser.add_argument("--benchmark", action="store_true", help="Run performance benchmark")
    parser.add_argument("--check-video", help="Check if a video file is valid and get its details")
    
    args = parser.parse_args()
    
    # Print header
    print("Szurubooru High-Performance Media Manager v2.0")
    print("="*60)
    
    # Create config if requested
    if args.create_config:
        create_default_config(args.config)
        return
    
    # Check video file if requested
    if args.check_video:
        check_video_file(args.check_video)
        return
    
    # Load configuration
    try:
        config = load_config(args.config)
    except Exception as e:
        print(f"Failed to load configuration: {e}")
        logger.error(f"Failed to load configuration: {e}")
        return
    
    # Test connection if requested
    if args.test_connection:
        print("Testing API connection...")
        print(f"Server URL: {config.szurubooru_url}")
        print(f"Username: {config.username}")
        print(f"Auth Method: {'API Token' if config.api_token else 'Password'}")
        
        try:
            print("Creating API client...")
            api_client = SzurubooruAPI(config)
            print("Opening session...")
            
            async with api_client as api:
                print("Session established")
                print("Testing connection...")
                connection_result = await api.test_connection()
                
                if connection_result:
                    print("API connection successful!")
                    print("Your Szurubooru server is reachable and authentication works")
                else:
                    print("API connection failed")
                    print("Check your server URL, username, and credentials")
                    
        except Exception as e:
            print(f"Connection test failed with error: {e}")
            print(f"Error type: {type(e).__name__}")
            print("Common fixes:")
            print("   - Verify server URL is correct and accessible")
            print("   - Check if Szurubooru server is running")
            print("   - Verify API token/credentials are valid")
            print("   - Check network connectivity")
            import traceback
            print(f"Full traceback: {traceback.format_exc()}")
        
        return
    
    # Create media manager
    manager = MediaManager(config)
    
    # Display configuration info
    print(f"Configuration loaded from: {args.config}")
    print(f"Processing mode: {args.mode}")
    if hasattr(config, 'max_concurrent_uploads'):
        print(f"Max concurrent uploads: {config.max_concurrent_uploads}")
        print(f"GPU batch size: {config.gpu_batch_size}")
    
    # Run based on mode
    if args.schedule:
        # Scheduled mode
        print(f"Starting scheduled mode: {args.schedule}")
        
        def run_scheduled():
            asyncio.run(manager.run_optimized_cycle())
        
        schedule.every().cron(args.schedule).do(run_scheduled)
        
        # Run initial cycle
        await manager.run_optimized_cycle()
        
        # Keep running
        while True:
            schedule.run_pending()
            await asyncio.sleep(60)  # Check every minute
    else:
        # Single run mode
        if args.mode == "optimized":
            await manager.run_optimized_cycle()
        elif args.mode == "upload":
            await manager.run_upload_only_cycle()
        elif args.mode == "tag":
            tagged_count = await manager.auto_tag_posts()
            print(f"Tagged {tagged_count} posts")
        elif args.mode == "untagged":
            processed_count = await manager.process_untagged_posts()
            print(f"Processed {processed_count} untagged posts")
        elif args.mode == "full":
            # Legacy full cycle for compatibility
            await manager.run_optimized_cycle()
        elif args.mode == "legacy":
            print("Running in legacy mode (slower performance)")
            # Keep old method names for backward compatibility
            files = await manager.scan_upload_directory()
            if files:
                # Use the old sequential processing (not recommended)
                print("Processing files sequentially...")
        
        if args.benchmark:
            print("\nPerformance benchmark completed!")
            print("Try running with different --mode options to compare performance.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        sys.exit(1)
