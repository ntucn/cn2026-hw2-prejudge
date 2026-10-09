#!/bin/bash
# Run the pre-judge on a submission kept on the host, in a one-shot container (T18).
#
#   ./run-in-docker.sh PATH/TO/your-repository [OUTPUT_DIR]
#
# The submission is mounted read-only and copied inside the container before
# anything is built; results go to OUTPUT_DIR (default ./output, never inside
# the submission). The container has no network and is removed afterwards.
# Set CN_HW2_IMAGE to use another image tag.

IMAGE="${CN_HW2_IMAGE:-cnta/cn-hw2:2026}"
if [ -z "$1" ]; then
  echo "Usage: ./run-in-docker.sh PATH/TO/your-repository [OUTPUT_DIR]" >&2
  exit 1
fi
SCRIPT_PATH=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SUBMISSION=$(cd -- "$1" && pwd -P) || exit 2
OUTPUT_DIR=$(realpath -m -- "${2:-${SCRIPT_PATH}/output}") || exit 2
case "${OUTPUT_DIR}/" in
  "${SUBMISSION}/"*)
    echo "Output directory must be outside the original submission." >&2
    exit 2
    ;;
esac
if ! command -v docker > /dev/null 2>&1; then
  echo "INCOMPLETE: docker was not found; install Docker or run run.sh inside the course container." >&2
  exit 2
fi
mkdir -p -- "$OUTPUT_DIR" || exit 2
exec docker run --rm --init --network none \
  --user "$(id -u):$(id -g)" --env HOME=/tmp \
  --volume "${SUBMISSION}:/mnt/submission:ro" \
  --volume "${SCRIPT_PATH}:/opt/prejudge:ro" \
  --volume "${OUTPUT_DIR}:/mnt/output" \
  "$IMAGE" bash /opt/prejudge/run.sh /mnt/submission /mnt/output
