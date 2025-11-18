#!/usr/bin/env python3
"""
Intelligent test selection based on changed files in a PR.

This script analyzes git diffs and determines which fork folders have been modified,
then generates targeted pytest commands for json_infra tests.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import List, Set, Dict

from ethereum_spec_tools.forks import Hardfork


# Get fork mapping and order from ethereum_spec_tools
def get_fork_info() -> tuple[Dict[str, str], List[str]]:
    """
    Get fork mapping and chronological order from Hardfork.discover().

    Returns:
        Tuple of (fork_mapping dict, ordered list of fork json_test_names)
    """
    forks = list(Hardfork.discover())
    fork_mapping = {fork.short_name: fork.json_test_name for fork in forks}
    fork_order = [fork.json_test_name for fork in forks]
    return fork_mapping, fork_order


FORK_MAPPING, FORK_ORDER = get_fork_info()


def get_changed_files_from_commits(commit1: str, commit2: str) -> List[str]:
    """
    Get list of changed files between two git commits.

    Args:
        commit1: First commit hash
        commit2: Second commit hash

    Returns:
        List of changed file paths
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", commit1, commit2],
            capture_output=True,
            text=True,
            check=True,
        )
        return [
            line.strip() for line in result.stdout.splitlines() if line.strip()
        ]
    except subprocess.CalledProcessError as e:
        print(
            f"Error: Failed to get git diff between {commit1} and {commit2}",
            file=sys.stderr,
        )
        print(f"Git error: {e.stderr}", file=sys.stderr)
        sys.exit(1)


def extract_affected_forks(changed_files: List[str]) -> Set[str]:
    """
    Extract fork names from changed file paths.

    Args:
        changed_files: List of file paths that have changed

    Returns:
        Set of fork json_test_names that have been affected
    """
    affected_forks = set()

    for file_path in changed_files:
        path = Path(file_path)
        parts = path.parts

        # Check if path contains src/ethereum/forks/{fork_name}/
        if len(parts) >= 4 and parts[0] == "src" and parts[1] == "ethereum" and parts[2] == "forks":
            fork_short_name = parts[3]
            fork_json_name = FORK_MAPPING.get(fork_short_name)
            if fork_json_name:
                affected_forks.add(fork_json_name)

    return affected_forks


def generate_pytest_args(affected_forks: Set[str]) -> str:
    """
    Generate pytest arguments based on affected forks.

    Args:
        affected_forks: Set of fork json_test_names

    Returns:
        String of pytest arguments
    """
    if not affected_forks:
        return ""  # No fork-specific changes, run all tests

    if len(affected_forks) == 1:
        # Single fork - use --fork filter
        fork = list(affected_forks)[0]
        return f'--fork "{fork}"'

    # Multiple forks - find earliest and latest based on FORK_ORDER
    # Get indices in the fork order
    fork_indices = []
    for fork in affected_forks:
        if fork in FORK_ORDER:
            fork_indices.append((FORK_ORDER.index(fork), fork))

    if not fork_indices:
        return ""  # Shouldn't happen, but handle gracefully

    fork_indices.sort()  # Sort by index
    earliest_fork = fork_indices[0][1]
    latest_fork = fork_indices[-1][1]

    return f'--from "{earliest_fork}" --until "{latest_fork}"'


def main():
    parser = argparse.ArgumentParser(
        description="Generate targeted test selection based on changed fork folders"
    )
    parser.add_argument(
        "commit1",
        help="First commit hash",
    )
    parser.add_argument(
        "commit2",
        help="Second commit hash",
    )
    parser.add_argument(
        "--output-format",
        choices=["args", "json", "summary"],
        default="args",
        help="Output format: 'args' for pytest arguments, "
        "'json' for structured data, 'summary' for human-readable",
    )
    parser.add_argument(
        "--github-output",
        help="Path to GitHub Actions output file (for setting outputs)",
    )

    args = parser.parse_args()

    # Get changed files from git diff
    changed_files = get_changed_files_from_commits(args.commit1, args.commit2)

    if not changed_files:
        print(
            f"No changed files between {args.commit1} and {args.commit2}. "
            "Running all tests.",
            file=sys.stderr,
        )
        if args.output_format == "args":
            print("")
        elif args.output_format == "json":
            print(json.dumps({"affected_forks": [], "pytest_args": ""}))
        sys.exit(0)

    # Extract affected forks
    affected_forks = extract_affected_forks(changed_files)
    pytest_args = generate_pytest_args(affected_forks)

    # Output based on format
    if args.output_format == "args":
        print(pytest_args)
    elif args.output_format == "json":
        result = {
            "affected_forks": sorted(affected_forks),
            "pytest_args": pytest_args,
        }
        print(json.dumps(result, indent=2))
    elif args.output_format == "summary":
        print("=== Test Selection Summary ===")
        print(f"Changed files analyzed: {len(changed_files)}")
        if affected_forks:
            print(f"Affected forks: {', '.join(sorted(affected_forks))}")
        else:
            print("No fork-specific changes detected - will run all tests")
        print(f"\nPytest arguments: {pytest_args or '(none - run all tests)'}")

    # Write to GitHub Actions output if requested
    if args.github_output:
        with open(args.github_output, "a") as f:
            f.write(f"pytest_args={pytest_args}\n")
            f.write(f"affected_forks={','.join(sorted(affected_forks))}\n")


if __name__ == "__main__":
    main()
