from flask import Flask, render_template, jsonify, request
import threading
import os
import time
import socket
import json
import signal
import sys
import logging
import webbrowser
import subprocess

app = Flask(__name__)

# Конфіг з сторінки FanControl
#CONFIG_FILE = 'ykeda_autofan.conf'
# Файл, в який будуть записуватися температури
TEMP_FILE = 'temp.json'
# Файл з логом від автофана, швидкість кульків і температура
#log_file_path = 'log/ykeda_autofan'
# Змінна для збереження останніх даних
data_cache = {"casefan": [], "thermosensors": []}


# Налаштування логування
logging.basicConfig(filename='log.txt', level=logging.ERROR, format='%(asctime)s - %(message)s')

# Функція для зчитування останнього рядка з файлу
def read_last_line_from_file(filepath):
    with open(filepath, 'r') as file:
        lines = file.readlines()
        if lines:
            return lines[-1].strip()
    return "{}"  # Повертаємо порожній JSON, якщо файл порожній

# Функція для оновлення кешу даними з файлу
#def update_data_cache():
#    global data_cache
#    while True:
#        try:
#            last_line = read_last_line_from_file(log_file_path)
#            data_cache = json.loads(last_line)
#        except Exception as e:
#            print(f"Error reading or parsing log file: {e}")
#        time.sleep(10)  # Оновлення кожні 10 секунд

# Запуск окремого потоку для оновлення даних у фоновому режимі
#data_updater_thread = threading.Thread(target=update_data_cache)
#data_updater_thread.daemon = True
#data_updater_thread.start()

#def load_config():
#    config = {}
#    with open(CONFIG_FILE, 'r') as file:
#        for line in file:
#            if '=' in line:
#                key, value = line.strip().split('=', 1)
#                config[key] = int(value) if value.isdigit() else value
#    return config

#def update_config(updates):
#    config = load_config()
#    config.update(updates)
#    with open(CONFIG_FILE, 'w') as file:
#        for k, v in config.items():
#            file.write(f"{k}={v}\n")

# Завантаження воркерів з файлу
def load_workers():
    try:
        with open('workers.json', 'r') as file:
            return json.load(file)
    except FileNotFoundError:
        return []

# Збереження воркерів у файл
def save_workers(workers):
    with open('workers.json', 'w') as file:
        json.dump(workers, file, indent=4)

# Ініціалізація воркерів
worker_data = {worker["ip"]: {"name": worker["name"], "info": None, "board": None, "getchipinfo": None, "getpool": None, "boardpow": None, "getnet": None, "fan": None, "status": "offline"} for worker in load_workers()}

stop_event = threading.Event()

def send_tcp_request(ip, port, request, timeout=2.0):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.connect((ip, port))
        s.sendall(request.encode())
        s.settimeout(timeout)
        response = b""
        try:
            while True:
                part = s.recv(1024)
                if not part:
                    break
                response += part
        except socket.timeout:
            pass
        return response.decode()

def query_server(ip, port, requests):
    results = {}
    for req in requests:
        request_data = json.dumps(req) + '\r\n'
        response_data = send_tcp_request(ip, port, request_data)
        results[req['id']] = response_data
    return results

def calculate_and_save_temperatures(worker_data):
    all_temps = {"temp": [], "mtemp": []}
    
    for ip, data in worker_data.items():
        if data["status"] == "online" and "getchipinfo" in data:
            chip_info = data["getchipinfo"]["ret"]["chips"]
            temps = [chip["temp"] for chip in chip_info if chip["temp"] > 0]
            if temps:
                max_temp = max(temps)
                # Округлення до цілого числа
                all_temps["temp"].append(int(round(max_temp)))
                all_temps["mtemp"].append(int(round(max_temp)))
            else:
                all_temps["temp"].append(0)
                all_temps["mtemp"].append(0)
        else:
            all_temps["temp"].append(0)
            all_temps["mtemp"].append(0)

    # Записуємо дані у файл
    try:
        with open(TEMP_FILE, 'w') as temp_file:
            json.dump(all_temps, temp_file)
        #print(f"Updated temperatures: {all_temps}")
    except Exception as e:
        print(f"Error writing to {TEMP_FILE}: {e}")

