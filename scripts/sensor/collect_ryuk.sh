#!/usr/bin/env bash

set -u

BASE="${RYUK_BASE_DIR:-$HOME/incident-data/ryuk}"
CONFIG_FILE="${RYUK_SENSOR_CONFIG:-$BASE/sensor_config.env}"

if [ -r "$CONFIG_FILE" ]; then
    # This file contains machine-specific lab endpoints and is not committed.
    # shellcheck disable=SC1090
    . "$CONFIG_FILE"
fi

SRC="${RYUK_SHARE_SOURCE:-/mnt/ryukshare}"
RAW="${RYUK_RAW_DIR:-$BASE/raw}"
NORMALIZED="${RYUK_NORMALIZED_DIR:-$BASE/normalized}"
CURL_CONNECT_TIMEOUT="${RYUK_CURL_CONNECT_TIMEOUT:-5}"
CURL_MAX_TIME="${RYUK_CURL_MAX_TIME:-15}"
SHARE_CHECK_TIMEOUT="${RYUK_SHARE_CHECK_TIMEOUT:-8}"
COPY_TIMEOUT="${RYUK_COPY_TIMEOUT:-15}"
USER01_EVIDENCE_ROOT="${RYUK_USER01_EVIDENCE_ROOT:-$SRC/user01_evidence}"

USER01_EVIDENCE_FILES=(
    "user01_execution_context.json"
    "user01_manifest.json"
    "user01_powershell_events.json"
    "user01_powershell_summary.json"
    "user01_process_collection_status.json"
    "user01_process_events.json"
    "user01_process_summary.json"
    "user01_smb_state.json"
)

if [ -z "${KALI_LOG_URL:-}" ]; then
    echo "[ERROR] KALI_LOG_URL is not set."
    echo "        Add it to $CONFIG_FILE or export it before running this script."
    exit 2
fi

mkdir -p "$RAW"
mkdir -p "$NORMALIZED"

KALI_FETCH_SUCCESS=false
KALI_FETCH_CURL_EXIT=0
KALI_FETCH_HTTP_STATUS="000"
KALI_FETCH_BYTES=0
KALI_FETCH_ATTEMPTED_AT=""
SOURCE_AVAILABLE=false
USER01_RUN_ID=""
USER01_SOURCE_DIR=""
USER01_DEST_DIR=""
USER01_FILES_COPIED=0
USER01_COLLECTION_SUCCESS=false

check_share_source() {
    if timeout "${SHARE_CHECK_TIMEOUT}s" ls -A "$SRC" >/dev/null 2>&1; then
        SOURCE_AVAILABLE=true
        return
    fi
    echo "[WARN] DC01 evidence source is not currently readable: $SRC"
}

copy_if_exists() {
    local file="$1"

    if [ "$SOURCE_AVAILABLE" != true ]; then
        echo "[MISS] Skipped DC01 evidence while source is unavailable: $file"
    elif [ -f "$SRC/$file" ]; then
        if timeout "${COPY_TIMEOUT}s" cp -f "$SRC/$file" "$RAW/$file"; then
            echo "[OK] Copied DC01 evidence: $file"
        else
            echo "[MISS] Timed out copying DC01 evidence: $file"
        fi
    else
        echo "[MISS] DC01 evidence not found: $file"
    fi
}

collect_user01_evidence() {
    local file
    local latest_run

    echo ""
    echo "Collecting USER01 endpoint evidence..."
    if [ "$SOURCE_AVAILABLE" != true ]; then
        echo "[MISS] USER01 evidence skipped while DC01 Share is unavailable"
        return
    fi
    if [ ! -d "$USER01_EVIDENCE_ROOT" ]; then
        echo "[MISS] USER01 evidence root not found: $USER01_EVIDENCE_ROOT"
        return
    fi

    latest_run="$(
        find "$USER01_EVIDENCE_ROOT" \
            -mindepth 1 \
            -maxdepth 1 \
            -type d \
            -printf '%f\n' 2>/dev/null \
        | grep -E '^[0-9]{8}-[0-9]{6}$' \
        | sort \
        | tail -n 1
    )"
    if [ -z "$latest_run" ]; then
        echo "[MISS] No timestamped USER01 evidence run was found"
        return
    fi

    USER01_RUN_ID="$latest_run"
    USER01_SOURCE_DIR="$USER01_EVIDENCE_ROOT/$USER01_RUN_ID"
    USER01_DEST_DIR="$RAW/user01/$USER01_RUN_ID"
    mkdir -p "$USER01_DEST_DIR"

    for file in "${USER01_EVIDENCE_FILES[@]}"; do
        if [ ! -f "$USER01_SOURCE_DIR/$file" ]; then
            echo "[MISS] USER01 evidence not found: $file"
            continue
        fi
        if timeout "${COPY_TIMEOUT}s" \
            cp -f "$USER01_SOURCE_DIR/$file" "$USER01_DEST_DIR/$file"; then
            USER01_FILES_COPIED=$((USER01_FILES_COPIED + 1))
            echo "[OK] Copied USER01 evidence: $file"
        else
            echo "[MISS] Timed out copying USER01 evidence: $file"
        fi
    done

    if [ "$USER01_FILES_COPIED" -eq "${#USER01_EVIDENCE_FILES[@]}" ]; then
        USER01_COLLECTION_SUCCESS=true
        echo "[OK] USER01 evidence run complete: $USER01_RUN_ID"
    else
        echo "[WARN] USER01 evidence run is incomplete: "
        echo "       copied=$USER01_FILES_COPIED expected=${#USER01_EVIDENCE_FILES[@]}"
    fi
}

