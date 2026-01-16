# Memory Profiling Guide

This guide explains how to use pytest-monitor to track memory consumption for tests in the execution-specs repository.

## Overview

`pytest-monitor` is integrated into the test suite and automatically captures:
- **Memory usage (MB)** - Peak memory consumption per test
- **Execution time** - Total, user, and kernel time
- **CPU usage** - CPU utilization percentage

All metrics are stored in a SQLite database (`.pymon`) for analysis.

## Running Tests with Memory Profiling

### Using `fill` command directly

```bash
# Run Amsterdam tests with memory profiling
uv run fill --clean --fork Amsterdam tests/amsterdam

# Run specific test file
uv run fill --clean --fork Amsterdam tests/amsterdam/eip7928_block_level_access_lists/test_block_access_lists.py

# Run specific test
uv run fill --fork Amsterdam tests/amsterdam/eip7928_block_level_access_lists/test_block_access_lists.py::test_bal_nonce_changes
```

Memory profiling is **automatically enabled** - pytest-monitor is loaded as a plugin.

### Using tox (PyPy3)

```bash
# Run all pypy3 tests with memory profiling
uvx --with=tox-uv tox -e pypy3

# Run specific test directory
uvx --with=tox-uv tox -e pypy3 -- tests/amsterdam

# Run specific test file
uvx --with=tox-uv tox -e pypy3 -- tests/amsterdam/eip7928_block_level_access_lists/test_block_access_lists.py
```

The `.pymon` database will be created in the root directory of the repository.

## Viewing Memory Statistics

### Using the helper script

A convenience script is provided to view memory statistics:

```bash
# Show top 20 tests by memory usage
uv run python view_memory_stats.py

# Show top 50 tests
uv run python view_memory_stats.py --limit 50

# Sort by execution time instead of memory
uv run python view_memory_stats.py --sort time

# Sort by CPU usage
uv run python view_memory_stats.py --sort cpu

# View help
uv run python view_memory_stats.py --help
```

### Using SQL directly

Query the database directly for custom analysis:

```bash
# Show schema
sqlite3 .pymon "PRAGMA table_info(TEST_METRICS);"

# Top 10 memory-intensive tests
sqlite3 .pymon "SELECT item, item_variant, mem_usage, total_time FROM TEST_METRICS ORDER BY mem_usage DESC LIMIT 10;"

# Average memory by test name
sqlite3 .pymon "SELECT item, AVG(mem_usage) as avg_mem, COUNT(*) as runs FROM TEST_METRICS GROUP BY item ORDER BY avg_mem DESC LIMIT 10;"

# Tests taking longest
sqlite3 .pymon "SELECT item, item_variant, total_time, mem_usage FROM TEST_METRICS ORDER BY total_time DESC LIMIT 10;"

# Tests with highest CPU usage
sqlite3 .pymon "SELECT item, item_variant, cpu_usage, mem_usage FROM TEST_METRICS ORDER BY cpu_usage DESC LIMIT 10;"
```

### Export to CSV

```bash
# Export all metrics to CSV
sqlite3 -header -csv .pymon "SELECT * FROM TEST_METRICS;" > test_metrics.csv
```

## Database Schema

The `.pymon` SQLite database contains three tables:

### TEST_METRICS
- `SESSION_H` - Session hash identifier
- `ITEM` - Test name
- `ITEM_VARIANT` - Test parameters/variant
- `ITEM_PATH` - Full path to test
- `TOTAL_TIME` - Total execution time (seconds)
- `USER_TIME` - User CPU time (seconds)
- `KERNEL_TIME` - Kernel CPU time (seconds)
- `CPU_USAGE` - CPU utilization (%)
- `MEM_USAGE` - Peak memory usage (MB)

### TEST_SESSIONS
- Session metadata

### EXECUTION_CONTEXTS
- Environment information

## Advanced Options

### Disable monitoring for specific tests

```bash
# Disable all monitoring
uv run fill --no-monitor --fork Amsterdam tests/amsterdam
```

### Change monitoring scope

```bash
# Monitor at class level instead of function level
uv run fill --restrict-scope-to=class --fork Amsterdam tests/amsterdam

# Monitor multiple scopes (function and class)
uv run fill --restrict-scope-to=function,class --fork Amsterdam tests/amsterdam
```

### Remote monitoring

Send metrics to a remote server:

```bash
uv run fill --remote-server=localhost:5000 --fork Amsterdam tests/amsterdam
```

## Limitations with PyPy

**Note**: When using PyPy (pypy3), `tracemalloc` is not available, so pytest-monitor uses `resource.getrusage()` which provides:
- ✅ Peak RSS (Resident Set Size) memory
- ❌ No detailed allocation tracking
- ❌ No memory snapshots

This is sufficient for identifying memory-intensive tests but won't provide detailed allocation traces.

## Cleaning Up

```bash
# Remove the database to start fresh
rm .pymon

# Or backup before cleaning
mv .pymon .pymon.backup
```

## Example Workflow

1. Run tests with memory profiling:
   ```bash
   uvx --with=tox-uv tox -e pypy3 -- tests/amsterdam
   ```

2. View top memory consumers:
   ```bash
   uv run python view_memory_stats.py --limit 20
   ```

3. Analyze specific tests:
   ```bash
   sqlite3 .pymon "SELECT item, item_variant, mem_usage, total_time FROM TEST_METRICS WHERE item LIKE '%access_lists%' ORDER BY mem_usage DESC;"
   ```

4. Export for further analysis:
   ```bash
   sqlite3 -header -csv .pymon "SELECT * FROM TEST_METRICS;" > amsterdam_tests_metrics.csv
   ```

## Troubleshooting

### Database not created
- Ensure pytest-monitor is installed: `uv pip list | grep pytest-monitor`
- Check that tests are actually running (not being skipped)
- Verify the database path is writable

### High memory usage not captured
- PyPy's garbage collection may delay memory reporting
- Try setting PyPy GC environment variables for more aggressive collection:
  ```bash
  PYPY_GC_MAX=1.5GB PYPY_GC_MIN=512MB uvx --with=tox-uv tox -e pypy3
  ```

### Database locked errors
- Close any SQLite viewers/tools accessing `.pymon`
- Remove stale lock files: `rm .pymon-*`

## References

- [pytest-monitor documentation](https://pytest-monitor.readthedocs.io/)
- [PyPy memory management](https://doc.pypy.org/en/latest/gc_info.html)
- [Python resource module](https://docs.python.org/3/library/resource.html)
