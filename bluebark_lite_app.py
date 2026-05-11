# bluebark_lite.py v0.112

import math
import os
import queue
import stat
import subprocess
import sys
import threading
import time

import RPi.GPIO as GPIO
from PIL import ImageFont
from luma.core.interface.serial import i2c
from luma.core.render import canvas
from luma.oled.device import ssd1306

import bluebark_lite


PIPE_PATH = "/dev/shm/tetra.bin"
RTL_POWER_FFTW = "/usr/local/bin/rtl_power_fftw"
SDR_SAMPLE_RATE = 2_000_000
NO_DATA_VALUE = -99.0
NO_DATA_SLEEP = 0.001
DISPLAY_INTERVAL = 0.05
RESTART_DELAY = 0.25

BTN_SHUTDOWN = 5
BTN_MUTE = 6
BTN_MODE = 13
BUZZER = 26

TOTAL_SIGNAL_STEPS = 13
FLOOR_DB_START = -32.0
MIN_THRESHOLD = -42.0
MAX_THRESHOLD = -14.0
STEP_SIZE = 2.0
ALARM_THRESHOLD = -16.0
NOISE_FLOOR_ESTIMATE = -46.0
CURRENT_GAIN = 496
AUTO_SQUELCH_SWEEPS = 15
AUTO_SQUELCH_MARGIN_DB = 2.0

SCAN_MODES = {
    "TESTPMR": {"freq": "446M:446.2M", "bins": 160, "avg": 400, "overlap": 16, "offset": 2},
    "WIDE": {"freq": "380M:385M", "bins": 80, "avg": 800, "overlap": 16, "offset": 2},
    "NARROW": {"freq": "380M:385M", "bins": 160, "avg": 400, "overlap": 16, "offset": 2},


}
DEFAULT_MODE = "NARROW"
MODE_SEQUENCE = ("NARROW", "WIDE")

STEP_Y = tuple(124 - (idx * 10) for idx in range(TOTAL_SIGNAL_STEPS))
STEP_THICKNESS = tuple(
    max(1, int(((idx / max(1, TOTAL_SIGNAL_STEPS - 1)) ** 1.5) * 7) + 1)
    for idx in range(TOTAL_SIGNAL_STEPS)
)
PEAK_BLOOM_STEPS = tuple(range(max(0, TOTAL_SIGNAL_STEPS - 4), TOTAL_SIGNAL_STEPS))
BOOT_ANGLES = tuple(range(0, 360, 45))
BOOT_CENTER_X = 32
BOOT_CENTER_Y = 64
BOOT_DRAW_MAX_RADIUS = 30
BOOT_DRAW_MIN_DEBRIS = 20
BOOT_DRAW_MAX_DEBRIS = 70


class LiteState:
    def __init__(self):
        self.floor_db = FLOOR_DB_START
        self.active_mode = DEFAULT_MODE

        self.is_muted = False
        self.screen_rotated = False
        self.is_active = False
        self.is_shutting_down = False
        self.has_received_data = False
        self.is_auto_muted = False
        self.is_auto_floor = False

        self.auto_squelch_done = False
        self.startup_sweep_count = 0
        self.startup_max_db = NO_DATA_VALUE

        self.alarm_hold_mono = 0.0
        self.high_alarm_start_mono = 0.0
        self.last_high_alarm_mono = 0.0
        self.last_beep_mono = 0.0

        self.smoothed_steps = 0
        self.last_step_drop_mono = 0.0
        self.peak_step = 0
        self.peak_hold_mono = 0.0
        self.peak_decay_mono = 0.0
        self.text_peak_db = NO_DATA_VALUE
        self.text_peak_mono = 0.0

        self.display_peak_db = NO_DATA_VALUE
        self.display_peak_lock = threading.Lock()

        self.current_temp_c = 0.0
        self.temp_display_until = 0.0
        self.sdr_process = None


