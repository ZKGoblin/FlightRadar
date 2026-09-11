import os
os.environ["SDL_AUDIODRIVER"] = "dummy"

import pygame
import math
import requests
import threading
import time

from wifi_setup import ensure_wifi, is_internet_connected

from aircraft_details import fetch_details_async, get_cached_details

if not ensure_wifi():
    print("Wi-Fi setup failed.")
    raise SystemExit


# Wi-Fi / internet monitoring.
# If the internet disappears while the radar is running, the
# program returns to the same touch-screen Wi-Fi setup.
WIFI_CHECK_INTERVAL = 5
last_wifi_check = time.time()


# CONFIGURATION

WINDOW_WIDTH = 480
WINDOW_HEIGHT = 320

# Render at higher resolution and downsample.
SCALE = 2

INTERNAL_WIDTH = WINDOW_WIDTH * SCALE
INTERNAL_HEIGHT = WINDOW_HEIGHT * SCALE

# Radar location

RADAR_LOCATIONS = {
    "EDINBURGH AIRPORT": {"lat": 55.947269, "lon": -3.360838},
    "NEWCASTLE AIRPORT": {"lat": 55.039444, "lon": -1.693096},
    "HEATHROW AIRPORT": {"lat": 51.469164, "lon": -0.45537},
    "MANCHESTER AIRPORT": {"lat": 53.355842, "lon": -2.277450},
}

RADAR_NAME = "EDINBURGH AIRPORT"

RADAR_LAT = RADAR_LOCATIONS[RADAR_NAME]["lat"]
RADAR_LON = RADAR_LOCATIONS[RADAR_NAME]["lon"]

RADAR_RANGE_KM = 50

# OpenSky

UPDATE_INTERVAL = 15

# How far back we look when determining whether an
# aircraft is genuinely climbing or descending.
ALTITUDE_TREND_SECONDS = 30

# Minimum altitude change required to show an indicator.
ALTITUDE_TREND_THRESHOLD = 150


# COLOURS

BACKGROUND = (5, 10, 7)

RADAR_GREEN = (45, 190, 70)
BRIGHT_GREEN = (80, 255, 100)
DIM_GREEN = (20, 90, 35)

TEXT_GREEN = (100, 230, 120)

AIRCRAFT_COLOUR = (235, 240, 225)
AIRCRAFT_HIGHLIGHT = (255, 255, 255)

PANEL_BACKGROUND = (8, 18, 12)

# ============================================================
# RADAR COLOUR SETTINGS
# ============================================================

current_radar_colour = (45, 190, 70)


def update_radar_colours():

    global RADAR_GREEN
    global BRIGHT_GREEN
    global DIM_GREEN
    global TEXT_GREEN

    r, g, b = current_radar_colour

    RADAR_GREEN = (r, g, b)

    BRIGHT_GREEN = (
        int(r + (255 - r) * 0.45),
        int(g + (255 - g) * 0.45),
        int(b + (255 - b) * 0.45),
    )

    DIM_GREEN = (int(r * 0.42), int(g * 0.42), int(b * 0.42))

    TEXT_GREEN = (
        int(r + (255 - r) * 0.30),
        int(g + (255 - g) * 0.30),
        int(b + (255 - b) * 0.30),
    )


update_radar_colours()

menu_open = False

# PYGAME

pygame.init()

window = pygame.display.set_mode((0, 0), pygame.FULLSCREEN | pygame.NOFRAME)

pygame.display.set_caption("Flight Radar")

clock = pygame.time.Clock()

canvas = pygame.Surface((INTERNAL_WIDTH, INTERNAL_HEIGHT))


# FONTS

# All text continues to use the same DejaVu Sans font.
# Change these values to tune the appearance on the 480x320 display.
FONT_PATH = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

FONT_SMALL_SIZE = 22
FONT_MEDIUM_SIZE = 25
FONT_LARGE_SIZE = 30

DETAILS_SMALL_SIZE = 25
DETAILS_MEDIUM_SIZE = 30
DETAILS_LARGE_SIZE = 35

small_font = pygame.font.Font(FONT_PATH, FONT_SMALL_SIZE)
font = pygame.font.Font(FONT_PATH, FONT_MEDIUM_SIZE)
large_font = pygame.font.Font(FONT_PATH, FONT_LARGE_SIZE)

DETAIL_FONT_SMALL = pygame.font.Font(FONT_PATH, DETAILS_SMALL_SIZE)
DETAIL_FONT_MEDIUM = pygame.font.Font(FONT_PATH, DETAILS_MEDIUM_SIZE)
DETAIL_FONT_LARGE = pygame.font.Font(FONT_PATH, DETAILS_LARGE_SIZE)

DETAILS_SMALL_FONT = DETAIL_FONT_SMALL
DETAILS_MEDIUM_FONT = DETAIL_FONT_MEDIUM
DETAILS_LARGE_FONT = DETAIL_FONT_LARGE


# RADAR

# ============================================================
# RADAR POSITION
# ============================================================

RADAR_RADIUS = 125 * SCALE

RADAR_CENTER = (INTERNAL_WIDTH // 2, INTERNAL_HEIGHT // 2)

RADAR_CENTER_NORMAL_X = INTERNAL_WIDTH // 2

RADAR_CENTER_SELECTED_X = int(INTERNAL_WIDTH * 0.68)

RADAR_CENTER_Y = int(INTERNAL_HEIGHT * 0.50)


def update_radar_layout():

    global RADAR_CENTER

    # Determine where the radar should be.

    if selected_aircraft is not None:

        new_center = (RADAR_CENTER_SELECTED_X, RADAR_CENTER_Y)

    else:

        new_center = (RADAR_CENTER_NORMAL_X, RADAR_CENTER_Y)

    # If the radar hasn't moved, do nothing.

    if new_center == RADAR_CENTER:

        return

    # Move the radar immediately.

    RADAR_CENTER = new_center

    # Snap all aircraft to their new positions.

    with aircraft_lock:

        for plane in aircraft.values():

            distance = plane.get("distance")
            bearing = plane.get("bearing")

            if distance is None or bearing is None:

                continue

            # Calculate the aircraft's position using
            # the newly positioned radar centre.

            new_x, new_y = radar_position(distance, bearing)

            # Update target position.

            plane["target_x"] = new_x
            plane["target_y"] = new_y

            # Immediately move the aircraft there.

            plane["display_x"] = new_x
            plane["display_y"] = new_y

            # Make this the starting point for the
            # next normal OpenSky animation.

            plane["start_x"] = new_x
            plane["start_y"] = new_y

            # Preserve the current heading.

            plane["start_heading"] = plane.get(
                "display_heading", plane.get("target_heading", 0)
            )


# AIRCRAFT DATA

aircraft = {}

aircraft_lock = threading.Lock()

api_status = "STARTING"

last_api_update = 0

selected_aircraft = None

selected_details = {}

# Aircraft details panel scrolling state
details_scroll_offset = 0
details_touch_start_y = None
details_last_touch_y = None
details_dragging = False
details_drag_moved = False

DETAILS_REFRESH_INTERVAL = 10

last_detail_refresh = 0

selected_aircraft_details = None

aircraft_details_loading = False

# DISTANCE


def haversine_distance(lat1, lon1, lat2, lon2):

    earth_radius = 6371.0

    lat1 = math.radians(lat1)
    lat2 = math.radians(lat2)

    dlat = lat2 - lat1
    dlon = math.radians(lon2 - lon1)

    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )

    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return earth_radius * c


# BEARING


def calculate_bearing(lat1, lon1, lat2, lon2):

    lat1 = math.radians(lat1)
    lat2 = math.radians(lat2)

    dlon = math.radians(lon2 - lon1)

    x = math.sin(dlon) * math.cos(lat2)

    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(
        dlon
    )

    bearing = math.degrees(math.atan2(x, y))

    return (bearing + 360) % 360


# RADAR POSITION


def radar_position(distance, bearing):

    cx, cy = RADAR_CENTER

    angle = math.radians(bearing)

    pixel_distance = distance / RADAR_RANGE_KM * RADAR_RADIUS

    x = cx + math.sin(angle) * pixel_distance

    y = cy - math.cos(angle) * pixel_distance

    return (x, y)


# ANGLE INTERPOLATION


def interpolate_angle(start, end, amount):

    difference = ((end - start + 180) % 360) - 180

    return (start + difference * amount) % 360


# OPEN SKY


