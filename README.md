# BlueBark Lite

A lightweight Raspberry Pi + RTL-SDR signal presence detector for TETRA-like RF activity. 


## What It Does

- Scans the configured RF bands with `rtl_power_fftw`.
- Keeps the strongest unread sweep in the Cython bridge so short bursts are not overwritten before Python sees them.
- Drives a small OLED meter and status readout.
- Uses three membrane buttons for mute, mode, squelch floor, shutdown, and OLED rotation.
- Beeps through an active buzzer when signal thresholds are crossed.

## OLED Layout

The OLED is intentionally minimal so it stays readable at a glance.

- Left side: a 13-step vertical signal bar that grows with detected energy.
- Peak marks: small dots that hold the strongest recent level for a short time.
- Right side: a mode letter, the current squelch floor, a mute icon when muted, and the peak dB text.
- Temporary overlay: CPU temperature replaces the bar briefly after a short press on the shutdown button.
- Alarm bloom: the upper signal bar brightens when the alarm threshold is reached.

## Project Layout

- `bluebark_lite_app.py`: main application, OLED UI, button handling, SDR restart logic.
- `bluebark_lite.pyx`: Cython DSP bridge for reading `rtl_power_fftw` output quickly.
- `setup.py`: build script for the Cython module.
- `bluebark-lite.service`: example systemd unit for boot startup.
- `WIRING.md`: exact button, OLED, and buzzer wiring for the current configuration.

## Dependencies

System packages:

- Raspberry Pi OS or DietPi on ARM64
- Python 3
- `rtl_power_fftw`
- I2C enabled system
- Build tools for Cython compilation, usually `gcc`, `make`, and Python headers

Python packages:

- `Cython`
- `RPi.GPIO`
- `luma.oled`
- `Pillow`

When you build `rtl_power_fftw` yourself, you also typically need FFTW and RTL-SDR development packages.



Example install set on Debian-based Pi systems:

```bash
sudo apt update
sudo apt install -y python3-dev python3-pip python3-setuptools gcc make libffi-dev libjpeg-dev zlib1g-dev
python3 -m pip install --upgrade pip
python3 -m pip install cython RPi.GPIO luma.oled Pillow
```

## Quick Start

1. Put this folder on the Pi, for example at `/root/bluebarklite`.
2. Enable I2C.
3. Install the Python and SDR dependencies.
4. Build the Cython module:

```bash
cd /root/bluebarklite
python3 setup.py build_ext --inplace
```

5. Run the app:

```bash
sudo python3 bluebark_lite_app.py
```

## systemd Startup

The included service file is meant as the starting point for boot startup.

```bash
sudo cp bluebark-lite.service /etc/systemd/system/bluebark-lite.service
sudo systemctl daemon-reload
sudo systemctl enable bluebark-lite.service
sudo systemctl start bluebark-lite.service
sudo systemctl status bluebark-lite.service
```

## Default Buttons configuration

- Shutdown button short press shows CPU temperature briefly and gives one beep.
- Shutdown button long press triggers shutdown after about 2 seconds.
- Mute button short press toggles mute.
- Mute button long press rotates the OLED display.
- Mode button short press raises the squelch floor in 2 dB steps and wraps back to the minimum.
- Mode button long press toggles between `NARROW` and `WIDE` SDR modes.

## Tuning Notes

- `TESTPMR` is aimed at the 446 MHz PMR range.

rtl_power_fftw scans the selected frequency range
        ↓
binary sweep data goes into a RAM FIFO
        ↓
Cython reads the FIFO and extracts the strongest peak
        ↓
Python handles display, buttons, threshold, and buzzer

Important: this is a signal presence and strength monitor only. It does not decode, demodulate, or reconstruct TETRA voice or data content.
This repository is provided as-is.

## License

MIT 
