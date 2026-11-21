"""Verified wiring for common Arduino Uno / Nano builds (Engineering Eyes, Issue #9).

A small local model writes plausible but wrong wiring ("GND (pin 1)" — pin 1 is TX), so the builds people
start with most come from these hand-checked recipes instead. Each step is small enough to verify from one
photo, and its `check` lists what that photo must show. Pins are for the Uno/Nano (5 V, I2C on A4/A5); other
boards (ESP32, Raspberry Pi, Pico, Mega I2C) get no recipe and fall through to the planner.
"""
import re

UNPLUG = {"title": "Unplug the Arduino",
          "instruction": "Unplug the USB cable (and any battery) so the board is unpowered while you wire.",
          "check": ["No cable is plugged into the Arduino's USB port"]}


def _upload(what: str, check: str) -> dict:
    return {"title": "Plug in and upload", "instruction": f"Plug the USB cable back in. {what}", "check": [check]}


RECIPES = [
    {
        "key": "led", "title": "Blink an LED on pin 13",
        "match": r"\bleds?\b", "avoid": r"\b(neopixel|ws2812|ws2811|strip|matrix|rgb|addressable)\b",
        "parts": ["Arduino Uno or Nano + USB cable", "Breadboard", "1 LED", "220 Ω resistor (red-red-brown)",
                  "2 jumper wires"],
        "steps": [
            UNPLUG,
            {"title": "Place the LED", "instruction": "Push the LED into the breadboard so its two legs are in two "
             "different rows. Remember which row has the LONGER leg — that's + (anode).",
             "check": ["An LED is pushed into the breadboard"]},
            {"title": "Add the resistor", "instruction": "Put one end of the 220 Ω resistor in the long-leg row and the "
             "other end in an empty row.",
             "check": ["A resistor is in the breadboard next to the LED"]},
            {"title": "Wire pin 13", "instruction": "Run a jumper wire from the resistor's free row to digital pin 13 "
             "on the Arduino (top header, next to GND).",
             "check": ["A wire runs from the breadboard to pin 13 on the Arduino"]},
            {"title": "Wire ground", "instruction": "Run a jumper wire from the LED's short-leg row to a GND pin on the "
             "Arduino — any pin labeled GND.",
             "check": ["A wire runs from the breadboard to a GND pin on the Arduino"]},
            _upload("In the Arduino IDE open File › Examples › 01.Basics › Blink, choose Board: Arduino Uno and the "
                    "right Port, then Upload. The LED blinks once a second.",
                    "The Arduino is plugged in and the LED is lit"),
        ],
    },
    {
        "key": "button", "title": "Read a push button on pin 2",
        "match": r"\b(push ?button|button|tactile switch|switch)\b", "avoid": r"\b(light switch|wall switch)\b",
        "parts": ["Arduino Uno or Nano + USB cable", "Breadboard", "4-leg tactile push button", "2 jumper wires"],
        "steps": [
            UNPLUG,
            {"title": "Place the button", "instruction": "Press the button into the breadboard so it straddles the "
             "center gap, two legs on each side.",
             "check": ["The button straddles the breadboard's center gap"]},
            {"title": "Wire pin 2", "instruction": "Run a jumper wire from one of the button's legs to digital pin 2.",
             "check": ["A jumper wire runs from a button leg to pin 2"]},
            {"title": "Wire ground", "instruction": "Run a jumper wire from the button leg diagonally opposite to a GND "
             "pin on the Arduino.",
             "check": ["A jumper wire runs from the diagonally opposite button leg to a pin labeled GND"]},
            _upload("Use pinMode(2, INPUT_PULLUP); — the pin reads LOW while the button is pressed and HIGH when "
                    "released, so no extra resistor is needed.", "The Arduino is plugged in"),
        ],
    },
    {
        "key": "pot", "title": "Read a potentiometer on A0",
        "match": r"\b(potentiometer|pot|knob|trimmer)\b", "avoid": None,
        "parts": ["Arduino Uno or Nano + USB cable", "Breadboard", "10 kΩ potentiometer", "3 jumper wires"],
        "steps": [
            UNPLUG,
            {"title": "Place the potentiometer", "instruction": "Push the potentiometer into the breadboard so its three "
             "legs are in three different rows.",
             "check": ["A potentiometer is pushed into the breadboard"]},
            {"title": "Wire 5V", "instruction": "Run a jumper wire from one OUTER leg to the 5V pin.",
             "check": ["A jumper wire runs from an outer leg to the pin labeled 5V"]},
            {"title": "Wire ground", "instruction": "Run a jumper wire from the OTHER outer leg to a GND pin.",
             "check": ["A jumper wire runs from the other outer leg to a pin labeled GND"]},
            {"title": "Wire A0", "instruction": "Run a jumper wire from the MIDDLE leg (the wiper) to analog pin A0.",
             "check": ["A jumper wire runs from the middle leg to pin A0"]},
            _upload("Open File › Examples › 01.Basics › AnalogReadSerial, upload, then open the Serial Monitor at 9600 "
                    "baud: turning the knob moves the number between 0 and 1023.", "The Arduino is plugged in"),
        ],
    },
    {
        "key": "servo", "title": "Sweep a servo on pin 9",
        "match": r"\b(servo|sg90|mg90|mg996)\b", "avoid": None,
        "parts": ["Arduino Uno or Nano + USB cable", "SG90-style micro servo", "3 jumper wires (male-male)"],
        "steps": [
            UNPLUG,
            {"title": "Wire servo ground", "instruction": "Connect the servo's BROWN (or black) wire to a GND pin.",
             "check": ["The servo's brown or black wire goes to a pin labeled GND"]},
            {"title": "Wire servo power", "instruction": "Connect the servo's RED wire to the 5V pin. One small servo is "
             "fine from USB; several or larger servos need their own 5–6 V supply with its ground tied to Arduino GND.",
             "check": ["The servo's red wire goes to the pin labeled 5V"]},
            {"title": "Wire the signal", "instruction": "Connect the servo's ORANGE (or yellow/white) wire to digital pin 9.",
             "check": ["The servo's orange, yellow or white wire goes to pin 9"]},
            _upload("Open File › Examples › Servo › Sweep and upload — the horn swings back and forth.",
                    "The Arduino is plugged in"),
        ],
    },
    {
        "key": "ultrasonic", "title": "Measure distance with an HC-SR04",
        "match": r"\b(hc-?sr04|ultrasonic|sonar|distance sensor)\b", "avoid": None,
        "parts": ["Arduino Uno or Nano + USB cable", "Breadboard", "HC-SR04 ultrasonic sensor", "4 jumper wires"],
        "steps": [
            UNPLUG,
            {"title": "Place the sensor", "instruction": "Push the sensor's 4 pins into 4 different breadboard rows, "
             "the two round 'eyes' facing out. The pins are labeled VCC, Trig, Echo, GND.",
             "check": ["The HC-SR04 sensor is pushed into the breadboard"]},
            {"title": "Wire power", "instruction": "VCC → 5V and GND → a GND pin.",
             "check": ["A wire runs from VCC to the pin labeled 5V", "A wire runs from GND to a pin labeled GND"]},
            {"title": "Wire Trig and Echo", "instruction": "Trig → digital pin 9 and Echo → digital pin 10.",
             "check": ["A wire runs from Trig to pin 9", "A wire runs from Echo to pin 10"]},
            _upload("Send a 10 µs HIGH pulse on pin 9, then pulseIn(10, HIGH) gives the echo time; distance in cm = "
                    "duration / 58. Print it to the Serial Monitor.", "The Arduino is plugged in"),
        ],
    },
    {
        "key": "dht", "title": "Read temperature & humidity with a DHT11/DHT22",
        "match": r"\b(dht ?11|dht ?22|am2302|temperature (and|&) humidity|humidity sensor)\b", "avoid": None,
        "parts": ["Arduino Uno or Nano + USB cable", "DHT11 or DHT22 module (3 pins)", "3 jumper wires"],
        "steps": [
            UNPLUG,
            {"title": "Wire power", "instruction": "Module + (VCC) → 5V and − (GND) → a GND pin. A bare 4-pin sensor "
             "(no board) also needs a 10 kΩ resistor between VCC and DATA; modules have it built in.",
             "check": ["A wire runs from the module's + or VCC to 5V", "A wire runs from its − or GND to GND"]},
            {"title": "Wire data", "instruction": "Module OUT / DATA / S → digital pin 2.",
             "check": ["A wire runs from the module's data pin to pin 2"]},
            _upload("Install 'DHT sensor library' by Adafruit (Tools › Manage Libraries), open its DHTtester example, set "
                    "DHTPIN 2 and DHTTYPE to DHT11 or DHT22, upload and open the Serial Monitor at 9600.",
                    "The Arduino is plugged in"),
        ],
    },
    {
        "key": "oled", "title": "Drive an I2C OLED (SSD1306)",
        "match": r"\b(oled|ssd1306|sh1106|i2c display|0\.96)\b", "avoid": r"\bmega\b",
        "parts": ["Arduino Uno or Nano + USB cable", "128×64 I2C OLED (4 pins)", "4 jumper wires"],
        "steps": [
            UNPLUG,
            {"title": "Read the pin labels", "instruction": "The 4 pins are labeled on the OLED — usually GND, VCC, SCL, "
             "SDA, but the order varies between modules, so go by the labels, not the position.",
             "check": ["The OLED's pin labels (GND, VCC, SCL, SDA) are readable in the photo"]},
            {"title": "Wire power", "instruction": "VCC → 5V (most modules accept 3.3–5 V; use 3.3V if yours says 3.3 V "
             "only) and GND → a GND pin.",
             "check": ["A wire runs from the OLED's VCC to 5V or 3.3V", "A wire runs from its GND to a GND pin"]},
            {"title": "Wire I2C", "instruction": "SDA → A4 and SCL → A5 (on a Uno R3 you can also use the SDA/SCL pins "
             "near AREF — they're the same signals).",
             "check": ["A wire runs from SDA to A4 or the SDA pin", "A wire runs from SCL to A5 or the SCL pin"]},
            _upload("Install 'Adafruit SSD1306' and 'Adafruit GFX', open the ssd1306_128x64_i2c example, make sure the "
                    "address is 0x3C, and upload.", "The OLED shows graphics or text"),
        ],
    },
    {
        "key": "buzzer", "title": "Play tones on a piezo buzzer",
        "match": r"\b(buzzer|piezo|beeper)\b", "avoid": None,
        "parts": ["Arduino Uno or Nano + USB cable", "Piezo buzzer", "2 jumper wires"],
        "steps": [
            UNPLUG,
            {"title": "Wire the buzzer", "instruction": "Buzzer + (the longer leg, or the side marked +) → digital pin 8; "
             "− → a GND pin.",
             "check": ["A wire runs from the buzzer's + leg to pin 8", "A wire runs from its − leg to a pin labeled GND"]},
            _upload("Open File › Examples › 02.Digital › toneMelody (it uses pin 8) and upload.",
                    "The Arduino is plugged in"),
        ],
    },
    {
        "key": "relay", "title": "Switch a relay module from pin 7 (low-voltage loads)",
        "match": r"\brelay\b", "avoid": None,
        "parts": ["Arduino Uno or Nano + USB cable", "5 V relay module (1 channel)", "3 jumper wires",
                  "A LOW-VOLTAGE load to switch (≤ 30 V DC)"],
        "steps": [
            UNPLUG,
            {"title": "Wire the relay's control side", "instruction": "Relay module VCC → 5V, GND → a GND pin, IN → "
             "digital pin 7. Don't touch the screw terminals yet.",
             "check": ["A wire runs from the relay's VCC to 5V", "A wire runs from its GND to GND",
                       "A wire runs from its IN pin to pin 7"]},
            {"title": "Test the click", "instruction": "Plug in USB and upload a sketch that sets pin 7 LOW for a second, "
             "then HIGH. Most modules are active-LOW: the relay clicks and its LED lights when IN is LOW.",
             "check": ["The relay module's indicator LED is lit"]},
            {"title": "Connect the low-voltage load", "instruction": "Unplug first. Wire ONLY a low-voltage DC load "
             "(≤ 30 V) through COM and NO. Never connect mains to a hobby relay board.",
             "check": ["The load's wires go to the relay's COM and NO terminals",
                       "The load is low-voltage (no mains plug or cord)"]},
        ],
        "caution": "only switch low-voltage DC loads with a hobby relay board — mains loads need an electrician",
    },
    {
        "key": "ldr", "title": "Read light level with a photoresistor (LDR)",
        "match": r"\b(ldr|photoresistor|photocell|light sensor|light level)\b", "avoid": None,
        "parts": ["Arduino Uno or Nano + USB cable", "Breadboard", "Photoresistor (LDR)", "10 kΩ resistor (brown-black-orange)",
                  "3 jumper wires"],
        "steps": [
            UNPLUG,
            {"title": "Place the LDR and resistor", "instruction": "Put the LDR's legs in two different rows. Put the "
             "10 kΩ resistor from one LDR leg's row into another empty row.",
             "check": ["A photoresistor and a resistor are in the breadboard"]},
            {"title": "Wire it up", "instruction": "The LDR's other leg → 5V. The resistor's far end → GND. The row where "
             "the LDR and resistor meet → A0.",
             "check": ["A wire runs from the LDR's free leg to 5V", "A wire runs from the resistor's far end to GND",
                       "A wire runs from the shared LDR/resistor row to A0"]},
            _upload("Open File › Examples › 01.Basics › AnalogReadSerial and the Serial Monitor at 9600: cover the LDR "
                    "and the number drops.", "The Arduino is plugged in"),
        ],
    },
]

# Different pinouts / logic levels: no Uno recipe. (A Mega matches the Uno except I2C — the OLED recipe
# avoids "mega" itself.)
_OTHER_BOARD = re.compile(r"\b(esp32|esp8266|nodemcu|raspberry pi|rpi|pi pico|rp2040|micro:?bit|stm32|teensy)\b", re.I)


# Most specific first: "switch a relay" must not land on the button recipe, "OLED" not on the LED one.
ORDER = ["relay", "ultrasonic", "dht", "oled", "servo", "buzzer", "ldr", "pot", "button", "led"]


def match(goal: str, seen_item: str = "") -> dict | None:
    """The verified recipe for this goal, or None (other boards, or nothing matches confidently)."""
    text = f"{goal} {seen_item}"
    if _OTHER_BOARD.search(text):
        return None
    for r in sorted(RECIPES, key=lambda r: ORDER.index(r["key"])):
        if re.search(r["match"], goal, re.I) and not (r["avoid"] and re.search(r["avoid"], goal, re.I)):
            return r
    return None
