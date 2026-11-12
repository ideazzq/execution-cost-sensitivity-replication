# Execution-Cost Sensitivity in Offline Prescriptive Process Monitoring

Replication package and deterministic logical-calendar reference for:

> **Execution-Cost Sensitivity in Offline Prescriptive Process Monitoring**  
> Zhiyu Wang, Zhiqi Zhang, and Chen Yan*  
> Taiyuan University of Technology, China

## Repository Layout

- `checks/`
  - `contract_example.py`: Executes all 8 unit contract fixtures (`ContractFixtures`), checks `CostlyRow` reference invariance, and verifies the constructed fast vs. slow host observation example.
  - `contract_example_result.json`: Recorded deterministic output from `contract_example.py`.
- `legacy/`
  - `reference_replay.py`: Standalone Python standard-library fixed logical-calendar discrete-event replay implementation.
  - `reference_fixtures.py`: Deterministic unit tests covering simultaneous release/arrival ties, rejection triggers, queue-update ties, EOF cutoff, treated-case handling, and conformal filtering.
  - `fetch_sources.py` & `source_manifest.json`: Retrieves and SHA-256/Git-blob verifies pinned upstream files from [`mshoush/conformal-prescriptive-monitoring`](https://github.com/mshoush/conformal-prescriptive-monitoring/tree/73dd48e3043ab49ea22a4cfade4379619b20def0) (commit `73dd48e`) and [`KiriBu10/MuProMAC`](https://github.com/KiriBu10/MuProMAC/tree/38a46e29182a2651c3ab44f0718adc2892d3114a) (commit `38a46e2`).
  - `build_variants.py` & `patches/`: Reproduces variants **H** (original rejected-row history retained) and **O** (unused rejected-row history block removed) from hash-verified upstream source (`compatibility.patch` and `history_removal.patch`).
  - `run_original.py`: Invokes either H or O under isolated environment flags.
  - `verify_reported_runs.py`: Verifies all 308 reported JSON/CSV run outputs archived in `data/reported_outputs.zip` against frozen public prediction streams and checks 92 fixed-calendar outputs across 46 reference conditions.
  - `summarize_runs.py`: Rebuilds stratified source-pair, policy-comparison, and group summaries from `data/normalized_runs.csv`.
  - `data/` & `reproduced_tables/`: Normalized metrics, provenance SHA-256 index, archived 308-run outputs (`data/reported_outputs.zip`), MuProMAC logical-clock control verification (`data/mupromac_control_summary.json`), and summarized CSV tables.
- `matched_study/`
  - Aggregated CSV/JSON tables (`run_metrics.csv`, `source_pairs.csv`, `observation_pairs.csv`, `policy_comparisons.csv`, `group_summary.csv`, `normalized_runs.json`, `independent_output_audit.json`) for the 160-run class-balanced matched experiment (`BPI2012` at `alpha=0.4` and `BPI2017` at `alpha=0.2`, `R=25`, `T=30 s`, 10 repetitions).
  - `runner/`: Instrumentation and variant preparation scripts (`prepare.py`, `worker.py`, `process_guard.py`, `source_manifest.json`). Raw multi-megabyte per-run `trace.json` logs are omitted from this lightweight repository to keep cloning fast.

## Quick Verification (Standard Library Only, Offline)

No external dependencies or network access are required to verify the timing contract and summary tables:

```powershell
# 1. Run deterministic contract fixtures and constructed timing counterexample
python checks/contract_example.py

# 2. Run the standalone unit test suite directly
python legacy/reference_fixtures.py

# 3. Rebuild the 308-run summary tables from bundled normalized metrics
cd legacy
python summarize_runs.py --runs data/normalized_runs.csv --output recomputed_tables
```

On Linux/macOS, substitute `python3` if needed.

## Full Source Variant Rebuild & Archived Run Verification

To re-fetch the pinned public upstream streams, rebuild variants **H** and **O**, and verify all 308 archived runs against public prediction inputs:

```powershell
python -m venv .venv
# Windows: .\.venv\Scripts\python.exe   |   Linux/macOS: ./.venv/bin/python
.\.venv\Scripts\python.exe -m pip install -r legacy/requirements-replay.txt

cd legacy
..\.venv\Scripts\python.exe fetch_sources.py --group prpm
..\.venv\Scripts\python.exe build_variants.py
..\.venv\Scripts\python.exe verify_reported_runs.py
```

To execute the fixed logical-calendar contract or live host-time source variants directly:

```powershell
# Deterministic fixed-calendar reference replay (std library only)
python reference_replay.py --csv upstream/prpm/data/bpic2017_adaptive.csv --policy CP --resources 5 --duration 30 --calendar H120 --output reproduced/ref_bpic2017_CP_R5_H120.json

# Direct source execution (H vs. O under live host timing)
..\.venv\Scripts\python.exe run_original.py --csv upstream/prpm/data/bpic2017_adaptive.csv --dataset bpic2017 --calibration adaptive --policy CP --variant history --resources 5 --duration 30 --output reproduced/history_bpic2017_CP_R5
..\.venv\Scripts\python.exe run_original.py --csv upstream/prpm/data/bpic2017_adaptive.csv --dataset bpic2017 --calibration adaptive --policy CP --variant removal --resources 5 --duration 30 --output reproduced/removal_bpic2017_CP_R5
```

To prepare the 160-run matched study runner variants (`history_off.py`, `history_on.py`, `removal_off.py`, `removal_on.py`):

```powershell
cd matched_study/runner
..\..\.venv\Scripts\python.exe prepare.py --upstream ../../legacy/upstream
```

## Notes

- Wall-clock timings depend on host execution speed; live `run_original.py` runs will reflect the local machine's loop speed, whereas `reference_replay.py`, `reference_fixtures.py`, and `checks/contract_example.py` are deterministic.
- Public datasets (`BPI2012`, `BPI2017`) and upstream code remain subject to their respective original licenses and DOIs cited in the manuscript.
