from fastapi import FastAPI, HTTPException, BackgroundTasks, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, HttpUrl
import yt_dlp
import os
import tempfile
import uuid
import asyncio
from typing import Optional, Literal
import shutil
from pathlib import Path
import logging
import json
import browser_cookie3
from datetime import datetime

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create temp directory for downloads and cookies
TEMP_DIR = Path("temp_downloads")
COOKIES_DIR = Path("cookies")
TEMP_DIR.mkdir(exist_ok=True)
COOKIES_DIR.mkdir(exist_ok=True)

class AudioRequest(BaseModel):
    url: HttpUrl
    format: Literal["mp3", "wav", "aac", "ogg", "m4a"] = "mp3"
    quality: Literal["best", "worst", "32", "64", "128", "192", "256", "320"] = "192"
    use_cookies: bool = True  # Enable cookies by default

class CookieStatus(BaseModel):
    browser_cookies_available: bool
    manual_cookies_available: bool
    last_updated: Optional[str]

class AudioInfo(BaseModel):
    title: str
    duration: int
    uploader: str
    view_count: Optional[int]
    upload_date: Optional[str]

def get_ydl_opts_with_cookies(base_opts: dict, use_cookies: bool = True) -> dict:
    """
    Add cookie configuration to yt-dlp options with improved fallback
    """
    if not use_cookies:
        return base_opts
    
    # Try manual cookie file first (most reliable)
    cookie_file = COOKIES_DIR / "youtube_cookies.txt"
    
    if cookie_file.exists():
        base_opts['cookiefile'] = str(cookie_file)
        logger.info("Using manual cookie file")
        return base_opts
    
    # Try to extract cookies to file first, then use the file
    if extract_browser_cookies_to_file():
        base_opts['cookiefile'] = str(cookie_file)
        logger.info("Extracted and using browser cookies from file")
        return base_opts
    
    # Last resort: try direct browser access (often fails)
    try:
        browsers = ['firefox', 'edge', 'safari']  # Skip chrome as it often fails
        for browser in browsers:
            try:
                base_opts['cookiesfrombrowser'] = (browser,)
                logger.info(f"Using cookies directly from {browser}")
                return base_opts
            except Exception as e:
                logger.warning(f"Failed to use {browser} cookies: {str(e)}")
                continue
    except Exception as e:
        logger.warning(f"Could not load any browser cookies: {str(e)}")
    
    # If all fails, continue without cookies
    logger.warning("No cookies available - some videos may fail to download")
    return base_opts

def extract_browser_cookies_to_file():
    """
    Extract cookies from browser and save to file with multiple browser support
    """
    cookie_file = COOKIES_DIR / "youtube_cookies.txt"
    
    # List of browsers to try in order of preference
    browsers = [
        ('firefox', browser_cookie3.firefox),
        ('edge', browser_cookie3.edge),
        ('chrome', browser_cookie3.chrome),
        ('safari', browser_cookie3.safari),
    ]
    
    for browser_name, browser_func in browsers:
        try:
            logger.info(f"Trying to extract cookies from {browser_name}...")
            cookies = list(browser_func(domain_name='youtube.com'))
            
            if not cookies:
                logger.warning(f"No YouTube cookies found in {browser_name}")
                continue
            
            # Write cookies to file
            with open(cookie_file, 'w') as f:
                f.write("# Netscape HTTP Cookie File\n")
                f.write("# This is a generated file! Do not edit.\n\n")
                
                for cookie in cookies:
                    # Handle None values and convert to proper format
                    domain = cookie.domain if cookie.domain else '.youtube.com'
                    path = cookie.path if cookie.path else '/'
                    secure = 'TRUE' if cookie.secure else 'FALSE'
                    expires = str(int(cookie.expires)) if cookie.expires else '0'
                    name = cookie.name if cookie.name else ''
                    value = cookie.value if cookie.value else ''
                    
                    # Write in Netscape format
                    f.write(f"{domain}\tTRUE\t{path}\t{secure}\t{expires}\t{name}\t{value}\n")
            
            logger.info(f"Successfully extracted {len(cookies)} cookies from {browser_name}")
            return True
            
        except Exception as e:
            logger.warning(f"Failed to extract cookies from {browser_name}: {str(e)}")
            continue
    
    logger.error("Failed to extract cookies from any browser")
    return False

