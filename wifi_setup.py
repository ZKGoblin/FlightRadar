import pygame
import subprocess
import time
import re
import threading
import urllib.request


# ============================================================
# CONFIGURATION
# ============================================================

# Display resolution is detected automatically at startup.
# The application uses the full physical display with no window border.
SCALE = 2


# ============================================================
# COLOURS
# ============================================================

BACKGROUND = (5, 10, 7)

RADAR_GREEN = (45, 190, 70)
BRIGHT_GREEN = (80, 255, 100)
DIM_GREEN = (20, 90, 35)
TEXT_GREEN = (100, 230, 120)

PANEL_BACKGROUND = (8, 18, 12)
WHITE = (235, 240, 225)
RED = (220, 70, 70)


# ============================================================
# PYGAME
# ============================================================

pygame.init()

# Fullscreen + borderless. Pygame chooses the display's native resolution.
screen = pygame.display.set_mode(
    (0, 0),
    pygame.FULLSCREEN | pygame.NOFRAME
)

WINDOW_WIDTH, WINDOW_HEIGHT = screen.get_size()

INTERNAL_WIDTH = WINDOW_WIDTH * SCALE
INTERNAL_HEIGHT = WINDOW_HEIGHT * SCALE

pygame.display.set_caption(
    "Flight Radar - Wi-Fi Setup"
)

clock = pygame.time.Clock()

canvas = pygame.Surface(
    (INTERNAL_WIDTH, INTERNAL_HEIGHT)
)


# ============================================================
# FONTS
# ============================================================

font = pygame.font.SysFont(
    "consolas",
    18 * SCALE
)

small_font = pygame.font.SysFont(
    "consolas",
    13 * SCALE
)

large_font = pygame.font.SysFont(
    "consolas",
    26 * SCALE,
    bold=True
)


# ============================================================
# WIFI STATE
# ============================================================

wifi_networks = []

selected_network = None

password = ""

password_visible = False

wifi_status = ""

wifi_error = ""

scanning = False

connecting = False

# Connection work is performed in a background thread so the
# Pygame interface never freezes while nmcli is connecting.
connection_thread = None
connection_result = None
connection_target = None

last_scan_time = 0


# ============================================================
# KEYBOARD
# ============================================================

LETTER_KEYBOARD_ROWS = [
    [
        "1", "2", "3", "4", "5",
        "6", "7", "8", "9", "0"
    ],
    [
        "Q", "W", "E", "R", "T",
        "Y", "U", "I", "O", "P"
    ],
    [
        "A", "S", "D", "F", "G",
        "H", "J", "K", "L"
    ],
    [
        "Z", "X", "C", "V", "B",
        "N", "M"
    ]
]

# Common Wi-Fi password symbols. More unusual printable characters
# can still be entered using a USB keyboard.
SYMBOL_KEYBOARD_ROWS = [
    ["!", "@", "#", "$", "%", "^", "&", "*", "(", ")"],
    ["-", "_", "=", "+", "[", "]", "{", "}", "\\", "`"],
    [";", ":", "'", '"', ",", ".", "<", ">", "/", "?"],
    ["|", "~", "\\", "€", "£"]
]

keyboard_mode = "letters"
keyboard_shifted = True


# ============================================================
# CHECK WIFI CONNECTION
# ============================================================

def is_wifi_connected():

    try:

        result = subprocess.run(
            [
                "nmcli",
                "-t",
                "-f",
                "DEVICE,TYPE,STATE",
                "device"
            ],
            capture_output=True,
            text=True,
            timeout=5
        )

        if result.returncode != 0:
            return False

        for line in result.stdout.splitlines():

            parts = line.split(":")

            if len(parts) < 3:
                continue

            device = parts[0]
            device_type = parts[1]
            state = parts[2]

            if (
                device_type == "wifi"
                and
                state == "connected"
            ):

                return True

        return False

    except Exception as error:

        print(
            "Wi-Fi status error:",
            error
        )

        return False


# ============================================================
# CHECK INTERNET CONNECTION
# ============================================================

