#!/bin/bash

# Regular Colors
BLACK='\033[0;30m'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[0;37m'
# Reset
COLOROFF='\033[0m'

# Stage budgets in seconds (T16). Each check's limit is on its runCheck line
# and covers that script's own request/command deadlines.
STRUCTURE_LIMIT=30
COPY_LIMIT=120
CLEAN_LIMIT=60
BUILD_LIMIT=120
HELPER_LIMIT=30
SERVER_READY_LIMIT=10
PSEUDO_READY_LIMIT=30
NPM_LIMIT=120
KILL_GRACE=10
STOP_LIMIT=5

# T17: services start in their own session; only these groups are stopped,
# never other server/node/python programs on the student's machine.
OWNED_GROUPS=()
CURRENT_CHECK=''
function stopGroups () {
  local pid
  for pid in "$@"; do kill -TERM -- "-$pid" 2> /dev/null; done
  local deadline=$((SECONDS + STOP_LIMIT))
  for pid in "$@"; do
    while kill -0 -- "-$pid" 2> /dev/null && [ "$SECONDS" -lt "$deadline" ]; do sleep 0.1; done
    kill -KILL -- "-$pid" 2> /dev/null
  done
  return 0
}

function checkResult (){
  if [ $2 -eq 0 ]; then
    echo -e "${GREEN}${1}:   pass${COLOROFF}"
  elif [ $2 -eq 2 ]; then
    echo -e "${YELLOW}${1}: incomplete (judge preparation, startup or timeout)${COLOROFF}"
  else
    echo -e "${RED}${1}: failed${3:+ ($3)}${COLOROFF}"
  fi
}


function runCheck () {
  local limit=$1
  shift
  # In the background so an interrupt reaches the trap at once; timeout leads
  # its own process group, which cleanup can stop.
  timeout -k "$KILL_GRACE" "$limit" python "$@" &
  CURRENT_CHECK=$!
  wait "$CURRENT_CHECK"
  local status=$?
  CURRENT_CHECK=''
  if [ "$status" -gt 2 ]; then
    echo "INCOMPLETE: $1 exited with status $status" >&2
    return 2
  fi
  return "$status"
}

# pass/needs_review/compile_failed/incomplete as an exit-style code.
function buildCode () {
  case "$1" in
    pass) return 0 ;;
    needs_review|compile_failed) return 1 ;;
    *) return 2 ;;
  esac
}

function testable () {
  [ "$1" = pass ] || [ "$1" = needs_review ]
}

if [ -z "$1" ]; then
    echo "You need to pass the path to 'repository' (not 'hw2') folder to start the script."
    exit 1
fi

