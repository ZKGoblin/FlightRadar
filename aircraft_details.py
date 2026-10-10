"""
Public API used by main.py:
    fetch_details_async(icao24, callsign=None, force=False)
    get_cached_details(icao24)

Data strategy
-------------
1. ADSBDB
   - Best free source for aircraft metadata + callsign/route.
2. HexDB
   - Free fallback for aircraft metadata and route.
3. ADSB.lol
   - Free live ADS-B fallback/enrichment.
   - Useful for live speed/position/type/registration.
4. Local JSON cache
   - Prevents repeated requests.
   - Static aircraft data is retained for days.
   - Route/live data expires independently.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import quote

import requests


# configuration


MODULE_DIR = Path(__file__).resolve().parent

# keep the cache beside main.py / aircraft_details.py.
CACHE_FILE = MODULE_DIR / "aircraft_details_cache.json"

# static aircraft identity changes very rarely.
STATIC_CACHE_SECONDS = 7 * 24 * 60 * 60

# flight routes can change from one flight to another.
ROUTE_CACHE_SECONDS = 15 * 60

# live ads-b enrichment is intentionally short-lived.
LIVE_CACHE_SECONDS = 30

# a failed provider is temporarily suppressed so one bad endpoint
# cannot be hammered repeatedly.
NEGATIVE_CACHE_SECONDS = 60

HTTP_TIMEOUT = 5
HTTP_RETRIES = 1
RETRY_DELAY = 0.6

# do not allow the local cache to grow forever.
MAX_CACHE_ENTRIES = 2500


# providers


ADSBDB_AIRCRAFT_URL = "https://api.adsbdb.com/v0/aircraft/"
ADSBDB_CALLSIGN_URL = "https://api.adsbdb.com/v0/callsign/"

HEXDB_AIRCRAFT_URL = "https://hexdb.io/api/v1/aircraft/"
HEXDB_ROUTE_URL = "https://hexdb.io/api/v1/route/icao/"

ADSBLOL_ICAO_URL = "https://api.adsb.lol/v2/icao/"


# http session


session = requests.Session()

session.headers.update(
    {
        "User-Agent": "FlightRadar/1.0 personal project",
        "Accept": "application/json",
        "Connection": "keep-alive",
    }
)


# memory state


details_cache = {}
live_cache = {}
negative_cache = {}

details_lock = threading.RLock()
live_lock = threading.RLock()
negative_lock = threading.RLock()

lookup_in_progress = set()
lookup_lock = threading.Lock()

cache_loaded = False
cache_load_lock = threading.Lock()

CACHE_SAVE_INTERVAL = 60
last_cache_save = 0
cache_save_lock = threading.Lock()
cache_dirty = False


# default record


def empty_details():
    return {
        # aircraft identity
        "registration": None,
        "aircraft_type": None,
        "aircraft_icao_type": None,
        "manufacturer": None,
        "airline": None,
        "airline_icao": None,
        "airline_iata": None,
        "operator": None,
        "owner": None,
        "country": None,

        # current flight
        "callsign": None,
        "origin": None,
        "origin_icao": None,
        "origin_name": None,
        "midpoint": None,
        "midpoint_icao": None,
        "midpoint_name": None,
        "destination": None,
        "destination_icao": None,
        "destination_name": None,
        "route": None,

        # optional live values
        "speed": None,
        "latitude": None,
        "longitude": None,
        "altitude": None,
        "heading": None,
        "vertical_rate": None,

        # rotation fields retained for compatibility with main.py.
        # free public sources generally cannot provide these reliably.
        "previous_flight": None,
        "next_flight": None,
        "outbound": None,
        "inbound": None,

        # diagnostics
        "source": None,
        "route_provider": None,
        "reliability": 0,
        "reliability_text": "NO DATA",

        # timestamps
        "last_updated": 0,
        "static_updated": 0,
        "route_updated": 0,
        "live_updated": 0,

        "loading": False,
    }


# basic helpers


def clean(value):
    if value is None:
        return None

    if isinstance(value, str):
        value = value.strip()

        if not value:
            return None

    return value


def first_valid(*values):
    for value in values:
        value = clean(value)

        if value is not None:
            return value

    return None


def normalise_icao24(icao24):
    value = clean(icao24)

    if not value:
        return None

    return str(value).strip().upper().replace("~", "")


def normalise_callsign(callsign):
    value = clean(callsign)

    if not value:
        return None

    return str(value).strip().upper()


def safe_float(value):
    try:
        if value is None:
            return None

        return float(value)

    except (TypeError, ValueError):
        return None


def valid_latitude(value):
    value = safe_float(value)

    if value is None or not -90 <= value <= 90:
        return None

    return value


def valid_longitude(value):
    value = safe_float(value)

    if value is None or not -180 <= value <= 180:
        return None

    return value


def url_part(value):
    return quote(str(value), safe="")


# cache file


def load_disk_cache():
    global cache_loaded

    with cache_load_lock:
        if cache_loaded:
            return

        cache_loaded = True

    try:
        if not CACHE_FILE.exists():
            return

        with CACHE_FILE.open(
            "r",
            encoding="utf-8"
        ) as file:
            raw = json.load(file)

        if not isinstance(raw, dict):
            return

        with details_lock:
            for key, value in raw.items():

                if not isinstance(value, dict):
                    continue

                normalised = normalise_icao24(key)

                if not normalised:
                    continue

                merged = empty_details()
                merged.update(value)

                details_cache[normalised] = merged

    except Exception as error:
        print("Aircraft details cache load error:", error)


def save_disk_cache():
    """
    Atomic cache save.

    The cache is deliberately only a small JSON file. If saving fails,
    the in-memory cache continues working normally.
    """

    global last_cache_save
    global cache_dirty

    now = time.time()

    with cache_save_lock:
        if (
            now - last_cache_save
            <
            CACHE_SAVE_INTERVAL
        ):
            cache_dirty = True
            return

        last_cache_save = now
        cache_dirty = False

    try:
        with details_lock:
            items = list(details_cache.items())

            if len(items) > MAX_CACHE_ENTRIES:
                items.sort(
                    key=lambda item: item[1].get(
                        "last_updated",
                        0
                    )
                    if isinstance(item[1], dict)
                    else 0,
                    reverse=True
                )

                items = items[:MAX_CACHE_ENTRIES]
            payload = dict(items)

        temporary = CACHE_FILE.with_suffix(
            CACHE_FILE.suffix + ".tmp"
        )

        with temporary.open(
            "w",
            encoding="utf-8"
        ) as file:
            json.dump(
                payload,
                file,
                ensure_ascii=False,
                separators=(",",":"),
            )

        os.replace(
            temporary,
            CACHE_FILE
        )

    except Exception as error:
        print(
            "cache error",
        )

def cache_cleanup():
    now = time.time()

    with details_lock:
        stale = []

        for icao24, record in details_cache.items():

            last = safe_float(
                record.get("last_updated", 0)
            ) or 0

            if (
                last
                and
                now - last > STATIC_CACHE_SECONDS * 2
            ):
                stale.append(icao24)

        for icao24 in stale:
            details_cache.pop(
                icao24,
                None
            )


# http


def http_get(
    url,
    params=None,
    timeout=HTTP_TIMEOUT,
    retries=HTTP_RETRIES,
):
    attempts = max(
        1,
        retries + 1
    )

    last_error = None

    for attempt in range(attempts):

        try:
            response = session.get(
                url,
                params=params,
                timeout=timeout,
            )

            # 404 is expected for unknown aircraft/routes.
            if response.status_code == 404:
                return response

            # rate limits and temporary server errors are retryable.
            if response.status_code in (
                408,
                425,
                429,
                500,
                502,
                503,
                504,
            ):

                last_error = (
                    f"HTTP {response.status_code}"
                )

                if attempt < attempts - 1:
                    time.sleep(
                        RETRY_DELAY
                        * (attempt + 1)
                    )

                    continue

            response.raise_for_status()

            return response

        except requests.RequestException as error:

            last_error = error

            if attempt < attempts - 1:
                time.sleep(
                    RETRY_DELAY
                    * (attempt + 1)
                )

                continue

    if last_error:
        raise last_error

    return None


def get_json(
    url,
    params=None,
    timeout=HTTP_TIMEOUT,
):
    try:
        response = http_get(
            url,
            params=params,
            timeout=timeout,
        )

        if response is None:
            return None

        if response.status_code == 404:
            return None

        data = response.json()

        if isinstance(data, dict):
            return data

    except Exception:
        return None

    return None


# merging


def merge_details(base, incoming):
    """
    Add useful incoming values without allowing None/blank data
    to erase good cached information.
    """

    result = empty_details()

    if base:
        result.update(base)

    if incoming:
        for key, value in incoming.items():

            value = clean(value)

            if value is not None:
                result[key] = value

    return result


def clear_route_fields(record):
    for key in (
        "origin",
        "origin_icao",
        "origin_name",
        "midpoint",
        "midpoint_icao",
        "midpoint_name",
        "destination",
        "destination_icao",
        "destination_name",
        "route",
        "route_provider",
    ):
        record[key] = None

    record["route_updated"] = 0


# negative cache


def negative_key(icao24, callsign=None):
    return (
        normalise_icao24(icao24),
        normalise_callsign(callsign),
    )


def recently_failed(
    icao24,
    callsign=None,
    force=False,
):
    if force:
        return False

    key = negative_key(
        icao24,
        callsign
    )

    now = time.time()

    with negative_lock:
        timestamp = negative_cache.get(
            key
        )

        if timestamp is None:
            return False

        if now - timestamp < NEGATIVE_CACHE_SECONDS:
            return True

        negative_cache.pop(
            key,
            None
        )

    return False


def mark_failed(
    icao24,
    callsign=None,
):
    with negative_lock:
        negative_cache[
            negative_key(
                icao24,
                callsign
            )
        ] = time.time()


def clear_failed(
    icao24,
    callsign=None,
):
    with negative_lock:
        negative_cache.pop(
            negative_key(
                icao24,
                callsign
            ),
            None
        )


# airport / route helpers


def airport_code(airport):
    if not isinstance(airport, dict):
        return None

    return first_valid(
        airport.get("icao_code"),
        airport.get("icao"),
        airport.get("iata_code"),
        airport.get("iata"),
    )


def airport_icao(airport):
    if not isinstance(airport, dict):
        return None

    return first_valid(
        airport.get("icao_code"),
        airport.get("icao"),
    )


def airport_name(airport):
    if not isinstance(airport, dict):
        return None

    return first_valid(
        airport.get("name"),
        airport.get("municipality"),
    )


def route_string(origin, destination):
    if not origin or not destination:
        return None

    return (
        str(origin)
        + " -> "
        + str(destination)
    )


def parse_route_string(route):
    """
    Handles common route forms such as:
        EGLL-EIDW
        EGLL -> EIDW
        EGLL/EIDW
    """

    if not route:
        return None, None

    text = str(route).strip().upper()

    for separator in (
        "->",
        "-",
        "/",
        " ",
    ):
        if separator in text:

            parts = [
                part.strip()
                for part in text.split(separator)
                if part.strip()
            ]

            if len(parts) >= 2:

                origin = parts[0]
                destination = parts[-1]

                if (
                    len(origin) >= 3
                    and
                    len(destination) >= 3
                ):
                    return (
                        origin,
                        destination
                    )

    return None, None


# adsbdb


def fetch_adsbdb(
    icao24,
    callsign=None,
):
    """
    ADSBDB combined endpoint.

    One request can return both aircraft metadata and the current
    callsign's route, which makes it the primary enrichment source.
    """

    icao24 = normalise_icao24(
        icao24
    )

    callsign = normalise_callsign(
        callsign
    )

    if not icao24:
        return None

    url = (
        ADSBDB_AIRCRAFT_URL
        + url_part(icao24)
    )

    params = {}

    if callsign:
        params["callsign"] = callsign

    try:
        data = get_json(
            url,
            params=params,
            timeout=HTTP_TIMEOUT,
        )

    except Exception as error:
        print(
            "ADSBDB error:",
            error
        )
        return None

    if not data:
        return None

    response = data.get(
        "response"
    )

    if not isinstance(
        response,
        dict
    ):
        return None

    result = empty_details()


    # aircraft


    aircraft = response.get(
        "aircraft"
    )

    if isinstance(
        aircraft,
        dict
    ):

        result["registration"] = first_valid(
            aircraft.get("registration")
        )

        result["aircraft_type"] = first_valid(
            aircraft.get("type")
        )

        result["aircraft_icao_type"] = first_valid(
            aircraft.get("icao_type"),
            aircraft.get("icao_type_code"),
        )

        result["manufacturer"] = first_valid(
            aircraft.get("manufacturer")
        )

        result["owner"] = first_valid(
            aircraft.get("registered_owner")
        )

        result["country"] = first_valid(
            aircraft.get(
                "registered_owner_country_name"
            )
        )

        result["operator"] = first_valid(
            aircraft.get(
                "registered_owner"
            )
        )

        result["airline_icao"] = first_valid(
            aircraft.get(
                "registered_owner_operator_flag_code"
            )
        )


    # flight route


    flight_route = response.get(
        "flightroute"
    )

    if isinstance(
        flight_route,
        dict
    ):

        result["callsign"] = first_valid(
            flight_route.get("callsign"),
            callsign,
        )

        airline = flight_route.get(
            "airline"
        )

        if isinstance(
            airline,
            dict
        ):

            result["airline"] = first_valid(
                airline.get("name")
            )

            result["airline_icao"] = first_valid(
                airline.get("icao"),
                result.get("airline_icao"),
            )

            result["airline_iata"] = first_valid(
                airline.get("iata")
            )

            result["operator"] = first_valid(
                airline.get("name"),
                result.get("operator"),
            )

            result["country"] = first_valid(
                result.get("country"),
                airline.get("country"),
            )

        origin = flight_route.get(
            "origin"
        )

        destination = flight_route.get(
            "destination"
        )

        midpoint = flight_route.get(
            "midpoint"
        )

        result["origin"] = airport_code(
            origin
        )

        result["origin_icao"] = airport_icao(
            origin
        )

        result["origin_name"] = airport_name(
            origin
        )

        if isinstance(
            midpoint,
            dict
        ):

            result["midpoint"] = airport_code(
                midpoint
            )

            result["midpoint_icao"] = airport_icao(
                midpoint
            )

            result["midpoint_name"] = airport_name(
                midpoint
            )

        result["destination"] = airport_code(
            destination
        )

        result["destination_icao"] = airport_icao(
            destination
        )

        result["destination_name"] = airport_name(
            destination
        )

        result["route"] = route_string(
            result["origin"],
            result["destination"],
        )

    useful = any(
        result.get(key)
        for key in (
            "registration",
            "aircraft_type",
            "aircraft_icao_type",
            "manufacturer",
            "airline",
            "origin",
            "destination",
            "callsign",
        )
    )

    if not useful:
        return None

    result["source"] = "ADSBDB"

    return result


# hexdb aircraft


def fetch_hexdb_aircraft(
    icao24
):
    icao24 = normalise_icao24(
        icao24
    )

    if not icao24:
        return None

    url = (
        HEXDB_AIRCRAFT_URL
        + url_part(icao24)
    )

    try:
        data = get_json(
            url,
            timeout=HTTP_TIMEOUT,
        )

    except Exception as error:
        print(
            "HexDB aircraft error:",
            error
        )
        return None

    if not data:
        return None

    if data.get("status") == "404":
        return None

    result = empty_details()

    result["registration"] = first_valid(
        data.get("Registration"),
        data.get("registration"),
    )

    result["aircraft_icao_type"] = first_valid(
        data.get("ICAOTypeCode"),
        data.get("icao_type"),
        data.get("ICAOType"),
    )

    result["aircraft_type"] = first_valid(
        data.get("Type"),
        data.get("type"),
    )

    result["manufacturer"] = first_valid(
        data.get("Manufacturer"),
        data.get("manufacturer"),
    )

    result["operator"] = first_valid(
        data.get("RegisteredOwners"),
        data.get("Operator"),
    )

    result["owner"] = first_valid(
        data.get("RegisteredOwners"),
        data.get("owner"),
    )

    result["airline_icao"] = first_valid(
        data.get("OperatorFlagCode"),
        data.get("operator_flag_code"),
    )

    if any(
        result.get(key)
        for key in (
            "registration",
            "aircraft_icao_type",
            "aircraft_type",
            "manufacturer",
            "owner",
        )
    ):
        result["source"] = "HEXDB"
        return result

    return None


# hexdb route


def fetch_hexdb_route(
    callsign
):
    callsign = normalise_callsign(
        callsign
    )

    if not callsign:
        return None

    url = (
        HEXDB_ROUTE_URL
        + url_part(callsign)
    )

    try:
        data = get_json(
            url,
            timeout=HTTP_TIMEOUT,
        )

    except Exception as error:
        print(
            "HexDB route error:",
            error
        )
        return None

    if not data:
        return None

    if data.get("status") == "404":
        return None

    route = first_valid(
        data.get("route")
    )

    if not route:
        return None

    origin, destination = (
        parse_route_string(route)
    )

    if not origin or not destination:
        return None

    result = empty_details()

    result["callsign"] = first_valid(
        data.get("flight"),
        callsign,
    )

    result["origin"] = origin
    result["origin_icao"] = origin
    result["destination"] = destination
    result["destination_icao"] = destination
    result["route"] = route_string(
        origin,
        destination,
    )
    result["route_provider"] = "HEXDB"

    return result


# adsb.lol live


def parse_adsblol_aircraft(item):
    if not isinstance(
        item,
        dict
    ):
        return None

    result = empty_details()

    result["callsign"] = normalise_callsign(
        first_valid(
            item.get("flight"),
            item.get("callsign"),
        )
    )

    result["registration"] = first_valid(
        item.get("r"),
        item.get("registration"),
    )

    result["aircraft_icao_type"] = first_valid(
        item.get("t"),
        item.get("type"),
        item.get("icao_type"),
    )

    result["aircraft_type"] = first_valid(
        item.get("typeDescription"),
        item.get("type_description"),
        item.get("description"),
    )

    result["latitude"] = valid_latitude(
        item.get("lat")
    )

    result["longitude"] = valid_longitude(
        item.get("lon")
    )

    # adsb.lol / readsb uses knots for gs.
    result["speed"] = safe_float(
        first_valid(
            item.get("gs"),
            item.get("ground_speed"),
        )
    )

    result["heading"] = safe_float(
        first_valid(
            item.get("track"),
            item.get("heading"),
        )
    )

    result["altitude"] = safe_float(
        first_valid(
            item.get("alt_baro"),
            item.get("alt_geom"),
        )
    )

    result["vertical_rate"] = safe_float(
        first_valid(
            item.get("baro_rate"),
            item.get("geom_rate"),
        )
    )

    if any(
        result.get(key) is not None
        for key in (
            "registration",
            "aircraft_icao_type",
            "latitude",
            "longitude",
            "speed",
            "heading",
        )
    ):
        result["source"] = "ADSB.LOL"
        return result

    return None


def fetch_adsblol_live(
    icao24,
    force=False,
):
    icao24 = normalise_icao24(
        icao24
    )

    if not icao24:
        return None

    now = time.time()

    with live_lock:
        cached = live_cache.get(
            icao24
        )

        if (
            cached
            and
            not force
            and
            now
            -
            cached.get(
                "live_updated",
                0
            )
            <
            LIVE_CACHE_SECONDS
        ):
            return dict(cached)

    url = (
        ADSBLOL_ICAO_URL
        + url_part(icao24)
    )

    try:
        data = get_json(
            url,
            timeout=HTTP_TIMEOUT,
        )

    except Exception as error:
        print(
            "ADSB.lol live error:",
            error
        )
        return None

    if not data:
        return None

    aircraft_list = data.get(
        "ac"
    )

    if not isinstance(
        aircraft_list,
        list
    ):
        return None

    # the exact hex lookup should normally return one aircraft.
    # if multiple records exist, choose the first one with a position.
    selected = None

    for item in aircraft_list:

        if not isinstance(
            item,
            dict
        ):
            continue

        item_hex = normalise_icao24(
            item.get("hex")
        )

        if item_hex == icao24:
            selected = item
            break

    if selected is None and aircraft_list:
        selected = aircraft_list[0]

    result = parse_adsblol_aircraft(
        selected
    )

    if not result:
        return None

    result["live_updated"] = now

    with live_lock:
        live_cache[icao24] = dict(
            result
        )

    return result


# reliability


def calculate_reliability(
    result
):
    """
    Produces a conservative 0-100 confidence score.

    This is NOT an aviation-certified confidence value. It is intended
    only to tell the UI how much independent/free data agrees.

    Strongest:
        ADSBDB aircraft + route + live ADS-B

    Weaker:
        HexDB only

    Live ADS-B is treated as strong evidence for instantaneous
    position/speed, while route data is treated separately.
    """

    if not result:
        return 0, "NO DATA"

    score = 0

    static_fields = (
        "registration",
        "aircraft_icao_type",
        "manufacturer",
        "airline",
    )

    static_count = sum(
        bool(result.get(key))
        for key in static_fields
    )

    if static_count >= 3:
        score += 30
    elif static_count == 2:
        score += 24
    elif static_count == 1:
        score += 15

    route_fields = (
        "origin",
        "destination",
        "route",
    )

    route_count = sum(
        bool(result.get(key))
        for key in route_fields
    )

    if route_count == 3:
        score += 30
    elif route_count == 2:
        score += 24
    elif route_count == 1:
        score += 12

    source = str(
        result.get(
            "source",
            ""
        )
    ).upper()

    if "ADSBDB" in source:
        score += 15

    if "HEXDB" in source:
        score += 8

    if result.get(
        "live_updated"
    ):
        age = (
            time.time()
            -
            safe_float(
                result.get(
                    "live_updated",
                    0
                )
            )
        )

        if age < 30:
            score += 20
        elif age < 120:
            score += 12
        else:
            score += 5

    if result.get(
        "speed"
    ) is not None:
        score += 3

    score = max(
        0,
        min(
            100,
            int(score)
        )
    )

    if score >= 85:
        text = "HIGH"
    elif score >= 65:
        text = "GOOD"
    elif score >= 40:
        text = "FAIR"
    elif score > 0:
        text = "LOW"
    else:
        text = "NO DATA"

    return score, text


# route identity


def route_belongs_to_callsign(
    result,
    callsign
):
    if not result:
        return False

    expected = normalise_callsign(
        callsign
    )

    actual = normalise_callsign(
        result.get(
            "callsign"
        )
    )

    if not expected or not actual:
        return True

    return expected == actual


# main detail fetch


def fetch_details(
    icao24,
    callsign=None,
    force=False,
    latitude=None,
    longitude=None,
):
    """
    Build the best currently available record.

    `force=True` means the caller wants a fresh route/live lookup.
    It intentionally does NOT force-download static aircraft data
    every 10 seconds, because that would be wasteful and can trigger
    provider rate limits.
    """

    load_disk_cache()

    icao24 = normalise_icao24(
        icao24
    )

    callsign = normalise_callsign(
        callsign
    )

    if not icao24:
        return empty_details()

    now = time.time()

    with details_lock:
        cached = details_cache.get(
            icao24
        )

    result = empty_details()

    if cached:
        result = merge_details(
            result,
            cached
        )


    # callsign supplied by opensky always wins over stale cache.


    if callsign:
        old_callsign = normalise_callsign(
            result.get("callsign")
        )

        if (
            old_callsign
            and
            old_callsign != callsign
        ):
            # the same physical aircraft can perform several flights
            # in a day. never carry the old flight's route over.
            clear_route_fields(
                result
            )

        result["callsign"] = callsign


    # position supplied by main.py.


    lat = valid_latitude(
        latitude
    )

    lon = valid_longitude(
        longitude
    )

    if lat is not None:
        result["latitude"] = lat

    if lon is not None:
        result["longitude"] = lon


    # static cache decision.


    static_updated = safe_float(
        result.get(
            "static_updated",
            0
        )
    ) or 0

    static_fresh = (
        static_updated > 0
        and
        now - static_updated
        <
        STATIC_CACHE_SECONDS
    )


    # adsbdb

    # even when static data is cached, run adsbdb if the route is
    # stale. this is the main way to detect a new flight.


    route_updated = safe_float(
        result.get(
            "route_updated",
            0
        )
    ) or 0

    route_fresh = (
        route_updated > 0
        and
        now - route_updated
        <
        ROUTE_CACHE_SECONDS
        and
        route_belongs_to_callsign(
            result,
            callsign
        )
    )

    need_adsbdb = (
        not static_fresh
        or
        not route_fresh
        or
        force
    )

    adsbdb_result = None

    if (
        need_adsbdb
        and
        not recently_failed(
            icao24,
            callsign,
            force=force
        )
    ):

        adsbdb_result = fetch_adsbdb(
            icao24,
            callsign
        )

        if adsbdb_result:

            # if adsbdb knows the current callsign, its route belongs
            # to that flight and can be accepted.
            incoming_callsign = normalise_callsign(
                adsbdb_result.get(
                    "callsign"
                )
            )

            if (
                callsign
                and
                incoming_callsign
                and
                incoming_callsign != callsign
            ):
                # keep static aircraft data, but reject the route.
                for key in (
                    "origin",
                    "origin_icao",
                    "origin_name",
                    "midpoint",
                    "midpoint_icao",
                    "midpoint_name",
                    "destination",
                    "destination_icao",
                    "destination_name",
                    "route",
                ):
                    adsbdb_result[key] = None

            else:
                if callsign:
                    adsbdb_result["callsign"] = callsign

                if (
                    adsbdb_result.get(
                        "origin"
                    )
                    and
                    adsbdb_result.get(
                        "destination"
                    )
                ):
                    adsbdb_result[
                        "route_updated"
                    ] = now

                    adsbdb_result[
                        "route_provider"
                    ] = "ADSBDB"

            # static information can be trusted for the long cache.
            if any(
                adsbdb_result.get(key)
                for key in (
                    "registration",
                    "aircraft_type",
                    "aircraft_icao_type",
                    "manufacturer",
                )
            ):
                adsbdb_result[
                    "static_updated"
                ] = now

            result = merge_details(
                result,
                adsbdb_result
            )

            clear_failed(
                icao24,
                callsign
            )

        else:
            mark_failed(
                icao24,
                callsign
            )


    # hexdb static fallback


    static_updated = safe_float(
        result.get(
            "static_updated",
            0
        )
    ) or 0

    static_fresh = (
        static_updated > 0
        and
        now - static_updated
        <
        STATIC_CACHE_SECONDS
    )

    missing_static = (
        not result.get("registration")
        or
        not result.get("aircraft_icao_type")
        or
        not result.get("manufacturer")
    )

    if (
        not static_fresh
        or
        missing_static
    ):

        hex_aircraft = fetch_hexdb_aircraft(
            icao24
        )

        if hex_aircraft:

            result = merge_details(
                result,
                hex_aircraft
            )

            result["static_updated"] = (
                result.get(
                    "static_updated"
                )
                or
                now
            )


    # hexdb route fallback


    route_updated = safe_float(
        result.get(
            "route_updated",
            0
        )
    ) or 0

    route_fresh = (
        route_updated > 0
        and
        now - route_updated
        <
        ROUTE_CACHE_SECONDS
        and
        route_belongs_to_callsign(
            result,
            callsign
        )
    )

    if (
        callsign
        and
        (
            not route_fresh
            or
            not result.get("route")
        )
    ):

        hex_route = fetch_hexdb_route(
            callsign
        )

        if (
            hex_route
            and
            route_belongs_to_callsign(
                hex_route,
                callsign
            )
        ):

            result = merge_details(
                result,
                hex_route
            )

            result["route_updated"] = now
            result["route_provider"] = "HEXDB"


    # live adsb.lol


    live = fetch_adsblol_live(
        icao24,
        force=force
    )

    if live:

        # do not allow an adsb.lol route/callsign from another flight
        # to overwrite the route selected by opensky.
        live_callsign = normalise_callsign(
            live.get("callsign")
        )

        if (
            not callsign
            or
            not live_callsign
            or
            live_callsign == callsign
        ):
            result = merge_details(
                result,
                live
            )

        else:
            # keep only instantaneous physical values.
            for key in (
                "speed",
                "latitude",
                "longitude",
                "altitude",
                "heading",
                "vertical_rate",
                "registration",
                "aircraft_icao_type",
            ):
                value = live.get(key)

                if value is not None:
                    result[key] = value


    # ensure opensky callsign remains authoritative.


    if callsign:
        result["callsign"] = callsign


    # ensure route is internally consistent.


    if (
        not result.get("route")
        and
        result.get("origin")
        and
        result.get("destination")
    ):
        result["route"] = route_string(
            result["origin"],
            result["destination"]
        )


    # reliability


    score, text = calculate_reliability(
        result
    )

    result["reliability"] = score
    result["reliability_text"] = text


    # status


    result["loading"] = False
    result["last_updated"] = now

    # preserve a static timestamp from an older successful lookup.
    if not result.get(
        "static_updated"
    ) and cached:
        result["static_updated"] = (
            cached.get(
                "static_updated",
                0
            )
        )

    with details_lock:
        details_cache[
            icao24
        ] = dict(result)

    save_disk_cache()

    return result


# async fetch


def fetch_details_async(
    icao24,
    callsign=None,
    force=False,
    latitude=None,
    longitude=None,
):
    """
    Background-compatible API used directly by main.py.

    Repeated requests for the same aircraft while a lookup is already
    running are ignored. This is particularly important because the
    radar refreshes every aircraft every 10 seconds.
    """

    load_disk_cache()

    cache_key = normalise_icao24(
        icao24
    )

    if not cache_key:
        return

    with lookup_lock:

        if cache_key in lookup_in_progress:
            return

        lookup_in_progress.add(
            cache_key
        )

    def worker():

        try:
            fetch_details(
                cache_key,
                callsign,
                force,
                latitude,
                longitude,
            )

        except Exception as error:

            print(
                "Aircraft detail worker error:",
                error
            )

        finally:

            with lookup_lock:
                lookup_in_progress.discard(
                    cache_key
                )

    thread = threading.Thread(
        target=worker,
        daemon=True,
        name=f"aircraft-details-{cache_key}",
    )

    thread.start()


# cache access


def get_cached_details(
    icao24
):
    """
    Return the latest known information immediately.

    This function never performs a blocking network request.
    """

    load_disk_cache()

    cache_key = normalise_icao24(
        icao24
    )

    if not cache_key:
        return empty_details()

    with details_lock:
        cached = details_cache.get(
            cache_key
        )

    if cached:
        return dict(cached)

    return empty_details()


# startup


# load the small local cache when the module is imported.

# this is deliberately fast and does not make any network requests.
load_disk_cache()
cache_cleanup()
