#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WD14 Tagger module for Szurubooru Manager
Handles AI tagging of images using WD14 Tagger
"""

import asyncio
import logging
from pathlib import Path
from typing import List, Tuple

logger = logging.getLogger(__name__)

# WD14 Tagger imports
try:
    import torch
    from wdtagger import Tagger
    WD14_AVAILABLE = True
except ImportError:
    WD14_AVAILABLE = False
    print("Warning: WD14 Tagger not available. Install with: pip install wdtagger")


class WDTaggerManager:
    """Manager for WD14 Tagger operations with GPU batch processing"""
    
    def __init__(self, config):
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
            model_name = "SmilingWolf/wd-swinv2-tagger-v3"
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
    
    def _process_result(self, result) -> Tuple[List[str], List[str], str]:
        """Process a single tagger result. Returns (general_tags, character_tags, safety)"""
        general_tags = []
        character_tags = []
        
        # Process general tags
        if hasattr(result, 'general_tag_data') and result.general_tag_data:
            for tag, confidence in result.general_tag_data.items():
                if confidence >= self.config.confidence_threshold and len(general_tags) < self.config.max_tags_per_image:
                    # Clean the tag
                    import re
                    clean_tag = re.sub(r'\s*\([\d.]+\)$', '', str(tag)).strip()
                    if clean_tag and len(clean_tag) > 1:
                        general_tags.append(clean_tag)
        
        # Process character tags
        if hasattr(result, 'character_tag_data') and result.character_tag_data:
            for tag, confidence in result.character_tag_data.items():
                if confidence >= self.config.confidence_threshold:
                    # Clean the tag
                    import re
                    clean_tag = re.sub(r'\s*\([\d.]+\)$', '', str(tag)).strip()
                    if clean_tag and len(clean_tag) > 1:
                        character_tags.append(clean_tag)
        
        # Extract safety rating
        safety = "unsafe"
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
            else:
                safety = "safe"
        
        return general_tags, character_tags, safety
    
    def _extract_character_tags_only(self, result) -> List[str]:
        """Extract only character tags from WD14 tagger result"""
        character_tags = []
        
        # Process character tags only
        if hasattr(result, 'character_tag_data') and result.character_tag_data:
            for tag, confidence in result.character_tag_data.items():
                if confidence >= self.config.confidence_threshold:
                    # Clean the tag (remove confidence values if present)
                    import re
                    clean_tag = re.sub(r'\s*\([\d.]+\)$', '', str(tag)).strip()
                    if clean_tag and len(clean_tag) > 1:
                        character_tags.append(clean_tag)
        
        return character_tags
    
    async def tag_image(self, image_path: Path) -> Tuple[List[str], List[str], str]:
        """Tag a single image using WD14 Tagger. Returns (general_tags, character_tags, safety)"""
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
            return [], [], "safe"
    
    async def tag_images_batch(self, image_paths: List[Path]) -> List[Tuple[List[str], List[str], str]]:
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
                
                # Process batch concurrently
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
                        results.append(([], [], "safe"))
                    else:
                        try:
                            processed_result = self._process_result(result)
                            results.append(processed_result)
                        except Exception:
                            results.append(([], [], "safe"))
                
                # Small delay between batches to prevent overwhelming GPU
                if i + batch_size < len(image_paths):
                    await asyncio.sleep(0.1)
        
        except Exception as e:
            logger.error(f"Error in batch tagging: {e}")
            # Return empty results for all images
            results = [([], [], "safe") for _ in image_paths]
        
        return results