def is_internet_connected():

    try:

        # NetworkManager's connectivity state is the quickest
        # check and does not require a Python network library.
        result = subprocess.run(
            [
                "nmcli",
                "-t",
                "-f",
                "CONNECTIVITY",
                "general"
            ],
            capture_output=True,
            text=True,
            timeout=5
        )

        state = result.stdout.strip().lower()

        if state == "full":
            return True

        if state in ("limited", "none"):
            return False

    except Exception as error:

        print(
            "Internet status error:",
            error
        )

    # If NetworkManager cannot determine connectivity, perform a
    # small HTTPS request. This also makes the check useful on
    # systems where NetworkManager reports connectivity as unknown.
    try:

        request = urllib.request.Request(
            "https://www.google.com/generate_204",
            method="HEAD"
        )

        with urllib.request.urlopen(
            request,
            timeout=3
        ) as response:

            return response.status in (200, 204)

    except Exception:

        return False


# ============================================================
# SCAN WIFI
# ============================================================

def scan_wifi():

    global wifi_networks
    global scanning
    global wifi_error

    scanning = True

    wifi_error = ""

    try:

        # Ask NetworkManager to perform a fresh scan.

        subprocess.run(
            [
                "nmcli",
                "device",
                "wifi",
                "rescan"
            ],
            capture_output=True,
            text=True,
            timeout=10
        )


        # Give the Wi-Fi adapter a moment to populate
        # the scan results.

        time.sleep(2)


        result = subprocess.run(
            [
                "nmcli",
                "-t",
                "-f",
                "SSID,SIGNAL,SECURITY",
                "device",
                "wifi",
                "list"
            ],
            capture_output=True,
            text=True,
            timeout=10
        )


        if result.returncode != 0:

            wifi_error = (
                "Unable to scan for Wi-Fi networks."
            )

            wifi_networks = []

            return


        networks = {}


        for line in result.stdout.splitlines():

            if not line.strip():
                continue


            # nmcli escapes special characters.
            #
            # Use the colon-separated format but only
            # split the first three fields.

            parts = line.split(":")

            if len(parts) < 3:
                continue


            ssid = parts[0]

            signal = parts[1]

            security = ":".join(
                parts[2:]
            )


            # Ignore networks with no SSID.

            if not ssid:
                continue


            try:

                signal_value = int(
                    signal
                )

            except ValueError:

                signal_value = 0


            # If the same SSID appears on 2.4 GHz
            # and 5 GHz, keep the strongest result.

            if (
                ssid not in networks
                or
                signal_value
                >
                networks[ssid]["signal"]
            ):

                networks[ssid] = {

                    "ssid": ssid,

                    "signal": signal_value,

                    "security": security

                }


        wifi_networks = sorted(

            networks.values(),

            key=lambda network:
                network["signal"],

            reverse=True

        )


    except Exception as error:

        print(
            "Wi-Fi scan error:",
            error
        )

        wifi_error = (
            "Wi-Fi scanning failed."
        )

        wifi_networks = []


    finally:

        scanning = False


# ============================================================
# CONNECT WIFI
# ============================================================

def _connect_wifi_worker(ssid, password):

    global connecting
    global wifi_status
    global wifi_error
    global connection_result

    try:

        # ----------------------------------------------------
        # Build nmcli command
        # ----------------------------------------------------

        if not password:

            command = [
                "nmcli",
                "device",
                "wifi",
                "connect",
                ssid
            ]

        else:

            command = [
                "nmcli",
                "device",
                "wifi",
                "connect",
                ssid,
                "password",
                password
            ]

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:

            error_text = (
                result.stderr.strip()
                or
                result.stdout.strip()
            )

            print(
                "Wi-Fi connection failed:",
                error_text
            )

            if (
                "Secrets were required"
                in error_text
                or
                "incorrect password"
                in error_text.lower()
            ):

                wifi_error = "Incorrect password."

            elif (
                "No network with SSID"
                in error_text
            ):

                wifi_error = (
                    "Network is no longer available."
                )

            else:

                wifi_error = "Unable to connect."

            connection_result = False
            return

        # ----------------------------------------------------
        # Wait for Wi-Fi and then actual internet access.
        # ----------------------------------------------------

        wifi_status = "CONNECTING..."

        for _ in range(20):

            if is_wifi_connected():

                wifi_status = "CHECKING INTERNET..."

                if is_internet_connected():

                    wifi_status = "CONNECTED"
                    connection_result = True
                    return

            time.sleep(0.5)

        wifi_error = (
            "Connected to Wi-Fi, but internet is unavailable."
        )

        connection_result = False

    except subprocess.TimeoutExpired:

        wifi_error = "Connection timed out."
        connection_result = False

    except Exception as error:

        print(
            "Wi-Fi connection error:",
            error
        )

        wifi_error = "Connection failed."
        connection_result = False

    finally:

        connecting = False


