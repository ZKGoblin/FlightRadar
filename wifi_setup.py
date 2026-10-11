import pygame
import subprocess
import time
import threading
import urllib.request


# the display resolution is detected automatically.
# this ui is designed specifically to work correctly on
# small 480x320 touchscreen displays.


# do not use the old scale = 2 system here.
# everything is drawn directly at the physical resolution.


BACKGROUND = (5, 10, 7)

RADAR_GREEN = (45, 190, 70)
BRIGHT_GREEN = (80, 255, 100)
DIM_GREEN = (20, 90, 35)
TEXT_GREEN = (100, 230, 120)

PANEL_BACKGROUND = (8, 18, 12)
WHITE = (235, 240, 225)
RED = (220, 70, 70)


pygame.init()

# fullscreen and borderless.
screen = pygame.display.set_mode(
    (0, 0),
    pygame.FULLSCREEN | pygame.NOFRAME
)

WINDOW_WIDTH, WINDOW_HEIGHT = screen.get_size()

pygame.display.set_caption(
    "Flight Radar - Wi-Fi Setup"
)

clock = pygame.time.Clock()

# draw directly at the physical display resolution.
canvas = pygame.Surface(
    (WINDOW_WIDTH, WINDOW_HEIGHT)
)


# these sizes are deliberately small enough for 480x320.

# dejavu sans is used instead of the old large consolas
# settings because it is normally available on raspberry pi
# systems and gives better compact rendering.

FONT_PATH = (
    "/usr/share/fonts/truetype/dejavu/"
    "DejaVuSans.ttf"
)

FONT_BOLD_PATH = (
    "/usr/share/fonts/truetype/dejavu/"
    "DejaVuSans-Bold.ttf"
)

font = pygame.font.Font(
    FONT_PATH,
    15
)

small_font = pygame.font.Font(
    FONT_PATH,
    11
)

tiny_font = pygame.font.Font(
    FONT_PATH,
    9
)

large_font = pygame.font.Font(
    FONT_BOLD_PATH,
    18
)

keyboard_font = pygame.font.Font(
    FONT_PATH,
    13
)


wifi_networks = []

selected_network = None

password = ""

password_visible = False

wifi_status = ""

wifi_error = ""

scanning = False

connecting = False

connection_thread = None

connection_result = None

connection_target = None


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


SYMBOL_KEYBOARD_ROWS = [
    [
        "!", "@", "#", "$", "%",
        "^", "&", "*", "(", ")"
    ],
    [
        "-", "_", "=", "+", "[",
        "]", "{", "}", "\\", "`"
    ],
    [
        ";", ":", "'", '"', ",",
        ".", "<", ">", "/", "?"
    ],
    [
        "|", "~", "\\", "€", "£"
    ]
]


keyboard_mode = "letters"

keyboard_shifted = True


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

            device_type = parts[1]
            state = parts[2]

            if (
                device_type == "wifi"
                and state == "connected"
            ):
                return True

        return False

    except Exception as error:
        print("Wi-Fi status error:", error)
        return False


def is_internet_connected():

    try:

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

        if state in (
            "limited",
            "none"
        ):
            return False

    except Exception as error:

        print(
            "Internet status error:",
            error
        )

    # fallback internet test.
    try:

        request = urllib.request.Request(
            "https://www.google.com/generate_204",
            method="HEAD"
        )

        with urllib.request.urlopen(
            request,
            timeout=3
        ) as response:

            return response.status in (
                200,
                204
            )

    except Exception:

        return False


def scan_wifi():

    global wifi_networks
    global scanning
    global wifi_error

    scanning = True

    wifi_error = ""

    try:

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

        time.sleep(1)

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
                "UNABLE TO SCAN FOR WIFI"
            )

            wifi_networks = []

            return

        networks = {}

        for line in result.stdout.splitlines():

            if not line.strip():
                continue

            parts = line.split(":")

            if len(parts) < 3:
                continue

            ssid = parts[0]

            signal = parts[1]

            security = ":".join(
                parts[2:]
            )

            if not ssid:
                continue

            try:

                signal_value = int(
                    signal
                )

            except ValueError:

                signal_value = 0

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
            "WIFI SCANNING FAILED"
        )

        wifi_networks = []

    finally:

        scanning = False


