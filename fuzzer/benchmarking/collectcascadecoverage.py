from common.timeout import timeout
from common.designcfgs import get_design_march_flags_nocompressed
from common.spike import calibrate_spikespeed
from params.runparams import PATH_TO_TMP, NO_REMOVE_TMPFILES
from cascade.fuzzfromdescriptor import NUM_MAX_BBS_UPPERBOUND, gen_new_test_instance, gen_fuzzerstate_elf_expectedvals
from cascade.fuzzsim import runsim_verilator, MAX_CYCLES_PER_INSTR, SETUP_CYCLES

import itertools
import json
import multiprocessing as mp
import os
import time
from tqdm import tqdm


@timeout(seconds=60*60*2)
def _measure_coverage_worker(memsize: int, design_name: str, randseed: int, nmax_bbs: int, authorize_privileges: bool):
    rtl_elfpath = None
    try:
        start_time = time.time()
        fuzzerstate, rtl_elfpath, _, _, _, _ = gen_fuzzerstate_elf_expectedvals(memsize, design_name, randseed, nmax_bbs, authorize_privileges, False)
        num_instrs = len(list(itertools.chain.from_iterable(fuzzerstate.instr_objs_seq)))
        coveragepath = os.path.join(PATH_TO_TMP, f"coverage_verilator_{design_name}_{randseed}_{nmax_bbs}_{memsize}.dat")
        is_stop_successful, _ = runsim_verilator(design_name, num_instrs*MAX_CYCLES_PER_INSTR + SETUP_CYCLES, rtl_elfpath, 0, 0, coveragepath)
        if not is_stop_successful:
            raise Exception(f"Timeout during Verilator coverage testing of design `{design_name}` for tuple ({memsize}, {design_name}, {randseed}, {nmax_bbs}, {authorize_privileges}).")
        return coveragepath, time.time() - start_time
    except Exception as e:
        print(f"Ignored failed instance with tuple: ({memsize}, {design_name}, {randseed}, {nmax_bbs}, {authorize_privileges}) -- {e}")
        return None, -1
    finally:
        if rtl_elfpath is not None and (not NO_REMOVE_TMPFILES) and os.path.exists(rtl_elfpath):
            os.remove(rtl_elfpath)


def _extract_covered_bins(coverage_path: str):
    covered_bins = set()
    with open(coverage_path, 'r') as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            fields = stripped.split()
            if len(fields) < 2:
                continue
            try:
                counter_val = int(fields[-1], 0)
            except ValueError:
                continue
            if counter_val > 0:
                covered_bins.add(' '.join(fields[:-1]))
    return covered_bins


def collect_coverage_cascade_verilator(design_name: str, num_cores: int, num_testcases: int, seed_offset: int = 0, can_authorize_privileges: bool = False):
    assert num_testcases > 0
    num_workers = min(num_cores, num_testcases)
    assert num_workers > 0

    calibrate_spikespeed(rvflags=get_design_march_flags_nocompressed(design_name))

    print(f"Starting Verilator coverage testing of `{design_name}` on {num_workers} processes.")

    fixed_num_bbs_env = os.environ.get("CASCADE_COV_FIXED_NUM_BBS")
    fixed_memsize_env = os.environ.get("CASCADE_COV_FIXED_MEMSIZE")
    fixed_num_bbs = int(fixed_num_bbs_env) if fixed_num_bbs_env is not None else None
    fixed_memsize = int(fixed_memsize_env) if fixed_memsize_env is not None else None

    descriptors = [
        gen_new_test_instance(
            design_name,
            seed_offset + i,
            can_authorize_privileges,
            fixed_memsize=fixed_memsize,
            fixed_num_bbs=fixed_num_bbs,
        )
        for i in range(num_testcases)
    ]

    collected_paths = []
    collected_durations = []
    with mp.Pool(processes=num_workers) as pool:
        for coverage_path, duration in tqdm(pool.starmap(_measure_coverage_worker, descriptors), total=num_testcases):
            if coverage_path is None or duration < 0:
                continue
            collected_paths.append(coverage_path)
            collected_durations.append(duration)

    coverage_sequence = []
    cumulative_covered_bins = set()
    for coverage_path in collected_paths:
        cumulative_covered_bins |= _extract_covered_bins(coverage_path)
        coverage_sequence.append(len(cumulative_covered_bins))
        if not NO_REMOVE_TMPFILES and os.path.exists(coverage_path):
            os.remove(coverage_path)

    json_filepath = os.path.join(PATH_TO_TMP, f"cascade_verilator_coverages_{design_name}_{num_testcases}_{NUM_MAX_BBS_UPPERBOUND}.json")
    with open(json_filepath, 'w') as f:
        json.dump({
            'covsum': coverage_sequence,
            'coverage_sequence': coverage_sequence,
            'durations': collected_durations,
            'num_successes': len(collected_paths),
            'num_testcases': num_testcases,
        }, f)

    print(f"Saved Cascade Verilator coverage-time data to {json_filepath}")
    return json_filepath