def connect_wifi(ssid, password):

    global connecting
    global wifi_status
    global wifi_error
    global connection_thread
    global connection_result
    global connection_target

    # Ignore another connection request while one is already running.
    if connecting:
        return True

    connecting = True
    wifi_status = "CONNECTING..."
    wifi_error = ""
    connection_result = None
    connection_target = ssid

    connection_thread = threading.Thread(
        target=_connect_wifi_worker,
        args=(ssid, password),
        daemon=True
    )

    connection_thread.start()

    return True

# ============================================================
# SIGNAL BARS
# ============================================================

def draw_signal_bars(
    x,
    y,
    signal
):

    bars = 4

    if signal >= 80:
        active = 4

    elif signal >= 60:
        active = 3

    elif signal >= 40:
        active = 2

    elif signal >= 20:
        active = 1

    else:
        active = 0


    for i in range(bars):

        height = (
            5 + i * 5
        ) * SCALE

        width = 4 * SCALE

        bar_x = (
            x
            +
            i * 7 * SCALE
        )

        bar_y = (
            y
            +
            20 * SCALE
            -
            height
        )


        colour = (
            BRIGHT_GREEN
            if i < active
            else DIM_GREEN
        )


        pygame.draw.rect(

            canvas,

            colour,

            (
                bar_x,
                bar_y,
                width,
                height
            )

        )


# ============================================================
# DRAW HEADER
# ============================================================

def draw_header():

    title = large_font.render(
        "FLIGHT RADAR",
        True,
        TEXT_GREEN
    )

    title_rect = title.get_rect(
        center=(
            INTERNAL_WIDTH // 2,
            80 * SCALE
        )
    )

    canvas.blit(
        title,
        title_rect
    )


    subtitle = small_font.render(
        "WIFI SETUP",
        True,
        DIM_GREEN
    )

    subtitle_rect = subtitle.get_rect(
        center=(
            INTERNAL_WIDTH // 2,
            115 * SCALE
        )
    )

    canvas.blit(
        subtitle,
        subtitle_rect
    )


# ============================================================
# DRAW NETWORK LIST
# ============================================================

def draw_network_list():

    panel_width = 700 * SCALE

    panel_x = (
        INTERNAL_WIDTH
        -
        panel_width
    ) // 2

    panel_y = 145 * SCALE

    panel_height = 550 * SCALE


    panel_rect = pygame.Rect(

        panel_x,

        panel_y,

        panel_width,

        panel_height

    )


    pygame.draw.rect(

        canvas,

        PANEL_BACKGROUND,

        panel_rect,

        border_radius=8 * SCALE

    )


    pygame.draw.rect(

        canvas,

        DIM_GREEN,

        panel_rect,

        width=1 * SCALE,

        border_radius=8 * SCALE

    )


    if scanning:

        text = font.render(

            "SCANNING FOR WIFI...",

            True,

            TEXT_GREEN

        )

        rect = text.get_rect(

            center=panel_rect.center

        )

        canvas.blit(
            text,
            rect
        )

        return


    if not wifi_networks:

        text = font.render(

            "NO WIFI NETWORKS FOUND",

            True,

            TEXT_GREEN

        )

        rect = text.get_rect(

            center=(

                panel_rect.centerx,

                panel_rect.centery - 20 * SCALE

            )

        )

        canvas.blit(
            text,
            rect
        )

    else:

        title = small_font.render(

            "SELECT A NETWORK",

            True,

            DIM_GREEN

        )

        canvas.blit(

            title,

            (

                panel_x
                +
                25 * SCALE,

                panel_y
                +
                20 * SCALE

            )

        )


        row_height = 62 * SCALE

        start_y = (
            panel_y
            +
            55 * SCALE
        )


        # Only display networks that fit on screen.

        max_networks = 8


        for index, network in enumerate(
            wifi_networks[:max_networks]
        ):

            row_y = (
                start_y
                +
                index * row_height
            )


            row_rect = pygame.Rect(

                panel_x
                +
                15 * SCALE,

                row_y,

                panel_width
                -
                30 * SCALE,

                52 * SCALE

            )


            if (
                selected_network
                and
                network["ssid"]
                ==
                selected_network["ssid"]
            ):

                pygame.draw.rect(

                    canvas,

                    DIM_GREEN,

                    row_rect,

                    border_radius=5 * SCALE

                )


            text = font.render(

                network["ssid"],

                True,

                TEXT_GREEN

            )


            canvas.blit(

                text,

                (

                    row_rect.x
                    +
                    15 * SCALE,

                    row_rect.y
                    +
                    12 * SCALE

                )

            )


            # Security indicator.

            if network["security"]:

                security = small_font.render(

                    "LOCK",

                    True,

                    DIM_GREEN

                )

            else:

                security = small_font.render(

                    "OPEN",

                    True,

                    DIM_GREEN

                )


            security_rect = security.get_rect(

                right=row_rect.right - 90 * SCALE,

                centery=row_rect.centery

            )


            canvas.blit(

                security,

                security_rect

            )


            draw_signal_bars(

                row_rect.right
                -
                60 * SCALE,

                row_rect.y
                +
                18 * SCALE,

                network["signal"]

            )


    # --------------------------------------------------------
    # RESCAN BUTTON
    # --------------------------------------------------------

    rescan_rect = pygame.Rect(

        panel_x
        +
        20 * SCALE,

        panel_rect.bottom
        -
        60 * SCALE,

        200 * SCALE,

        40 * SCALE

    )


    draw_button(

        rescan_rect,

        "RESCAN"

    )