state = LiteState()
c_shared_state = bluebark_lite.SharedState(
    state.floor_db,
    SCAN_MODES[state.active_mode]["bins"],
    SCAN_MODES[state.active_mode]["offset"],
)


try:
    serial = i2c(port=1, address=0x3C)
    device = ssd1306(serial, width=128, height=64, rotate=1)
except Exception as exc:
    print(f"OLED Error: {exc}")
    sys.exit(1)

try:
    sys_font = ImageFont.truetype("DejaVuSans.ttf", 14)
except Exception:
    sys_font = None


def monotonic_now():
    return time.monotonic()


def clamp_floor(value):
    return max(MIN_THRESHOLD, min(MAX_THRESHOLD, float(value)))


def set_floor_db(value, auto=False):
    state.floor_db = clamp_floor(value)
    state.is_auto_floor = auto
    c_shared_state.floor_db = state.floor_db


def get_cpu_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp", "r") as handle:
            return int(handle.read()) / 1000.0
    except Exception:
        return 0.0


GPIO.setwarnings(False)
GPIO.setmode(GPIO.BCM)
GPIO.setup(BUZZER, GPIO.OUT)
GPIO.output(BUZZER, False)
GPIO.setup([BTN_SHUTDOWN, BTN_MUTE, BTN_MODE], GPIO.IN, pull_up_down=GPIO.PUD_UP)


def ensure_fifo(path):
    if os.path.exists(path):
        path_stat = os.stat(path)
        if not stat.S_ISFIFO(path_stat.st_mode):
            os.remove(path)
    if not os.path.exists(path):
        os.mkfifo(path)


def raise_priority():
    try:
        os.nice(-10)
    except OSError:
        pass