app = FastAPI(title="YouTube Audio Downloader API", version="1.0.0")

@app.get("/cookies/status")
async def get_cookie_status() -> CookieStatus:
    """
    Check cookie availability status
    """
    manual_cookies = (COOKIES_DIR / "youtube_cookies.txt").exists()
    browser_cookies = False
    
    # Check if browser cookies are accessible from any browser
    browsers = [
        ('firefox', browser_cookie3.firefox),
        ('edge', browser_cookie3.edge), 
        ('chrome', browser_cookie3.chrome),
        ('safari', browser_cookie3.safari),
    ]
    
    for browser_name, browser_func in browsers:
        try:
            cookies = list(browser_func(domain_name='youtube.com'))
            if cookies:
                browser_cookies = True
                logger.info(f"Found {len(cookies)} cookies in {browser_name}")
                break
        except Exception as e:
            logger.debug(f"Could not access {browser_name} cookies: {str(e)}")
            continue
    
    last_updated = None
    if manual_cookies:
        cookie_file = COOKIES_DIR / "youtube_cookies.txt"
        last_updated = datetime.fromtimestamp(cookie_file.stat().st_mtime).isoformat()
    
    return CookieStatus(
        browser_cookies_available=browser_cookies,
        manual_cookies_available=manual_cookies,
        last_updated=last_updated
    )

@app.post("/cookies/extract")
async def extract_cookies():
    """
    Extract cookies from browser and save to file
    """
    success = extract_browser_cookies_to_file()
    if success:
        return {"message": "Cookies extracted successfully", "path": str(COOKIES_DIR / "youtube_cookies.txt")}
    else:
        raise HTTPException(status_code=500, detail="Failed to extract cookies from any browser. Try uploading a cookie file manually.")

@app.get("/cookies/troubleshoot")
async def troubleshoot_cookies():
    """
    Provide troubleshooting information for cookie issues
    """
    issues = []
    solutions = []
    
    # Check each browser
    browsers = [
        ('Chrome', browser_cookie3.chrome),
        ('Firefox', browser_cookie3.firefox),
        ('Edge', browser_cookie3.edge),
        ('Safari', browser_cookie3.safari),
    ]
    
    browser_status = {}
    
    for browser_name, browser_func in browsers:
        try:
            cookies = list(browser_func(domain_name='youtube.com'))
            browser_status[browser_name] = {
                "accessible": True,
                "cookie_count": len(cookies),
                "error": None
            }
        except Exception as e:
            browser_status[browser_name] = {
                "accessible": False,
                "cookie_count": 0,
                "error": str(e)
            }
            
            if "Chrome" in browser_name and "Could not copy" in str(e):
                issues.append("Chrome cookie database is locked")
                solutions.extend([
                    "Close all Chrome windows and try again",
                    "Use Firefox or Edge instead",
                    "Export cookies manually using browser extension"
                ])
    
    return {
        "browser_status": browser_status,
        "issues": issues,
        "solutions": solutions,
        "recommended_action": "Use /cookies/upload with a manually exported cookie file if automatic extraction fails"
    }