# ============================================================
# PASSWORD KEYBOARD HELPERS
# ============================================================

def get_active_keyboard_rows():
    if keyboard_mode == "symbols":
        return SYMBOL_KEYBOARD_ROWS
    return LETTER_KEYBOARD_ROWS


def get_keyboard_key_value(key):
    if keyboard_mode == "letters" and key.isalpha():
        return key if keyboard_shifted else key.lower()
    return key


# ============================================================
# DRAW PASSWORD SCREEN
# ============================================================

def draw_password_screen():

    panel_width = 760 * SCALE

    panel_x = (
        INTERNAL_WIDTH
        -
        panel_width
    ) // 2

    panel_y = 135 * SCALE

    panel_height = 690 * SCALE


    panel_rect = pygame.Rect(

        panel_x,

        panel_y,

        panel_width,

        panel_height

    )


    pygame.draw.rect(

        canvas,

        PANEL_BACKGROUND,

        panel_rect,

        border_radius=8 * SCALE

    )


    pygame.draw.rect(

        canvas,

        DIM_GREEN,

        panel_rect,

        width=1 * SCALE,

        border_radius=8 * SCALE

    )


    # --------------------------------------------------------
    # NETWORK NAME
    # --------------------------------------------------------

    title = font.render(

        selected_network["ssid"],

        True,

        TEXT_GREEN

    )


    title_rect = title.get_rect(

        center=(

            panel_rect.centerx,

            panel_y
            +
            35 * SCALE

        )

    )


    canvas.blit(

        title,

        title_rect

    )


    # --------------------------------------------------------
    # PASSWORD FIELD
    # --------------------------------------------------------

    password_rect = pygame.Rect(

        panel_x
        +
        40 * SCALE,

        panel_y
        +
        75 * SCALE,

        panel_width
        -
        80 * SCALE,

        55 * SCALE

    )


    pygame.draw.rect(

        canvas,

        BACKGROUND,

        password_rect,

        border_radius=5 * SCALE

    )


    pygame.draw.rect(

        canvas,

        DIM_GREEN,

        password_rect,

        width=1 * SCALE,

        border_radius=5 * SCALE

    )


    if password_visible:

        displayed_password = password

    else:

        displayed_password = (
            "•" * len(password)
        )


    if not displayed_password:

        displayed_password = (
            "PASSWORD"
        )


    password_text = font.render(

        displayed_password,

        True,

        TEXT_GREEN

    )


    canvas.blit(

        password_text,

        (

            password_rect.x
            +
            15 * SCALE,

            password_rect.y
            +
            15 * SCALE

        )

    )


    # --------------------------------------------------------
    # KEYBOARD
    # --------------------------------------------------------

    keyboard_start_y = (
        panel_y
        +
        155 * SCALE
    )

    key_width = 55 * SCALE
    key_height = 50 * SCALE
    key_gap = 7 * SCALE

    active_rows = get_active_keyboard_rows()

    for row_index, row in enumerate(active_rows):

        total_width = (
            len(row) * key_width
            +
            (len(row) - 1) * key_gap
        )

        start_x = (
            panel_rect.centerx
            -
            total_width // 2
        )

        row_y = (
            keyboard_start_y
            +
            row_index * (key_height + key_gap)
        )

        for key_index, key in enumerate(row):

            key_x = (
                start_x
                +
                key_index * (key_width + key_gap)
            )

            key_rect = pygame.Rect(
                key_x,
                row_y,
                key_width,
                key_height
            )

            draw_keyboard_key(
                key_rect,
                get_keyboard_key_value(key)
            )

    # --------------------------------------------------------
    # SPECIAL KEYS
    # --------------------------------------------------------

    special_y = (
        keyboard_start_y
        +
        len(active_rows) * (key_height + key_gap)
        +
        5 * SCALE
    )

    # DELETE
    backspace_rect = pygame.Rect(
        panel_x + 20 * SCALE,
        special_y,
        150 * SCALE,
        50 * SCALE
    )
    draw_button(backspace_rect, "DELETE")

    # SPACE
    space_rect = pygame.Rect(
        panel_x + 180 * SCALE,
        special_y,
        170 * SCALE,
        50 * SCALE
    )
    draw_button(space_rect, "SPACE")

    # SHIFT / LOWERCASE
    shift_rect = pygame.Rect(
        panel_x + 360 * SCALE,
        special_y,
        120 * SCALE,
        50 * SCALE
    )
    draw_button(
        shift_rect,
        "UPPER" if keyboard_shifted else "LOWER"
    )

    # LETTERS / SYMBOLS
    mode_rect = pygame.Rect(
        panel_x + 490 * SCALE,
        special_y,
        105 * SCALE,
        50 * SCALE
    )
    draw_button(
        mode_rect,
        "123" if keyboard_mode == "letters" else "ABC"
    )

    # SHOW / HIDE PASSWORD
    show_rect = pygame.Rect(
        panel_x + 605 * SCALE,
        special_y,
        135 * SCALE,
        50 * SCALE
    )
    draw_button(
        show_rect,
        "HIDE" if password_visible else "SHOW"
    )

    # --------------------------------------------------------
    # BOTTOM BUTTONS
    # --------------------------------------------------------

    bottom_y = (
        panel_rect.bottom
        -
        70 * SCALE
    )


    cancel_rect = pygame.Rect(

        panel_x
        +
        40 * SCALE,

        bottom_y,

        200 * SCALE,

        50 * SCALE

    )


    connect_rect = pygame.Rect(

        panel_rect.right
        -
        240 * SCALE,

        bottom_y,

        200 * SCALE,

        50 * SCALE

    )


    draw_button(

        cancel_rect,

        "BACK"

    )


    draw_button(

        connect_rect,

        "CONNECT"

    )


    # --------------------------------------------------------
    # STATUS / ERROR
    # --------------------------------------------------------

    if wifi_error:

        error_text = small_font.render(

            wifi_error,

            True,

            RED

        )

        error_rect = error_text.get_rect(

            center=(

                panel_rect.centerx,

                bottom_y
                -
                18 * SCALE

            )

        )

        canvas.blit(

            error_text,

            error_rect

        )


    elif wifi_status:

        status_text = small_font.render(

            wifi_status,

            True,

            TEXT_GREEN

        )

        status_rect = status_text.get_rect(

            center=(

                panel_rect.centerx,

                bottom_y
                -
                18 * SCALE

            )

        )

        canvas.blit(

            status_text,

            status_rect

        )


