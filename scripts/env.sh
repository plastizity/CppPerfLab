# Source from any directory; no global shell configuration is modified.
cppperflab_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="${cppperflab_root}/.local/bin:${PATH}"
export CPPPERFLAB_TOOL_DIR="${cppperflab_root}/vendor/hyperfine-1.19.0"
export CPPPERFLAB_PERF_RECORD_PATH="${cppperflab_root}/vendor/rocky-tools/usr/bin/perf"
if [ -z "${CPPPERFLAB_NATIVE_RESOURCE_PATH:-}" ] && [ -x "${cppperflab_root}/.local/bin/perflab-resource" ]; then
  export CPPPERFLAB_NATIVE_RESOURCE_PATH="${cppperflab_root}/.local/bin/perflab-resource"
fi
export CPPPERFLAB_STORE="${cppperflab_root}/.perflab"
unset cppperflab_root
