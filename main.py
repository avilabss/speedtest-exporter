import os
import json
import time
import logging
import subprocess

from flask import Flask
from prometheus_client import make_wsgi_app, Gauge
from waitress import serve
from shutil import which


# Create Flask app
app = Flask("speedtest-exporter")

# Setup logging
logging.basicConfig(
    encoding="utf-8",
    format="%(levelname)s:     %(message)s",
)

log = logging.getLogger(__name__)
log.setLevel(logging.DEBUG)

# Create Metrics
server = Gauge("speedtest_server_id", "Speedtest server ID used to test")
jitter = Gauge("speedtest_jitter_latency_milliseconds", "Speedtest current Jitter in ms")
ping = Gauge("speedtest_ping_latency_milliseconds", "Speedtest current Ping in ms")
download_speed = Gauge("speedtest_download_bits_per_second", "Speedtest current Download Speed in bit/s")
upload_speed = Gauge("speedtest_upload_bits_per_second", "Speedtest current Upload speed in bits/s")
status = Gauge("speedtest_status", "Speedtest status for whether the scrape worked")

# Cache speedtest results
speedtest_cache_timeout = time.time()


def bytes_to_bits(bytes_per_sec):
    return bytes_per_sec * 8


def bits_to_megabits(bits_per_sec):
    megabits = round(bits_per_sec * (10**-6), 2)
    return megabits


def is_json(json_data) -> bool:
    try:
        json.loads(json_data)

    except ValueError:
        return False

    return True


def run_speedtest(server_id: str = None, timeout: int = None) -> tuple:
    cmd = [
        "speedtest", 
        "--format=json-pretty", 
        "--progress=no",
        "--accept-license", 
        "--accept-gdpr",
    ]

    if server_id:
        cmd.append(f"--server-id={server_id}")

    try:
        output = subprocess.check_output(cmd, timeout=timeout)

    except subprocess.CalledProcessError as e:
        output = e.output
        if not is_json(output):
            log.error("Speedtest CLI error not in JSON format.")
            return (0, 0, 0, 0, 0, 0)

    if is_json(output):
        data = json.loads(output)

        if "error" in data:
            log.error("Socker error while speedtest.")
            log.error(data["error"])
            return (0, 0, 0, 0, 0, 0)

        if "type" in data:
            if data["type"] == "log":
                log.info(str(data["timestamp"]) + " - " + str(data["message"]))

            if data["type"] == "result":
                actual_server = int(data["server"]["id"])
                actual_jitter = data["ping"]["jitter"]
                actual_ping = data["ping"]["latency"]
                actual_download_speed = bytes_to_bits(data["download"]["bandwidth"])
                actual_upload_speed = bytes_to_bits(data["upload"]["bandwidth"])
                return (actual_server, actual_jitter, actual_ping, actual_download_speed, actual_upload_speed, 1)

    log.error("Successfull speedtest had no json result.")
    return (0, 0, 0, 0, 0, 0)


@app.route("/metrics")
def record_speedtest():
    global speedtest_cache_timeout
    
    cache_timeout = int(os.getenv("SPEEDTEST_CACHE_TIMEOUT", 900))
    speedtest_server = os.getenv("SPEEDTEST_SERVER", None)
    speedtest_run_timeout = int(os.getenv("SPEEDTEST_RUN_TIMEOUT", 90))

    if time.time() > speedtest_cache_timeout:
        r_server, r_jitter, r_ping, r_download, r_upload, r_status = run_speedtest(speedtest_server, speedtest_run_timeout)
        log.info(f"Server={r_server} Jitter={r_jitter}ms Ping={r_ping}ms Download={bits_to_megabits(r_download)} Upload={bits_to_megabits(r_upload)}")

        server.set(r_server)        
        jitter.set(r_jitter)
        ping.set(r_ping)
        download_speed.set(r_download)
        upload_speed.set(r_upload)
        status.set(r_status)

        speedtest_cache_timeout = time.time() + cache_timeout

    return make_wsgi_app()


@app.route("/")
def index():
    return f"Speedtest exporter 🐲"


if __name__ == "__main__":
    HOST = os.getenv("SPEEDTEST_HOST", "0.0.0.0")
    PORT = os.getenv("SPEEDTEST_PORT", 8000)
    
    log.info(f"Starting exporter on http://{HOST}:{PORT}")
    serve(app, host="0.0.0.0", port=PORT)