def _connect_wifi_worker(ssid, password):
    global connecting, wifi_status, wifi_error, connection_result

    try:
        print()
        print("========================================")
        print("STARTING WIFI CONNECTION")
        print("SSID:", repr(ssid))
        print("PASSWORD LENGTH:", len(password))
        print("========================================")

        wifi_status = "CONNECTING..."
        wifi_error = ""
        connection_result = None


        device_result = subprocess.run(
            [
                "nmcli",
                "-t",
                "-f",
                "DEVICE,TYPE",
                "device"
            ],
            capture_output=True,
            text=True,
            timeout=5
        )

        wifi_device = None

        for line in device_result.stdout.splitlines():
            parts = line.split(":")

            if len(parts) >= 2:
                device_name = parts[0]
                device_type = parts[1]

                if device_type == "wifi":
                    wifi_device = device_name
                    break

        if not wifi_device:
            print("ERROR: No Wi-Fi device found.")
            print("NMCLI DEVICE OUTPUT:")
            print(device_result.stdout)

            wifi_error = "NO WIFI DEVICE FOUND"
            connection_result = False
            return

        print("Wi-Fi device:", wifi_device)


        print("Removing old profiles for this SSID...")

        connection_result_old = subprocess.run(
            [
                "nmcli",
                "-t",
                "-f",
                "NAME,UUID,TYPE",
                "connection",
                "show"
            ],
            capture_output=True,
            text=True,
            timeout=5
        )

        for line in connection_result_old.stdout.splitlines():

            if not line.strip():
                continue

            parts = line.split(":")

            if len(parts) < 3:
                continue

            profile_name = parts[0]
            profile_uuid = parts[1]
            profile_type = parts[2]

            if (
                profile_type == "802-11-wireless"
                and profile_name == ssid
            ):
                print("Deleting old profile:", profile_name)
                print("UUID:", profile_uuid)

                delete_result = subprocess.run(
                    [
                        "nmcli",
                        "connection",
                        "delete",
                        "uuid",
                        profile_uuid
                    ],
                    capture_output=True,
                    text=True,
                    timeout=10
                )

                print(
                    "DELETE RETURN CODE:",
                    delete_result.returncode
                )

                print(
                    "DELETE STDERR:",
                    delete_result.stderr.strip()
                )


        wifi_status = "CREATING WIFI PROFILE..."

        # use a unique profile name so networkmanager cannot
        # accidentally select an old profile.
        profile_name = "FlightRadar-WiFi"

        # remove an old flightradar profile if one exists.
        old_profile = subprocess.run(
            [
                "nmcli",
                "connection",
                "show",
                profile_name
            ],
            capture_output=True,
            text=True,
            timeout=5
        )

        if old_profile.returncode == 0:
            print("Deleting old FlightRadar Wi-Fi profile.")

            subprocess.run(
                [
                    "nmcli",
                    "connection",
                    "delete",
                    profile_name
                ],
                capture_output=True,
                text=True,
                timeout=10
            )

        print("Creating new Wi-Fi profile...")

        add_result = subprocess.run(
            [
                "nmcli",
                "connection",
                "add",
                "type",
                "wifi",
                "ifname",
                wifi_device,
                "con-name",
                profile_name,
                "ssid",
                ssid
            ],
            capture_output=True,
            text=True,
            timeout=10
        )

        print("ADD RETURN CODE:", add_result.returncode)
        print("ADD STDOUT:", add_result.stdout.strip())
        print("ADD STDERR:", add_result.stderr.strip())

        if add_result.returncode != 0:
            wifi_error = "UNABLE TO CREATE WIFI PROFILE"
            connection_result = False
            return


        print("Configuring WPA-PSK security...")

        modify_result = subprocess.run(
            [
                "nmcli",
                "connection",
                "modify",
                profile_name,
                "802-11-wireless-security.key-mgmt",
                "wpa-psk",
                "802-11-wireless-security.psk",
                password
            ],
            capture_output=True,
            text=True,
            timeout=10
        )

        print(
            "MODIFY RETURN CODE:",
            modify_result.returncode
        )

        print(
            "MODIFY STDOUT:",
            modify_result.stdout.strip()
        )

        print(
            "MODIFY STDERR:",
            modify_result.stderr.strip()
        )

        if modify_result.returncode != 0:
            wifi_error = "UNABLE TO CONFIGURE WIFI"
            connection_result = False
            return


        wifi_status = "CONNECTING..."

        print("Activating new Wi-Fi profile...")

        up_result = subprocess.run(
            [
                "nmcli",
                "connection",
                "up",
                profile_name
            ],
            capture_output=True,
            text=True,
            timeout=35
        )

        print("UP RETURN CODE:", up_result.returncode)
        print("UP STDOUT:", up_result.stdout.strip())
        print("UP STDERR:", up_result.stderr.strip())

        if up_result.returncode != 0:

            error_text = (
                up_result.stderr.strip()
                or up_result.stdout.strip()
            )

            if (
                "Secrets were required" in error_text
                or "incorrect password" in error_text.lower()
            ):
                wifi_error = "INCORRECT PASSWORD"

            elif "No network with SSID" in error_text:
                wifi_error = "NETWORK NO LONGER AVAILABLE"

            elif (
                "No suitable device found" in error_text
                or "not available" in error_text.lower()
            ):
                wifi_error = "WIFI DEVICE NOT READY"

            else:
                print("FULL NMCLI ERROR:", error_text)
                wifi_error = "UNABLE TO CONNECT"

            connection_result = False
            return


        wifi_status = "CHECKING WIFI..."

        print("Wi-Fi profile activated. Checking connection...")

        for _ in range(20):

            if is_wifi_connected():

                print("Wi-Fi is connected.")

                wifi_status = "CHECKING INTERNET..."

                if is_internet_connected():

                    print("Internet connection confirmed.")
                    print("WIFI AND INTERNET CONNECTED.")
                    print("========================================")
                    print()

                    wifi_status = "CONNECTED"
                    connection_result = True

                    return

            time.sleep(0.5)


        print("Wi-Fi connected but internet could not be confirmed.")

        wifi_error = "WIFI CONNECTED - NO INTERNET"
        connection_result = False

    except subprocess.TimeoutExpired:
        print("NMCLI connection timed out.")

        wifi_error = "CONNECTION TIMED OUT"
        connection_result = False

    except Exception as error:
        print("Wi-Fi connection error:", error)

        wifi_error = "CONNECTION FAILED"
        connection_result = False

    finally:
        connecting = False

