# BlueBark Lite Wiring

This is the current wiring used by the Lite build in `bluebark_lite_app.py`.

## Membrane Buttons

All three buttons are wired as active-low inputs with the Pi's internal pull-ups enabled.

| Function | BCM | Physical Pin | Wiring |
| --- | --- | --- | --- |
| Shutdown | GPIO 5 | Pin 29 | Button shorts GPIO 5 to GND |
| Mute / Rotate | GPIO 6 | Pin 31 | Button shorts GPIO 6 to GND |
| Mode / Squelch | GPIO 13 | Pin 33 | Button shorts GPIO 13 to GND |

Button common side goes to ground.

Behavior:

- Short press on Shutdown shows CPU temperature for about 3 seconds and gives one beep.
- Long press on Shutdown shuts the system down after about 2 seconds.
- Short press on Mute toggles mute.
- Long press on Mute rotates the OLED 180 degrees.
- Short press on Mode raises the squelch floor by 2 dB and wraps back to the minimum.
- Long press on Mode switches between `NARROW` and `WIDE` modes and restarts the SDR process.

## OLED

The OLED is an SSD1306 on I2C bus 1.

| Signal | Raspberry Pi | Notes |
| --- | --- | --- |
| SDA | GPIO 2 / Pin 3 | I2C data |
| SCL | GPIO 3 / Pin 5 | I2C clock |
| GND | Any GND pin | Ground |
| VCC | 3.3V or module-rated supply | Use the voltage your OLED board expects |

Code settings:

- I2C address: `0x3C`
- Device: `ssd1306`
- Resolution: `128x64`
- Rotation: `1`

On-screen meaning:

- Left vertical bar shows the current detected signal strength.
- Small peak dots show the strongest recent level before the display decays.
- The single letter on the right is the active mode.
- The number below it is the squelch floor.
- The mute icon appears when mute is enabled or auto-mute is active.
- The lower text area shows the strongest recent dB reading, or CPU temperature when that overlay is active.

## Active Buzzer

| Function | BCM | Physical Pin | Wiring |
| --- | --- | --- | --- |
| Buzzer | GPIO 26 | Pin 37 | Active buzzer control pin |

Behavior:

- GPIO high turns the buzzer on.
- GPIO low turns the buzzer off.
- The code uses short beep sequences, not PWM.

If your buzzer module draws more current than a GPIO pin should source, use a transistor or driver stage.

## Notes

- The code assumes the Pi-side pull-ups are active on the three button inputs.
- The button switches should connect the GPIO pin to ground when pressed.
- If you rewire to different pins, update `BTN_SHUTDOWN`, `BTN_MUTE`, `BTN_MODE`, and `BUZZER` in `bluebark_lite_app.py`.
