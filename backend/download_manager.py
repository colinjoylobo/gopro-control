"""
Download Manager for GoPro Videos
"""
import requests
import httpx
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Callable
import logging

logger = logging.getLogger(__name__)

GOPRO_IP = "http://10.5.5.9:8080"


class DownloadManager:
    def __init__(self, download_dir: Optional[Path] = None):
        if download_dir is None:
            # Use user's home directory for production deployment
            home = Path.home()
            # macOS: ~/Documents/GoPro Downloads
            # Windows: C:\Users\username\Documents\GoPro Downloads
            # Linux: ~/Documents/GoPro Downloads
            download_dir = home / "Documents" / "GoPro Downloads"

        self.download_dir = download_dir
        self.download_dir.mkdir(parents=True, exist_ok=True)

    def get_media_list(self) -> List[Dict]:
        """Get all media files from GoPro, sorted by date (newest first)"""
        try:
            resp = requests.get(f"{GOPRO_IP}/gopro/media/list", timeout=10)
            data = resp.json()

            if not data.get("media"):
                return []

            # Parse and sort media files
            all_files = []

            for media_dir in data["media"]:
                dir_name = media_dir["d"]

                for file_info in media_dir["fs"]:
                    file_name = file_info["n"]

                    # Convert size to int (API sometimes returns string)
                    try:
                        file_size = int(file_info.get("s", 0))
                    except (ValueError, TypeError):
                        file_size = 0

                    # Extract date from directory name (e.g., "100GOPRO")
                    # or use modification time if available
                    try:
                        mod_time = int(file_info.get("mod", 0))
                    except (ValueError, TypeError):
                        mod_time = 0

                    all_files.append({
                        "directory": dir_name,
                        "filename": file_name,
                        "size": file_size,
                        "mod_time": mod_time,
                        "url": f"{GOPRO_IP}/videos/DCIM/{dir_name}/{file_name}"
                    })

            # Sort by modification time (newest first)
            all_files.sort(key=lambda x: x["mod_time"], reverse=True)

            return all_files

        except Exception as e:
            logger.error(f"Failed to get media list: {e}")
            return []

    def download_file(
        self,
        url: str,
        output_path: Path,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> bool:
        """Download a file with progress tracking"""
        try:
            # Check if already exists
            if output_path.exists():
                logger.info(f"File already exists: {output_path.name}")
                return True

            # Create parent directory
            output_path.parent.mkdir(parents=True, exist_ok=True)

            # Download
            logger.info(f"Downloading: {output_path.name}")

            r = requests.get(url, stream=True, timeout=120)
            total = int(r.headers.get('content-length', 0))
            downloaded = 0

            with open(output_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)

                        # Call progress callback
                        if progress_callback and total > 0:
                            progress_callback(downloaded, total)

            mb = output_path.stat().st_size / (1024 * 1024)
            logger.info(f"Downloaded: {output_path.name} ({mb:.1f} MB)")
            return True

        except Exception as e:
            logger.error(f"Download failed: {e}")
            # Clean up partial file
            if output_path.exists():
                output_path.unlink()
            return False

    def download_all_from_camera(
        self,
        serial: str,
        progress_callback: Optional[Callable[[str, int, int, int], None]] = None,
        max_files: Optional[int] = None
    ) -> List[Path]:
        """
        Download files from camera
        progress_callback(filename, current_file_idx, total_files, percent)
        max_files: Optional - Download only the last N files (None = download all)
        """
        downloaded_files = []

        try:
            logger.info("=" * 60)
            logger.info(f"📥 Starting download for camera {serial}")

            # Get media list
            logger.info("Fetching media list from camera...")
            media_list = self.get_media_list()

            if not media_list:
                logger.warning("⚠️  No media files found on camera")
                return []

            # Limit to last N files if specified
            if max_files and max_files > 0:
                total_available = len(media_list)
                media_list = media_list[:max_files]  # Already sorted newest first
                total_files = len(media_list)
                logger.info(f"✓ Found {total_available} files on camera")
                logger.info(f"📊 Downloading last {total_files} file(s) (newest first)")
            else:
                total_files = len(media_list)
                logger.info(f"✓ Found {total_files} files to download (sorted newest first)")

            logger.info("=" * 60)

            # Create folder name with today's date and camera name
            today = datetime.now().strftime("%Y-%m-%d")
            folder_name = f"{today}_GoPro{serial}"

            # Download each file
            for idx, media in enumerate(media_list, 1):
                filename = media["filename"]
                url = media["url"]

                # Convert size to int (API sometimes returns string)
                try:
                    size_bytes = int(media.get("size", 0))
                    size_mb = size_bytes / (1024 * 1024)
                except (ValueError, TypeError):
                    size_mb = 0
                    logger.warning(f"Could not parse file size for {filename}")

                logger.info(f"[{idx}/{total_files}] {filename} ({size_mb:.1f} MB)")

                # Create output path with date-based organization
                output_path = self.download_dir / folder_name / filename

                # Progress callback for this file
                def file_progress(downloaded: int, total: int):
                    if progress_callback:
                        percent = int((downloaded / total) * 100) if total > 0 else 0
                        progress_callback(filename, idx, total_files, percent)

                # Download
                success = self.download_file(url, output_path, file_progress)

                if success:
                    downloaded_files.append(output_path)
                    logger.info(f"  ✓ Downloaded successfully")
                else:
                    logger.error(f"  ✗ Download failed")

            logger.info("=" * 60)
            logger.info(f"✅ Download complete: {len(downloaded_files)}/{total_files} files")
            logger.info("=" * 60)
            return downloaded_files

        except Exception as e:
            logger.error(f"❌ Download all failed: {e}", exc_info=True)
            return downloaded_files

    async def upload_to_s3(
        self,
        file_path: Path,
        serial: str,
        backend_url: str,
        api_key: str,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> bool:
        """Upload file to S3 via backend (supports large files via presigned URLs)"""
        try:
            logger.info(f"Uploading: {file_path.name}")
            logger.info(f"Backend URL: {backend_url}")

            # Validate backend URL
            if not backend_url or not backend_url.startswith('http'):
                raise ValueError(f"Invalid backend URL: {backend_url}")

            s3_key = f"GoPro_{serial}/{file_path.name}"
            file_size = file_path.stat().st_size
            file_size_mb = file_size / (1024 * 1024)

            logger.info(f"File size: {file_size_mb:.1f} MB")

            # Use presigned URL for files > 32MB
            if file_size > 32 * 1024 * 1024:
                logger.info(f"File is > 32MB, using presigned URL method")
                return await self._upload_large_file_presigned(
                    file_path, s3_key, backend_url, api_key
                )
            else:
                logger.info(f"File is <= 32MB, using direct upload")
                return await self._upload_small_file_direct(
                    file_path, s3_key, backend_url, api_key
                )

        except Exception as e:
            logger.error(f"❌ Upload failed: {e}")
            import traceback
            traceback.print_exc()
            return False

    async def _upload_small_file_direct(
        self,
        file_path: Path,
        s3_key: str,
        backend_url: str,
        api_key: str
    ) -> bool:
        """Direct upload for files <= 32MB"""
        try:
            with open(file_path, 'rb') as f:
                files = {"file": (file_path.name, f, "video/mp4")}
                data = {"s3Key": s3_key}
                headers = {"X-API-Key": api_key}

                async with httpx.AsyncClient(timeout=300.0) as client:
                    logger.info(f"Sending POST request to {backend_url}")
                    resp = await client.post(backend_url, files=files, data=data, headers=headers)
                    resp.raise_for_status()
                    logger.info(f"Response status: {resp.status_code}")

            logger.info(f"✅ Uploaded: {file_path.name}")
            return True

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 413:
                logger.error(f"❌ File too large for direct upload (413)")
                logger.info(f"Retrying with presigned URL method...")
                return await self._upload_large_file_presigned(
                    file_path, s3_key, backend_url, api_key
                )
            raise

    async def _upload_large_file_presigned(
        self,
        file_path: Path,
        s3_key: str,
        backend_url: str,
        api_key: str,
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> bool:
        """Upload large files using presigned URL (bypasses 32MB limit)"""
        try:
            # Step 1: Get presigned upload URL
            presigned_url = backend_url.replace('/upload-file', '/upload-file-presigned')
            logger.info(f"Step 1: Getting presigned URL from {presigned_url}")

            headers = {"X-API-Key": api_key}
            data = {
                "filename": s3_key,
                "content_type": "video/mp4"
            }

            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(presigned_url, headers=headers, data=data)
                resp.raise_for_status()
                result = resp.json()

            upload_url = result["upload_url"]
            file_url = result["file_url"]
            upload_headers = result["instructions"]["headers"]
            upload_headers["x-ms-blob-type"] = "BlockBlob"  # Required for Azure

            logger.info(f"✓ Got presigned URL")
            logger.info(f"Step 2: Uploading directly to Azure storage...")

            # Step 2: Upload directly to Azure blob storage with progress
            file_size = file_path.stat().st_size

            # Read file in chunks and track progress
            chunk_size = 8 * 1024 * 1024  # 8MB chunks
            uploaded = 0

            with open(file_path, 'rb') as f:
                file_data = f.read()

            # Report initial progress
            if progress_callback:
                progress_callback(0, file_size)

            async with httpx.AsyncClient(timeout=600.0) as client:
                resp = await client.put(upload_url, headers=upload_headers, content=file_data)
                resp.raise_for_status()

                # Report completion
                if progress_callback:
                    progress_callback(file_size, file_size)

            logger.info(f"✅ Uploaded: {file_path.name} (via presigned URL)")
            logger.info(f"File URL: {file_url}")
            return True

        except Exception as e:
            logger.error(f"❌ Presigned upload failed: {e}")
            raise

    def get_downloaded_files(self, serial: Optional[str] = None) -> List[Dict]:
        """Get list of downloaded files (supports both old and new folder formats)"""
        files = []

        if serial:
            # Try new format first (YYYY-MM-DD_GoProXXXX), then fall back to old (GoPro_XXXX)
            # Search for all folders matching this serial
            for camera_dir in self.download_dir.glob(f"*GoPro{serial}"):
                if camera_dir.is_dir():
                    for file_path in camera_dir.glob("*.*"):
                        if file_path.is_file():
                            files.append({
                                "path": str(file_path),
                                "name": file_path.name,
                                "size": file_path.stat().st_size,
                                "serial": serial,
                                "folder": camera_dir.name
                            })

            # Also check old format for backwards compatibility
            old_format_dir = self.download_dir / f"GoPro_{serial}"
            if old_format_dir.exists():
                for file_path in old_format_dir.glob("*.*"):
                    if file_path.is_file():
                        files.append({
                            "path": str(file_path),
                            "name": file_path.name,
                            "size": file_path.stat().st_size,
                            "serial": serial,
                            "folder": old_format_dir.name
                        })
        else:
            # Get all downloaded files from all folders
            # New format: YYYY-MM-DD_GoProXXXX
            for camera_dir in self.download_dir.glob("*_GoPro*"):
                if camera_dir.is_dir():
                    # Extract serial from folder name (e.g., "2026-02-18_GoPro8881" -> "8881")
                    import re
                    match = re.search(r'GoPro(\d+)', camera_dir.name)
                    if match:
                        serial = match.group(1)
                        for file_path in camera_dir.glob("*.*"):
                            if file_path.is_file():
                                files.append({
                                    "path": str(file_path),
                                    "name": file_path.name,
                                    "size": file_path.stat().st_size,
                                    "serial": serial,
                                    "folder": camera_dir.name
                                })

            # Old format: GoPro_XXXX (for backwards compatibility)
            for camera_dir in self.download_dir.glob("GoPro_*"):
                if camera_dir.is_dir() and not camera_dir.name.startswith("20"):  # Avoid double-counting
                    serial = camera_dir.name.replace("GoPro_", "")
                    for file_path in camera_dir.glob("*.*"):
                        if file_path.is_file():
                            files.append({
                                "path": str(file_path),
                                "name": file_path.name,
                                "size": file_path.stat().st_size,
                                "serial": serial,
                                "folder": camera_dir.name
                            })

        # Sort by modification time (newest first)
        files.sort(key=lambda x: Path(x["path"]).stat().st_mtime, reverse=True)

        return files

    def get_files_grouped_by_camera(self) -> Dict[str, List[Dict]]:
        """Get all files grouped by camera serial number and folder"""
        grouped = {}

        # Get all files
        all_files = self.get_downloaded_files()

        # Group by folder name (which includes date and camera)
        for file_info in all_files:
            folder = file_info.get("folder", "unknown")

            if folder not in grouped:
                grouped[folder] = []

            grouped[folder].append(file_info)

        return grouped