def fetch_aircraft():

    global aircraft
    global api_status
    global last_api_update

    try:

        # Bounding box

        lat_delta = (RADAR_RANGE_KM / 111) * 1.15

        lon_delta = (RADAR_RANGE_KM / (111 * math.cos(math.radians(RADAR_LAT)))) * 1.15

        lamin = RADAR_LAT - lat_delta
        lamax = RADAR_LAT + lat_delta

        lomin = RADAR_LON - lon_delta
        lomax = RADAR_LON + lon_delta

        # API request

        url = "https://opensky-network.org/api/states/all"

        params = {"lamin": lamin, "lamax": lamax, "lomin": lomin, "lomax": lomax}

        response = requests.get(url, params=params, timeout=8)

	if response.status_code == 429:
		retry_after = response.headers.get(
			"X-Rate-Limit-Retry-After-Seconds",
			"60"
		)

		print(
			f"OpenSky rate limited(429). "
			f"Retry after {retry_after} seconds."
		)

		api_status = "RATE_LIMITED"

		return

        response.raise_for_status()

        data = response.json()

        states = data.get("states", [])

        # The time at which these states were received.

        update_time = time.time()

        new_aircraft = {}

        # Process states

        for state in states:

            if len(state) < 17:
                continue

            icao24 = state[0]

            callsign = state[1]

            latitude = state[6]

            longitude = state[5]

            altitude = state[7]

            on_ground = state[8]

            velocity = state[9]

            heading = state[10]

            vertical_rate = state[11]

            squawk = state[14]

            # Need a position.

            if latitude is None or longitude is None:
                continue

            # Ignore aircraft on the ground.

            if on_ground:
                continue

            # Distance

            distance = haversine_distance(RADAR_LAT, RADAR_LON, latitude, longitude)

            if distance > RADAR_RANGE_KM:
                continue

            # Bearing

            bearing = calculate_bearing(RADAR_LAT, RADAR_LON, latitude, longitude)

            # Callsign

            if callsign:

                callsign = callsign.strip()

            else:

                callsign = icao24.upper()

            # Units

            speed_knots = None

            if velocity is not None:

                speed_knots = velocity * 1.94384

            altitude_ft = None

            if altitude is not None:

                altitude_ft = altitude * 3.28084

            vertical_rate_fpm = None

            if vertical_rate is not None:

                vertical_rate_fpm = vertical_rate * 196.8504

            # Target radar position

            target_x, target_y = radar_position(distance, bearing)

            # Create aircraft record

            new_aircraft[icao24] = {
                "icao24": icao24,
                "callsign": callsign,
                "latitude": latitude,
                "longitude": longitude,
                "position_history": [],
                "altitude": altitude_ft,
                "speed": speed_knots,
                "heading": heading,
                "vertical_rate": vertical_rate_fpm,
                "squawk": squawk,
                "distance": distance,
                "bearing": bearing,
                "target_x": target_x,
                "target_y": target_y,
                "target_heading": (heading if heading is not None else bearing),
                "update_time": update_time,
                # Stores altitude readings for the
                # rolling climb/descent calculation.
                "altitude_history": [],
            }

        for plane in new_aircraft.values():
            fetch_details_async(plane["icao24"], plane["callsign"], force=False)

        # Update aircraft positions

        with aircraft_lock:

            for icao24, new_plane in new_aircraft.items():

                # Existing aircraft

                if icao24 in aircraft:

                    old_plane = aircraft[icao24]

                    old_position_history = old_plane.get("position_history", [])

                    new_plane["position_history"] = list(old_position_history)

                    # Keep the aircraft where it visually is
                    # when a new API update arrives.

                    old_x = old_plane.get("display_x", new_plane["target_x"])

                    old_y = old_plane.get("display_y", new_plane["target_y"])

                    old_heading = old_plane.get(
                        "display_heading", new_plane["target_heading"]
                    )

                    new_plane["start_x"] = old_x
                    new_plane["start_y"] = old_y

                    new_plane["start_heading"] = old_heading

                    # ------------------------------------------------
                    # ALTITUDE HISTORY
                    # ------------------------------------------------

                    old_history = old_plane.get("altitude_history", [])

                    # Make a copy so the old aircraft record
                    # isn't modified while we're processing it.

                    altitude_history = list(old_history)

                    # Add the newest altitude reading.

                    if new_plane["altitude"] is not None:

                        altitude_history.append((update_time, new_plane["altitude"]))

                    # Remove readings older than our trend period.

                    cutoff_time = update_time - ALTITUDE_TREND_SECONDS

                    altitude_history = [
                        reading
                        for reading in altitude_history
                        if reading[0] >= cutoff_time
                    ]

                    # Keep the history.

                    new_plane["altitude_history"] = altitude_history

                # New aircraft

                else:

                    new_plane["start_x"] = new_plane["target_x"]

                    new_plane["start_y"] = new_plane["target_y"]

                    new_plane["start_heading"] = new_plane["target_heading"]

                    # Start altitude history.

                    if new_plane["altitude"] is not None:

                        new_plane["altitude_history"] = [
                            (update_time, new_plane["altitude"])
                        ]

                    else:

                        new_plane["altitude_history"] = []

                new_plane["display_x"] = new_plane["start_x"]

                new_plane["display_y"] = new_plane["start_y"]

                new_plane["display_heading"] = new_plane["start_heading"]

                aircraft[icao24] = new_plane

            # Remove aircraft no longer seen

            current_ids = set(new_aircraft.keys())

            old_ids = list(aircraft.keys())

            for icao24 in old_ids:

                if icao24 not in current_ids:

                    del aircraft[icao24]

        last_api_update = update_time

        api_status = "LIVE"

    except requests.exceptions.RequestException as error:

        print("OpenSky request error:", error)

        api_status = "ERROR"

    except Exception as error:
	print("Opensky error", error)
	api_status = "ERROR"

def reposition_aircraft():

    with aircraft_lock:

        for plane in aircraft.values():

            distance = plane.get("distance")

            bearing = plane.get("bearing")

            if distance is None or bearing is None:

                continue

            target_x, target_y = radar_position(distance, bearing)

            plane["target_x"] = target_x

            plane["target_y"] = target_y


# API THREAD


def api_loop():

    while True:

        fetch_aircraft()

        time.sleep(UPDATE_INTERVAL)


api_thread = threading.Thread(target=api_loop, daemon=True)

api_thread.start()


# SMOOTH AIRCRAFT ANIMATION


def update_animation():

    current_time = time.time()

    with aircraft_lock:

        for plane in aircraft.values():

            start_x = plane["start_x"]
            start_y = plane["start_y"]

            target_x = plane["target_x"]
            target_y = plane["target_y"]

            elapsed = current_time - plane["update_time"]

            amount = elapsed / UPDATE_INTERVAL

            # Keep animation between 0 and 1.

            amount = max(0.0, min(1.0, amount))

            smooth_amount = amount * amount * (3 - 2 * amount)

            # Position

            plane["display_x"] = start_x + (target_x - start_x) * smooth_amount

            plane["display_y"] = start_y + (target_y - start_y) * smooth_amount

            # Heading

            plane["display_heading"] = interpolate_angle(
                plane["start_heading"], plane["target_heading"], smooth_amount
            )


# ============================================================
# SELECTED AIRCRAFT DETAIL UPDATER
# ============================================================


def update_selected_details():

    global selected_details
    global last_detail_refresh

    if selected_aircraft is None:

        return

    current_time = time.time()

    # Find the currently selected aircraft.

    with aircraft_lock:

        plane = aircraft.get(selected_aircraft)

    # Aircraft disappeared from radar.

    if plane is None:

        return

    # --------------------------------------------------------
    # REFRESH EXTERNAL INFORMATION
    # --------------------------------------------------------

    if current_time - last_detail_refresh >= DETAILS_REFRESH_INTERVAL:

        last_detail_refresh = current_time

        fetch_details_async(plane["icao24"], plane["callsign"], force=True)

    # --------------------------------------------------------
    # GET LATEST CACHED INFORMATION
    # --------------------------------------------------------

    selected_details = get_cached_details(plane["icao24"])


# RADAR


def draw_radar():

    cx, cy = RADAR_CENTER

    # Outer circle

    pygame.draw.circle(canvas, RADAR_GREEN, RADAR_CENTER, RADAR_RADIUS, 2 * SCALE)

    # Range rings

    for fraction in [0.25, 0.50, 0.75]:

        radius = int(RADAR_RADIUS * fraction)

        pygame.draw.circle(canvas, DIM_GREEN, RADAR_CENTER, radius, SCALE)

    # Horizontal line

    pygame.draw.line(
        canvas, DIM_GREEN, (cx - RADAR_RADIUS, cy), (cx + RADAR_RADIUS, cy), SCALE
    )

    # Vertical line

    pygame.draw.line(
        canvas, DIM_GREEN, (cx, cy - RADAR_RADIUS), (cx, cy + RADAR_RADIUS), SCALE
    )


# COMPASS


def draw_compass():

    cx, cy = RADAR_CENTER

    directions = [("N", 0), ("E", 90), ("S", 180), ("W", 270)]

    for label, bearing in directions:

        angle = math.radians(bearing)

        x = cx + math.sin(angle) * (RADAR_RADIUS + 10 * SCALE)

        y = cy - math.cos(angle) * (RADAR_RADIUS + 10 * SCALE)

        text = large_font.render(label, True, TEXT_GREEN)

        rect = text.get_rect(center=(int(x), int(y)))

        canvas.blit(text, rect)


# AIRCRAFT ICON


def create_aircraft_icon():

    size = 48 * SCALE

    icon = pygame.Surface((size, size), pygame.SRCALPHA)

    c = size // 2

    # Main fuselage

    fuselage = [
        (c, 2 * SCALE),
        (c + 3 * SCALE, 10 * SCALE),
        (c + 3 * SCALE, 28 * SCALE),
        (c + 6 * SCALE, 37 * SCALE),
        (c, 34 * SCALE),
        (c - 6 * SCALE, 37 * SCALE),
        (c - 3 * SCALE, 28 * SCALE),
        (c - 3 * SCALE, 10 * SCALE),
    ]

    pygame.draw.polygon(icon, AIRCRAFT_COLOUR, fuselage)

    # Main wings

    wings = [
        (c - 2 * SCALE, 15 * SCALE),
        (c - 19 * SCALE, 27 * SCALE),
        (c - 18 * SCALE, 30 * SCALE),
        (c - 3 * SCALE, 24 * SCALE),
        (c + 3 * SCALE, 24 * SCALE),
        (c + 18 * SCALE, 30 * SCALE),
        (c + 19 * SCALE, 27 * SCALE),
        (c + 2 * SCALE, 15 * SCALE),
    ]

    pygame.draw.polygon(icon, AIRCRAFT_COLOUR, wings)

    # Tailplane

    tail = [
        (c - 2 * SCALE, 29 * SCALE),
        (c - 11 * SCALE, 36 * SCALE),
        (c - 10 * SCALE, 38 * SCALE),
        (c, 34 * SCALE),
        (c + 10 * SCALE, 38 * SCALE),
        (c + 11 * SCALE, 36 * SCALE),
        (c + 2 * SCALE, 29 * SCALE),
    ]

    pygame.draw.polygon(icon, AIRCRAFT_COLOUR, tail)

    # Cockpit / nose highlight

    pygame.draw.line(
        icon, AIRCRAFT_HIGHLIGHT, (c, 5 * SCALE), (c, 30 * SCALE), 2 * SCALE
    )

    return icon


