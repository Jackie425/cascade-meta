import os
import sys


def _should_isolate_datadir_by_seed() -> bool:
    return os.environ.get("CASCADE_ISOLATE_DATADIR_BY_SEED", "1").lower() not in {"0", "false", "no"}


def _configure_seed_datadir(seed_offset: int) -> str:
    datadir = os.environ["CASCADE_DATADIR"]
    if _should_isolate_datadir_by_seed():
        datadir = os.path.join(datadir, f"seed_{seed_offset}")
        os.environ["CASCADE_DATADIR"] = datadir

    os.makedirs(datadir, exist_ok=True)
    return datadir


def main(argv=None):
    argv = sys.argv if argv is None else argv

    if "CASCADE_ENV_SOURCED" not in os.environ:
        raise Exception("The Cascade environment must be sourced prior to running the Python recipes.")

    if len(argv) < 4:
        raise Exception("Usage: python do_collect_cascade_coverage.py <design_name> <num_cores> <num_testcases> [seed_offset] [can_authorize_privileges:0|1]")

    design_name = argv[1]
    num_cores = int(argv[2])
    num_testcases = int(argv[3])
    seed_offset = int(argv[4]) if len(argv) >= 5 else 0
    can_authorize_privileges = bool(int(argv[5])) if len(argv) >= 6 else False

    datadir = _configure_seed_datadir(seed_offset)
    print(f"Using Cascade data directory: {datadir}")

    from benchmarking.collectcascadecoverage import collect_coverage_cascade_verilator

    return collect_coverage_cascade_verilator(
        design_name=design_name,
        num_cores=num_cores,
        num_testcases=num_testcases,
        seed_offset=seed_offset,
        can_authorize_privileges=can_authorize_privileges,
    )


if __name__ == '__main__':
    main()