def connect_wifi(
    ssid,
    password
):

    global connecting
    global wifi_status
    global wifi_error
    global connection_thread
    global connection_result
    global connection_target

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


def draw_signal_bars(
    x,
    y,
    signal
):

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

    for i in range(4):

        height = 4 + i * 3

        width = 3

        bar_x = x + i * 5

        bar_y = (
            y
            +
            14
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


def draw_header():

    title = large_font.render(
        "FLIGHT RADAR",
        True,
        TEXT_GREEN
    )

    title_rect = title.get_rect(
        center=(
            WINDOW_WIDTH // 2,
            16
        )
    )

    canvas.blit(
        title,
        title_rect
    )

    subtitle = tiny_font.render(
        "WIFI SETUP",
        True,
        DIM_GREEN
    )

    subtitle_rect = subtitle.get_rect(
        center=(
            WINDOW_WIDTH // 2,
            35
        )
    )

    canvas.blit(
        subtitle,
        subtitle_rect
    )


def draw_button(
    rect,
    text,
    font_to_use=small_font
):

    pygame.draw.rect(
        canvas,
        PANEL_BACKGROUND,
        rect,
        border_radius=4
    )

    pygame.draw.rect(
        canvas,
        DIM_GREEN,
        rect,
        width=1,
        border_radius=4
    )

    surface = font_to_use.render(
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
        border_radius=3
    )

    pygame.draw.rect(
        canvas,
        DIM_GREEN,
        rect,
        width=1,
        border_radius=3
    )

    surface = keyboard_font.render(
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


def get_active_keyboard_rows():

    if keyboard_mode == "symbols":
        return SYMBOL_KEYBOARD_ROWS

    return LETTER_KEYBOARD_ROWS


def get_keyboard_key_value(key):

    if (
        keyboard_mode == "letters"
        and
        key.isalpha()
    ):

        if keyboard_shifted:
            return key

        return key.lower()

    return key


def draw_network_list():

    panel_x = 10

    panel_y = 48

    panel_width = WINDOW_WIDTH - 20

    panel_height = WINDOW_HEIGHT - 58

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
        border_radius=6
    )

    pygame.draw.rect(
        canvas,
        DIM_GREEN,
        panel_rect,
        width=1,
        border_radius=6
    )


    if scanning:

        text = font.render(
            "SCANNING FOR WIFI...",
            True,
            TEXT_GREEN
        )

        rect = text.get_rect(
            center=(
                panel_rect.centerx,
                145
            )
        )

        canvas.blit(
            text,
            rect
        )

        return


    title = tiny_font.render(
        "SELECT A NETWORK",
        True,
        DIM_GREEN
    )

    canvas.blit(
        title,
        (
            panel_x + 12,
            panel_y + 10
        )
    )


    if not wifi_networks:

        text = small_font.render(
            "NO WIFI NETWORKS FOUND",
            True,
            TEXT_GREEN
        )

        rect = text.get_rect(
            center=(
                panel_rect.centerx,
                135
            )
        )

        canvas.blit(
            text,
            rect

        )

    else:

        # keep enough room at the bottom for rescan.
        max_networks = 5

        row_height = 34

        start_y = panel_y + 32

        for index, network in enumerate(
            wifi_networks[:max_networks]
        ):

            row_y = (
                start_y
                +
                index * row_height
            )

            row_rect = pygame.Rect(
                panel_x + 7,
                row_y,
                panel_width - 14,
                29
            )

            # selected network.
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
                    border_radius=4
                )


            ssid_text = small_font.render(
                network["ssid"],
                True,
                TEXT_GREEN
            )

            # prevent extremely long names
            # from running into the signal bars.
            available_width = 265

            if ssid_text.get_width() > available_width:

                display_name = network["ssid"]

                while (
                    len(display_name) > 3
                    and
                    small_font.size(
                        display_name + "..."
                    )[0]
                    >
                    available_width
                ):

                    display_name = (
                        display_name[:-1]
                    )

                display_name += "..."

                ssid_text = small_font.render(
                    display_name,
                    True,
                    TEXT_GREEN
                )

            canvas.blit(
                ssid_text,
                (
                    row_rect.x + 8,
                    row_rect.y + 7
                )
            )

            # security.
            if network["security"]:

                security_text = tiny_font.render(
                    "LOCK",
                    True,
                    DIM_GREEN
                )

            else:

                security_text = tiny_font.render(
                    "OPEN",
                    True,
                    DIM_GREEN
                )

            security_rect = security_text.get_rect(
                right=row_rect.right - 38,
                centery=row_rect.centery
            )

            canvas.blit(
                security_text,
                security_rect
            )

            # signal.
            draw_signal_bars(
                row_rect.right - 28,
                row_rect.y + 7,
                network["signal"]
            )


    rescan_rect = pygame.Rect(
        panel_x + 10,
        panel_rect.bottom - 37,
        110,
        27
    )

    draw_button(
        rescan_rect,
        "RESCAN"
    )


    if wifi_error:

        error_text = tiny_font.render(
            wifi_error,
            True,
            RED
        )

        error_rect = error_text.get_rect(
            center=(
                panel_rect.centerx + 55,
                panel_rect.bottom - 23
            )
        )

        canvas.blit(
            error_text,
            error_rect
        )


