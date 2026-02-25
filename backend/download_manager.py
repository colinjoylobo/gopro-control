"""
Download Manager for GoPro Videos
"""
import re
import requests
import httpx
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Callable
import logging

logger = logging.getLogger(__name__)

GOPRO_IP = "http://10.5.5.9:8080"


def format_size(size_bytes: int) -> str:
    """Convert bytes to human-readable string (e.g., '2.4 GB')"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


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

    @staticmethod
    def _sanitize_filename(name: str) -> str:
        """Replace characters that are invalid in filenames with underscores"""
        return re.sub(r'[<>:"/\\|?*]', '_', name).strip()

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

    def get_media_summary(self) -> Dict:
        """Get summary of media on camera with full file details"""
        media_list = self.get_media_list()

        if not media_list:
            return {
                "total_files": 0,
                "total_size_bytes": 0,
                "total_size_human": format_size(0),
                "video_count": 0,
                "videos": [],
                "other_count": 0,
                "others": []
            }

        videos = []
        others = []

        for f in media_list:
            entry = {
                "directory": f["directory"],
                "filename": f["filename"],
                "size": f["size"],
                "size_human": format_size(f["size"]),
                "mod_time": f["mod_time"],
                "url": f["url"]
            }
            if f["filename"].upper().endswith(".MP4"):
                videos.append(entry)
            else:
                others.append(entry)

        total_size = sum(f["size"] for f in media_list)

        return {
            "total_files": len(media_list),
            "total_size_bytes": total_size,
            "total_size_human": format_size(total_size),
            "video_count": len(videos),
            "videos": videos,
            "other_count": len(others),
            "others": others
        }

    def erase_all_media(self) -> bool:
        """Delete all media from GoPro SD card. Requires WiFi connection to camera."""
        try:
            resp = requests.delete(f"{GOPRO_IP}/gopro/media/all", timeout=30)
            if resp.status_code == 200:
                logger.info("Successfully erased all media from camera")
                return True
            else:
                logger.error(f"Erase failed with status {resp.status_code}: {resp.text}")
                return False
        except Exception as e:
            logger.error(f"Erase all media failed: {e}")
            return False

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
        max_files: Optional[int] = None,
        shoot_name: Optional[str] = None,
        take_number: Optional[int] = None
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

            # Create folder path — use shoot/take hierarchy if provided
            if shoot_name and take_number is not None:
                safe_name = self._sanitize_filename(shoot_name)
                output_base = self.download_dir / safe_name / f"Take_{take_number:02d}" / f"GoPro{serial}"
            else:
                today = datetime.now().strftime("%Y-%m-%d")
                output_base = self.download_dir / f"{today}_GoPro{serial}"

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

                # Create output path
                output_path = output_base / filename

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

    def download_latest_from_camera(
        self,
        serial: str,
        progress_callback: Optional[Callable[[str, int, int, int], None]] = None,
        shoot_name: Optional[str] = None,
        take_number: Optional[int] = None
    ) -> List[Path]:
        """
        Download only the latest video (.MP4) from camera
        progress_callback(filename, current_file_idx, total_files, percent)
        """
        downloaded_files = []

        try:
            logger.info("=" * 60)
            logger.info(f"Starting latest video download for camera {serial}")

            media_list = self.get_media_list()

            if not media_list:
                logger.warning("No media files found on camera")
                return []

            # Filter to .MP4 files only
            video_files = [f for f in media_list if f["filename"].upper().endswith(".MP4")]

            if not video_files:
                logger.warning("No .MP4 video files found on camera")
                return []

            latest = video_files[0]
            filename = latest["filename"]
            url = latest["url"]

            try:
                size_bytes = int(latest.get("size", 0))
                size_mb = size_bytes / (1024 * 1024)
            except (ValueError, TypeError):
                size_mb = 0

            logger.info(f"Latest video: {filename} ({size_mb:.1f} MB)")

            if shoot_name and take_number is not None:
                safe_name = self._sanitize_filename(shoot_name)
                output_path = self.download_dir / safe_name / f"Take_{take_number:02d}" / f"GoPro{serial}" / filename
            else:
                today = datetime.now().strftime("%Y-%m-%d")
                output_path = self.download_dir / f"{today}_GoPro{serial}" / filename

            def file_progress(downloaded: int, total: int):
                if progress_callback:
                    percent = int((downloaded / total) * 100) if total > 0 else 0
                    progress_callback(filename, 1, 1, percent)

            success = self.download_file(url, output_path, file_progress)

            if success:
                downloaded_files.append(output_path)
                logger.info(f"  Downloaded successfully")
            else:
                logger.error(f"  Download failed")

            return downloaded_files

        except Exception as e:
            logger.error(f"Download latest failed: {e}", exc_info=True)
            return downloaded_files

    def download_selected_from_camera(
        self,
        serial: str,
        file_list: List[Dict],
        progress_callback: Optional[Callable[[str, int, int, int], None]] = None,
        shoot_name: Optional[str] = None,
        take_number: Optional[int] = None
    ) -> List[Path]:
        """
        Download selected files from camera.
        file_list: list of {directory, filename} dicts
        progress_callback(filename, current_file_idx, total_files, percent)
        """
        downloaded_files = []

        try:
            total_files = len(file_list)
            logger.info("=" * 60)
            logger.info(f"Downloading {total_files} selected file(s) for camera {serial}")

            if shoot_name and take_number is not None:
                safe_name = self._sanitize_filename(shoot_name)
                output_base = self.download_dir / safe_name / f"Take_{take_number:02d}" / f"GoPro{serial}"
            else:
                today = datetime.now().strftime("%Y-%m-%d")
                output_base = self.download_dir / f"{today}_GoPro{serial}"

            for idx, file_info in enumerate(file_list, 1):
                directory = file_info["directory"]
                filename = file_info["filename"]
                url = f"{GOPRO_IP}/videos/DCIM/{directory}/{filename}"

                logger.info(f"[{idx}/{total_files}] {filename}")

                output_path = output_base / filename

                def file_progress(downloaded: int, total: int):
                    if progress_callback:
                        percent = int((downloaded / total) * 100) if total > 0 else 0
                        progress_callback(filename, idx, total_files, percent)

                success = self.download_file(url, output_path, file_progress)

                if success:
                    downloaded_files.append(output_path)
                    logger.info(f"  Downloaded successfully")
                else:
                    logger.error(f"  Download failed")

            logger.info("=" * 60)
            logger.info(f"Selected download complete: {len(downloaded_files)}/{total_files} files")
            return downloaded_files

        except Exception as e:
            logger.error(f"Selected download failed: {e}", exc_info=True)
            return downloaded_files

    async def upload_file_to_backend(
        self,
        file_path: Path,
        s3_key: str,
        backend_url: str,
        api_key: str,
        content_type: str = "video/mp4"
    ) -> Optional[str]:
        """Upload a file to the backend and return the resulting URL.

        Automatically selects presigned URL upload for files > 32MB,
        direct multipart upload otherwise.
        Returns the file URL string on success, or None on failure.
        """
        if not backend_url or not backend_url.startswith('http'):
            raise ValueError(f"Invalid backend URL: {backend_url}")

        file_size = file_path.stat().st_size
        file_size_mb = file_size / (1024 * 1024)
        logger.info(f"Uploading: {file_path.name} ({file_size_mb:.1f} MB)")

        if file_size > 32 * 1024 * 1024:
            logger.info(f"File is > 32MB, using presigned URL method")
            return await self._upload_via_presigned(
                file_path, s3_key, backend_url, api_key, content_type
            )
        else:
            logger.info(f"File is <= 32MB, using direct upload")
            return await self._upload_direct(
                file_path, s3_key, backend_url, api_key, content_type
            )

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
            s3_key = f"GoPro_{serial}/{file_path.name}"
            url = await self.upload_file_to_backend(
                file_path, s3_key, backend_url, api_key
            )
            return url is not None
        except Exception as e:
            logger.error(f"❌ Upload failed: {e}", exc_info=True)
            return False

    async def _upload_direct(
        self,
        file_path: Path,
        s3_key: str,
        backend_url: str,
        api_key: str,
        content_type: str = "video/mp4"
    ) -> Optional[str]:
        """Direct multipart upload for files <= 32MB. Returns URL or None."""
        try:
            with open(file_path, 'rb') as f:
                files = {"file": (file_path.name, f, content_type)}
                data = {"s3Key": s3_key}
                headers = {"X-API-Key": api_key}

                async with httpx.AsyncClient(timeout=300.0) as client:
                    logger.info(f"Sending POST request to {backend_url}")
                    resp = await client.post(backend_url, files=files, data=data, headers=headers)
                    resp.raise_for_status()
                    result = resp.json()
                    logger.info(f"Response status: {resp.status_code}")

            url = result.get("url") or result.get("fileUrl") or result.get("s3Url")
            logger.info(f"✅ Uploaded: {file_path.name}")
            return url

        except httpx.HTTPStatusError as e:
            if e.response.status_code == 413:
                logger.error(f"❌ File too large for direct upload (413)")
                logger.info(f"Retrying with presigned URL method...")
                return await self._upload_via_presigned(
                    file_path, s3_key, backend_url, api_key, content_type
                )
            raise

    async def _upload_via_presigned(
        self,
        file_path: Path,
        s3_key: str,
        backend_url: str,
        api_key: str,
        content_type: str = "video/mp4"
    ) -> Optional[str]:
        """Upload via presigned URL (streaming, no full file read). Returns URL."""
        # Step 1: Get presigned upload URL
        presigned_url = backend_url.replace('/upload-file', '/upload-file-presigned')
        logger.info(f"Step 1: Getting presigned URL from {presigned_url}")

        headers = {"X-API-Key": api_key}
        data = {
            "filename": s3_key,
            "content_type": content_type
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

        # Step 2: Stream file directly (no f.read() into memory)
        file_size = file_path.stat().st_size
        upload_headers["Content-Length"] = str(file_size)

        with open(file_path, 'rb') as f:
            async with httpx.AsyncClient(timeout=600.0) as client:
                resp = await client.put(upload_url, headers=upload_headers, content=f)
                resp.raise_for_status()

        logger.info(f"✅ Uploaded: {file_path.name} (via presigned URL)")
        logger.info(f"File URL: {file_url}")
        return file_url

    def get_downloaded_files(self, serial: Optional[str] = None) -> List[Dict]:
        """Get list of downloaded files (supports legacy flat folders and shoot/take hierarchy)"""
        files = []
        seen_paths = set()

        def add_file(file_path, serial_num, folder, shoot_name=None, take_number=None, take_folder=None):
            path_str = str(file_path)
            if path_str in seen_paths:
                return
            seen_paths.add(path_str)
            entry = {
                "path": path_str,
                "name": file_path.name,
                "size": file_path.stat().st_size,
                "serial": serial_num,
                "folder": folder
            }
            if shoot_name:
                entry["shoot_name"] = shoot_name
            if take_number is not None:
                entry["take_number"] = take_number
            if take_folder:
                entry["take_folder"] = take_folder
            files.append(entry)

        # Scan shoot/take hierarchy: {download_dir}/{ShootName}/Take_NN/GoProXXXX/
        for shoot_dir in self.download_dir.iterdir():
            if not shoot_dir.is_dir():
                continue
            for take_dir in shoot_dir.iterdir():
                if not take_dir.is_dir() or not take_dir.name.startswith("Take_"):
                    continue
                take_match = re.match(r'Take_(\d+)', take_dir.name)
                if not take_match:
                    continue
                take_num = int(take_match.group(1))
                for cam_dir in take_dir.iterdir():
                    if not cam_dir.is_dir() or not cam_dir.name.startswith("GoPro"):
                        continue
                    cam_serial = cam_dir.name.replace("GoPro", "")
                    if serial and cam_serial != serial:
                        continue
                    for file_path in cam_dir.glob("*.*"):
                        if file_path.is_file():
                            add_file(
                                file_path, cam_serial,
                                f"{shoot_dir.name}/{take_dir.name}/{cam_dir.name}",
                                shoot_name=shoot_dir.name,
                                take_number=take_num,
                                take_folder=take_dir.name
                            )

        # Scan legacy flat folders
        if serial:
            for camera_dir in self.download_dir.glob(f"*GoPro{serial}"):
                if camera_dir.is_dir():
                    for file_path in camera_dir.glob("*.*"):
                        if file_path.is_file():
                            add_file(file_path, serial, camera_dir.name)

            old_format_dir = self.download_dir / f"GoPro_{serial}"
            if old_format_dir.exists():
                for file_path in old_format_dir.glob("*.*"):
                    if file_path.is_file():
                        add_file(file_path, serial, old_format_dir.name)
        else:
            # Date-based format: YYYY-MM-DD_GoProXXXX
            for camera_dir in self.download_dir.glob("*_GoPro*"):
                if camera_dir.is_dir():
                    match = re.search(r'GoPro(\d+)', camera_dir.name)
                    if match:
                        cam_serial = match.group(1)
                        for file_path in camera_dir.glob("*.*"):
                            if file_path.is_file():
                                add_file(file_path, cam_serial, camera_dir.name)

            # Old format: GoPro_XXXX
            for camera_dir in self.download_dir.glob("GoPro_*"):
                if camera_dir.is_dir() and not camera_dir.name.startswith("20"):
                    cam_serial = camera_dir.name.replace("GoPro_", "")
                    for file_path in camera_dir.glob("*.*"):
                        if file_path.is_file():
                            add_file(file_path, cam_serial, camera_dir.name)

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