@app.post("/cookies/upload")
async def upload_cookies(cookie_file: UploadFile = File(...)):
    """
    Upload a cookie file (Netscape format)
    """
    if not cookie_file.filename.endswith('.txt'):
        raise HTTPException(status_code=400, detail="Cookie file must be a .txt file")
    
    cookie_path = COOKIES_DIR / "youtube_cookies.txt"
    
    try:
        content = await cookie_file.read()
        with open(cookie_path, 'wb') as f:
            f.write(content)
        
        return {"message": "Cookie file uploaded successfully", "path": str(cookie_path)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to save cookie file: {str(e)}")

@app.delete("/cookies")
async def delete_cookies():
    """
    Delete stored cookie file
    """
    cookie_file = COOKIES_DIR / "youtube_cookies.txt"
    if cookie_file.exists():
        cookie_file.unlink()
        return {"message": "Cookie file deleted"}
    else:
        raise HTTPException(status_code=404, detail="No cookie file found")

@app.get("/debug/{video_id}")
async def debug_video(video_id: str):
    """
    Debug a specific video - show detailed format information
    """
    url = f"https://www.youtube.com/watch?v={video_id}"
    
    ydl_opts = {
        'quiet': False,
        'no_warnings': False,
        'dump_single_json': True,
    }
    
    # Add cookies
    ydl_opts = get_ydl_opts_with_cookies(ydl_opts, True)
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            
            # Get basic info
            basic_info = {
                'title': info.get('title'),
                'duration': info.get('duration'),
                'uploader': info.get('uploader'),
                'age_limit': info.get('age_limit'),
                'availability': info.get('availability'),
            }
            
            # Get formats
            formats = []
            audio_formats = []
            
            for fmt in info.get('formats', []):
                format_info = {
                    'format_id': fmt.get('format_id'),
                    'ext': fmt.get('ext'),
                    'resolution': fmt.get('resolution'),
                    'fps': fmt.get('fps'),
                    'vcodec': fmt.get('vcodec'),
                    'acodec': fmt.get('acodec'),
                    'filesize': fmt.get('filesize'),
                    'tbr': fmt.get('tbr'),
                    'abr': fmt.get('abr'),
                    'format_note': fmt.get('format_note'),
                }
                formats.append(format_info)
                
                # Separate audio-only formats
                if fmt.get('vcodec') == 'none' and fmt.get('acodec') != 'none':
                    audio_formats.append(format_info)
            
            return {
                'basic_info': basic_info,
                'total_formats': len(formats),
                'audio_only_formats': len(audio_formats),
                'all_formats': formats,
                'audio_formats': audio_formats,
                'recommended_format': 'worst' if formats else 'none_available'
            }
            
    except Exception as e:
        logger.error(f"Debug error: {str(e)}")
        return {
            'error': str(e),
            'suggestion': 'Try a different video or check if the video is available in your region'
        }

@app.post("/info")
async def get_video_info(request: AudioRequest) -> AudioInfo:
    """
    Get information about the YouTube video without downloading
    """
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
    }
    
    # Add cookies if requested
    ydl_opts = get_ydl_opts_with_cookies(ydl_opts, request.use_cookies)
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(str(request.url), download=False)
            
            return AudioInfo(
                title=info.get('title', 'Unknown'),
                duration=info.get('duration', 0),
                uploader=info.get('uploader', 'Unknown'),
                view_count=info.get('view_count'),
                upload_date=info.get('upload_date')
            )
    except Exception as e:
        logger.error(f"Error extracting info: {str(e)}")
        if "Sign in to confirm" in str(e):
            raise HTTPException(
                status_code=401, 
                detail="YouTube requires authentication. Please extract cookies using /cookies/extract or upload them via /cookies/upload"
            )
        raise HTTPException(status_code=400, detail=f"Failed to extract video info: {str(e)}")

