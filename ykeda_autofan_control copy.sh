#!/usr/bin/env bash

# Вказуємо шляхи до конфігураційних файлів та необхідних файлів проекту
YKEDA_AUTOFAN_CONF="/skanICEriver/ykeda_autofan.conf"
TEMP_FILE="/skanICEriver/temp.json"
CLI_OUTPUT="/skanICEriver/log/ykeda_autofan"
MAINTENANCE_SEM_NAME="/tmp/ykeda_autofan_maintenance"
YKEDA_PORT=

# Завантажуємо конфігурацію
if [[ -f $YKEDA_AUTOFAN_CONF ]]; then
  source $YKEDA_AUTOFAN_CONF
else
  echo "Конфігураційний файл $YKEDA_AUTOFAN_CONF не знайдено."
  exit 1
fi

# Перевірка наявності jq
if ! command -v jq &> /dev/null; then
    echo "jq не знайдено. Встановіть jq для роботи скрипта."
    exit 1
fi

# Перевірка наявності конфігураційного файлу
check_config() {
  if [ ! -f $YKEDA_AUTOFAN_CONF ]; then
    echo "${RED}No config $YKEDA_AUTOFAN_CONF${NOCOLOR}"
  fi
}

# Перевірка наявності блокування обслуговування
check_sem() {
  if [[ -f $MAINTENANCE_SEM_NAME ]]; then
    local a=0
    let a=`date +%s`-`stat --format='%Y' $MAINTENANCE_SEM_NAME`
    if [[ a -le 60 ]]; then
      return 1 # У режимі обслуговування
    else
      return 0
    fi
  fi
}

# Визначення різниці у часі модифікації файлу
get_file_time_diff(){
  local a=999
  [[ -f $CLI_OUTPUT ]] && let a=`date +%s`-`stat --format='%Y' $CLI_OUTPUT`
  echo $a
}

# Запуск прослуховувача
start_listener(){
  stty -F $YKEDA_PORT raw ispeed 9600 ospeed 9600 -ignpar cs8 -cstopb -echo
  sleep 0.2
  screen -dm -S ykeda_autofan cat $YKEDA_PORT
  for i in {1..25}; do
    sleep 0.2
    local session_count=`screen -ls ykeda_autofan | grep -c "ykeda_autofan"`
    [[ $session_count -gt 0 ]] && break
    [[ $i -ge 25 ]] && echo -e "${RED}screen session not found in 25 iterations, check logs and flash drive speed${NOCOLOR}"
  done
}

# Зупинка прослуховувача
stop_listener(){
  local screens=(`screen -ls ykeda_autofan | grep -Po "\K[0-9]+(?=\.ykeda_autofan)" | sort --unique`)
  for pid in "${screens[@]}"; do
    timeout 1 screen -S $pid.ykeda_autofan -X quit
  done

  rm -f $CLI_OUTPUT
}

# Відправка запиту
send_request(){
  check_sem
  [[ $? -ne 0 ]] && return 1

  local session_count=`screen -ls ykeda_autofan | grep -c "ykeda_autofan"`
  if [[ $session_count -eq 0 ]]; then
    start_listener
  fi

  if [[ `get_file_time_diff` -gt 30 ]]; then
    stop_listener
    start_listener
  fi

  echo -n $1 > $YKEDA_PORT
  sleep 2

  if [[ -f ${CLI_OUTPUT} ]]; then
    local rtn=`tail -1 ${CLI_OUTPUT}`
    echo "$rtn"
  fi
}

# Функція для отримання статистики
get_stats(){
  if [[ -f $YKEDA_AUTOFAN_CONF ]]; then
    source $YKEDA_AUTOFAN_CONF
  fi

  [[ $AUTO_ENABLED -eq 0 ]] && FAN_MODE=1 || FAN_MODE=2

  # Зчитування даних з temp.json
  temp_array=
  mtemp_array=
  if [ -f $TEMP_FILE ]; then
    temp_array=`jq -c ".temp" < $TEMP_FILE`
    mtemp_array=`jq -c ".mtemp" < $TEMP_FILE`
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
  
  send_request $request_str
}

# Функція для отримання JSON
get_json(){
  local answer=`get_stats`
  echo "$answer" | jq -c '.'
}

# Функція діагностики
diagnostic(){
  local answer=`send_request '{"diagnostic"}'`
  echo "$answer" | jq '.message' >> /dev/null 2>&1
  if [[ $? -eq 0 ]] && [[ $answer =~ "message" ]]; then
    echo "$answer"
  fi
}

# Перевірка роботи вентилятора
fan_check(){
  local answer=`send_request '{"fan_check"}'`
  echo "$answer" | jq '.message' >> /dev/null 2>&1
  if [[ $? -eq 0 ]] && [[ $answer =~ "fan check" ]]; then
    echo "$answer" | jq -r '.message'
  fi
}

# Відправка повідомлення
msg(){
  local answer=`send_request '{"message":"'$1'"}'`
  echo "$answer" | jq '.message' >> /dev/null 2>&1
  if [[ $? -eq 0 ]] && [[ $answer =~ "message" ]]; then
    echo "$answer" | jq -r '.message'
  fi
}

# Визначення порту підключення
if [[ `lsusb -v -d 10c4:ea60 | grep iSerial | awk '{print $3}'` == 'ykeda_autofan' ]]; then
  YKEDA_PORT=/dev/ttyUSB0
fi

if [[ `lsusb -v -d 10c4:ea70 | grep iSerial | awk '{print $3}'` == 'ykeda_autofan' ]]; then
  YKEDA_PORT=/dev/ttyUSB1
fi

# Головний блок обробки аргументів
if [[ -n $YKEDA_PORT ]]; then
  case $1 in
    "--get_json")
      get_json
      ;;
    "--diagnostic")
      diagnostic
      ;;
    "--fan_check")
      fan_check
      ;;
    "--msg")
      msg $2
      ;;
    *)
      echo "Error: wrong command."
      exit 1
      ;;
  esac
else
  if [[ $1 == "--get_json" ]]; then
    echo "{}"
  else
    echo "No Ykeda Autofan found"
  fi
fi