SCRIPT_PATH=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
SUBMISSION_PATH=$(cd -- "$1" && pwd -P) || exit 2
OUTPUT_DIR="${2:-${SCRIPT_PATH}/output}"
if [[ "$OUTPUT_DIR" != /* ]]; then OUTPUT_DIR="$(pwd)/${OUTPUT_DIR}"; fi
OUTPUT_DIR=$(realpath -m -- "$OUTPUT_DIR") || exit 2
# A default output beneath the submission would change the original tree.
case "${OUTPUT_DIR}/" in
  "${SUBMISSION_PATH}/"*)
    if [ -n "$2" ]; then
      echo "Output directory must be outside the original submission." >&2
      exit 2
    fi
    OUTPUT_DIR=$(mktemp -d /tmp/cn-hw2-prejudge-results.XXXXXX) || exit 2
    ;;
esac
mkdir -p -- "$OUTPUT_DIR"
WORK_ROOT=$(mktemp -d /tmp/cn-hw2-prejudge.XXXXXX) || exit 2
function cleanupRun () {
  trap - EXIT INT TERM
  if [ -n "$CURRENT_CHECK" ]; then stopGroups "$CURRENT_CHECK"; fi
  if [ "${#OWNED_GROUPS[@]}" -gt 0 ]; then stopGroups "${OWNED_GROUPS[@]}"; fi
  rm -rf -- "$WORK_ROOT"
}
trap cleanupRun EXIT
trap 'echo "INCOMPLETE: pre-judge interrupted" >&2; exit 130' INT TERM
TEST_PATH="${WORK_ROOT}/tests"
mkdir -p -- "$TEST_PATH"
/bin/cp -a "${SCRIPT_PATH}/scripts/." "$TEST_PATH/" || exit 2
cd -- "$TEST_PATH" || exit 2
READY_HELPER="${TEST_PATH}/assets/service_readiness.py"
STRUCTURE_JSON="${OUTPUT_DIR}/structure.json"
STRUCTURE_LOG="${OUTPUT_DIR}/structure.log"
SC_PREP_RET=0
rm -f -- "$STRUCTURE_JSON" "$STRUCTURE_LOG" || SC_PREP_RET=2
echo -e "${YELLOW}-----structure-check-----${COLOROFF}"
runCheck "$STRUCTURE_LIMIT" structure-checker.py "$SUBMISSION_PATH" --json-output "$STRUCTURE_JSON" > "$STRUCTURE_LOG" 2>&1
SC_RET=$?
if [ "$SC_PREP_RET" -ne 0 ] || [ "$SC_RET" -gt 2 ] || [ ! -f "$STRUCTURE_JSON" ]; then SC_RET=2; fi
if [ -f "$STRUCTURE_LOG" ]; then cat -- "$STRUCTURE_LOG"; fi
echo "Structure evidence: $OUTPUT_DIR"

# Every destructive or generated operation below uses an owned work copy.
REPO_PATH="${WORK_ROOT}/submission"
mkdir -p -- "${REPO_PATH}/hw2"
PREP_RET=0
if [ -d "${SUBMISSION_PATH}/hw2" ]; then
  timeout -k 5 "$COPY_LIMIT" /bin/cp -aL "${SUBMISSION_PATH}/hw2/." "${REPO_PATH}/hw2/" || PREP_RET=2
fi
chmod -R u+rwX -- "${REPO_PATH}/hw2" || PREP_RET=2
if [ "$PREP_RET" -ne 0 ]; then echo "INCOMPLETE: could not prepare submission copy." >&2; fi

echo -e "${YELLOW}-----startup-----${COLOROFF}"
cd -- "${REPO_PATH}/hw2"
dos2unix web/*
timeout -k 5 "$CLEAN_LIMIT" make clean
rm -f -- server client

echo "done."
echo -e "${YELLOW}-----build-----${COLOROFF}"
cd -- "${REPO_PATH}/hw2"
MISSING_TOOLS=''
for tool in make gcc g++; do
  command -v "$tool" > /dev/null 2>&1 || MISSING_TOOLS="${MISSING_TOOLS} ${tool}"
done

DEFAULT_MAKE_STATUS=''
# Builds one target at a time and sets BUILD_RESULT.
function buildTarget () {
  local target=$1
  local log="${OUTPUT_DIR}/make-${target}.log"
  local status
  if [ "$PREP_RET" -ne 0 ]; then
    BUILD_RESULT=incomplete
    echo "INCOMPLETE: the submission copy was not prepared" | tee "$log"
    return
  elif [ -n "$MISSING_TOOLS" ]; then
    BUILD_RESULT=incomplete
    echo "INCOMPLETE: build tools not found:${MISSING_TOOLS}" | tee "$log"
    return
  fi
  timeout -k 5 "$BUILD_LIMIT" make "$target" > "$log" 2>&1
  status=$?
  # FAQ: the makefile may change as long as plain `make` builds both programs.
  if [ "$status" -ne 0 ] && [ ! -x "$target" ] && grep -qF "No rule to make target '${target}'." "$log"; then
    if [ -z "$DEFAULT_MAKE_STATUS" ]; then
      printf 'NOTE: no %s target; running plain make\n' "$target" >> "$log"
      timeout -k 5 "$BUILD_LIMIT" make >> "$log" 2>&1
      DEFAULT_MAKE_STATUS=$?
    else
      printf 'NOTE: no %s target; plain make already ran (exit %s)\n' "$target" "$DEFAULT_MAKE_STATUS" >> "$log"
    fi
    status=$DEFAULT_MAKE_STATUS
  fi
  cat -- "$log"
  if [ "$status" -ge 124 ]; then
    # make itself exits 0-2; larger codes come from timeout or a signal.
    BUILD_RESULT=incomplete
    echo "INCOMPLETE: make ${target} did not finish (exit ${status}, limit ${BUILD_LIMIT} s)" | tee -a "$log"
  elif [ -f "$target" ] && [ -x "$target" ]; then
    BUILD_RESULT=pass
    if [ "$status" -ne 0 ]; then
      BUILD_RESULT=needs_review
      echo "NEEDS REVIEW: make exited ${status} but produced ./${target}; testing it" | tee -a "$log"
    fi
  else
    BUILD_RESULT=compile_failed
    echo "COMPILE FAILED: make ${target} exited ${status} without producing ./${target}" | tee -a "$log"
  fi
}
buildTarget server
SERVER_BUILD=$BUILD_RESULT
buildTarget client
CLIENT_BUILD=$BUILD_RESULT
echo "Build logs: $OUTPUT_DIR"

# Starts "$@" in the background and waits, without connecting, until it
# listens on PORT. Sets START_RET (0 started or student-side failure, 2 judge
# side) and START_RUN (1 when the check may run).
function startService () {
  local label=$1
  local port=$2
  local limit=$3
  local owner=$4
  shift 4
  START_RET=0
  START_RUN=1
  STARTED_PID=''
  if timeout -k 5 "$HELPER_LIMIT" python "$READY_HELPER" free "$port" > /dev/null; [ "$?" -eq 1 ]; then
    echo "INCOMPLETE: port ${port} is already in use; stop other servers and run again." >&2
    START_RET=2
    START_RUN=0
    return
  fi
  setsid "$@" &
  local pid=$!
  STARTED_PID=$pid
  OWNED_GROUPS+=("$pid")
  local started=$SECONDS
  local rows
  rows=$(timeout -k 5 $((limit + HELPER_LIMIT)) python "$READY_HELPER" wait \
    --deadline "$limit" --json "${OUTPUT_DIR}/startup-${label}.json" "${label}:${port}:${pid}")
  local state detail
  IFS=$'\t' read -r _ state detail <<< "$rows"
  case "$state" in
    ready) ;;
    exited|not_listening)
      echo "${label}: ${detail}" >&2
      if [ "$owner" = judge ]; then START_RET=2; START_RUN=0; fi
      ;;
    foreign_listener)
      echo "INCOMPLETE: ${label}: ${detail}" >&2
      START_RET=2
      START_RUN=0
      ;;
    *)
      # Keep the old fixed wait, but the startup was not verified.
      echo "INCOMPLETE: ${label}: readiness could not be observed (Linux /proc is required)" >&2
      local left=$((limit - (SECONDS - started)))
      if [ "$left" -gt 0 ]; then sleep "$left"; fi
      START_RET=2
      ;;
  esac
}

echo "done."
echo -e "${YELLOW}-----server-test-----${COLOROFF}"
SVR_NOTE=''
if testable "$SERVER_BUILD"; then
  /bin/cp -f "${TEST_PATH}/assets/secret" "${REPO_PATH}/hw2"
  startService server-8080 8080 "$SERVER_READY_LIMIT" student \
    bash -c 'cd -- "$1" && exec ./server 8080 > /dev/null 2>&1' _ "${REPO_PATH}/hw2"
  SVR0_START=$START_RET
  SVR0_RET=2
  if [ "$START_RUN" -eq 1 ]; then
    cd -- "${TEST_PATH}" && runCheck 110 server-0.py 8080 "${REPO_PATH}"
    SVR0_RET=$?
  fi
  if [ -n "$STARTED_PID" ]; then stopGroups "$STARTED_PID"; fi
  startService server-7777 7777 "$SERVER_READY_LIMIT" student \
    bash -c 'cd -- "$1" && exec ./server 7777 > /dev/null 2>&1' _ "${REPO_PATH}/hw2"
  SVR1_START=$START_RET
  SVR1_RET=2
  if [ "$START_RUN" -eq 1 ]; then
    cd -- "${TEST_PATH}" && runCheck 300 server-1.py 7777 "${REPO_PATH}"
    SVR1_RET=$?
  fi
  if [ -n "$STARTED_PID" ]; then stopGroups "$STARTED_PID"; fi
  rm -f -- "${REPO_PATH}/hw2/secret"
else
  buildCode "$SERVER_BUILD"
  SVR0_RET=$?
  SVR1_RET=$SVR0_RET
  SVR0_START=0
  SVR1_START=0
  SVR_NOTE="not run: server ${SERVER_BUILD}"
fi

echo -e "${YELLOW}-----client-test-----${COLOROFF}"
CLI_NOTE=''
if testable "$CLIENT_BUILD"; then
  cd -- "${TEST_PATH}/assets/pseudo-server"
  # The course image preinstalls these modules (NODE_PATH); otherwise install once.
  if node -e "require('express'); require('multer'); require('express-basic-auth')" \
      > "${OUTPUT_DIR}/pseudo-server-npm.log" 2>&1; then
    NPM_STATUS=0
  else
    timeout -k 5 "$NPM_LIMIT" npm ci >> "${OUTPUT_DIR}/pseudo-server-npm.log" 2>&1
    NPM_STATUS=$?
  fi
  CLI0_RET=2
  CLI1_RET=2
  NODE_START=2
  if [ "$NPM_STATUS" -ne 0 ]; then
    echo "INCOMPLETE: pseudo-server npm ci failed (exit ${NPM_STATUS}); see ${OUTPUT_DIR}/pseudo-server-npm.log" >&2
  else
    echo "Wait for server to wake up..."
    startService pseudo-server-4500 4500 "$PSEUDO_READY_LIMIT" judge \
      bash -c 'cd -- "$1" && exec node app.js > /dev/null 2>&1' _ "${TEST_PATH}/assets/pseudo-server"
    NODE_START=$START_RET
    if [ "$START_RUN" -eq 1 ]; then
      cd -- "${TEST_PATH}" && runCheck 120 client-0.py 4500 "${REPO_PATH}" nodejs
      CLI0_RET=$?
      cd -- "${TEST_PATH}" && runCheck 250 client-1.py 4500 "${REPO_PATH}" nodejs
      CLI1_RET=$?
    fi
    if [ -n "$STARTED_PID" ]; then stopGroups "$STARTED_PID"; fi
  fi

  echo -e "${YELLOW}-----client-test-with-odd-server-----${COLOROFF}"
  echo "Wait for server to wake up..."
  startService pseudo-server-2024 2024 "$PSEUDO_READY_LIMIT" judge \
    bash -c 'cd -- "$1" && exec python web.py > /dev/null 2>&1' _ "${TEST_PATH}/assets/pseudo-server"
  FLASK_START=$START_RET
  CLI2_RET=2
  if [ "$START_RUN" -eq 1 ]; then
    cd -- "${TEST_PATH}" && runCheck 120 client-0.py 2024 "${REPO_PATH}" flask
    CLI2_RET=$?
  fi
  if [ -n "$STARTED_PID" ]; then stopGroups "$STARTED_PID"; fi
else
  buildCode "$CLIENT_BUILD"
  CLI0_RET=$?
  CLI1_RET=$CLI0_RET
  CLI2_RET=$CLI0_RET
  NODE_START=0
  FLASK_START=0
  CLI_NOTE="not run: client ${CLIENT_BUILD}"
fi

echo -e "${YELLOW}-----clean-up-----${COLOROFF}"
if [ "${#OWNED_GROUPS[@]}" -gt 0 ]; then stopGroups "${OWNED_GROUPS[@]}"; fi
echo "done."
echo -e "${YELLOW}-----summary-----${COLOROFF}"

if [ "$SC_RET" -eq 1 ]; then
  echo -e "${YELLOW}structure-check: needs_review (manual decision; automatic deduction: 0)${COLOROFF}"
else
  checkResult "structure-check                          " "$SC_RET"
fi
for target in server client; do
  if [ "$target" = server ]; then state=$SERVER_BUILD; else state=$CLIENT_BUILD; fi
  if [ "$state" = needs_review ]; then
    echo -e "${YELLOW}build                       (${target}): needs_review (make failed but produced ./${target})${COLOROFF}"
  else
    buildCode "$state"
    checkResult "build                       (${target})" $? "compile failed"
  fi
done
checkResult "server-test                 (server-0.py)" ${SVR0_RET} "$SVR_NOTE"
checkResult "server-test                 (server-1.py)" ${SVR1_RET} "$SVR_NOTE"
checkResult "client-test                 (client-0.py)" ${CLI0_RET} "$CLI_NOTE"
checkResult "client-test                 (client-1.py)" ${CLI1_RET} "$CLI_NOTE"
checkResult "client-test-with-odd-server (client-0.py)" ${CLI2_RET} "$CLI_NOTE"

# Report failure only after every script and cleanup have been attempted.
buildCode "$SERVER_BUILD"
SERVER_BUILD_RET=$?
buildCode "$CLIENT_BUILD"
CLIENT_BUILD_RET=$?
FINAL_RET=0
for status in "$PREP_RET" "$SC_RET" "$SERVER_BUILD_RET" "$CLIENT_BUILD_RET" \
    "$SVR0_START" "$SVR1_START" "$NODE_START" "$FLASK_START" \
    "$SVR0_RET" "$SVR1_RET" "$CLI0_RET" "$CLI1_RET" "$CLI2_RET"; do
  if [ "$status" -eq 2 ]; then
    FINAL_RET=2
  elif [ "$status" -ne 0 ] && [ "$FINAL_RET" -eq 0 ]; then
    FINAL_RET=1
  fi
done
exit "$FINAL_RET"