@app.post("/download")
async def download_audio(request: AudioRequest, background_tasks: BackgroundTasks):
    """
    Download YouTube video as audio file
    """
    # Generate unique filename
    file_id = str(uuid.uuid4())
    
    # Configure quality settings
    if request.quality == "best":
        audio_quality = "0"  # Best quality
    elif request.quality == "worst":
        audio_quality = "9"  # Worst quality
    else:
        audio_quality = request.quality
    
    # Set up output path
    output_path = TEMP_DIR / file_id
    output_path.mkdir(exist_ok=True)
    
    # Configure yt-dlp options with simpler format selection
    ydl_opts = {
        'format': 'worst',  # Start with simplest format that should always work
        'outtmpl': str(output_path / f'%(title)s.%(ext)s'),
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': request.format,
            'preferredquality': audio_quality,
        }],
        'quiet': False,  # Enable output to see what's happening
        'no_warnings': False,
    }
    
    # Add cookies if requested
    ydl_opts = get_ydl_opts_with_cookies(ydl_opts, request.use_cookies)
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            # Get video info first
            info = ydl.extract_info(str(request.url), download=False)
            title = info.get('title', 'audio')
            
            # Clean filename
            safe_title = "".join(c for c in title if c.isalnum() or c in (' ', '-', '_')).rstrip()
            
            # Download
            ydl.download([str(request.url)])
            
            # Find the downloaded file
            downloaded_files = list(output_path.glob(f"*.{request.format}"))
            if not downloaded_files:
                raise HTTPException(status_code=500, detail="Download completed but file not found")
            
            file_path = downloaded_files[0]
            
            # Schedule cleanup after response
            background_tasks.add_task(cleanup_file, output_path)
            
            return FileResponse(
                path=file_path,
                filename=f"{safe_title}.{request.format}",
                media_type=f"audio/{request.format}"
            )
            
    except Exception as e:
        # Clean up on error
        if output_path.exists():
            shutil.rmtree(output_path, ignore_errors=True)
        logger.error(f"Download error: {str(e)}")
        if "Sign in to confirm" in str(e):
            raise HTTPException(
                status_code=401, 
                detail="YouTube requires authentication. Please extract cookies using /cookies/extract or upload them via /cookies/upload"
            )
        raise HTTPException(status_code=400, detail=f"Download failed: {str(e)}")

@app.post("/stream")
async def stream_audio(request: AudioRequest):
    """
    Stream audio directly without saving to disk (experimental)
    """
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'no_warnings': True,
    }
    
    # Add cookies if requested
    ydl_opts = get_ydl_opts_with_cookies(ydl_opts, request.use_cookies)
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(str(request.url), download=False)
            
            # Get direct audio URL
            formats = info.get('formats', [])
            audio_url = None
            
            for fmt in formats:
                if fmt.get('acodec') != 'none' and fmt.get('vcodec') == 'none':
                    audio_url = fmt.get('url')
                    break
            
            if not audio_url:
                raise HTTPException(status_code=404, detail="No suitable audio stream found")
            
            return {"stream_url": audio_url, "title": info.get('title')}
            
    except Exception as e:
        logger.error(f"Streaming error: {str(e)}")
        if "Sign in to confirm" in str(e):
            raise HTTPException(
                status_code=401, 
                detail="YouTube requires authentication. Please extract cookies using /cookies/extract or upload them via /cookies/upload"
            )
        raise HTTPException(status_code=400, detail=f"Failed to get stream: {str(e)}")

async def cleanup_file(file_path: Path):
    """
    Clean up temporary files after a delay
    """
    await asyncio.sleep(60)  # Wait 1 minute before cleanup
    try:
        if file_path.exists():
            shutil.rmtree(file_path, ignore_errors=True)
            logger.info(f"Cleaned up: {file_path}")
    except Exception as e:
        logger.error(f"Cleanup error: {str(e)}")

@app.get("/")
async def root():
    return {
        "message": "YouTube Audio Downloader API",
        "endpoints": {
            "/info": "Get video information",
            "/download": "Download audio file", 
            "/stream": "Get direct stream URL",
            "/debug/{video_id}": "Debug video formats and info",
            "/cookies/status": "Check cookie availability",
            "/cookies/extract": "Extract cookies from browser",
            "/cookies/upload": "Upload cookie file",
            "/cookies/troubleshoot": "Troubleshoot cookie issues",
            "/cookies": "Delete stored cookies",
            "/docs": "API documentation"
        }
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

# Cleanup old files on startup
@app.on_event("startup")
async def startup_event():
    # Clean any existing temp files
    if TEMP_DIR.exists():
        for item in TEMP_DIR.iterdir():
            if item.is_dir():
                shutil.rmtree(item, ignore_errors=True)
    logger.info("API started successfully")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)