# ============================================================
# DRAW CONNECTING SCREEN
# ============================================================

def draw_connecting_screen():

    panel_width = 700 * SCALE
    panel_height = 430 * SCALE

    panel_x = (
        INTERNAL_WIDTH - panel_width
    ) // 2

    panel_y = (
        INTERNAL_HEIGHT - panel_height
    ) // 2

    panel_rect = pygame.Rect(
        panel_x,
        panel_y,
        panel_width,
        panel_height
    )

    pygame.draw.rect(
        canvas,
        PANEL_BACKGROUND,
        panel_rect,
        border_radius=8 * SCALE
    )

    pygame.draw.rect(
        canvas,
        DIM_GREEN,
        panel_rect,
        width=1 * SCALE,
        border_radius=8 * SCALE
    )

    title = font.render(
        "CONNECTING TO WIFI",
        True,
        TEXT_GREEN
    )

    title_rect = title.get_rect(
        center=(
            panel_rect.centerx,
            panel_y + 70 * SCALE
        )
    )

    canvas.blit(title, title_rect)

    network_name = connection_target or "NETWORK"

    network = small_font.render(
        network_name,
        True,
        DIM_GREEN
    )

    network_rect = network.get_rect(
        center=(
            panel_rect.centerx,
            panel_y + 110 * SCALE
        )
    )

    canvas.blit(network, network_rect)

    # Animated spinner made from four small squares.
    frame = int(time.time() * 8) % 4

    spinner_y = panel_y + 190 * SCALE

    for index in range(4):

        size = 14 * SCALE
        gap = 22 * SCALE

        rect = pygame.Rect(
            panel_rect.centerx
            - (3 * gap) // 2
            + index * gap
            - size // 2,
            spinner_y,
            size,
            size
        )

        colour = (
            BRIGHT_GREEN
            if index == frame
            else DIM_GREEN
        )

        pygame.draw.rect(
            canvas,
            colour,
            rect,
            border_radius=3 * SCALE
        )

    status = wifi_status or "CONNECTING..."

    status_text = small_font.render(
        status,
        True,
        TEXT_GREEN
    )

    status_rect = status_text.get_rect(
        center=(
            panel_rect.centerx,
            panel_y + 250 * SCALE
        )
    )

    canvas.blit(status_text, status_rect)

    message = small_font.render(
        "PLEASE WAIT",
        True,
        DIM_GREEN
    )

    message_rect = message.get_rect(
        center=(
            panel_rect.centerx,
            panel_y + 295 * SCALE
        )
    )

    canvas.blit(message, message_rect)


