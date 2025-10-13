#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Utility functions for Szurubooru Manager
"""

from pathlib import Path

# Try to import python-magic for better file type detection
try:
    import magic
    MAGIC_AVAILABLE = True
except ImportError:
    MAGIC_AVAILABLE = False


def is_video_file(file_path: Path) -> bool:
    """Check if a file is a video based on its content using python-magic, with extension fallback"""
    if MAGIC_AVAILABLE:
        try:
            # Use python-magic to detect actual MIME type from file content
            mime_type = magic.from_file(str(file_path), mime=True)
            return mime_type and mime_type.startswith('video/')
        except Exception:
            # Fall back to extension-based detection if magic fails
            pass
    
    # Extension-based fallback for when python-magic is unavailable or fails
    video_extensions = {'.mp4', '.webm', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.m4v', '.3gp', '.ogv'}
    return file_path.suffix.lower() in video_extensions


def is_image_file(file_path: Path) -> bool:
    """Check if a file is an image based on its content using python-magic, with extension fallback"""
    if MAGIC_AVAILABLE:
        try:
            # Use python-magic to detect actual MIME type from file content
            mime_type = magic.from_file(str(file_path), mime=True)
            return mime_type and mime_type.startswith('image/')
        except Exception:
            # Fall back to extension-based detection if magic fails
            pass
    
    # Extension-based fallback for when python-magic is unavailable or fails
    image_extensions = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.tif'}
    return file_path.suffix.lower() in image_extensions


def check_video_file(file_path: str):
    """Check if a video file is valid and get its details"""
    from pathlib import Path
    
    # ANSI color codes for terminal output
    RED = '\033[91m'
    GREEN = '\033[92m'
    BLUE = '\033[94m'
    YELLOW = '\033[93m'
    RESET = '\033[0m'
    
    path = Path(file_path)
    if not path.exists():
        print(f"{RED}[ERROR]{RESET} File does not exist: {file_path}")
        return
    
    print(f"{BLUE}[INFO]{RESET} Analyzing file: {path.name}")
    print(f"{BLUE}[INFO]{RESET} Path: {path}")
    print(f"{BLUE}[INFO]{RESET} Size: {path.stat().st_size:,} bytes")
    
    # Check MIME type using python-magic if available
    if MAGIC_AVAILABLE:
        try:
            mime_type = magic.from_file(str(path), mime=True)
            print(f"{BLUE}[INFO]{RESET} MIME type (magic): {mime_type}")
            
            if mime_type and mime_type.startswith('video/'):
                print(f"{GREEN}[SUCCESS]{RESET} Detected as video file by python-magic")
            elif mime_type and mime_type.startswith('image/'):
                print(f"{YELLOW}[WARNING]{RESET} Detected as image file, not video")
            else:
                print(f"{YELLOW}[WARNING]{RESET} Not detected as video by python-magic")
        except Exception as e:
            print(f"{YELLOW}[WARNING]{RESET} python-magic detection failed: {e}")
    else:
        print(f"{YELLOW}[WARNING]{RESET} python-magic not available, using fallback methods")
        import mimetypes
        mime_type, _ = mimetypes.guess_type(str(path))
        print(f"{BLUE}[INFO]{RESET} MIME type (guess): {mime_type}")
    
    # Check file header
    try:
        with open(path, 'rb') as f:
            header = f.read(16)
            print(f"{BLUE}[INFO]{RESET} File header: {header.hex()}")
            
            # Check for common video signatures
            if header.startswith(b'\x00\x00\x00') and header[4:8] in [b'ftyp', b'mdat']:
                print(f"{GREEN}[SUCCESS]{RESET} MP4 signature detected")
                ftyp = header[4:8].decode('ascii', errors='ignore')
                print(f"   Format: {ftyp}")
            elif header.startswith(b'RIFF'):
                print(f"{GREEN}[SUCCESS]{RESET} AVI signature detected")
            elif header.startswith(b'\x1a\x45\xdf\xa3'):
                print(f"{GREEN}[SUCCESS]{RESET} WebM/MKV signature detected")
            elif header.startswith(b'GIF8'):
                print(f"{GREEN}[SUCCESS]{RESET} GIF signature detected")
            else:
                print(f"{YELLOW}[WARNING]{RESET} No recognized video signature found")
                print(f"{YELLOW}[WARNING]{RESET} This might not be a valid video file")
                
    except Exception as e:
        print(f"{RED}[ERROR]{RESET} Error reading file: {e}")
    
    # Check using our is_video_file function (uses magic if available, else extension)
    if is_video_file(path):
        print(f"{GREEN}[SUCCESS]{RESET} Identified as video file")
    else:
        print(f"{RED}[ERROR]{RESET} Not identified as video file")

