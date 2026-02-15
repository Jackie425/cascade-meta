from benchmarking.collectcascadecoverage import collect_coverage_cascade_verilator

import os
import sys

if __name__ == '__main__':
    if "CASCADE_ENV_SOURCED" not in os.environ:
        raise Exception("The Cascade environment must be sourced prior to running the Python recipes.")

    if len(sys.argv) < 4:
        raise Exception("Usage: python do_collect_cascade_coverage.py <design_name> <num_cores> <num_testcases> [seed_offset] [can_authorize_privileges:0|1]")

    design_name = sys.argv[1]
    num_cores = int(sys.argv[2])
    num_testcases = int(sys.argv[3])
    seed_offset = int(sys.argv[4]) if len(sys.argv) >= 5 else 0
    can_authorize_privileges = bool(int(sys.argv[5])) if len(sys.argv) >= 6 else False

    collect_coverage_cascade_verilator(
        design_name=design_name,
        num_cores=num_cores,
        num_testcases=num_testcases,
        seed_offset=seed_offset,
        can_authorize_privileges=can_authorize_privileges,
    )
else:
    raise Exception("This module must be at the toplevel.")
