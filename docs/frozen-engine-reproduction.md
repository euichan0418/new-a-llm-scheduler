# Frozen C source reproduction

The historical server is the **C** artifact of the original step10 campaign,
not the newer teammate A/R/P engine. The recorded source revision is
`c8b9d61d12b4997d902a03e5dc55a185d18c3bc9` in the public
[codinghuman0/llama-scheduler-base](https://github.com/codinghuman0/llama-scheduler-base/commit/c8b9d61d12b4997d902a03e5dc55a185d18c3bc9).
Its Git tree is `e38bc2f0817d25b74f3b4053a0269925647ca835`.

The source commit already includes explicit compute profiles, legacy server
mode, semantic `llama_scx_phase_v1` and gated diagnostics. No numerical-engine
patch is applied by default. Research changes are preserved in that commit's
history (including c2c398510, 2ec582958, 1bb4bd7d2, e5b385887 and c8b9d61d1).
The provisioner refuses unknown patch contracts instead of skipping them.

The exact historical CMake argument list, with private source/build paths
removed, is in `config/autotune-provision.json`. It enables Release, shared
libraries, CPU/native/OpenMP, CPU repack/llamafile, phase HINT; disables TRACE,
GPU/BLAS/RPC, LTO, UI downloads, tests/examples, and external system GGML.
Build target is `llama-server`, with one compile job. Native ISA compilation is
bound to the current Intel CPU and compiler identity; binaries are not portable
artifacts and are not expected to have the old i5 hash on another PC.

Outputs must include llama-server plus libggml-base, libggml-cpu, libggml,
libllama-common, libllama-server-impl, libllama, and libmtmd. ELF loader resolution
must select project DSOs from the same build; libgomp must be linked. Exported
phase symbol and CLI options --threads/--threads-batch/--threads-http and
--server-compute-profile-mode are checked. These are provisioning checks;
actual OpenMP teams, model loading, response tokens/hash and cleanup require
fresh Linux Audit/Clean sessions and are never inferred from a successful link.

Build reuse validates recipe/source tree, CPU binding, compiler versions,
CMake options and all output/cache/compile-command hashes. Source changes or
incomplete builds are preserved and rejected. Original Frozen files, GGML and
models are never modified. The new build has independent provenance and a new
runtime baseline. No performance equivalence or safety approval is implied.

The source repository is MIT licensed. Existing source notices remain in the
checkout. The scheduler/launcher distribution retains its existing license
notices and does not embed or redistribute model weights or engine binaries.
