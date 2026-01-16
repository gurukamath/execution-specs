#!/usr/bin/env python3
"""View memory statistics from pytest-monitor database."""

import sqlite3
import sys
from pathlib import Path


def format_memory(mb: float) -> str:
    """Format memory in MB to human-readable string."""
    if mb < 1:
        return f"{mb * 1024:.2f} KB"
    elif mb > 1024:
        return f"{mb / 1024:.2f} GB"
    return f"{mb:.2f} MB"


def view_memory_stats(db_path: str = ".pymon", limit: int = 20, sort_by: str = "mem_usage"):
    """
    View memory statistics from pytest-monitor database.

    Args:
        db_path: Path to the .pymon database file
        limit: Number of results to show (default: 20)
        sort_by: Column to sort by (mem_usage, total_time, cpu_usage)
    """
    if not Path(db_path).exists():
        print(f"Error: Database file '{db_path}' not found.")
        print("Run tests with pytest-monitor enabled first.")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Valid sort columns
    valid_sorts = {
        "mem": "mem_usage",
        "mem_usage": "mem_usage",
        "memory": "mem_usage",
        "time": "total_time",
        "total_time": "total_time",
        "cpu": "cpu_usage",
        "cpu_usage": "cpu_usage",
    }

    sort_column = valid_sorts.get(sort_by.lower(), "mem_usage")

    query = f"""
    SELECT
        item,
        item_variant,
        mem_usage,
        total_time,
        cpu_usage,
        user_time,
        kernel_time
    FROM TEST_METRICS
    ORDER BY {sort_column} DESC
    LIMIT ?
    """

    cursor.execute(query, (limit,))
    results = cursor.fetchall()

    if not results:
        print("No test metrics found in database.")
        return

    # Print header
    print(f"\n{'Test Name':<50} {'Variant':<30} {'Memory':<12} {'Time (s)':<10} {'CPU %':<8}")
    print("=" * 120)

    # Print results
    for row in results:
        test_name, variant, mem_usage, total_time, cpu_usage, user_time, kernel_time = row
        mem_str = format_memory(mem_usage)

        # Truncate long names
        test_name_short = test_name[:47] + "..." if len(test_name) > 50 else test_name
        variant_short = variant[:27] + "..." if len(variant) > 30 else variant

        print(f"{test_name_short:<50} {variant_short:<30} {mem_str:<12} {total_time:<10.3f} {cpu_usage:<8.2f}")

    # Print summary statistics
    cursor.execute("SELECT COUNT(*), AVG(mem_usage), MAX(mem_usage), SUM(total_time) FROM TEST_METRICS")
    count, avg_mem, max_mem, total_time = cursor.fetchone()

    print("\n" + "=" * 120)
    print(f"Total tests: {count}")
    print(f"Average memory: {format_memory(avg_mem)}")
    print(f"Peak memory: {format_memory(max_mem)}")
    print(f"Total time: {total_time:.2f}s")
    print()

    conn.close()


def main():
    """Main entry point."""
    import argparse

    parser = argparse.ArgumentParser(
        description="View memory statistics from pytest-monitor database",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python view_memory_stats.py                    # Show top 20 by memory
  python view_memory_stats.py --limit 50         # Show top 50
  python view_memory_stats.py --sort time        # Sort by execution time
  python view_memory_stats.py --sort cpu         # Sort by CPU usage
        """
    )

    parser.add_argument(
        "--db",
        default=".pymon",
        help="Path to pytest-monitor database (default: .pymon)"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Number of results to show (default: 20)"
    )
    parser.add_argument(
        "--sort",
        choices=["mem", "memory", "time", "cpu"],
        default="mem",
        help="Sort by: mem/memory (default), time, or cpu"
    )

    args = parser.parse_args()

    view_memory_stats(args.db, args.limit, args.sort)


if __name__ == "__main__":
    main()
