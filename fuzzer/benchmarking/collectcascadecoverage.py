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
    start_time = time.time()
    rtl_elfpath = None
    try:
        fuzzerstate, rtl_elfpath, _, _, _, _ = gen_fuzzerstate_elf_expectedvals(memsize, design_name, randseed, nmax_bbs, authorize_privileges, False)
        num_instrs = len(list(itertools.chain.from_iterable(fuzzerstate.instr_objs_seq)))
        coveragepath = os.path.join(PATH_TO_TMP, f"coverage_verilator_{design_name}_{randseed}_{nmax_bbs}_{memsize}.dat")
        pcov_memory_path = os.path.join(PATH_TO_TMP, f"pcov_memory_verilator_{design_name}_{randseed}_{nmax_bbs}_{memsize}.txt")

        is_stop_successful, _ = runsim_verilator(
            design_name,
            num_instrs*MAX_CYCLES_PER_INSTR + SETUP_CYCLES,
            rtl_elfpath,
            0,
            0,
            coveragepath,
            pcov_memory_path=pcov_memory_path,
        )
        if not is_stop_successful:
            raise Exception(f"Timeout during Verilator coverage testing of design `{design_name}` for tuple ({memsize}, {design_name}, {randseed}, {nmax_bbs}, {authorize_privileges}).")
        return coveragepath, pcov_memory_path, time.time() - start_time
    except Exception as e:
        print(f"Ignored failed instance with tuple: ({memsize}, {design_name}, {randseed}, {nmax_bbs}, {authorize_privileges}) -- {e}")
        return None, None, time.time() - start_time
    finally:
        if rtl_elfpath is not None and (not NO_REMOVE_TMPFILES) and os.path.exists(rtl_elfpath):
            os.remove(rtl_elfpath)


def _measure_coverage_worker_star(args):
    return _measure_coverage_worker(*args)


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


def _extract_pcov_memory_bits_from_dump(pcov_memory_path: str):
    # Dump format (tab-separated):
    # <module_name>\t<instance_name>\t<size>\t<covered_idx_csv>
    # covered_idx_csv can be empty.
    if not pcov_memory_path or not os.path.exists(pcov_memory_path):
        return {}

    per_instance = {}
    with open(pcov_memory_path, 'r') as f:
        for line in f:
            stripped = line.rstrip('\n')
            if not stripped:
                continue
            fields = stripped.split('\t')
            if len(fields) != 4:
                continue
            module_name, instance_name, size_str, covered_csv = fields
            if not module_name or not instance_name:
                continue
            try:
                map_size = int(size_str, 0)
            except ValueError:
                continue
            if map_size <= 0:
                continue

            covered_bits = set()
            if covered_csv:
                for bit_str in covered_csv.split(','):
                    if not bit_str:
                        continue
                    try:
                        bit_idx = int(bit_str, 0)
                    except ValueError:
                        continue
                    if 0 <= bit_idx < map_size:
                        covered_bits.add(bit_idx)

            if instance_name not in per_instance:
                per_instance[instance_name] = {
                    "module": module_name,
                    "size": map_size,
                    "bits": set(),
                }
            if per_instance[instance_name]["module"] != module_name:
                continue
            if per_instance[instance_name]["size"] != map_size:
                continue
            per_instance[instance_name]["bits"] |= covered_bits
    return per_instance


def _append_pcov_union(cumulative_pcov_by_instance: dict, newly_covered_pcov_memory: dict):
    for instance_name, entry in newly_covered_pcov_memory.items():
        if instance_name not in cumulative_pcov_by_instance:
            cumulative_pcov_by_instance[instance_name] = {
                "module": entry["module"],
                "size": entry["size"],
                "bits": set(),
            }
        if cumulative_pcov_by_instance[instance_name]["module"] != entry["module"]:
            continue
        if cumulative_pcov_by_instance[instance_name]["size"] != entry["size"]:
            continue
        cumulative_pcov_by_instance[instance_name]["bits"] |= entry["bits"]


def _sum_union_bits(cumulative_pcov_by_instance: dict) -> int:
    return sum(len(entry["bits"]) for entry in cumulative_pcov_by_instance.values())


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

    num_successes = 0
    collected_durations = []
    real_covsum_filepath = os.path.join(
        PATH_TO_TMP,
        f"cascade_verilator_real_covsum_{design_name}_{num_testcases}_{NUM_MAX_BBS_UPPERBOUND}.csv"
    )

    coverage_sequence = []
    cumulative_covered_bins = set()
    cumulative_pcov_by_instance = {}
    collection_start_time = time.time()
    interrupted_by_user = False

    with open(real_covsum_filepath, 'w') as realtime_covsum_file:
        realtime_covsum_file.write("time_seconds,covsum\n")
        realtime_covsum_file.flush()
        os.fsync(realtime_covsum_file.fileno())

        pool = mp.Pool(processes=num_workers)
        try:
            for coverage_path, pcov_memory_path, duration in tqdm(pool.imap_unordered(_measure_coverage_worker_star, descriptors), total=num_testcases):
                is_successful = coverage_path is not None and pcov_memory_path is not None and duration >= 0
                if is_successful:
                    num_successes += 1
                    collected_durations.append(duration)

                    cumulative_covered_bins |= _extract_covered_bins(coverage_path)
                    coverage_sequence.append(len(cumulative_covered_bins))

                    newly_covered_pcov_memory = _extract_pcov_memory_bits_from_dump(pcov_memory_path)
                    _append_pcov_union(cumulative_pcov_by_instance, newly_covered_pcov_memory)

                    if not NO_REMOVE_TMPFILES and os.path.exists(coverage_path):
                        os.remove(coverage_path)
                    if not NO_REMOVE_TMPFILES and os.path.exists(pcov_memory_path):
                        os.remove(pcov_memory_path)

                # Write one point after every finished testcase so Ctrl-C does not lose all progress.
                wall_elapsed_seconds = time.time() - collection_start_time
                realtime_covsum_file.write(f"{wall_elapsed_seconds:.6f},{_sum_union_bits(cumulative_pcov_by_instance)}\n")
                realtime_covsum_file.flush()
                os.fsync(realtime_covsum_file.fileno())
        except KeyboardInterrupt:
            interrupted_by_user = True
            print("Interrupted by user. Saving partial coverage results.")
            pool.terminate()
        else:
            pool.close()
        finally:
            pool.join()

    json_filepath = os.path.join(PATH_TO_TMP, f"cascade_verilator_coverages_{design_name}_{num_testcases}_{NUM_MAX_BBS_UPPERBOUND}.json")
    with open(json_filepath, 'w') as f:
        json.dump({
            'covsum': coverage_sequence,
            'coverage_sequence': coverage_sequence,
            'durations': collected_durations,
            'num_successes': num_successes,
            'num_testcases': num_testcases,
            'real_covsum_filepath': real_covsum_filepath,
            'interrupted_by_user': interrupted_by_user,
        }, f)

    print(f"Saved real PCOV covsum timeline to {real_covsum_filepath}")
    print(f"Saved Cascade Verilator coverage-time data to {json_filepath}")
    return json_filepath
