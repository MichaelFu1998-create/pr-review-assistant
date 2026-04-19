"""On-demand tool installation."""

import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)


def is_command_available(command: str) -> bool:
    """Check if a command is available on PATH."""
    return shutil.which(command) is not None


def run_install(install_cmd: str | list[str], timeout: int = 120) -> bool:
    """Run an installation command. Returns True on success."""
    if isinstance(install_cmd, str):
        install_cmd = install_cmd.split()

    logger.info(f"Installing tool: {' '.join(install_cmd)}")
    try:
        result = subprocess.run(
            install_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if result.returncode == 0:
            logger.info("Installation successful")
            return True
        else:
            logger.warning(f"Installation failed (exit code {result.returncode}): {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        logger.warning(f"Installation timed out after {timeout}s")
        return False
    except Exception as e:
        logger.warning(f"Installation error: {e}")
        return False