def draw_password_screen():

    panel_x = 7

    panel_y = 43

    panel_width = WINDOW_WIDTH - 14

    panel_height = WINDOW_HEIGHT - 46

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
        border_radius=6
    )

    pygame.draw.rect(
        canvas,
        DIM_GREEN,
        panel_rect,
        width=1,
        border_radius=6
    )


    network_name = selected_network["ssid"]

    title = small_font.render(
        network_name,
        True,
        TEXT_GREEN
    )

    # truncate long network names.
    if title.get_width() > 250:

        display_name = network_name

        while (
            len(display_name) > 3
            and
            small_font.size(
                display_name + "..."
            )[0]
            >
            250
        ):

            display_name = (
                display_name[:-1]
            )

        display_name += "..."

        title = small_font.render(
            display_name,
            True,
            TEXT_GREEN
        )

    title_rect = title.get_rect(
        center=(
            panel_rect.centerx,
            panel_y + 13
        )
    )

    canvas.blit(
        title,
        title_rect
    )


    password_rect = pygame.Rect(
        panel_x + 20,
        panel_y + 28,
        panel_width - 40,
        27
    )

    pygame.draw.rect(
        canvas,
        BACKGROUND,
        password_rect,
        border_radius=4
    )

    pygame.draw.rect(
        canvas,
        DIM_GREEN,
        password_rect,
        width=1,
        border_radius=4
    )

    if password_visible:

        displayed_password = password

    else:

        displayed_password = (
            "•" * len(password)
        )

    if not displayed_password:

        displayed_password = "PASSWORD"

    password_text = small_font.render(
        displayed_password,
        True,
        TEXT_GREEN
    )

    # if the password becomes longer than the field,
    # only display the rightmost portion.
    available_width = password_rect.width - 14

    if password_text.get_width() > available_width:

        visible_password = displayed_password

        while (
            len(visible_password) > 1
            and
            small_font.size(
                "..." + visible_password
            )[0]
            >
            available_width
        ):

            visible_password = (
                visible_password[1:]
            )

        displayed_password = (
            "..." + visible_password
        )

        password_text = small_font.render(
            displayed_password,
            True,
            TEXT_GREEN
        )

    canvas.blit(
        password_text,
        (
            password_rect.x + 7,
            password_rect.y + 6
        )
    )


    keyboard_start_y = panel_y + 61

    key_width = 37

    key_height = 25

    key_gap = 3

    active_rows = get_active_keyboard_rows()

    for row_index, row in enumerate(
        active_rows
    ):

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
            row_index
            *
            (
                key_height
                +
                key_gap
            )
        )

        for key_index, key in enumerate(row):

            key_x = (
                start_x
                +
                key_index
                *
                (
                    key_width
                    +
                    key_gap
                )
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


    special_y = (
        keyboard_start_y
        +
        len(active_rows)
        *
        (
            key_height
            +
            key_gap
        )
        +
        4
    )


    backspace_rect = pygame.Rect(
        panel_x + 10,
        special_y,
        72,
        28
    )

    draw_button(
        backspace_rect,
        "DELETE",
        tiny_font
    )


    space_rect = pygame.Rect(
        panel_x + 86,
        special_y,
        92,
        28
    )

    draw_button(
        space_rect,
        "SPACE",
        tiny_font
    )


    shift_rect = pygame.Rect(
        panel_x + 182,
        special_y,
        70,
        28
    )

    draw_button(
        shift_rect,
        (
            "UPPER"
            if keyboard_shifted
            else
            "LOWER"
        ),
        tiny_font
    )


    mode_rect = pygame.Rect(
        panel_x + 256,
        special_y,
        65,
        28
    )

    draw_button(
        mode_rect,
        (
            "123"
            if keyboard_mode == "letters"
            else
            "ABC"
        ),
        tiny_font
    )


    show_rect = pygame.Rect(
        panel_x + 325,
        special_y,
        80,
        28
    )

    draw_button(
        show_rect,
        (
            "HIDE"
            if password_visible
            else
            "SHOW"
        ),
        tiny_font
    )


    status_y = special_y + 34

    if wifi_error:

        error_text = tiny_font.render(
            wifi_error,
            True,
            RED
        )

        error_rect = error_text.get_rect(
            center=(
                panel_rect.centerx,
                status_y
            )
        )

        canvas.blit(
            error_text,
            error_rect
        )

    elif wifi_status:

        status_text = tiny_font.render(
            wifi_status,
            True,
            TEXT_GREEN
        )

        status_rect = status_text.get_rect(
            center=(
                panel_rect.centerx,
                status_y
            )
        )

        canvas.blit(
            status_text,
            status_rect
        )


    bottom_y = WINDOW_HEIGHT - 35

    back_rect = pygame.Rect(
        panel_x + 15,
        bottom_y,
        115,
        29
    )

    connect_rect = pygame.Rect(
        panel_rect.right - 130,
        bottom_y,
        115,
        29
    )

    draw_button(
        back_rect,
        "BACK"
    )

    draw_button(
        connect_rect,
        "CONNECT"
    )


def draw_connecting_screen():

    panel_width = 340

    panel_height = 190

    panel_x = (
        WINDOW_WIDTH
        -
        panel_width
    ) // 2

    panel_y = (
        WINDOW_HEIGHT
        -
        panel_height
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
        border_radius=7
    )

    pygame.draw.rect(
        canvas,
        DIM_GREEN,
        panel_rect,
        width=1,
        border_radius=7
    )


    title = font.render(
        "CONNECTING TO WIFI",
        True,
        TEXT_GREEN
    )

    title_rect = title.get_rect(
        center=(
            panel_rect.centerx,
            panel_y + 35
        )
    )

    canvas.blit(
        title,
        title_rect
    )


    network_name = (
        connection_target
        or
        "NETWORK"
    )

    network = small_font.render(
        network_name,
        True,
        DIM_GREEN
    )

    # truncate long names.
    if network.get_width() > 250:

        display_name = network_name

        while (
            len(display_name) > 3
            and
            small_font.size(
                display_name + "..."
            )[0]
            >
            250
        ):

            display_name = (
                display_name[:-1]
            )

        display_name += "..."

        network = small_font.render(
            display_name,
            True,
            DIM_GREEN
        )

    network_rect = network.get_rect(
        center=(
            panel_rect.centerx,
            panel_y + 65
        )
    )

    canvas.blit(
        network,
        network_rect
    )


    frame = int(
        time.time() * 8
    ) % 4

    spinner_y = panel_y + 100

    size = 11

    gap = 23

    for index in range(4):

        rect = pygame.Rect(
            panel_rect.centerx
            -
            (3 * gap) // 2
            +
            index * gap
            -
            size // 2,
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
            border_radius=2
        )


    status = (
        wifi_status
        or
        "CONNECTING..."
    )

    status_text = tiny_font.render(
        status,
        True,
        TEXT_GREEN
    )

    status_rect = status_text.get_rect(
        center=(
            panel_rect.centerx,
            panel_y + 140
        )
    )

    canvas.blit(
        status_text,
        status_rect
    )

    message = tiny_font.render(
        "PLEASE WAIT",
        True,
        DIM_GREEN
    )

    message_rect = message.get_rect(
        center=(
            panel_rect.centerx,
            panel_y + 163
        )
    )

    canvas.blit(
        message,
        message_rect
    )


def handle_network_click(
    mouse_pos
):

    global selected_network
    global password
    global wifi_error
    global keyboard_mode
    global keyboard_shifted

    x, y = mouse_pos

    panel_x = 10

    panel_y = 48

    panel_width = WINDOW_WIDTH - 20

    panel_height = WINDOW_HEIGHT - 58

    panel_rect = pygame.Rect(
        panel_x,
        panel_y,
        panel_width,
        panel_height
    )


    rescan_rect = pygame.Rect(
        panel_x + 10,
        panel_rect.bottom - 37,
        110,
        27
    )

    if rescan_rect.collidepoint(
        x,
        y
    ):

        scan_wifi()

        return True


    row_height = 34

    start_y = panel_y + 32

    for index, network in enumerate(
        wifi_networks[:5]
    ):

        row_y = (
            start_y
            +
            index * row_height
        )

        row_rect = pygame.Rect(
            panel_x + 7,
            row_y,
            panel_width - 14,
            29
        )

        if row_rect.collidepoint(
            x,
            y
        ):

            selected_network = network

            password = ""

            wifi_error = ""

            keyboard_mode = "letters"

            keyboard_shifted = True

            # open networks connect immediately.
            if not network["security"]:

                connect_wifi(
                    network["ssid"],
                    ""
                )

            return True

    return False


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

    panel_x = 7

    panel_y = 43

    panel_width = WINDOW_WIDTH - 14

    panel_height = WINDOW_HEIGHT - 46

    panel_rect = pygame.Rect(
        panel_x,
        panel_y,
        panel_width,
        panel_height
    )


    keyboard_start_y = panel_y + 61

    key_width = 37

    key_height = 25

    key_gap = 3

    active_rows = get_active_keyboard_rows()

    for row_index, row in enumerate(
        active_rows
    ):

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
            row_index
            *
            (
                key_height
                +
                key_gap
            )
        )

        for key_index, key in enumerate(row):

            key_rect = pygame.Rect(
                start_x
                +
                key_index
                *
                (
                    key_width
                    +
                    key_gap
                ),
                row_y,
                key_width,
                key_height
            )

            if key_rect.collidepoint(
                x,
                y
            ):

                password += (
                    get_keyboard_key_value(
                        key
                    )
                )

                wifi_error = ""

                return True


    special_y = (
        keyboard_start_y
        +
        len(active_rows)
        *
        (
            key_height
            +
            key_gap
        )
        +
        4
    )


    backspace_rect = pygame.Rect(
        panel_x + 10,
        special_y,
        72,
        28
    )

    if backspace_rect.collidepoint(
        x,
        y
    ):

        password = password[:-1]

        return True


    space_rect = pygame.Rect(
        panel_x + 86,
        special_y,
        92,
        28
    )

    if space_rect.collidepoint(
        x,
        y
    ):

        password += " "

        return True


    shift_rect = pygame.Rect(
        panel_x + 182,
        special_y,
        70,
        28
    )

    if shift_rect.collidepoint(
        x,
        y
    ):

        keyboard_shifted = (
            not keyboard_shifted
        )

        return True


    mode_rect = pygame.Rect(
        panel_x + 256,
        special_y,
        65,
        28
    )

    if mode_rect.collidepoint(
        x,
        y
    ):

        if keyboard_mode == "letters":

            keyboard_mode = "symbols"

        else:

            keyboard_mode = "letters"

        return True


    show_rect = pygame.Rect(
        panel_x + 325,
        special_y,
        80,
        28
    )

    if show_rect.collidepoint(
        x,
        y
    ):

        password_visible = (
            not password_visible
        )

        return True


    bottom_y = WINDOW_HEIGHT - 35

    back_rect = pygame.Rect(
        panel_x + 15,
        bottom_y,
        115,
        29
    )

    if back_rect.collidepoint(
        x,
        y
    ):

        selected_network = None

        password = ""

        wifi_error = ""

        keyboard_mode = "letters"

        keyboard_shifted = True

        return True


    connect_rect = pygame.Rect(
        panel_rect.right - 130,
        bottom_y,
        115,
        29
    )

    if connect_rect.collidepoint(
        x,
        y
    ):

        if not password:

            wifi_error = (
                "ENTER A PASSWORD"
            )

            return True

        connect_wifi(
            selected_network["ssid"],
            password
        )

        return True

    return False


