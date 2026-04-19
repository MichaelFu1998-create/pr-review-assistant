"""Auto-detect the tech stack from changed files and repo config files."""

import logging
import os

logger = logging.getLogger(__name__)

DETECTION_RULES: dict[str, dict] = {
    "python": {
        "extensions": [".py"],
        "config_files": ["requirements.txt", "pyproject.toml", "setup.py", "Pipfile", "setup.cfg"],
    },
    "javascript": {
        "extensions": [".js", ".jsx", ".mjs"],
        "config_files": ["package.json"],
    },
    "typescript": {
        "extensions": [".ts", ".tsx"],
        "config_files": ["tsconfig.json"],
    },
    "java": {
        "extensions": [".java"],
        "config_files": ["pom.xml", "build.gradle", "build.gradle.kts"],
    },
    "go": {
        "extensions": [".go"],
        "config_files": ["go.mod"],
    },
    "ruby": {
        "extensions": [".rb"],
        "config_files": ["Gemfile"],
    },
    "rust": {
        "extensions": [".rs"],
        "config_files": ["Cargo.toml"],
    },
    "csharp": {
        "extensions": [".cs"],
        "config_files": [],  # .csproj needs glob
    },
    "cpp": {
        "extensions": [".cpp", ".cc", ".c", ".h", ".hpp"],
        "config_files": ["CMakeLists.txt"],
    },
    "kotlin": {
        "extensions": [".kt", ".kts"],
        "config_files": [],
    },
    "swift": {
        "extensions": [".swift"],
        "config_files": ["Package.swift"],
    },
    "php": {
        "extensions": [".php"],
        "config_files": ["composer.json"],
    },
    "dockerfile": {
        "extensions": [],
        "config_files": ["Dockerfile", "docker-compose.yml", "docker-compose.yaml"],
        "filename_patterns": ["Dockerfile"],
    },
    "shell": {
        "extensions": [".sh", ".bash", ".zsh"],
        "config_files": [],
    },
    "terraform": {
        "extensions": [".tf"],
        "config_files": [],
    },
}


def detect_stack(changed_files: list[str], workspace: str = ".") -> list[str]:
    """Detect tech stack from changed files and repo config files.

    Args:
        changed_files: List of file paths changed in the PR.
        workspace: Path to the repo root.

    Returns:
        List of detected language names (e.g. ["python", "javascript"]).
    """
    detected = set()

    # Check file extensions in changed files
    for filepath in changed_files:
        filename = os.path.basename(filepath)
        for lang, rules in DETECTION_RULES.items():
            # Check extensions
            for ext in rules["extensions"]:
                if filepath.endswith(ext):
                    detected.add(lang)

            # Check filename patterns (e.g., Dockerfile)
            for pattern in rules.get("filename_patterns", []):
                if filename.startswith(pattern):
                    detected.add(lang)

    # Check for config files in repo root
    for lang, rules in DETECTION_RULES.items():
        for config_file in rules["config_files"]:
            if os.path.exists(os.path.join(workspace, config_file)):
                detected.add(lang)

    logger.info(f"Detected tech stack: {sorted(detected)}")
    return sorted(detected)
