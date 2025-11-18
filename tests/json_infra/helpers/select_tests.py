"""
Targeted test selection based on changed files.

This module reads a list of changed files and determines which fork
folders have been modified, then provides functions to generate targeted
pytest commands.
"""

from pathlib import Path
from typing import List

from ethereum_spec_tools.forks import Hardfork

FORK_MAPPING = {
    fork.short_name: fork.json_test_name for fork in Hardfork.discover()
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
    # Read changed files from disk
    changed_files_file = Path(files_path)
    if not changed_files_file.exists():
        print(f"File list file {files_path} does not exist or is empty!!")
        return []

    with open(changed_files_file, "r") as f:
        changed_files = [line.strip() for line in f if line.strip()]

    # Extract affected forks
    affected_forks = set()
    run_all_tests = False

    for file_path in changed_files:
        path = Path(file_path)
        parts = path.parts

        # Check if path contains src/ethereum/forks/{fork_name}/
        if file_path.startswith("src/ethereum/"):
            if parts[2] == "forks":
                fork_short_name = parts[3]
                fork_json_name = FORK_MAPPING.get(fork_short_name)
                if fork_json_name:
                    affected_forks.add(fork_json_name)
            else:
                run_all_tests = True
                break
        elif file_path.startswith("tests/json_infra/"):
            run_all_tests = True

    if run_all_tests:
        return [fork.json_test_name for fork in Hardfork.discover()]

    return list(affected_forks)