def stop_sdr_process():
    proc = state.sdr_process
    state.sdr_process = None

    if proc is not None and proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                pass

    subprocess.run(
        ["pkill", "-9", "rtl_power_fftw"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def restart_sdr():
    cfg = SCAN_MODES[state.active_mode]

    state.has_received_data = False
    state.is_active = False
    state.auto_squelch_done = False
    state.startup_sweep_count = 0
    state.startup_max_db = NO_DATA_VALUE
    state.alarm_hold_mono = 0.0
    state.high_alarm_start_mono = 0.0
    state.last_high_alarm_mono = 0.0
    state.last_beep_mono = 0.0
    state.is_auto_muted = False
    state.smoothed_steps = 0
    state.last_step_drop_mono = 0.0
    state.peak_step = 0
    state.peak_hold_mono = 0.0
    state.peak_decay_mono = 0.0
    state.text_peak_db = NO_DATA_VALUE
    state.text_peak_mono = 0.0

    with state.display_peak_lock:
        state.display_peak_db = NO_DATA_VALUE

    c_shared_state.max_val = NO_DATA_VALUE
    c_shared_state.floor_db = state.floor_db
    c_shared_state.bins = cfg["bins"]
    c_shared_state.offset = cfg["offset"]

    stop_sdr_process()
    time.sleep(RESTART_DELAY)
    ensure_fifo(PIPE_PATH)

    base_path = PIPE_PATH[:-4] if PIPE_PATH.endswith(".bin") else PIPE_PATH
    cmd = [
        RTL_POWER_FFTW,
        "-f",
        cfg["freq"],
        "-r",
        str(SDR_SAMPLE_RATE),
        "-b",
        str(cfg["bins"]),
        "-n",
        str(cfg["avg"]),
        "-o",
        str(cfg["overlap"]),
        "-g",
        str(CURRENT_GAIN),
        "-c",
        "-q",
        "-m",
        base_path,
    ]
    state.sdr_process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        preexec_fn=raise_priority,
    )


beep_queue = queue.Queue(maxsize=4)


def buzzer_worker():
    while not state.is_shutting_down:
        try:
            num_beeps = beep_queue.get(timeout=1.0)
        except queue.Empty:
            continue

        if state.is_muted or state.is_shutting_down:
            continue

        for _ in range(num_beeps):
            if state.is_shutting_down:
                break
            try:
                GPIO.output(BUZZER, True)
                time.sleep(0.05)
                GPIO.output(BUZZER, False)
                time.sleep(0.05)
            except Exception:
                break


def beep_async(num_beeps):
    if state.is_muted or state.is_shutting_down:
        return
    try:
        beep_queue.put_nowait(num_beeps)
    except queue.Full:
        pass


def publish_display_peak(db_val):
    with state.display_peak_lock:
        if db_val > state.display_peak_db:
            state.display_peak_db = db_val


def consume_display_peak():
    with state.display_peak_lock:
        current_peak = state.display_peak_db
        state.display_peak_db = NO_DATA_VALUE
    return current_peak


def mode_letter():
    if state.active_mode == "WIDE":
        return "W"
    if state.active_mode == "TESTPMR":
        return "T"
    return "N"


def next_mode():
    try:
        current_idx = MODE_SEQUENCE.index(state.active_mode)
    except ValueError:
        return MODE_SEQUENCE[0]
    return MODE_SEQUENCE[(current_idx + 1) % len(MODE_SEQUENCE)]


def rotate_screen():
    state.screen_rotated = not state.screen_rotated
    if state.screen_rotated:
        device.command(0xA1, 0xC8)
    else:
        device.command(0xA0, 0xC0)


def draw_signal_step(draw, step_idx, thickness=1, fill_color="white"):
    y_center = STEP_Y[step_idx]
    draw.line((4, y_center, 28, y_center), fill=fill_color, width=thickness)


def draw_peak_marker(draw, step_idx):
    y_center = STEP_Y[step_idx]
    draw.line((0, y_center - 1, 0, y_center + 1), fill="white", width=1)


def render_boot_animation(now_mono):
    with canvas(device) as draw:
        phase = (now_mono * 0.8) % 1.0
        blast_radius = int(phase * BOOT_DRAW_MAX_RADIUS)
        if blast_radius > 0:
            draw.ellipse(
                (
                    BOOT_CENTER_X - blast_radius,
                    BOOT_CENTER_Y - blast_radius,
                    BOOT_CENTER_X + blast_radius,
                    BOOT_CENTER_Y + blast_radius,
                ),
                outline="white",
            )

        debris_start = phase * BOOT_DRAW_MIN_DEBRIS
        debris_end = phase * BOOT_DRAW_MAX_DEBRIS
        spin_offset = now_mono * 30.0
        for angle in BOOT_ANGLES:
            radians = math.radians(angle + spin_offset)
            x1 = BOOT_CENTER_X + int(math.cos(radians) * debris_start)
            y1 = BOOT_CENTER_Y + int(math.sin(radians) * debris_start)
            x2 = BOOT_CENTER_X + int(math.cos(radians) * debris_end)
            y2 = BOOT_CENTER_Y + int(math.sin(radians) * debris_end)
            if 0 <= x2 <= 64 and 0 <= y2 <= 128:
                draw.line((x1, y1, x2, y2), fill="white")


def update_display(db_val):
    if state.is_shutting_down:
        return

    now_mono = monotonic_now()

    if not state.has_received_data:
        render_boot_animation(now_mono)
        return

    threshold = state.floor_db if state.is_active else (state.floor_db + 1.0)
    state.is_active = db_val >= threshold

    if db_val >= ALARM_THRESHOLD:
        state.alarm_hold_mono = now_mono
    is_alarm = (now_mono - state.alarm_hold_mono) < 1.5

    db_range = MAX_THRESHOLD - NOISE_FLOOR_ESTIMATE
    raw_ratio = max(0.0, min(1.0, (db_val - NOISE_FLOOR_ESTIMATE) / db_range))
    target_steps = int((raw_ratio ** 2.2) * TOTAL_SIGNAL_STEPS)

    if target_steps >= state.smoothed_steps:
        state.smoothed_steps = target_steps
        state.last_step_drop_mono = now_mono
    elif state.smoothed_steps > 0 and (now_mono - state.last_step_drop_mono) > 0.25:
        state.smoothed_steps -= 1
        state.last_step_drop_mono = now_mono

    if state.smoothed_steps >= state.peak_step:
        state.peak_step = state.smoothed_steps
        state.peak_hold_mono = now_mono
    elif state.peak_step > 0 and (now_mono - state.peak_hold_mono) > 3.0:
        if (now_mono - state.peak_decay_mono) > 0.15:
            state.peak_step -= 1
            state.peak_decay_mono = now_mono

    if db_val >= state.text_peak_db:
        state.text_peak_db = db_val
        state.text_peak_mono = now_mono
    elif (now_mono - state.text_peak_mono) > 2.0:
        state.text_peak_db = db_val
        state.text_peak_mono = now_mono

    with canvas(device) as draw:
        draw.rectangle((42, 0, 55, 13), outline="white", fill="black")
        draw.text((45, 1), mode_letter(), fill="white", font=sys_font)

        floor_text = f"{abs(int(state.floor_db))}*" if state.is_auto_floor else f"{abs(int(state.floor_db))}"
        draw.text((36, 35), floor_text, fill="white", font=sys_font)

        draw_mute = state.is_muted or (state.is_auto_muted and int(now_mono) % 2 == 0)
        if draw_mute:
            draw.polygon([(36, 68), (36, 76), (40, 76), (45, 81), (45, 63), (40, 68)], outline="white")
            draw.line((49, 68, 57, 76), fill="white")
            draw.line((57, 68, 49, 76), fill="white")

        peak_text = f"{int(state.text_peak_db)}" if state.text_peak_db > NOISE_FLOOR_ESTIMATE else "---"
        draw.text((36, 108), peak_text, fill="white", font=sys_font)

        if now_mono < state.temp_display_until:
            draw.text((2, 45), "CPU:", fill="white", font=sys_font)
            draw.text((2, 65), f"{state.current_temp_c:.0f}C", fill="white", font=sys_font)
        else:
            max_step = min(TOTAL_SIGNAL_STEPS, state.smoothed_steps)
            for idx in range(max_step):
                draw_signal_step(draw, idx, thickness=STEP_THICKNESS[idx])

            if is_alarm or state.smoothed_steps >= TOTAL_SIGNAL_STEPS:
                if ((math.sin(now_mono * 15.0) + 1.0) / 2.0) > 0.5:
                    for idx in PEAK_BLOOM_STEPS:
                        draw_signal_step(draw, idx, thickness=10)

            for idx in range(state.peak_step):
                draw_peak_marker(draw, idx)


def display_loop():
    while not state.is_shutting_down:
        update_display(consume_display_peak())
        time.sleep(DISPLAY_INTERVAL)


def start_shutdown():
    state.is_shutting_down = True
    c_shared_state.shutdown = True

    with canvas(device) as draw:
        draw.text((18, 56), "BYE", fill="white", font=sys_font)

    time.sleep(1.0)
    stop_sdr_process()
    GPIO.output(BUZZER, False)

    try:
        device.clear()
        device.hide()
    except Exception:
        pass

    shutdown_cmd = ["/sbin/shutdown", "-h", "now"]
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        shutdown_cmd.insert(0, "sudo")

    subprocess.Popen(shutdown_cmd)

    while True:
        time.sleep(10)


def button_monitor():
    while True:
        if state.is_shutting_down:
            time.sleep(0.2)
            continue

        if not GPIO.input(BTN_SHUTDOWN):
            press_start = monotonic_now()
            while not GPIO.input(BTN_SHUTDOWN):
                if (monotonic_now() - press_start) > 2.0:
                    start_shutdown()
                time.sleep(0.05)

            if (monotonic_now() - press_start) < 2.0 and not state.is_shutting_down:
                state.current_temp_c = get_cpu_temp()
                state.temp_display_until = monotonic_now() + 3.0
                beep_async(1)
            time.sleep(0.05)

        if not GPIO.input(BTN_MUTE):
            press_start = monotonic_now()
            long_press = False
            while not GPIO.input(BTN_MUTE):
                if (monotonic_now() - press_start) > 2.0 and not long_press:
                    rotate_screen()
                    beep_async(1)
                    long_press = True
                time.sleep(0.05)

            if not long_press:
                state.is_muted = not state.is_muted
            with state.display_peak_lock:
                state.display_peak_db = NO_DATA_VALUE
            time.sleep(0.2)

        if not GPIO.input(BTN_MODE):
            press_start = monotonic_now()
            long_press = False
            while not GPIO.input(BTN_MODE):
                if (monotonic_now() - press_start) > 2.0 and not long_press:
                    state.active_mode = next_mode()
                    restart_sdr()
                    beep_async(3)
                    long_press = True
                time.sleep(0.05)

            if not long_press:
                next_floor = state.floor_db + STEP_SIZE
                if next_floor > MAX_THRESHOLD:
                    next_floor = MIN_THRESHOLD
                set_floor_db(next_floor, auto=False)
                with state.display_peak_lock:
                    state.display_peak_db = NO_DATA_VALUE
                beep_async(2)
            time.sleep(0.3)

        time.sleep(0.05)


def main():
    threading.Thread(target=buzzer_worker, daemon=True).start()
    threading.Thread(target=button_monitor, daemon=True).start()
    threading.Thread(target=display_loop, daemon=True).start()

    restart_sdr()
    threading.Thread(target=bluebark_lite.c_sdr_data_thread, args=(c_shared_state,), daemon=True).start()

    while True:
        if state.is_shutting_down:
            time.sleep(1.0)
            continue

        db_val = c_shared_state.consume_peak()
        if db_val == NO_DATA_VALUE:
            time.sleep(NO_DATA_SLEEP)
            continue

        state.has_received_data = True

        if not state.auto_squelch_done:
            state.startup_sweep_count += 1
            if db_val > state.startup_max_db:
                state.startup_max_db = db_val

            if state.startup_sweep_count >= AUTO_SQUELCH_SWEEPS:
                auto_floor = math.ceil((state.startup_max_db + AUTO_SQUELCH_MARGIN_DB) / 2.0) * 2
                set_floor_db(auto_floor, auto=True)
                state.auto_squelch_done = True

            time.sleep(0.01)
            continue

        publish_display_peak(db_val)

        now_mono = monotonic_now()
        is_high_alarm = db_val >= ALARM_THRESHOLD

        if is_high_alarm:
            state.last_high_alarm_mono = now_mono
            if state.high_alarm_start_mono == 0.0:
                state.high_alarm_start_mono = now_mono
            elif (now_mono - state.high_alarm_start_mono) > 5.0:
                state.is_auto_muted = True
        elif (now_mono - state.last_high_alarm_mono) > 5.0:
            state.high_alarm_start_mono = 0.0
            state.is_auto_muted = False

        if db_val < state.floor_db:
            continue

        if is_high_alarm:
            target_beeps = 3
            current_cooldown = 1.5
        else:
            target_beeps = 1
            db_from_alarm = ALARM_THRESHOLD - db_val
            if db_from_alarm <= 2.0:
                current_cooldown = 0.4
            elif db_from_alarm <= 5.0:
                current_cooldown = 0.6
            elif db_from_alarm <= 10.0:
                current_cooldown = 1.0
            else:
                current_cooldown = 2.0

        if not state.is_auto_muted and (now_mono - state.last_beep_mono) > current_cooldown:
            beep_async(target_beeps)
            state.last_beep_mono = now_mono


if __name__ == "__main__":
    try:
        main()
    finally:
        c_shared_state.shutdown = True
        stop_sdr_process()
        GPIO.output(BUZZER, False)
        try:
            GPIO.cleanup()
        except Exception:
            pass