def ensure_wifi():

    """
    Runs the complete Wi-Fi setup screen.

    Returns:

        True:
            Wi-Fi and internet are connected.

        False:
            User exited using ESC.
    """

    global selected_network
    global password
    global wifi_error
    global wifi_status
    global connection_result
    global connection_target
    global keyboard_mode
    global keyboard_shifted
    global password_visible


    if (
        is_wifi_connected()
        and
        is_internet_connected()
    ):

        print(
            "Wi-Fi and internet already connected."
        )

        return True


    selected_network = None

    password = ""

    password_visible = False

    wifi_error = ""

    wifi_status = ""

    connection_result = None

    connection_target = None

    keyboard_mode = "letters"

    keyboard_shifted = True


    scan_wifi()


    while True:


        if connection_result is True:

            wifi_status = "CONNECTED"

            canvas.fill(
                BACKGROUND
            )

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

            time.sleep(0.4)

            connection_result = None

            selected_network = None

            connection_target = None

            return True


        if (
            connection_result is False
            and
            not connecting
        ):

            connection_result = None

            # keep the selected network so the user
            # can enter another password.


        for event in pygame.event.get():

            # ignore normal window close events.

            # esc remains the only exit method.
            if event.type == pygame.QUIT:

                continue


            if event.type == pygame.KEYDOWN:

                if event.key == pygame.K_ESCAPE:

                    pygame.quit()

                    return False

                # physical keyboard password input.
                if (
                    selected_network is not None
                    and
                    not connecting
                ):

                    if (
                        event.key
                        ==
                        pygame.K_BACKSPACE
                    ):

                        password = (
                            password[:-1]
                        )

                    elif (
                        event.key
                        ==
                        pygame.K_SPACE
                    ):

                        password += " "

                    elif (
                        event.key
                        ==
                        pygame.K_RETURN
                    ):

                        if password:

                            connect_wifi(
                                selected_network[
                                    "ssid"
                                ],
                                password
                            )

                        else:

                            wifi_error = (
                                "ENTER A PASSWORD"
                            )

                    elif (
                        event.unicode
                        and
                        event.unicode.isprintable()
                    ):

                        password += (
                            event.unicode
                        )


            if connecting:

                continue


            if event.type == pygame.MOUSEBUTTONDOWN:

                if event.button != 1:

                    continue

                # because the ui is drawn directly at the
                # physical resolution, no scale conversion
                # is required here.
                mouse_pos = event.pos

                if selected_network is not None:

                    handle_password_click(
                        mouse_pos
                    )

                else:

                    handle_network_click(
                        mouse_pos
                    )


        canvas.fill(
            BACKGROUND
        )

        draw_header()

        if connecting:

            draw_connecting_screen()

        elif selected_network is None:

            draw_network_list()

        else:

            draw_password_screen()


        screen.blit(
            canvas,
            (0, 0)
        )

        pygame.display.flip()

        clock.tick(30)

    return False