def update_worker_data(ip, port):
    while not stop_event.is_set():
        requests = [
            {"id": "info"},
            {"id": "board"},
            {"id": "getchipinfo"},
            {"id": "getpool"},
            {"id": "boardpow"},
            {"id": "getnet"},
            {"id": "fan"}
        ]
        try:
            responses = query_server(ip, port, requests)
            
            # Оновлення даних воркера
            for req_id, response in responses.items():
                worker_data[ip][req_id] = json.loads(response)
            
            # Оновлення статусу воркера
            worker_data[ip]["status"] = "online"
            
            # Після оновлення даних воркера, обчислюємо та зберігаємо температури
            calculate_and_save_temperatures(worker_data)

        except Exception as e:
            logging.error(f"Error updating data for worker {ip}: {str(e)}")
            worker_data[ip]["status"] = "offline"
        
        stop_event.wait(10)


def start_data_collection():
    threads = []
    for ip in worker_data.keys():
        t = threading.Thread(target=update_worker_data, args=(ip, 4111))
        t.start()
        threads.append(t)
    return threads

def signal_handler(sig, frame):
    print('Stopping threads...')
    stop_event.set()
    sys.exit(0)
    
@app.route('/apply_fan_settings_to_all', methods=['POST'])
def apply_fan_settings_to_all():
    fan_speed = request.json.get('ratio')
    fan_mode = request.json.get('mode')
    fan_control = request.json.get('control')

    success_ips = []
    failed_ips = []
    errors = []

    for ip in worker_data.keys():
        request_data = json.dumps({
            "id": "sethw",
            "fan": {
                "control": fan_control,  # доданий параметр control
                "ratio": fan_speed
            },
            "mode": fan_mode
        }) + '\r\n'

        try:
            response = send_tcp_request(ip, 4111, request_data)
            response_json = json.loads(response)

            if response_json.get('ret', {}).get('code') == 0:
                success_ips.append(ip)
            else:
                failed_ips.append(ip)
                errors.append(f"Worker {ip} returned error code.")
        except Exception as e:
            failed_ips.append(ip)
            errors.append(f"Worker {ip} failed with error: {str(e)}")

    if failed_ips:
        return jsonify({
            'status': 'error',
            'message': 'Some fan settings were not applied',
            'success_ips': success_ips,
            'failed_ips': failed_ips,
            'errors': errors
        }), 500

    return jsonify({
        'status': 'success',
        'message': 'Fan settings applied to all workers',
        'success_ips': success_ips
    })


# Роут для калібрування кулерів
@app.route('/api/recalibrate', methods=['POST'])
def recalibrate():
    try:
        # Виконання bash-команди
        command = "/bin/bash /skanICEriver/ykeda_autofan_control.sh --fan_check"
        result = subprocess.check_output(command, shell=True).decode('utf-8')
        return result, 200
    except subprocess.CalledProcessError as e:
        return f"Error: {e}", 500

@app.route('/api/fan_data')
def get_fan_data():
    return jsonify(data_cache)

#@app.route('/config')
#def get_config():
#    config = load_config()
#    return jsonify(config)

#@app.route('/update_config', methods=['POST'])
#def update_config_route():
#    updates = {}
#    for key, value in request.form.items():
#        updates[key] = int(value) if value.isdigit() else value
#    update_config(updates)
#    return jsonify({"status": "success", "message": "Configuration updated."})

@app.route('/fan_control')
def fan_control():
    return render_template('fan_control.html')


@app.route('/')
def index():
    return render_template('index.html')

@app.route('/temperatures')
def temperatures():
    return jsonify(worker_data)

