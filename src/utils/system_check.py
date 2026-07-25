"""FFmpeg discovery helpers shared by media-generation workflows."""
import shutil
import platform
import os
import sys
from typing import Optional
import logging

logger = logging.getLogger(__name__)


def get_ffmpeg_path() -> Optional[str]:
    """
    Get path to ffmpeg binary, preferring bundled version.

    For packaged applications (PyInstaller, etc.), this will first look for
    a bundled ffmpeg in the application directory, then fall back to system PATH,
    then check common installation paths (especially useful on Windows).

    Returns:
        Path to ffmpeg executable, or None if not found
    """
    # For PyInstaller or similar packagers
    if getattr(sys, 'frozen', False):
        # Running in a bundle
        bundle_dir = getattr(sys, '_MEIPASS', os.path.dirname(sys.executable))

        # Try common bundle locations
        ffmpeg_name = 'ffmpeg.exe' if platform.system() == 'Windows' else 'ffmpeg'
        possible_paths = [
            os.path.join(bundle_dir, 'bin', ffmpeg_name),
            os.path.join(bundle_dir, 'ffmpeg', ffmpeg_name),
            os.path.join(bundle_dir, ffmpeg_name),
        ]

        for path in possible_paths:
            if os.path.exists(path) and os.access(path, os.X_OK):
                logger.info(f"Using bundled ffmpeg at: {path}")
                return path

    # Fall back to system ffmpeg via PATH
    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        logger.info(f"Using system ffmpeg at: {system_ffmpeg}")
        return system_ffmpeg

    # On Windows, shutil.which() may fail if PATH wasn't refreshed after install.
    # Check common Windows installation paths as fallback.
    if platform.system() == "Windows":
        common_windows_paths = [
            os.path.join(os.environ.get("ProgramFiles", "C:\\Program Files"), "ffmpeg", "bin", "ffmpeg.exe"),
            os.path.join(os.environ.get("ProgramFiles(x86)", "C:\\Program Files (x86)"), "ffmpeg", "bin", "ffmpeg.exe"),
            os.path.join(os.environ.get("LOCALAPPDATA", ""), "ffmpeg", "bin", "ffmpeg.exe"),
            os.path.join(os.environ.get("USERPROFILE", ""), "ffmpeg", "bin", "ffmpeg.exe"),
            os.path.join(os.environ.get("USERPROFILE", ""), "Desktop", "ffmpeg", "bin", "ffmpeg.exe"),
            os.path.join(os.environ.get("USERPROFILE", ""), "Downloads", "ffmpeg", "bin", "ffmpeg.exe"),
            # Scoop package manager
            os.path.join(os.environ.get("USERPROFILE", ""), "scoop", "shims", "ffmpeg.exe"),
            # Chocolatey package manager
            os.path.join(os.environ.get("ChocolateyInstall", "C:\\ProgramData\\chocolatey"), "bin", "ffmpeg.exe"),
            # winget / common manual installs
            "C:\\ffmpeg\\bin\\ffmpeg.exe",
            "C:\\tools\\ffmpeg\\bin\\ffmpeg.exe",
        ]
        for path in common_windows_paths:
            if path and os.path.isfile(path):
                logger.info(f"Using ffmpeg found at common Windows path: {path}")
                return path

        logger.warning(
            "FFmpeg not found in PATH or common Windows paths. "
            "If FFmpeg is installed, ensure its 'bin' folder is in the system PATH "
            "and restart the application."
        )

    return None


def get_ffmpeg_install_instructions() -> str:
    """
    Get platform-specific installation instructions for ffmpeg.

    Note: For bundled applications, this is mainly for fallback scenarios.
    """
    system = platform.system()

    if system == "Windows":
        return (
            "FFmpeg Installation (Windows):\n"
            "1. Download from: https://www.gyan.dev/ffmpeg/builds/\n"
            "2. Extract the downloaded file\n"
            "3. Add the 'bin' folder to your system PATH\n"
            "4. Restart the application\n\n"
            "Alternatively, download from: https://ffmpeg.org/download.html"
        )
    elif system == "Darwin":  # macOS
        return (
            "FFmpeg Installation (macOS):\n"
            "1. Install Homebrew if not already installed: https://brew.sh/\n"
            "2. Open Terminal and run: brew install ffmpeg\n"
            "3. Restart the application\n\n"
            "Alternatively, download from: https://ffmpeg.org/download.html"
        )
    elif system == "Linux":
        return (
            "FFmpeg Installation (Linux):\n"
            "Ubuntu/Debian: sudo apt-get update && sudo apt-get install ffmpeg\n"
            "Fedora: sudo dnf install ffmpeg\n"
            "Arch Linux: sudo pacman -S ffmpeg\n\n"
            "Or download from: https://ffmpeg.org/download.html"
        )
    else:
        return (
            "FFmpeg Installation:\n"
            "Please visit https://ffmpeg.org/download.html for installation instructions\n"
            "for your operating system."
        )
