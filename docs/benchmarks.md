# Benchmarks

Everything in the tables below was **measured**; estimates appear only in the
"What this means for the client's server" section and are labelled. Scripts are in
`bench/`, raw results in `bench/results/`, and `bench/report.py` renders the tables.

## Test conditions (read this first)

* **Machine:** development workstation, Intel Core i9-14900K (24 cores / 32 threads, no
  AVX-512), 64 GB RAM, Windows 11, **no GPU**. This is not the client's server (Ryzen 5 9600X,
  6 cores / 12 threads, AVX-512).
* **Emulating the VM:** the server and the LLM process were pinned to 12 logical CPUs (six
  P-cores with Hyper-Threading) and given high CPU priority — the equivalent of a Proxmox VM
  with a fixed vCPU set and a higher `cpuunits` weight.
* **Background load:** during every run the same machine was also running unrelated heavy
  jobs from other work (a 22-process parameter search and several video renders), keeping all
  32 logical CPUs near 100%. This is a harsher version of "existing Proxmox workloads are
  running". The load is reported per run (machine CPU %). A matrix-multiply probe on the
  pinned cores reached roughly a quarter of the CPU's normal throughput, so **absolute
  latencies here are pessimistic**.
* **Audio:** FLEURS (Google, CC-BY-4.0) read speech — real human speakers in each language.
  English demo clips were made with a local TTS voice. Public read speech is cleaner than
  real dictation; milestone 1 repeats these tests on the client's own recordings.
* **Scoring:** word error rate (WER) and character error rate (CER) after lower-casing and
  removing punctuation (and Arabic diacritics / letter variants); chrF for translation.

<!-- TABLES-START -->
<!-- TABLES-END -->