AIRCRAFT_ICON = create_aircraft_icon()


# ============================================================
# AIRCRAFT DETAILS WINDOW
# ============================================================


# DRAW AIRCRAFT


def draw_aircraft():

    with aircraft_lock:

        planes = list(aircraft.values())

    for plane in planes:

        x = plane["display_x"]
        y = plane["display_y"]

        heading = plane["display_heading"]

        # Rotate aircraft

        rotated = pygame.transform.rotozoom(AIRCRAFT_ICON, -heading, 1.0)

        rect = rotated.get_rect(center=(int(x), int(y)))

        canvas.blit(rotated, rect)

        # ------------------------------------------------
        # LABEL BOX
        # ------------------------------------------------

        # Callsign

        label = font.render(plane["callsign"], True, AIRCRAFT_HIGHLIGHT)

        # Altitude

        altitude = None

        if plane["altitude"] is not None:

            altitude_text = f'{int(plane["altitude"]):,} ft'

            # Determine climb/descent trend.

            indicator = ""

            history = plane.get("altitude_history", [])

            if len(history) >= 2:

                oldest_time, oldest_altitude = history[0]

                newest_time, newest_altitude = history[-1]

                altitude_change = newest_altitude - oldest_altitude

                time_span = newest_time - oldest_time

                # Only use the trend if we have
                # enough time between readings.

                if time_span >= 10:

                    if altitude_change >= ALTITUDE_TREND_THRESHOLD:

                        indicator = " ▲"

                    elif altitude_change <= -ALTITUDE_TREND_THRESHOLD:

                        indicator = " ▼"

            altitude = small_font.render(altitude_text + indicator, True, TEXT_GREEN)

        # Padding inside the box

        padding_x = 6 * SCALE
        padding_y = 4 * SCALE

        # Work out box size

        box_width = label.get_width()

        box_height = label.get_height()

        if altitude is not None:

            box_width = max(box_width, altitude.get_width())

            box_height += altitude.get_height() + 2 * SCALE

        box_width += padding_x * 2

        box_height += padding_y * 2

        # Position remains exactly where the old
        # callsign was positioned.

        box_x = int(x + 18 * SCALE)
        box_y = int(y - 14 * SCALE)

        # Keep labels visible on the smaller 480x320 display.
        box_x = max(2 * SCALE, min(box_x, INTERNAL_WIDTH - box_width - 2 * SCALE))
        box_y = max(2 * SCALE, min(box_y, INTERNAL_HEIGHT - box_height - 2 * SCALE))

        box_rect = pygame.Rect(box_x, box_y, box_width, box_height)

        # Dark background

        pygame.draw.rect(canvas, PANEL_BACKGROUND, box_rect, border_radius=3 * SCALE)

        # Subtle green border

        pygame.draw.rect(
            canvas, DIM_GREEN, box_rect, width=1 * SCALE, border_radius=3 * SCALE
        )

        # Callsign

        canvas.blit(label, (box_x + padding_x, box_y + padding_y))

        # Altitude

        if altitude is not None:

            canvas.blit(
                altitude,
                (box_x + padding_x, box_y + padding_y + label.get_height() + 2 * SCALE),
            )


# RANGE LABELS


def draw_range_labels():

    percentages = [25, 50, 75]

    for percentage in percentages:

        radius = RADAR_RADIUS * percentage / 100

        distance = RADAR_RANGE_KM * percentage / 100

        text = small_font.render(f"{distance:g} km", True, DIM_GREEN)

        canvas.blit(
            text, (RADAR_CENTER[0] + 5 * SCALE, RADAR_CENTER[1] - radius + 5 * SCALE)
        )


# INFORMATION


def draw_information():

    # Location

    location = font.render(RADAR_NAME, True, TEXT_GREEN)

    canvas.blit(location, (70 * SCALE, 18 * SCALE))

    # Range

    range_text = font.render(f"RANGE {RADAR_RANGE_KM} KM", True, TEXT_GREEN)

    range_rect = range_text.get_rect(top=18 * SCALE, right=INTERNAL_WIDTH - 20 * SCALE)

    canvas.blit(range_text, range_rect)

    # Aircraft count

    with aircraft_lock:

        count = len(aircraft)

    count_text = font.render(f"AIRCRAFT {count}", True, TEXT_GREEN)

    canvas.blit(count_text, (20 * SCALE, INTERNAL_HEIGHT - 35 * SCALE))

    # Status

    status = small_font.render(api_status, True, TEXT_GREEN)

    status_rect = status.get_rect(
        bottom=INTERNAL_HEIGHT - 18 * SCALE, right=INTERNAL_WIDTH - 20 * SCALE
    )

    canvas.blit(status, status_rect)


# RADAR CENTRE


def draw_radar_centre():

    pygame.draw.circle(canvas, BRIGHT_GREEN, RADAR_CENTER, 5 * SCALE)


# COLOR WHEEL

COLOR_WHEEL_RADIUS = 62 * SCALE
COLOR_WHEEL_SIZE = COLOR_WHEEL_RADIUS * 2

color_wheel_surface = None


def create_color_wheel():
    size = COLOR_WHEEL_SIZE

    surface = pygame.Surface((size, size), pygame.SRCALPHA)

    center = size // 2
    radius = COLOR_WHEEL_RADIUS

    for y in range(size):
        for x in range(size):
            dx = x - center
            dy = y - center

            distance = math.sqrt(dx * dx + dy * dy)

            if distance > radius:
                continue

            angle = math.degrees(math.atan2(dy, dx))

            hue = (angle + 360) % 360

            saturation = 1.0
            value = 1.0

            color = pygame.Color(0)
            color.hsva = (hue, saturation * 100, value * 100, 100)

            surface.set_at((x, y), (color.r, color.g, color.b, 255))

    return surface


color_wheel_surface = create_color_wheel()


# ============================================================
# COORDINATE VALIDATION
# ============================================================


def validate_coordinates(latitude_text, longitude_text):

    try:

        # Must not be empty

        if not latitude_text.strip():
            return False, "LATITUDE IS EMPTY"

        if not longitude_text.strip():
            return False, "LONGITUDE IS EMPTY"

        latitude = float(latitude_text)

        longitude = float(longitude_text)

        # ----------------------------------------------------
        # Check for valid numerical values
        # ----------------------------------------------------

        if not math.isfinite(latitude):
            return False, "INVALID LATITUDE"

        if not math.isfinite(longitude):
            return False, "INVALID LONGITUDE"

        # ----------------------------------------------------
        # Latitude range
        # ----------------------------------------------------

        if latitude < -90 or latitude > 90:

            return False, "LATITUDE MUST BE -90 TO 90"

        # ----------------------------------------------------
        # Longitude range
        # ----------------------------------------------------

        if longitude < -180 or longitude > 180:

            return False, "LONGITUDE MUST BE -180 TO 180"

        # ----------------------------------------------------
        # Everything is valid
        # ----------------------------------------------------

        return True, ""

    except ValueError:

        return False, "INVALID COORDINATES"


# ============================================================
# LOCATION KEYPAD INPUT
# ============================================================


def keypad_input(character):

    global custom_latitude
    global custom_longitude
    global location_error

    if location_input_field == "latitude":

        value = custom_latitude

    else:

        value = custom_longitude

    # --------------------------------------------------------
    # NUMBER
    # --------------------------------------------------------

    if character.isdigit():

        value += character

    # --------------------------------------------------------
    # DECIMAL POINT
    # --------------------------------------------------------

    elif character == ".":

        if "." not in value:

            # Allow "." to become "0."

            if value == "":
                value = "0."

            else:
                value += "."

    # --------------------------------------------------------
    # MINUS
    # --------------------------------------------------------

    elif character == "-":

        # Minus can only be the first character.

        if value == "":

            value = "-"

        elif value.startswith("-"):

            # Remove minus if already present.

            value = value[1:]

        else:

            # Put minus at the beginning.

            value = "-" + value

    # --------------------------------------------------------
    # BACKSPACE
    # --------------------------------------------------------

    elif character == "BACKSPACE":

        value = value[:-1]

    # --------------------------------------------------------
    # CLEAR
    # --------------------------------------------------------

    elif character == "CLEAR":

        value = ""

    # --------------------------------------------------------
    # SAVE VALUE
    # --------------------------------------------------------

    if location_input_field == "latitude":

        custom_latitude = value

    else:

        custom_longitude = value

    # Clear old error while typing.

    location_error = ""


# ============================================================
# MENU
# ============================================================

MENU_BUTTON_SIZE = 42 * SCALE
# Invisible touch area around the menu button. The visible button remains the same size.
MENU_BUTTON_HITBOX_SIZE = 58 * SCALE
MENU_MARGIN = 12 * SCALE

MENU_WIDTH = 210 * SCALE
MENU_HEIGHT = 125 * SCALE

MENU_X = MENU_MARGIN
MENU_Y = MENU_MARGIN

menu_open = False

menu_page = "main"

current_radar_location = "EDINBURGH AIRPORT"

custom_latitude = ""
custom_longitude = ""

location_input_field = "latitude"

location_error = ""

location_verified = False

# ============================================================
# MENU BUTTON
# ============================================================


