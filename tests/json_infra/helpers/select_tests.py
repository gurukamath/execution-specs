"""
Targeted test selection based on changed files.

This module reads a list of changed files and determines which fork
folders have been modified, then provides functions to generate targeted
pytest commands.
"""

from pathlib import Path
from typing import List

from .. import TestHardfork

FORK_MAPPING = {
    fork.short_name: fork.json_test_name for fork in TestHardfork.discover()
}


def extract_affected_forks(files_path: str) -> List[str]:
    """
    Extract fork names from changed file paths read from disk.

    Args:
        files_path: Path to file containing changed file paths
        (one per line)

    Returns:
        List of fork json_test_names that have been affected

    """
    all_forks = [fork.json_test_name for fork in Hardfork.discover()]
    # Read changed files from disk
    changed_files_file = Path(files_path)
    if not changed_files_file.exists():
        print(f"File list file {files_path} does not exist or is empty!!")
        return all_forks

    with open(changed_files_file, "r") as f:
        changed_files = [line.strip() for line in f if line.strip()]

    # Extract affected forks
    affected_forks = set()

    for file_path in changed_files:

        # Check if path contains src/ethereum/forks/{fork_name}/
        if file_path.startswith("src/ethereum/"):
            parts = Path(file_path).parts
            if len(parts) >= 4 and parts[2] == "forks":
                # Example src/ethereum/forks/berlin/__init__.py
                fork_short_name = parts[3]
                fork_json_name = FORK_MAPPING.get(fork_short_name)
                if fork_json_name:
                    affected_forks.add(fork_json_name)
            else:
                # Example src/ethereum/exceptions.py
                return all_forks
        elif file_path.startswith(
            "src/ethereum_spec_tools/evm_tools"
        ) or file_path.startswith("tests/json_infra/"):
            return all_forks

    return list(affected_forks)