fetch_kali_log() {
    local temp_path

    echo ""
    echo "Fetching Kali HTTP access log..."
    KALI_FETCH_ATTEMPTED_AT="$(date --iso-8601=seconds)"
    temp_path="$(mktemp "$RAW/.kali_http_access.log.XXXXXX")"

    if KALI_FETCH_HTTP_STATUS="$(
        curl -sS \
            --connect-timeout "$CURL_CONNECT_TIMEOUT" \
            --max-time "$CURL_MAX_TIME" \
            -o "$temp_path" \
            -w "%{http_code}" \
            "$KALI_LOG_URL"
    )"; then
        KALI_FETCH_CURL_EXIT=0
    else
        KALI_FETCH_CURL_EXIT=$?
    fi

    if [ "$KALI_FETCH_CURL_EXIT" -eq 0 ] \
        && [ "$KALI_FETCH_HTTP_STATUS" = "200" ] \
        && [ -s "$temp_path" ]; then
        mv -f "$temp_path" "$RAW/kali_http_access.log"
        KALI_FETCH_SUCCESS=true
        KALI_FETCH_BYTES="$(stat -c '%s' "$RAW/kali_http_access.log")"
        echo "[OK] Fetched Kali HTTP log ($KALI_FETCH_BYTES bytes)"
    else
        rm -f "$temp_path"
        echo "[MISS] Could not fetch a non-empty Kali HTTP log"
        echo "       curl_exit=$KALI_FETCH_CURL_EXIT http_status=$KALI_FETCH_HTTP_STATUS"
    fi
}

write_collection_metadata() {
    local dc01_share
    local metadata_temp

    dc01_share="$(findmnt -n -o SOURCE --target "$SRC" 2>/dev/null || true)"
    metadata_temp="$(mktemp "$RAW/.collection_metadata.json.XXXXXX")"

    cat > "$metadata_temp" <<EOF
{
  "collector_host": "SENSOR01",
  "scenario": "Ryuk-like safe incident simulation",
  "collection_schema_version": "1.1",
  "collection_time": "$(date --iso-8601=seconds)",
  "dc01_share": "$dc01_share",
  "dc01_share_available": $SOURCE_AVAILABLE,
  "kali_http_log_url": "$KALI_LOG_URL",
  "raw_output_folder": "$RAW",
  "kali_http_log_fetch": {
    "success": $KALI_FETCH_SUCCESS,
    "curl_exit_code": $KALI_FETCH_CURL_EXIT,
    "http_status": "$KALI_FETCH_HTTP_STATUS",
    "bytes": $KALI_FETCH_BYTES,
    "attempted_at": "$KALI_FETCH_ATTEMPTED_AT"
  },
  "user01_evidence": {
    "success": $USER01_COLLECTION_SUCCESS,
    "run_id": "$USER01_RUN_ID",
    "source_folder": "$USER01_SOURCE_DIR",
    "raw_folder": "$USER01_DEST_DIR",
    "files_expected": ${#USER01_EVIDENCE_FILES[@]},
    "files_copied": $USER01_FILES_COPIED
  }
}
EOF

    mv -f "$metadata_temp" "$RAW/collection_metadata.json"
}

echo "=== SENSOR01 Ryuk Evidence Collection ==="
echo "Time: $(date --iso-8601=seconds)"
echo ""

check_share_source
copy_if_exists "detection_stage2_user_v4.json"
copy_if_exists "stage4_impact_summary.json"
copy_if_exists "stage4_user_execution_impact_log.txt"
copy_if_exists "stage4_discovery_log.txt"
copy_if_exists "stage4_credential_exposure_simulation_log.txt"
copy_if_exists "stage4_scheduled_task_deployment_simulation_log.txt"
copy_if_exists "stage4_service_recovery_dryrun_log.txt"
copy_if_exists "restore_version_d_result.json"

collect_user01_evidence
fetch_kali_log
write_collection_metadata

echo ""
echo "Collection completed."
echo "Raw evidence folder: $RAW"
