"""Small geography helpers: distances and rough travel-time estimates."""
import math

LatLng = tuple[float, float]

# Rough planning constants. Tune these if your demo route changes.
ROAD_FACTOR = 1.25       # roads are longer than straight lines
GROUND_KMH = 80          # average courier speed incl. city driving
GROUND_OVERHEAD_MIN = 10 # loading / parking / walking into the hospital
FLIGHT_KMH = 550         # light jet / regional cruise, averaged
FLIGHT_OVERHEAD_MIN = 25 # taxi, climb, descent


def haversine_km(a: LatLng, b: LatLng) -> float:
    lat1, lon1 = map(math.radians, a)
    lat2, lon2 = map(math.radians, b)
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def lerp(a: LatLng, b: LatLng, t: float) -> LatLng:
    t = max(0.0, min(1.0, t))
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def ground_minutes(a: LatLng, b: LatLng) -> float:
    return haversine_km(a, b) * ROAD_FACTOR / GROUND_KMH * 60 + GROUND_OVERHEAD_MIN


def flight_minutes(a: LatLng, b: LatLng) -> float:
    return haversine_km(a, b) / FLIGHT_KMH * 60 + FLIGHT_OVERHEAD_MIN