# ============================================================
# BUTTON HELPERS
# ============================================================

def draw_button(
    rect,
    text
):

    pygame.draw.rect(

        canvas,

        PANEL_BACKGROUND,

        rect,

        border_radius=5 * SCALE

    )


    pygame.draw.rect(

        canvas,

        DIM_GREEN,

        rect,

        width=1 * SCALE,

        border_radius=5 * SCALE

    )


    surface = small_font.render(

        text,

        True,

        TEXT_GREEN

    )


    text_rect = surface.get_rect(

        center=rect.center

    )


    canvas.blit(

        surface,

        text_rect

    )


def draw_keyboard_key(
    rect,
    key
):

    pygame.draw.rect(

        canvas,

        PANEL_BACKGROUND,

        rect,

        border_radius=4 * SCALE

    )


    pygame.draw.rect(

        canvas,

        DIM_GREEN,

        rect,

        width=1 * SCALE,

        border_radius=4 * SCALE

    )


    surface = font.render(

        key,

        True,

        TEXT_GREEN

    )


    text_rect = surface.get_rect(

        center=rect.center

    )


    canvas.blit(

        surface,

        text_rect

    )


# ============================================================
# MOUSE HANDLING
# ============================================================

def handle_network_click(
    mouse_pos
):

    global selected_network
    global password
    global wifi_error
    global keyboard_mode
    global keyboard_shifted

    x, y = mouse_pos


    panel_width = 700 * SCALE

    panel_x = (
        INTERNAL_WIDTH
        -
        panel_width
    ) // 2

    panel_y = 145 * SCALE

    panel_height = 550 * SCALE


    panel_rect = pygame.Rect(

        panel_x,

        panel_y,

        panel_width,

        panel_height

    )


    # RESCAN

    rescan_rect = pygame.Rect(

        panel_x
        +
        20 * SCALE,

        panel_rect.bottom
        -
        60 * SCALE,

        200 * SCALE,

        40 * SCALE

    )


    if rescan_rect.collidepoint(
        x,
        y
    ):

        scan_wifi()

        return


    # Network rows

    row_height = 62 * SCALE

    start_y = (
        panel_y
        +
        55 * SCALE
    )


    for index, network in enumerate(
        wifi_networks[:8]
    ):

        row_y = (
            start_y
            +
            index * row_height
        )


        row_rect = pygame.Rect(

            panel_x
            +
            15 * SCALE,

            row_y,

            panel_width
            -
            30 * SCALE,

            52 * SCALE

        )


        if row_rect.collidepoint(
            x,
            y
        ):

            selected_network = network

            password = ""

            wifi_error = ""

            # Open networks don't need a password.

            if not network["security"]:

                if connect_wifi(
                    network["ssid"],
                    ""
                ):

                    return True

            return True


    return False