def draw_menu_button():

    button_rect = pygame.Rect(MENU_X, MENU_Y, MENU_BUTTON_SIZE, MENU_BUTTON_SIZE)

    pygame.draw.rect(canvas, PANEL_BACKGROUND, button_rect, border_radius=5 * SCALE)

    pygame.draw.rect(
        canvas, DIM_GREEN, button_rect, width=1 * SCALE, border_radius=5 * SCALE
    )

    # Three horizontal lines

    line_left = MENU_X + 9 * SCALE
    line_right = MENU_X + 33 * SCALE

    for offset in [11, 20, 29]:

        pygame.draw.line(
            canvas,
            TEXT_GREEN,
            (line_left, MENU_Y + offset * SCALE),
            (line_right, MENU_Y + offset * SCALE),
            2 * SCALE,
        )


# ============================================================
# MAIN MENU
# ============================================================


def draw_main_menu():

    menu_x = MENU_X

    menu_y = MENU_Y + MENU_BUTTON_SIZE + 6 * SCALE

    menu_height = 160 * SCALE

    menu_rect = pygame.Rect(menu_x, menu_y, MENU_WIDTH, menu_height)

    # --------------------------------------------------------
    # BACKGROUND
    # --------------------------------------------------------

    pygame.draw.rect(canvas, PANEL_BACKGROUND, menu_rect, border_radius=5 * SCALE)

    # --------------------------------------------------------
    # BORDER
    # --------------------------------------------------------

    pygame.draw.rect(
        canvas, DIM_GREEN, menu_rect, width=1 * SCALE, border_radius=5 * SCALE
    )

    # --------------------------------------------------------
    # MENU OPTIONS
    # --------------------------------------------------------

    options = [
        ("RADAR COLOUR", "colour"),
        ("RADAR RADIUS", "radius"),
        ("RADAR LOCATION", "location"),
    ]

    option_start_y = menu_y + 10 * SCALE
    option_height = 45 * SCALE

    for index, (text, page) in enumerate(options):

        option_y = option_start_y + index * option_height

        option_rect = pygame.Rect(
            menu_x + 8 * SCALE, option_y, MENU_WIDTH - 16 * SCALE, 40 * SCALE
        )

        # Text

        option_text = small_font.render(text, True, TEXT_GREEN)

        canvas.blit(option_text, (menu_x + 18 * SCALE, option_y + 12 * SCALE))

        # Arrow

        arrow = small_font.render(">", True, TEXT_GREEN)

        arrow_rect = arrow.get_rect(
            center=(menu_x + MENU_WIDTH - 20 * SCALE, option_y + 20 * SCALE)
        )

        canvas.blit(arrow, arrow_rect)


# ============================================================
# RADAR LOCATION MENU
# ============================================================


def draw_location_menu():

    menu_x = MENU_X

    menu_y = MENU_Y + MENU_BUTTON_SIZE + 6 * SCALE

    menu_height = 250 * SCALE

    menu_rect = pygame.Rect(menu_x, menu_y, MENU_WIDTH, menu_height)

    # --------------------------------------------------------
    # BACKGROUND
    # --------------------------------------------------------

    pygame.draw.rect(canvas, PANEL_BACKGROUND, menu_rect, border_radius=5 * SCALE)

    # --------------------------------------------------------
    # BORDER
    # --------------------------------------------------------

    pygame.draw.rect(
        canvas, DIM_GREEN, menu_rect, width=1 * SCALE, border_radius=5 * SCALE
    )

    # --------------------------------------------------------
    # BACK
    # --------------------------------------------------------

    back_text = small_font.render("< BACK", True, TEXT_GREEN)

    canvas.blit(back_text, (menu_x + 12 * SCALE, menu_y + 10 * SCALE))

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    title = small_font.render("RADAR LOCATION", True, TEXT_GREEN)

    title_rect = title.get_rect(
        top=menu_y + 10 * SCALE, right=menu_x + MENU_WIDTH - 12 * SCALE
    )

    canvas.blit(title, title_rect)

    # --------------------------------------------------------
    # LOCATION OPTIONS
    # --------------------------------------------------------

    options = [
        "EDINBURGH AIRPORT",
        "NEWCASTLE AIRPORT",
        "HEATHROW AIRPORT",
        "MANCHESTER AIRPORT",
        "CUSTOM LOCATION",
    ]

    option_start_y = menu_y + 45 * SCALE
    option_height = 40 * SCALE

    for index, name in enumerate(options):

        option_y = option_start_y + index * option_height

        option_rect = pygame.Rect(
            menu_x + 8 * SCALE, option_y, MENU_WIDTH - 16 * SCALE, 32 * SCALE
        )

        # Highlight current location

        if current_radar_location == name:

            pygame.draw.rect(canvas, DIM_GREEN, option_rect, border_radius=3 * SCALE)

        # Text

        text = small_font.render(name, True, TEXT_GREEN)

        canvas.blit(text, (menu_x + 18 * SCALE, option_y + 8 * SCALE))


def get_custom_keypad_rects():

    menu_x = 6 * SCALE
    menu_y = 6 * SCALE

    menu_width = INTERNAL_WIDTH - 12 * SCALE
    menu_height = INTERNAL_HEIGHT - 12 * SCALE

    field_height = 48 * SCALE
    fields_y = menu_y + 58 * SCALE

    keypad_gap = 7 * SCALE

    keypad_width = menu_width - 60 * SCALE

    key_width = (keypad_width - 2 * keypad_gap) // 3

    key_height = 48 * SCALE

    keypad_x = menu_x + (menu_width - keypad_width) // 2

    keypad_y = fields_y + field_height + 45 * SCALE

    keys = [["7", "8", "9"], ["4", "5", "6"], ["1", "2", "3"], ["-", "0", "."]]

    key_rects = []

    for row, key_row in enumerate(keys):

        for column, key in enumerate(key_row):

            x = keypad_x + column * (key_width + keypad_gap)

            y = keypad_y + row * (key_height + keypad_gap)

            rect = pygame.Rect(x, y, key_width, key_height)

            key_rects.append((key, rect))

    return key_rects


# ============================================================
# CUSTOM LOCATION ENTRY MENU
# ============================================================