@app.route('/worker/<ip>')
def worker_detail(ip):
    return render_template('worker_detail.html', ip=ip)

@app.route('/worker_data/<ip>')
def worker_data_detail(ip):
    return jsonify(worker_data.get(ip, {}))

@app.route('/log')
def log():
    return render_template('log.html')

@app.route('/log_content', methods=['GET'])
def log_content():
    try:
        with open('log.txt', 'r') as file:
            log_data = file.read()
        return jsonify({"log_content": log_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/clear_log', methods=['POST'])
def clear_log():
    try:
        open('log.txt', 'w').close()
        return '', 204
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/worker')
def worker():
    return render_template('worker.html')

@app.route('/workers')
def workers():
    return jsonify([{"ip": ip, "name": data["name"]} for ip, data in worker_data.items()])

@app.route('/add_worker', methods=['POST'])
def add_worker():
    ip = request.form['ip']
    name = request.form['name']
    if not name:
        return jsonify({"error": "Worker name cannot be empty"}), 400
    if ip not in worker_data:
        worker_data[ip] = {"name": name, "info": None, "board": None, "getchipinfo": None, "getpool": None, "boardpow": None, "getnet": None, "fan": None, "status": "offline"}
        workers = load_workers()
        workers.append({"ip": ip, "name": name})
        save_workers(workers)
    return jsonify({"status": "success"})

@app.route('/remove_worker', methods=['POST'])
def remove_worker():
    ip = request.form['ip']
    if ip in worker_data:
        del worker_data[ip]
        workers = load_workers()
        workers = [worker for worker in workers if worker["ip"] != ip]
        save_workers(workers)
    return jsonify({"status": "success"})

def load_flight_sheets():
    try:
        with open('flight_sheets.json', 'r') as file:
            return json.load(file)
    except FileNotFoundError:
        return []

def save_flight_sheets(flight_sheets):
    with open('flight_sheets.json', 'w') as file:
        json.dump(flight_sheets, file, indent=4)
        
@app.route('/about')
def about():
    return render_template('about.html')

@app.route('/flight_sheet')
def flight_sheet():
    return render_template('flight_sheet.html')

@app.route('/flight_sheets')
def get_flight_sheets():
    flight_sheets = load_flight_sheets()
    return jsonify(flight_sheets)

@app.route('/add_flight_sheet', methods=['POST'])
def add_flight_sheet():
    flight_sheet = request.json
    flight_sheets = load_flight_sheets()
    flight_sheets.append(flight_sheet)
    save_flight_sheets(flight_sheets)
    return '', 204

@app.route('/remove_flight_sheet', methods=['POST'])
def remove_flight_sheet():
    name = request.form['name']
    flight_sheets = load_flight_sheets()
    flight_sheets = [sheet for sheet in flight_sheets if sheet['name'] != name]
    save_flight_sheets(flight_sheets)
    return '', 204

@app.route('/flight_sheet/<name>')
def get_flight_sheet(name):
    flight_sheets = load_flight_sheets()
    flight_sheet = next((sheet for sheet in flight_sheets if sheet['name'] == name), None)
    if flight_sheet:
        return jsonify(flight_sheet)
    return jsonify({'error': 'Flight sheet not found'}), 404

@app.route('/apply_flight_sheet', methods=['POST'])
def apply_flight_sheet():
    sheet_name = request.form['sheet_name']
    ip = request.form['ip']
    flight_sheets = load_flight_sheets()
    flight_sheet = next((sheet for sheet in flight_sheets if sheet['name'] == sheet_name), None)

    if not flight_sheet:
        return jsonify({'status': 'error', 'message': 'Flight sheet not found'}), 404

    pools = flight_sheet['pools']
    wallet = pools[3]["wallet"]
    password = pools[4]["password"]
    worker_name = worker_data[ip]['name']
    pools_data = [
        {"no": 1, "addr": pools[0]["pool_addr1"], "user": f"{wallet}.{worker_name}", "pass": password, "suffix": ""},
        {"no": 2, "addr": pools[1]["pool_addr2"], "user": f"{wallet}.{worker_name}", "pass": password, "suffix": ""},
        {"no": 3, "addr": pools[2]["pool_addr3"], "user": f"{wallet}.{worker_name}", "pass": password, "suffix": ""}
    ]

    request_data = json.dumps({"id": "setpool", "pools": pools_data}) + '\r\n'
    try:
        response = send_tcp_request(ip, 4111, request_data)
        response_json = json.loads(response)
        if response_json.get('ret', {}).get('code') == 0:
            return jsonify({'status': 'success', 'message': f'Flight sheet {sheet_name} applied to worker {ip}.', 'response': response})
        else:
            return jsonify({'status': 'error', 'message': f'Worker {ip} returned error code.', 'response': response}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

@app.route('/apply_flight_sheet_to_all', methods=['POST'])
def apply_flight_sheet_to_all():
    sheet_name = request.form['sheet_name']
    flight_sheets = load_flight_sheets()
    flight_sheet = next((sheet for sheet in flight_sheets if sheet['name'] == sheet_name), None)

    if not flight_sheet:
        return jsonify({'status': 'error', 'message': 'Flight sheet not found'}), 404

    pools = flight_sheet['pools']
    wallet = pools[3]["wallet"]
    password = pools[4]["password"]
    success_ips = []
    failed_ips = []
    errors = []

    for ip in worker_data.keys():
        worker_name = worker_data[ip]['name']
        pools_data = [
            {"no": 1, "addr": pools[0]["pool_addr1"], "user": f"{wallet}.{worker_name}", "pass": password, "suffix": ""},
            {"no": 2, "addr": pools[1]["pool_addr2"], "user": f"{wallet}.{worker_name}", "pass": password, "suffix": ""},
            {"no": 3, "addr": pools[2]["pool_addr3"], "user": f"{wallet}.{worker_name}", "pass": password, "suffix": ""}
        ]

        request_data = json.dumps({"id": "setpool", "pools": pools_data}) + '\r\n'
        try:
            response = send_tcp_request(ip, 4111, request_data)
            response_json = json.loads(response)
            if response_json.get('ret', {}).get('code') == 0:
                success_ips.append(ip)
            else:
                failed_ips.append(ip)
                errors.append(f"Worker {ip} returned error code.")
        except Exception as e:
            failed_ips.append(ip)
            errors.append(f"Worker {ip} failed with error: {str(e)}")

    if failed_ips:
        return jsonify({'status': 'error', 'message': 'Some flight sheets were not applied', 'success_ips': success_ips, 'failed_ips': failed_ips, 'errors': errors}), 500

    return jsonify({'status': 'success', 'message': f'Flight sheet {sheet_name} applied to all workers', 'success_ips': success_ips})

@app.route('/restart_worker', methods=['POST'])
def restart_worker():
    ip = request.form['ip']
    request_data = json.dumps({"id": "restart"}) + '\r\n'
    try:
        response = send_tcp_request(ip, 4111, request_data)
        response_json = json.loads(response)
        if response_json.get('ret', {}).get('code') == 0:
            return jsonify({'status': 'success', 'message': f'Worker {ip} is restarting.'})
        else:
            return jsonify({'status': 'error', 'message': f'Worker {ip} returned error code.', 'response': response}), 500
    except Exception as e:
        return jsonify({'status': 'error', 'message': str(e)}), 500

if __name__ == '__main__':
    # Вивід посилання в консоль
    print("Server is running. Access the web interface at http://127.0.0.1:5000")
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Вимкнення логування HTTP-запитів
    logging.getLogger('werkzeug').disabled = True
    
    data_threads = start_data_collection()
    
    app.run(debug=True, threaded=True, host='0.0.0.0')

    for t in data_threads:
        t.join()