# ============================================================
# PASSWORD CLICK HANDLING
# ============================================================

def handle_password_click(
    mouse_pos
):

    global password
    global password_visible
    global selected_network
    global wifi_error
    global keyboard_mode
    global keyboard_shifted

    x, y = mouse_pos

    panel_width = 760 * SCALE
    panel_x = (INTERNAL_WIDTH - panel_width) // 2
    panel_y = 135 * SCALE
    panel_height = 690 * SCALE

    panel_rect = pygame.Rect(
        panel_x, panel_y, panel_width, panel_height
    )

    keyboard_start_y = panel_y + 155 * SCALE
    key_width = 55 * SCALE
    key_height = 50 * SCALE
    key_gap = 7 * SCALE

    active_rows = get_active_keyboard_rows()

    for row_index, row in enumerate(active_rows):

        total_width = (
            len(row) * key_width
            +
            (len(row) - 1) * key_gap
        )

        start_x = panel_rect.centerx - total_width // 2

        row_y = (
            keyboard_start_y
            +
            row_index * (key_height + key_gap)
        )

        for key_index, key in enumerate(row):

            key_rect = pygame.Rect(
                start_x + key_index * (key_width + key_gap),
                row_y,
                key_width,
                key_height
            )

            if key_rect.collidepoint(x, y):
                password += get_keyboard_key_value(key)
                return True

    special_y = (
        keyboard_start_y
        +
        len(active_rows) * (key_height + key_gap)
        +
        5 * SCALE
    )

    # DELETE
    backspace_rect = pygame.Rect(
        panel_x + 20 * SCALE,
        special_y,
        150 * SCALE,
        50 * SCALE
    )

    if backspace_rect.collidepoint(x, y):
        password = password[:-1]
        return True

    # SPACE
    space_rect = pygame.Rect(
        panel_x + 180 * SCALE,
        special_y,
        170 * SCALE,
        50 * SCALE
    )

    if space_rect.collidepoint(x, y):
        password += " "
        return True

    # SHIFT / LOWERCASE
    shift_rect = pygame.Rect(
        panel_x + 360 * SCALE,
        special_y,
        120 * SCALE,
        50 * SCALE
    )

    if shift_rect.collidepoint(x, y):
        keyboard_shifted = not keyboard_shifted
        return True

    # LETTERS / SYMBOLS
    mode_rect = pygame.Rect(
        panel_x + 490 * SCALE,
        special_y,
        105 * SCALE,
        50 * SCALE
    )

    if mode_rect.collidepoint(x, y):
        keyboard_mode = (
            "symbols"
            if keyboard_mode == "letters"
            else "letters"
        )
        return True

    # SHOW / HIDE
    show_rect = pygame.Rect(
        panel_x + 605 * SCALE,
        special_y,
        135 * SCALE,
        50 * SCALE
    )

    if show_rect.collidepoint(x, y):
        password_visible = not password_visible
        return True

    # --------------------------------------------------------
    # BACK
    # --------------------------------------------------------

    bottom_y = panel_rect.bottom - 70 * SCALE

    cancel_rect = pygame.Rect(
        panel_x + 40 * SCALE,
        bottom_y,
        200 * SCALE,
        50 * SCALE
    )

    if cancel_rect.collidepoint(x, y):
        selected_network = None
        password = ""
        wifi_error = ""
        keyboard_mode = "letters"
        keyboard_shifted = True
        return True

    # --------------------------------------------------------
    # CONNECT
    # --------------------------------------------------------

    connect_rect = pygame.Rect(
        panel_rect.right - 240 * SCALE,
        bottom_y,
        200 * SCALE,
        50 * SCALE
    )

    if connect_rect.collidepoint(x, y):
        if not password:
            wifi_error = "ENTER A PASSWORD"
            return True

        connect_wifi(
            selected_network["ssid"],
            password
        )
        return True

    return False


# ============================================================
# MAIN WIFI SETUP
# ============================================================

