#!/usr/bin/env bash

YKEDA_AUTOFAN_CONF="/skanICEriver/ykeda_autofan.conf"
TEMP_FILE="/skanICEriver/temp.json"
CLI_OUTPUT="/skanICEriver/log/ykeda_autofan"
MAINTENANCE_SEM_NAME="/tmp/ykeda_autofan_maintenance"
YKEDA_PORT=

# Функції скрипту

check_config() {
  if [ ! -f $YKEDA_AUTOFAN_CONF ]; then
    echo2 "${RED}No config $YKEDA_AUTOFAN_CONF${NOCOLOR}"
  fi
}

check_sem() {
  if [[ -f $MAINTENANCE_SEM_NAME ]]; then
    a=0
    let a=$(date +%s)-$(stat --format='%Y' $MAINTENANCE_SEM_NAME)
    if [[ $a -le 60 ]]; then
      return 1 # coolbox is in maintenance mode
    else
      return 0
    fi
  fi
}

get_file_time_diff(){
  local a=999
  [[ -f $CLI_OUTPUT ]] && let a=$(date +%s)-$(stat --format='%Y' $CLI_OUTPUT)
  echo $a
}

start_listener(){
  stty -F $YKEDA_PORT raw ispeed 9600 ospeed 9600 -ignpar cs8 -cstopb -echo
  sleep 0.2

  screen -dm -c /skanICEriver/screenrc.ykeda_autofan cat $YKEDA_PORT
  for i in {1..25}; do
    sleep 0.2
    local session_count=$(screen -ls ykeda_autofan | grep -c "ykeda_autofan")
    [[ $session_count -gt 0 ]] && break
    [[ $i -ge 25 ]] && echo -e "${RED}screen miner not found in 25 iterations, check logs and maybe flash drive speed${NOCOLOR}"
  done
}

stop_listener(){
  local screens=($(screen -ls ykeda_autofan | grep -Po "\K[0-9]+(?=\.ykeda_autofan)" | sort --unique))
  for pid in "${screens[@]}"; do
    timeout 1 screen -S $pid.ykeda_autofan -X quit
  done
  rm -f $CLI_OUTPUT
}

send_request(){
  check_sem
  [[ $? -ne 0 ]] && return 1 # coolbox is in maintenance mode

  local session_count=$(screen -ls ykeda_autofan | grep -c "ykeda_autofan")
  if [[ $session_count -eq 0 ]]; then
    start_listener
  fi

  if [[ $(get_file_time_diff) -gt 30 ]]; then
    stop_listener
    start_listener
  fi

  echo -n "$1" > $YKEDA_PORT
  sleep 2

  if [[ -f ${CLI_OUTPUT} ]]; then
    local rtn=$(tail -1 ${CLI_OUTPUT})
    echo "$rtn"
  fi
}

get_stats(){
  if [[ -f $YKEDA_AUTOFAN_CONF ]]; then
    source $YKEDA_AUTOFAN_CONF
  fi

  [[ $AUTO_ENABLED -eq 0 ]] && FAN_MODE=1 || FAN_MODE=2

  temp_array=
  mtemp_array=
  if [ -f $TEMP_FILE ]; then
    temp_array=$(jq -c ".temp" < $TEMP_FILE)
    mtemp_array=$(jq -c ".mtemp" < $TEMP_FILE)
  fi
  temp_array=${temp_array//\"}
  mtemp_array=${mtemp_array//\"}

  [[ -z $TARGET_TEMP ]] && TARGET_TEMP=60
  [[ -z $TARGET_MEM_TEMP ]] && TARGET_MEM_TEMP=90
  [[ -z $MANUAL_FAN ]] && MANUAL_FAN=50
  [[ -z $FAN_MODE ]] && FAN_MODE=1
  [[ -z $MIN_FAN ]] && MIN_FAN=30
  [[ -z $MAX_FAN ]] && MAX_FAN=100

  request_str='{"gpu_temp":'$temp_array',"gpu_mtemp":'$mtemp_array',"target_temp":'$TARGET_TEMP',"target_mtemp":'$TARGET_MEM_TEMP',"manual_fan_speed":'$MANUAL_FAN',"fan_mode":'$FAN_MODE',"min_fan":'$MIN_FAN',"max_fan":'$MAX_FAN'}'

  send_request "$request_str"
}

get_json(){
  local answer=$(get_stats)
  if echo "$answer" | jq '.thermosensors' > /dev/null 2>&1; then
    echo "$answer" | jq -c '.'
  fi
}

diagnostic(){
  local answer=$(send_request '{"diagnostic"}')
  if echo "$answer" | jq '.message' > /dev/null 2>&1; then
    echo "$answer"
  fi
}

fan_check(){
  local answer=$(send_request '{"fan_check"}')
  if echo "$answer" | jq '.message' > /dev/null 2>&1; then
    echo "$answer" | jq -r '.message'
  fi
}

msg(){
  local answer=$(send_request '{"message":"'$1'"}')
  if echo "$answer" | jq '.message' > /dev/null 2>&1; then
    echo "$answer" | jq -r '.message'
  fi
}

# Визначення YKEDA_PORT
YKEDA_PORT=/dev/ttyUSB0  # Змініть на правильний порт

if [[ -n $YKEDA_PORT ]]; then
  if [[ $1 == "--get_json" ]]; then
    get_json
  elif [[ $1 == "--diagnostic" ]]; then
    diagnostic
  elif [[ $1 == "--fan_check" ]]; then
    fan_check
  elif [[ $1 == "--msg" ]]; then
    msg "$2"
  else
    echo "Error: wrong command."
    exit 1
  fi
else
  if [[ $1 == "--get_json" ]]; then
    echo "{}"
  else
    echo "No Ykeda Autofan found"
  fi
fi