def draw_custom_location_menu():
    # Full-screen custom location entry. This is intentionally much larger
    # than the normal menu so coordinate entry is reliable on the touchscreen.
    menu_x = 6 * SCALE
    menu_y = 6 * SCALE
    menu_width = INTERNAL_WIDTH - 12 * SCALE
    menu_height = INTERNAL_HEIGHT - 12 * SCALE

    menu_rect = pygame.Rect(menu_x, menu_y, menu_width, menu_height)

    pygame.draw.rect(canvas, PANEL_BACKGROUND, menu_rect, border_radius=6 * SCALE)
    pygame.draw.rect(
        canvas, DIM_GREEN, menu_rect, width=1 * SCALE, border_radius=6 * SCALE
    )

    # Header
    back_rect = pygame.Rect(menu_x + 6 * SCALE, menu_y + 5 * SCALE, 70 * SCALE, 30 * SCALE)
    back_text = small_font.render("< BACK", True, TEXT_GREEN)
    canvas.blit(back_text, (back_rect.x + 5 * SCALE, back_rect.y + 5 * SCALE))

    title = small_font.render("ENTER LOCATION", True, TEXT_GREEN)
    title_rect = title.get_rect(
        top=menu_y + 8 * SCALE, right=menu_x + menu_width - 8 * SCALE
    )
    canvas.blit(title, title_rect)

    # Coordinate fields
    field_width = 215 * SCALE
    field_height = 40 * SCALE
    fields_y = menu_y + 42 * SCALE

    latitude_rect = pygame.Rect(menu_x + 10 * SCALE, fields_y, field_width, field_height)
    longitude_rect = pygame.Rect(
        menu_x + menu_width - field_width - 10 * SCALE, fields_y, field_width, field_height
    )

    def draw_field(rect, label, value, active):
        field_colour = DIM_GREEN if active else (12, 30, 18)
        pygame.draw.rect(canvas, field_colour, rect, border_radius=4 * SCALE)
        pygame.draw.rect(
            canvas, TEXT_GREEN, rect, width=1 * SCALE, border_radius=4 * SCALE
        )
        label_surface = small_font.render(label, True, TEXT_GREEN)
        canvas.blit(label_surface, (rect.x + 7 * SCALE, rect.y + 3 * SCALE))
        value_text = value if value else "0"
        value_surface = font.render(value_text, True, AIRCRAFT_HIGHLIGHT)
        canvas.blit(value_surface, (rect.x + 7 * SCALE, rect.y + 17 * SCALE))

    draw_field(
        latitude_rect, "LATITUDE", custom_latitude, location_input_field == "latitude"
    )
    draw_field(
        longitude_rect, "LONGITUDE", custom_longitude, location_input_field == "longitude"
    )

    # Error line
    if location_error:
        error_surface = small_font.render(location_error, True, (255, 100, 100))
        error_rect = error_surface.get_rect(
            center=(menu_x + menu_width // 2, menu_y + 87 * SCALE)
        )
        canvas.blit(error_surface, error_rect)

    # Numeric keypad
    keys = [
        ["7", "8", "9"],
        ["4", "5", "6"],
        ["1", "2", "3"],
        ["-", "0", "."],
    ]

    keypad_x = menu_x + 78 * SCALE
    keypad_y = menu_y + 94 * SCALE
    key_gap = 5 * SCALE
    key_width = 90 * SCALE
    key_height = 32 * SCALE

    for row, key_row in enumerate(keys):
        for column, key in enumerate(key_row):
            x = keypad_x + column * (key_width + key_gap)
            y = keypad_y + row * (key_height + key_gap)
            key_rect = pygame.Rect(x, y, key_width, key_height)

            pygame.draw.rect(
                canvas, (10, 25, 15), key_rect, border_radius=4 * SCALE
            )
            pygame.draw.rect(
                canvas, DIM_GREEN, key_rect, width=1 * SCALE, border_radius=4 * SCALE
            )

            key_text = font.render(key, True, TEXT_GREEN)
            key_text_rect = key_text.get_rect(center=key_rect.center)
            canvas.blit(key_text, key_text_rect)

    # Bottom controls
    action_y = menu_y + 240 * SCALE
    action_height = 30 * SCALE

    backspace_rect = pygame.Rect(keypad_x, action_y, 132 * SCALE, action_height)
    clear_rect = pygame.Rect(
        keypad_x + 138 * SCALE, action_y, 132 * SCALE, action_height
    )

    for rect in (backspace_rect, clear_rect):
        pygame.draw.rect(canvas, (10, 25, 15), rect, border_radius=4 * SCALE)
        pygame.draw.rect(
            canvas, DIM_GREEN, rect, width=1 * SCALE, border_radius=4 * SCALE
        )

    backspace_text = small_font.render("BACKSPACE", True, TEXT_GREEN)
    clear_text = small_font.render("CLEAR", True, TEXT_GREEN)
    canvas.blit(backspace_text, backspace_text.get_rect(center=backspace_rect.center))
    canvas.blit(clear_text, clear_text.get_rect(center=clear_rect.center))

    verify_rect = pygame.Rect(
        menu_x + 105 * SCALE, menu_y + 275 * SCALE, 258 * SCALE, 30 * SCALE
    )
    pygame.draw.rect(canvas, DIM_GREEN, verify_rect, border_radius=4 * SCALE)
    pygame.draw.rect(
        canvas, TEXT_GREEN, verify_rect, width=1 * SCALE, border_radius=4 * SCALE
    )
    verify_text = small_font.render("VERIFY & USE LOCATION", True, TEXT_GREEN)
    canvas.blit(verify_text, verify_text.get_rect(center=verify_rect.center))


def draw_colour_menu():

    menu_x = MENU_X

    menu_y = MENU_Y + MENU_BUTTON_SIZE + 6 * SCALE

    menu_height = 220 * SCALE

    menu_rect = pygame.Rect(menu_x, menu_y, MENU_WIDTH, menu_height)

    pygame.draw.rect(canvas, PANEL_BACKGROUND, menu_rect, border_radius=5 * SCALE)

    pygame.draw.rect(
        canvas, DIM_GREEN, menu_rect, width=1 * SCALE, border_radius=5 * SCALE
    )

    # --------------------------------------------------------
    # BACK BUTTON
    # --------------------------------------------------------

    back_text = small_font.render("< BACK", True, TEXT_GREEN)

    canvas.blit(back_text, (menu_x + 12 * SCALE, menu_y + 10 * SCALE))

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    title = small_font.render("RADAR COLOUR", True, TEXT_GREEN)

    title_rect = title.get_rect(
        top=menu_y + 10 * SCALE, right=menu_x + MENU_WIDTH - 12 * SCALE
    )

    canvas.blit(title, title_rect)

    wheel_x = menu_x + MENU_WIDTH // 2 - COLOR_WHEEL_RADIUS

    wheel_y = menu_y + 48 * SCALE

    canvas.blit(color_wheel_surface, (wheel_x, wheel_y))

    selected_color = pygame.Color(*current_radar_colour)

    hue = selected_color.hsva[0]

    angle = math.radians(hue)

    marker_x = (
        wheel_x
        + COLOR_WHEEL_RADIUS
        + math.cos(angle) * (COLOR_WHEEL_RADIUS - 5 * SCALE)
    )

    marker_y = (
        wheel_y
        + COLOR_WHEEL_RADIUS
        + math.sin(angle) * (COLOR_WHEEL_RADIUS - 5 * SCALE)
    )

    pygame.draw.circle(canvas, (0, 0, 0), (int(marker_x), int(marker_y)), 6 * SCALE)

    pygame.draw.circle(
        canvas, (255, 255, 255), (int(marker_x), int(marker_y)), 4 * SCALE
    )

    r, g, b = current_radar_colour

    color_text = small_font.render(f"RGB {r}, {g}, {b}", True, TEXT_GREEN)

    color_rect = color_text.get_rect(
        center=(menu_x + MENU_WIDTH // 2, menu_y + 200 * SCALE)
    )

    canvas.blit(color_text, color_rect)


# ============================================================
# RADIUS MENU
# ============================================================


def draw_radius_menu():

    menu_x = MENU_X

    menu_y = MENU_Y + MENU_BUTTON_SIZE + 6 * SCALE

    menu_height = 170 * SCALE

    menu_rect = pygame.Rect(menu_x, menu_y, MENU_WIDTH, menu_height)

    pygame.draw.rect(canvas, PANEL_BACKGROUND, menu_rect, border_radius=5 * SCALE)

    pygame.draw.rect(
        canvas, DIM_GREEN, menu_rect, width=1 * SCALE, border_radius=5 * SCALE
    )

    # --------------------------------------------------------
    # BACK BUTTON
    # --------------------------------------------------------

    back_text = small_font.render("< BACK", True, TEXT_GREEN)

    canvas.blit(back_text, (menu_x + 12 * SCALE, menu_y + 10 * SCALE))

    # --------------------------------------------------------
    # TITLE
    # --------------------------------------------------------

    title = small_font.render("RADAR RADIUS", True, TEXT_GREEN)

    title_rect = title.get_rect(
        top=menu_y + 10 * SCALE, right=menu_x + MENU_WIDTH - 12 * SCALE
    )

    canvas.blit(title, title_rect)

    # --------------------------------------------------------
    # OPTIONS
    # --------------------------------------------------------

    options = [30, 50, 100]

    option_start_y = menu_y + 45 * SCALE

    option_height = 38 * SCALE

    for index, radius in enumerate(options):

        option_y = option_start_y + index * option_height

        option_rect = pygame.Rect(
            menu_x + 8 * SCALE, option_y, MENU_WIDTH - 16 * SCALE, 32 * SCALE
        )

        # Highlight current selection

        if RADAR_RANGE_KM == radius:

            pygame.draw.rect(canvas, DIM_GREEN, option_rect, border_radius=3 * SCALE)

        text = small_font.render(f"{radius} KM", True, TEXT_GREEN)

        canvas.blit(text, (menu_x + 18 * SCALE, option_y + 8 * SCALE))


# ============================================================
# DRAW MENU
# ============================================================


def draw_menu():

    if not menu_open:
        return

    if menu_page == "main":
        draw_main_menu()

    elif menu_page == "colour":
        draw_colour_menu()

    elif menu_page == "radius":
        draw_radius_menu()

    elif menu_page == "location":
        draw_location_menu()

    elif menu_page == "custom_location":
        draw_custom_location_menu()


# ============================================================
# AIRCRAFT CLICK DETECTION
# ============================================================


def handle_aircraft_click(mouse_pos):

    global selected_aircraft
    global selected_details
    global last_detail_refresh

    mouse_x, mouse_y = mouse_pos

    # Don't select aircraft while a menu is open.

    if menu_open:

        return False

    with aircraft_lock:

        planes = list(aircraft.values())

    # Search backwards so aircraft drawn later
    # get priority when they overlap.

    for plane in reversed(planes):

        x = plane["display_x"]
        y = plane["display_y"]

        distance = math.sqrt((mouse_x - x) ** 2 + (mouse_y - y) ** 2)

        # Aircraft selection radius.

        if distance <= 28 * SCALE:

            selected_aircraft = plane["icao24"]

            selected_details = get_cached_details(plane["icao24"])

            last_detail_refresh = 0

            # Immediately request details.

            fetch_details_async(plane["icao24"], plane["callsign"], force=True)

            return True

    return False


def deselect_aircraft():

    global selected_aircraft
    global selected_details
    global details_scroll_offset
    global details_touch_start_y
    global details_last_touch_y
    global details_dragging
    global details_drag_moved

    selected_aircraft = None

    selected_details = {}

    details_scroll_offset = 0
    details_touch_start_y = None
    details_last_touch_y = None
    details_dragging = False
    details_drag_moved = False


def change_radar_location(location_name, latitude=None, longitude=None):

    global RADAR_NAME
    global RADAR_LAT
    global RADAR_LON
    global current_radar_location
    global api_status
    global last_api_update

    # ========================================================
    # PREDEFINED LOCATION
    # ========================================================

    if location_name in RADAR_LOCATIONS:

        location = RADAR_LOCATIONS[location_name]

        new_latitude = location["lat"]
        new_longitude = location["lon"]

    # ========================================================
    # CUSTOM LOCATION
    # ========================================================

    elif (
        location_name == "CUSTOM LOCATION"
        and latitude is not None
        and longitude is not None
    ):

        new_latitude = latitude
        new_longitude = longitude

    else:

        return False

    # ========================================================
    # UPDATE RADAR
    # ========================================================

    RADAR_NAME = location_name

    RADAR_LAT = new_latitude

    RADAR_LON = new_longitude

    current_radar_location = location_name

    # ========================================================
    # CLEAR OLD AIRCRAFT
    # ========================================================

    with aircraft_lock:

        aircraft.clear()

    deselect_aircraft()

    api_status = "UPDATING LOCATION"

    last_api_update = 0

    print(
        f"Radar location changed to "
        f"{RADAR_NAME} "
        f"({RADAR_LAT:.6f}, {RADAR_LON:.6f})"
    )

    return True


# ============================================================
# AIRCRAFT DETAILS PANEL
# ============================================================

DETAIL_PANEL_WIDTH = int(INTERNAL_WIDTH * 0.35)

DETAIL_PANEL_MARGIN = 4 * SCALE


def draw_aircraft_details_panel():

    global details_scroll_offset

    if selected_aircraft is None:
        return

    with aircraft_lock:
        plane = aircraft.get(selected_aircraft)

    if plane is None:
        return

    # ========================================================
    # PANEL GEOMETRY
    # ========================================================

    panel_x = DETAIL_PANEL_MARGIN
    panel_y = 38 * SCALE
    panel_width = DETAIL_PANEL_WIDTH
    panel_height = INTERNAL_HEIGHT - panel_y - 6 * SCALE

    panel_rect = pygame.Rect(panel_x, panel_y, panel_width, panel_height)

    # ========================================================
    # PANEL BACKGROUND
    # ========================================================

    pygame.draw.rect(canvas, PANEL_BACKGROUND, panel_rect, border_radius=7 * SCALE)

    pygame.draw.rect(
        canvas, DIM_GREEN, panel_rect, width=1 * SCALE, border_radius=7 * SCALE
    )

    # ========================================================
    # CLOSE BUTTON
    # ========================================================

    close_rect = pygame.Rect(
        panel_x + panel_width - 38 * SCALE, panel_y + 10 * SCALE, 28 * SCALE, 28 * SCALE
    )

    pygame.draw.rect(canvas, DIM_GREEN, close_rect, border_radius=4 * SCALE)

    close_text = DETAILS_SMALL_FONT.render("X", True, TEXT_GREEN)

    close_text_rect = close_text.get_rect(center=close_rect.center)

    canvas.blit(close_text, close_text_rect)

    # ========================================================
    # TITLE
    # ========================================================

    callsign = plane.get("callsign", "UNKNOWN") or "UNKNOWN"

    title = DETAILS_LARGE_FONT.render(str(callsign), True, TEXT_GREEN)

    # Keep title clear of the close button.
    title_area_width = panel_width - 60 * SCALE

    if title.get_width() > title_area_width:
        title = DETAILS_SMALL_FONT.render(str(callsign), True, TEXT_GREEN)

    canvas.blit(title, (panel_x + 14 * SCALE, panel_y + 10 * SCALE))

    # ========================================================
    # ICAO24 / SQUAWK
    # ========================================================

    squawk = plane.get("squawk")

    if squawk is not None:
        try:
            squawk_text = f"{int(squawk):04d}"
        except (ValueError, TypeError):
            squawk_text = str(squawk)
    else:
        squawk_text = "UNKNOWN"

    combined_text = "SQUAWK " + squawk_text

    combined_surface = DETAILS_SMALL_FONT.render(combined_text, True, DIM_GREEN)

    # If the combined line is too long, draw the two pieces separately.
    if combined_surface.get_width() <= panel_width - 28 * SCALE:
        canvas.blit(combined_surface, (panel_x + 14 * SCALE, panel_y + 38 * SCALE))
    else:
        squawk_surface = DETAILS_SMALL_FONT.render("SQ " + squawk_text, True, DIM_GREEN)

        canvas.blit(
            squawk_surface,
            (
                panel_x + panel_width - squawk_surface.get_width() - 14 * SCALE,
                panel_y + 38 * SCALE,
            ),
        )

    # ========================================================
    # CONTENT VIEWPORT
    # ========================================================

    content_x = panel_x + 14 * SCALE
    content_top = panel_y + 62 * SCALE
    content_bottom = panel_y + panel_height - 22 * SCALE
    content_height = max(1, content_bottom - content_top)

    # ========================================================
    # DETAILS
    # ========================================================

    details = selected_details or {}

    def detail_value(key, default="UNKNOWN"):
        value = details.get(key)

        if value is None:
            return default

        if isinstance(value, str):
            value = value.strip()
            if not value:
                return default

        return value

    registration = detail_value("registration")
    aircraft_type = detail_value("aircraft_type")
    aircraft_icao_type = detail_value("aircraft_icao_type", "")
    manufacturer = detail_value("manufacturer")
    airline = detail_value("airline")
    airline_icao = detail_value("airline_icao", "")
    airline_iata = detail_value("airline_iata", "")
    country = detail_value("country")
    origin = detail_value("origin")
    destination = detail_value("destination")
    route = detail_value("route")

    altitude = plane.get("altitude")
    speed = plane.get("speed")
    heading = plane.get("heading")
    vertical_rate = plane.get("vertical_rate")
    latitude = plane.get("latitude")
    longitude = plane.get("longitude")
    distance = plane.get("distance")

    altitude_text = f"{int(altitude):,} ft" if altitude is not None else "UNKNOWN"
    speed_text = f"{int(speed)} kt" if speed is not None else "UNKNOWN"
    heading_text = f"{int(heading)}°" if heading is not None else "UNKNOWN"
    vertical_text = (
        f"{int(vertical_rate):+,} ft/min" if vertical_rate is not None else "UNKNOWN"
    )
    latitude_text = f"{latitude:.4f}" if latitude is not None else "UNKNOWN"
    longitude_text = f"{longitude:.4f}" if longitude is not None else "UNKNOWN"
    distance_text = f"{distance:.1f} km" if distance is not None else "UNKNOWN"

    type_text = aircraft_type

    if aircraft_icao_type:
        if type_text == "UNKNOWN":
            type_text = aircraft_icao_type
        else:
            type_text = f"{type_text} ({aircraft_icao_type})"

    airline_text = airline
    airline_codes = []

    if airline_icao:
        airline_codes.append(str(airline_icao))

    if airline_iata:
        airline_codes.append(str(airline_iata))

    if airline_codes:
        airline_text = str(airline) + " (" + "/".join(airline_codes) + ")"

    if route and route != "UNKNOWN":
        route_text = route
    elif origin != "UNKNOWN" and destination != "UNKNOWN":
        route_text = f"{origin} -> {destination}"
    else:
        route_text = "UNKNOWN"

    # ========================================================
    # FLIGHT ROTATION
    # ========================================================

    outbound = details.get("outbound")
    inbound = details.get("inbound")
    previous_flight = details.get("previous_flight")
    next_flight = details.get("next_flight")

    def rotation_text(flight):
        if isinstance(flight, dict):
            origin_value = flight.get("origin", "UNKNOWN")
            destination_value = flight.get("destination", "UNKNOWN")
            callsign_value = flight.get("callsign")

            text = f"{origin_value} -> {destination_value}"

            if callsign_value:
                text += f"  {callsign_value}"

            return text

        if flight:
            return str(flight)

        return "UNKNOWN"

    # ========================================================
    # BUILD CONTENT
    # ========================================================

    content_items = []

    def add_header(text):
        content_items.append(("header", text))

    def add_row(label, value):
        content_items.append(("row", label, str(value)))

    add_row("ROUTE", route_text)
    add_row("AIRLINE", airline_text)
    add_row("REGISTRATION", registration)

    if outbound or inbound or previous_flight or next_flight:
        add_header("FLIGHT ROTATION")

        if outbound:
            add_row("OUTBOUND", rotation_text(outbound))

        if inbound:
            add_row("INBOUND", rotation_text(inbound))

        if previous_flight:
            add_row("PREVIOUS", rotation_text(previous_flight))

        if next_flight:
            add_row("NEXT", rotation_text(next_flight))

    add_row("AIRCRAFT TYPE", type_text)
    add_row("MANUFACTURER", manufacturer)
    add_row("COUNTRY", country)

    add_row("ALTITUDE", altitude_text)
    add_row("SPEED", speed_text)
    add_row("HEADING", heading_text)
    add_row("VERTICAL RATE", vertical_text)
    add_row("POSITION", f"{latitude_text}, {longitude_text}")
    add_row("DISTANCE", distance_text)

    # ========================================================
    # TEXT WRAPPING
    # ========================================================

    max_text_width = panel_width - 28 * SCALE

    def wrap_text(text, font_to_use, max_width):
        text = str(text)

        if not text:
            return [""]

        # First try the complete string.
        if font_to_use.size(text)[0] <= max_width:
            return [text]

        words = text.split()
        lines = []
        current = ""

        for word in words:
            test = word if not current else current + " " + word

            if font_to_use.size(test)[0] <= max_width:
                current = test
                continue

            if current:
                lines.append(current)

            # Handle a single word that is wider than the panel.
            if font_to_use.size(word)[0] > max_width:
                piece = ""
                for character in word:
                    test_piece = piece + character
                    if font_to_use.size(test_piece)[0] <= max_width:
                        piece = test_piece
                    else:
                        if piece:
                            lines.append(piece)
                        piece = character
                current = piece
            else:
                current = word

        if current:
            lines.append(current)

        return lines or [""]

    # ========================================================
    # CALCULATE CONTENT HEIGHT
    # ========================================================

    header_height = 18 * SCALE
    label_height = 13 * SCALE
    value_line_height = 14 * SCALE
    row_gap = 5 * SCALE

    prepared_items = []
    total_content_height = 0

    for item in content_items:
        if item[0] == "header":
            prepared_items.append(("header", item[1]))
            total_content_height += header_height
        else:
            label = item[1]
            value = item[2]
            lines = wrap_text(value, DETAILS_SMALL_FONT, max_text_width)

            prepared_items.append(("row", label, lines))

            total_content_height += label_height
            total_content_height += len(lines) * value_line_height
            total_content_height += row_gap

    # Add a little breathing room at the bottom.
    total_content_height += 8 * SCALE

    max_scroll = max(0, total_content_height - content_height)

    # Clamp the current position.
    details_scroll_offset = max(0, min(details_scroll_offset, max_scroll))

    # ========================================================
    # DRAW SCROLLABLE CONTENT
    # ========================================================

    old_clip = canvas.get_clip()

    canvas.set_clip(pygame.Rect(panel_x, content_top, panel_width, content_height))

    content_y = content_top - details_scroll_offset

    for item in prepared_items:

        if item[0] == "header":

            surface = DETAILS_SMALL_FONT.render(item[1], True, DIM_GREEN)

            canvas.blit(surface, (content_x, content_y))

            content_y += header_height

        else:

            label = item[1]
            lines = item[2]

            label_surface = DETAILS_SMALL_FONT.render(label, True, DIM_GREEN)

            canvas.blit(label_surface, (content_x, content_y))

            content_y += label_height

            for line in lines:

                value_surface = DETAILS_SMALL_FONT.render(line, True, TEXT_GREEN)

                canvas.blit(value_surface, (content_x, content_y))

                content_y += value_line_height

            content_y += row_gap

    canvas.set_clip(old_clip)

    # ========================================================
    # SCROLLBAR
    # ========================================================

    if max_scroll > 0:

        scrollbar_x = panel_x + panel_width - 7 * SCALE

        scrollbar_top = content_top
        scrollbar_height = content_height

        pygame.draw.rect(
            canvas,
            DIM_GREEN,
            pygame.Rect(scrollbar_x, scrollbar_top, 3 * SCALE, scrollbar_height),
            border_radius=2 * SCALE,
        )

        thumb_height = max(
            22 * SCALE, int(scrollbar_height * content_height / total_content_height)
        )

        thumb_range = max(0, scrollbar_height - thumb_height)

        thumb_y = scrollbar_top + int(thumb_range * details_scroll_offset / max_scroll)

        pygame.draw.rect(
            canvas,
            TEXT_GREEN,
            pygame.Rect(scrollbar_x, thumb_y, 3 * SCALE, thumb_height),
            border_radius=2 * SCALE,
        )

    # ========================================================
    # UPDATE STATUS
    # ========================================================

    update_time = details.get("last_updated", 0)

    if update_time:
        age = int(time.time() - update_time)
        update_text = f"DETAILS UPDATED {age}s AGO"
    else:
        update_text = "LOADING DETAILS..."

    update_surface = small_font.render(update_text, True, DIM_GREEN)

    # Clip status text to the panel so it can never extend outside.
    status_clip = canvas.get_clip()

    canvas.set_clip(panel_rect)

    canvas.blit(
        update_surface, (panel_x + 14 * SCALE, panel_y + panel_height - 20 * SCALE)
    )

    canvas.set_clip(status_clip)


def handle_details_click(mouse_pos):

    global details_scroll_offset
    global details_touch_start_y
    global details_last_touch_y
    global details_dragging
    global details_drag_moved

    if selected_aircraft is None:
        return False

    mouse_x, mouse_y = mouse_pos

    panel_x = DETAIL_PANEL_MARGIN
    panel_y = 38 * SCALE
    panel_width = DETAIL_PANEL_WIDTH
    panel_height = INTERNAL_HEIGHT - panel_y - 6 * SCALE

    panel_rect = pygame.Rect(panel_x, panel_y, panel_width, panel_height)

    # --------------------------------------------------------
    # CLOSE BUTTON
    # --------------------------------------------------------

    close_rect = pygame.Rect(
        panel_x + panel_width - 46 * SCALE, panel_y + 4 * SCALE, 40 * SCALE, 40 * SCALE
    )

    if close_rect.collidepoint(mouse_x, mouse_y):
        deselect_aircraft()
        details_scroll_offset = 0
        details_touch_start_y = None
        details_last_touch_y = None
        details_dragging = False
        details_drag_moved = False
        return True

    # --------------------------------------------------------
    # CLICK OUTSIDE DETAILS PANEL
    # --------------------------------------------------------
    # Clicking anywhere outside the panel closes it. This is
    # deliberately handled before the menu/aircraft handlers so
    # the click cannot fall through to another UI element.

    if not panel_rect.collidepoint(mouse_x, mouse_y):
        deselect_aircraft()
        details_scroll_offset = 0
        details_touch_start_y = None
        details_last_touch_y = None
        details_dragging = False
        details_drag_moved = False
        return True

    # --------------------------------------------------------
    # START DETAILS DRAG
    # --------------------------------------------------------

    details_touch_start_y = mouse_y
    details_last_touch_y = mouse_y
    details_dragging = True
    details_drag_moved = False

    return True


# ============================================================
# DETAILS PANEL DRAG / SCROLL HELPERS
# ============================================================


def handle_details_motion(mouse_pos):

    global details_scroll_offset
    global details_last_touch_y
    global details_drag_moved

    if not details_dragging:
        return False

    if selected_aircraft is None:
        return False

    mouse_x, mouse_y = mouse_pos

    # Only scroll while the pointer/finger is within the panel.
    panel_x = DETAIL_PANEL_MARGIN
    panel_y = 38 * SCALE
    panel_width = DETAIL_PANEL_WIDTH
    panel_height = INTERNAL_HEIGHT - panel_y - 6 * SCALE

    panel_rect = pygame.Rect(panel_x, panel_y, panel_width, panel_height)

    if not panel_rect.collidepoint(mouse_x, mouse_y):
        return True

    if details_last_touch_y is None:
        details_last_touch_y = mouse_y
        return True

    delta = details_last_touch_y - mouse_y

    if abs(delta) >= 1:
        details_scroll_offset += delta
        details_drag_moved = True
        details_last_touch_y = mouse_y

    return True


def handle_details_release():

    global details_touch_start_y
    global details_last_touch_y
    global details_dragging
    global details_drag_moved

    details_touch_start_y = None
    details_last_touch_y = None
    details_dragging = False
    details_drag_moved = False


# ============================================================
# CUSTOM LOCATION CLICK HANDLER
# ============================================================


def handle_custom_location_click(mouse_pos):
    global menu_page
    global custom_latitude
    global custom_longitude
    global location_input_field
    global location_error
    global menu_open

    mouse_x, mouse_y = mouse_pos

    menu_x = 6 * SCALE
    menu_y = 6 * SCALE
    menu_width = INTERNAL_WIDTH - 12 * SCALE

    # Back
    back_rect = pygame.Rect(menu_x + 2 * SCALE, menu_y + 2 * SCALE, 82 * SCALE, 36 * SCALE)
    if back_rect.collidepoint(mouse_x, mouse_y):
        menu_page = "location"
        location_error = ""
        return True

    # Coordinate fields
    field_width = 215 * SCALE
    field_height = 40 * SCALE
    fields_y = menu_y + 42 * SCALE

    latitude_rect = pygame.Rect(menu_x + 10 * SCALE, fields_y, field_width, field_height)
    longitude_rect = pygame.Rect(
        menu_x + menu_width - field_width - 10 * SCALE, fields_y, field_width, field_height
    )

    if latitude_rect.collidepoint(mouse_x, mouse_y):
        location_input_field = "latitude"
        location_error = ""
        return True

    if longitude_rect.collidepoint(mouse_x, mouse_y):
        location_input_field = "longitude"
        location_error = ""
        return True

    # Numeric keypad
    keys = [
        ["7", "8", "9"],
        ["4", "5", "6"],
        ["1", "2", "3"],
        ["-", "0", "."],
    ]

    keypad_x = menu_x + 78 * SCALE
    keypad_y = menu_y + 94 * SCALE
    key_gap = 5 * SCALE
    key_width = 90 * SCALE
    key_height = 32 * SCALE

    for row, key_row in enumerate(keys):
        for column, key in enumerate(key_row):
            x = keypad_x + column * (key_width + key_gap)
            y = keypad_y + row * (key_height + key_gap)
            key_rect = pygame.Rect(x, y, key_width, key_height)
            if key_rect.collidepoint(mouse_x, mouse_y):
                keypad_input(key)
                return True

    # Bottom controls
    action_y = menu_y + 230 * SCALE
    action_height = 34 * SCALE

    backspace_rect = pygame.Rect(keypad_x, action_y, 132 * SCALE, action_height)
    clear_rect = pygame.Rect(
        keypad_x + 138 * SCALE, action_y, 132 * SCALE, action_height
    )

    if backspace_rect.collidepoint(mouse_x, mouse_y):
        keypad_input("BACKSPACE")
        return True

    if clear_rect.collidepoint(mouse_x, mouse_y):
        keypad_input("CLEAR")
        return True

    # Verify
    verify_rect = pygame.Rect(
        menu_x + 105 * SCALE, menu_y + 269 * SCALE, 258 * SCALE, 30 * SCALE
    )

    if verify_rect.collidepoint(mouse_x, mouse_y):
        valid, error = validate_coordinates(custom_latitude, custom_longitude)
        if not valid:
            location_error = error
            return True

        latitude = float(custom_latitude)
        longitude = float(custom_longitude)
        change_radar_location("CUSTOM LOCATION", latitude, longitude)

        menu_page = "main"
        location_error = ""
        return True

    return True


# ============================================================
# HANDLE MENU CLICKS
# ============================================================


def handle_menu_click(mouse_pos):

    global menu_open
    global menu_page
    global current_radar_colour
    global RADAR_RANGE_KM

    global custom_latitude
    global custom_longitude
    global location_input_field
    global location_error

    mouse_x, mouse_y = mouse_pos

    # --------------------------------------------------------
    # MENU BUTTON
    # --------------------------------------------------------

    button_rect = pygame.Rect(
        MENU_X - (MENU_BUTTON_HITBOX_SIZE - MENU_BUTTON_SIZE) // 2,
        MENU_Y - (MENU_BUTTON_HITBOX_SIZE - MENU_BUTTON_SIZE) // 2,
        MENU_BUTTON_HITBOX_SIZE,
        MENU_BUTTON_HITBOX_SIZE,
    )

    if button_rect.collidepoint(mouse_x, mouse_y):

        menu_open = not menu_open

        if menu_open:

            menu_page = "main"

        return True

    # If menu isn't open, nothing else to do.

    if not menu_open:

        return False

    # --------------------------------------------------------
    # MENU POSITION
    # --------------------------------------------------------

    menu_x = MENU_X

    menu_y = MENU_Y + MENU_BUTTON_SIZE + 6 * SCALE

    # ========================================================
    # MAIN MENU
    # ========================================================

    if menu_page == "main":

        # --------------------------------------------
        # RADAR COLOUR
        # --------------------------------------------

        colour_rect = pygame.Rect(
            menu_x + 8 * SCALE, menu_y + 10 * SCALE, MENU_WIDTH - 16 * SCALE, 40 * SCALE
        )

        if colour_rect.collidepoint(mouse_x, mouse_y):

            menu_page = "colour"

            return True

        # --------------------------------------------
        # RADAR RADIUS
        # --------------------------------------------

        radius_rect = pygame.Rect(
            menu_x + 8 * SCALE, menu_y + 55 * SCALE, MENU_WIDTH - 16 * SCALE, 40 * SCALE
        )

        if radius_rect.collidepoint(mouse_x, mouse_y):

            menu_page = "radius"

            return True

    # ========================================================
    # LOCATION MENU
    # ========================================================

    elif menu_page == "location":

        # --------------------------------------------
        # BACK
        # --------------------------------------------

        back_rect = pygame.Rect(menu_x, menu_y, 70 * SCALE, 35 * SCALE)

        if back_rect.collidepoint(mouse_x, mouse_y):

            menu_page = "main"

            return True

        # --------------------------------------------
        # LOCATION OPTIONS
        # --------------------------------------------

        options = [
            "EDINBURGH AIRPORT",
            "NEWCASTLE AIRPORT",
            "HEATHROW AIRPORT",
            "MANCHESTER AIRPORT",
            "CUSTOM LOCATION",
        ]

        option_start_y = menu_y + 45 * SCALE

        option_height = 38 * SCALE

        for index, name in enumerate(options):

            option_y = option_start_y + index * option_height

            option_rect = pygame.Rect(
                menu_x + 8 * SCALE, option_y, MENU_WIDTH - 16 * SCALE, 32 * SCALE
            )

            if option_rect.collidepoint(mouse_x, mouse_y):

                if name == "CUSTOM LOCATION":

                    custom_latitude = ""
                    custom_longitude = ""

                    location_input_field = "latitude"

                    location_error = ""

                    menu_page = "custom_location"

                else:

                    change_radar_location(name)

                    menu_page = "main"

                return True

    # ========================================================
    # COLOUR MENU
    # ========================================================

    elif menu_page == "colour":

        # --------------------------------------------
        # BACK
        # --------------------------------------------

        back_rect = pygame.Rect(menu_x, menu_y, 70 * SCALE, 35 * SCALE)

        if back_rect.collidepoint(mouse_x, mouse_y):

            menu_page = "main"

            return True

        # --------------------------------------------
        # COLOUR OPTIONS
        # --------------------------------------------

        wheel_x = menu_x + MENU_WIDTH // 2 - COLOR_WHEEL_RADIUS

        wheel_y = menu_y + 48 * SCALE

        wheel_center_x = wheel_x + COLOR_WHEEL_RADIUS
        wheel_center_y = wheel_y + COLOR_WHEEL_RADIUS

        dx = mouse_x - wheel_center_x

        dy = mouse_y - wheel_center_y

        distance = math.sqrt(dx * dx + dy * dy)

        if distance <= COLOR_WHEEL_RADIUS:
            angle = math.degrees(math.atan2(dy, dx))

            hue = (angle + 360) % 360

            color = pygame.Color(0)

            color.hsva = (hue, 100, 100, 100)

            current_radar_colour = (color.r, color.g, color.b)

            update_radar_colours()

            return True

        color_menu_height = 220 * SCALE

        color_menu_rect = pygame.Rect(menu_x, menu_y, MENU_WIDTH, color_menu_height)

        if not color_menu_rect.collidepoint(mouse_x, mouse_y):
            menu_open = False
            return True

        return True

    # ========================================================
    # RADIUS MENU
    # ========================================================

    elif menu_page == "radius":

        # --------------------------------------------
        # BACK
        # --------------------------------------------

        back_rect = pygame.Rect(menu_x, menu_y, 70 * SCALE, 35 * SCALE)

        if back_rect.collidepoint(mouse_x, mouse_y):

            menu_page = "main"

            return True

        # --------------------------------------------
        # RADIUS OPTIONS
        # --------------------------------------------

        options = [30, 50, 100]

        option_start_y = menu_y + 45 * SCALE

        option_height = 38 * SCALE

        for index, radius in enumerate(options):

            option_y = option_start_y + index * option_height

            option_rect = pygame.Rect(
                menu_x + 8 * SCALE, option_y, MENU_WIDTH - 16 * SCALE, 32 * SCALE
            )

            if option_rect.collidepoint(mouse_x, mouse_y):

                RADAR_RANGE_KM = radius

                menu_page = "main"

                return True

    # --------------------------------------------
    # RADAR LOCATION
    # --------------------------------------------

    location_rect = pygame.Rect(
        menu_x + 8 * SCALE, menu_y + 100 * SCALE, MENU_WIDTH - 16 * SCALE, 40 * SCALE
    )

    if location_rect.collidepoint(mouse_x, mouse_y):

        menu_page = "location"

        return True

    # --------------------------------------------------------
    # CLICK OUTSIDE MENU
    # --------------------------------------------------------

    menu_height = 220 * SCALE

    menu_rect = pygame.Rect(menu_x, menu_y, MENU_WIDTH, menu_height)

    if not menu_rect.collidepoint(mouse_x, mouse_y):

        menu_open = False

        return True

    return False


# MAIN LOOP

running = True

while running:

    # ------------------------------------------------------------
    # INTERNET CONNECTION MONITOR
    # ------------------------------------------------------------

    current_time = time.time()

    if current_time - last_wifi_check >= WIFI_CHECK_INTERVAL:

        last_wifi_check = current_time

        if not is_internet_connected():

            print("Internet connection lost. Returning to Wi-Fi setup.")

            # Stop showing stale aircraft data while the network
            # is unavailable. The API thread will safely continue
            # retrying in the background.
            with aircraft_lock:
                aircraft.clear()

            pygame.display.set_caption("Flight Radar - Wi-Fi Setup")

            if not ensure_wifi():
                running = False
                continue

            pygame.display.set_caption("Flight Radar")

            last_wifi_check = time.time()

    # ------------------------------------------------------------
    # EVENTS
    # ------------------------------------------------------------

    for event in pygame.event.get():

        # --------------------------------------------------------
        # WINDOW CLOSE / ESCAPE
        # --------------------------------------------------------

        if event.type == pygame.QUIT:

            running = False
            continue

        if event.type == pygame.KEYDOWN:

            if event.key == pygame.K_ESCAPE:

                running = False

            continue

        # --------------------------------------------------------
        # LEFT MOUSE BUTTON / TOUCH DOWN
        # --------------------------------------------------------

        if event.type == pygame.MOUSEBUTTONDOWN:

            # ----------------------------------------------------
            # SCROLL WHEEL UP
            # ----------------------------------------------------

            if event.button == 4:

                if selected_aircraft is not None:

                    details_scroll_offset -= 60 * SCALE

                continue

            # ----------------------------------------------------
            # SCROLL WHEEL DOWN
            # ----------------------------------------------------

            if event.button == 5:

                if selected_aircraft is not None:

                    details_scroll_offset += 60 * SCALE

                continue

            # ----------------------------------------------------
            # LEFT CLICK / TOUCH
            # ----------------------------------------------------

            if event.button == 1:

                mouse_pos = (event.pos[0] * SCALE, event.pos[1] * SCALE)

                # ------------------------------------------------
                # DETAILS PANEL HAS PRIORITY
                # ------------------------------------------------
                #
                # This is deliberately checked BEFORE the menu and
                # aircraft handlers.
                #
                # Therefore:
                #
                #   * X closes the details panel.
                #   * Touching inside starts a details drag.
                #   * Clicking outside closes the panel.
                #   * A details click cannot accidentally select
                #     an aircraft underneath the panel.
                #

                if selected_aircraft is not None:

                    if handle_details_click(mouse_pos):

                        continue

                # ------------------------------------------------
                # MENU
                # ------------------------------------------------

                if menu_open and menu_page == "custom_location":

                    if handle_custom_location_click(mouse_pos):

                        continue

                if handle_menu_click(mouse_pos):

                    continue

                # ------------------------------------------------
                # AIRCRAFT
                # ------------------------------------------------

                handle_aircraft_click(mouse_pos)

                continue

        # --------------------------------------------------------
        # TOUCH / MOUSE MOVEMENT
        # --------------------------------------------------------

        if event.type == pygame.MOUSEMOTION:

            if selected_aircraft is not None and details_dragging:

                mouse_pos = (event.pos[0] * SCALE, event.pos[1] * SCALE)

                handle_details_motion(mouse_pos)

            continue

        # --------------------------------------------------------
        # TOUCH / MOUSE RELEASE
        # --------------------------------------------------------

        if event.type == pygame.MOUSEBUTTONUP:

            if event.button == 1:

                handle_details_release()

            continue

    # ============================================================
    # UPDATE RADAR LAYOUT
    # ============================================================

    old_center = RADAR_CENTER

    update_radar_layout()

    if old_center != RADAR_CENTER:

        reposition_aircraft()

    # ============================================================
    # UPDATE AIRCRAFT MOVEMENT / DETAILS
    # ============================================================

    update_animation()

    update_selected_details()

    # ============================================================
    # DRAW
    # ============================================================

    canvas.fill(BACKGROUND)

    draw_radar()

    draw_range_labels()

    draw_compass()

    draw_aircraft()

    draw_radar_centre()

    draw_information()

    draw_menu_button()

    draw_menu()

    draw_aircraft_details_panel()

    # ============================================================
    # DOWNSAMPLE TO DISPLAY
    # ============================================================

    display_surface = pygame.transform.smoothscale(
        canvas, (WINDOW_WIDTH, WINDOW_HEIGHT)
    )

    window.blit(display_surface, (0, 0))

    pygame.display.flip()