def ensure_wifi():

    """
    Runs the complete Wi-Fi setup screen.

    Returns:
        True  -> Wi-Fi and internet connected
        False -> setup failed / user exited
    """

    global selected_network
    global password
    global wifi_error
    global wifi_status
    global connection_result
    global connection_target
    global keyboard_mode
    global keyboard_shifted

    # --------------------------------------------------------
    # FIRST CHECK
    # --------------------------------------------------------

    if (
        is_wifi_connected()
        and
        is_internet_connected()
    ):

        print(
            "Wi-Fi and internet already connected."
        )

        return True

    # --------------------------------------------------------
    # Reset connection state from a previous run.
    # --------------------------------------------------------

    selected_network = None
    password = ""
    wifi_error = ""
    wifi_status = ""
    connection_result = None
    connection_target = None
    keyboard_mode = "letters"
    keyboard_shifted = True

    # --------------------------------------------------------
    # INITIAL SCAN
    # --------------------------------------------------------

    scan_wifi()

    running = True

    while running:

        # --------------------------------------------------------
        # CHECK RESULT OF BACKGROUND CONNECTION
        # --------------------------------------------------------

        if connection_result is True:

            # Leave the successful state visible very briefly,
            # without blocking the Pygame event loop.
            wifi_status = "CONNECTED"

            canvas.fill(BACKGROUND)
            draw_header()
            draw_connecting_screen()

            display_surface = pygame.transform.smoothscale(
                canvas,
                (
                    WINDOW_WIDTH,
                    WINDOW_HEIGHT
                )
            )

            screen.blit(
                display_surface,
                (0, 0)
            )

            pygame.display.flip()
            pygame.event.pump()
            time.sleep(0.25)

            connection_result = None
            selected_network = None
            connection_target = None

            return True

        # A failed background connection returns to the password
        # screen so the user can correct the password or go back.
        if (
            connection_result is False
            and
            not connecting
        ):

            connection_result = None

        # --------------------------------------------------------
        # EVENTS
        # --------------------------------------------------------

        for event in pygame.event.get():

            if event.type == pygame.QUIT:

                # Ignore window-close events. ESC on a physical keyboard
                # is the only way to exit the application.
                continue

            # ESC is intentionally the only keyboard exit.
            # Touchscreen input cannot generate this event.
            if event.type == pygame.KEYDOWN:

                if event.key == pygame.K_ESCAPE:
                    pygame.quit()
                    return False

                # A physical keyboard can also be used for Wi-Fi passwords.
                if (
                    selected_network is not None
                    and
                    not connecting
                ):
                    if event.key == pygame.K_BACKSPACE:
                        password = password[:-1]

                    elif event.key == pygame.K_SPACE:
                        password += " "

                    elif event.key == pygame.K_RETURN:
                        if password:
                            connect_wifi(
                                selected_network["ssid"],
                                password
                            )
                        else:
                            wifi_error = "ENTER A PASSWORD"

                    elif event.unicode and event.unicode.isprintable():
                        password += event.unicode

            # While connecting, keep processing QUIT events but do
            # not allow the user to accidentally start another job.
            if connecting:
                continue

            if event.type == pygame.MOUSEBUTTONDOWN:

                if event.button != 1:
                    continue

                mouse_pos = (
                    event.pos[0] * SCALE,
                    event.pos[1] * SCALE
                )

                # ------------------------------------------------
                # PASSWORD SCREEN
                # ------------------------------------------------

                if selected_network is not None:

                    handle_password_click(
                        mouse_pos
                    )

                # ------------------------------------------------
                # NETWORK SCREEN
                # ------------------------------------------------

                else:

                    handle_network_click(
                        mouse_pos
                    )

        # --------------------------------------------------------
        # DRAW
        # --------------------------------------------------------

        canvas.fill(BACKGROUND)

        draw_header()

        if connecting:

            draw_connecting_screen()

        elif selected_network is None:

            draw_network_list()

        else:

            draw_password_screen()

        # --------------------------------------------------------
        # DISPLAY
        # --------------------------------------------------------

        display_surface = pygame.transform.smoothscale(
            canvas,
            (
                WINDOW_WIDTH,
                WINDOW_HEIGHT
            )
        )

        screen.blit(
            display_surface,
            (0, 0)
        )

        pygame.display.flip()

        clock.tick(30)

    return False