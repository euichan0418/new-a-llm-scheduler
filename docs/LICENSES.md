# License and provenance inventory

The top-level LICENSE is copied verbatim from the original `tools/sched_ext/LICENSE` (GNU GPL version 2). Existing `SPDX-License-Identifier: GPL-2.0` declarations and upstream copyright notices are retained. This release does not replace them with an MIT license.

| Component | Existing license basis | Release treatment |
|---|---|---|
| NEW_A BPF, userspace loader and selected-child helper | GPL-2.0 source notices and sched_ext directory LICENSE | Source/copyright notices retained |
| Linux scx_simple-derived lifecycle and scheduler code | Original notices identify Linux v6.17 scx_simple and Meta/Tejun Heo/David Vernet copyrights | Notices retained in source; no relicensing |
| Python/C topology and research support | Existing GPL-2.0 notices and sched_ext directory LICENSE | Original source or documented build-support relocation |
| topology detection shell helper | Existing sched_ext directory LICENSE; no per-file SPDX in original | Directory license preserved; not silently assigned a different license |
| `ggml/include/ggml.h` | Upstream llama.cpp/GGML MIT LICENSE | Header retained; MIT text preserved at LICENSES/UPSTREAM-MIT.txt |
| libbpf, libelf, zlib, Linux UAPI and GCC/system headers | External system dependencies under their own terms | Not vendored in this release |
| Frozen engine and model weights | Separate external inputs | Not redistributed; no license assertion for user-supplied models |

LICENSES/GPL-2.0.txt duplicates the original GPL text for inventory purposes. PUBLICATION_PROVENANCE.json identifies copied sources and packaging-only adaptations. PUBLICATION_MANIFEST.json records the reviewed public file hashes. No private credentials or model binaries are part of the package.

Preserved upstream copyright names/emails are required attribution, not accidentally included local account configuration. The Git commit uses the authenticated account's GitHub noreply identity instead of a private email address.

## Research update additions

Runtime Lite BPF, derivation, diagnostics and original tests retain GPL-2.0 notices. The BPF source and candidate.diff are unchanged research copies; packaging-only build/import/plan adaptations and the original ELF inventory extraction are recorded in PUBLICATION_PROVENANCE.json. No additional third-party implementation, binary, model or system library is vendored. Existing E policy source, LICENSE and upstream attribution are preserved. The sanitized measurement tables contain numeric research results, not raw model outputs or user/system identities.
