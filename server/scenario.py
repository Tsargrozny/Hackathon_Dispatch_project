"""The demo scenario: places, organ types and the planned route.

Everything here is editable. Change the route, organ or timings to tell a
different story on stage. Organizations and people are fictional; hospital
and airport coordinates are real so the map looks right.
"""

PLACES = {
    "donor":   {"name": "Renown Regional Medical Center", "short": "Renown, Reno", "lat": 39.5260, "lng": -119.7960, "kind": "hospital"},
    "rno":     {"name": "Reno-Tahoe Intl (RNO)", "short": "RNO airport", "lat": 39.4991, "lng": -119.7681, "kind": "airport", "charter": True},
    "lax":     {"name": "Los Angeles Intl (LAX) cargo", "short": "LAX cargo", "lat": 33.9416, "lng": -118.4085, "kind": "airport", "charter": False},
    "vny":     {"name": "Van Nuys Airport (VNY) charter terminal", "short": "VNY charter", "lat": 34.2098, "lng": -118.4895, "kind": "airport", "charter": True},
    "smf":     {"name": "Sacramento Intl (SMF)", "short": "SMF airport", "lat": 38.6954, "lng": -121.5908, "kind": "airport", "charter": True},
    "cedars":  {"name": "Cedars-Sinai Medical Center", "short": "Cedars-Sinai, LA", "lat": 34.0754, "lng": -118.3803, "kind": "center"},
    "ucdavis": {"name": "UC Davis Medical Center", "short": "UC Davis, Sacramento", "lat": 38.5536, "lng": -121.4559, "kind": "center"},
    "ucsf":    {"name": "UCSF Medical Center", "short": "UCSF, San Francisco", "lat": 37.7631, "lng": -122.4576, "kind": "center"},
}

# Typical maximum cold-storage times (minutes). Conservative ends of the usual ranges.
ORGANS = {
    "kidney": {"label": "Kidney", "window_min": 24 * 60},
    "liver":  {"label": "Liver", "window_min": 10 * 60},
    "heart":  {"label": "Heart", "window_min": 5 * 60},
}

SCENARIO = {
    "shipment_id": "KI-2614",
    "organ": "kidney",
    # Cold time already used before the trip starts (allocation took a while).
    "elapsed_at_start_min": 13 * 60,
    "destination": "cedars",
    "clock_start": "02:40",  # wall-clock time the story starts
    "opo": "Basin Donor Network (OPO)",
    "coordinator": "Dana, OPO on-call coordinator",
}

# Status thresholds as a share of the organ's total window.
AMBER_MARGIN_SHARE = 0.20
RED_MARGIN_SHARE = 0.08
TEMP_OK = (1.0, 8.0)     # amber outside this
TEMP_RED_ABOVE = 10.0    # red above this


def p(key):
    pl = PLACES[key]
    return (pl["lat"], pl["lng"])


def initial_legs():
    """The planned route: commercial cargo, the usual way kidneys travel."""
    return [
        dict(name="Packaging at donor hospital", mode="stationary", start=p("donor"), end=p("donor"),
             duration=20, custodian="Recovery team, Basin Donor Network"),
        dict(name="Courier to RNO cargo", mode="ground", start=p("donor"), end=p("rno"),
             duration=15, custodian="Courier J. Ortiz, Silver State Couriers"),
        dict(name="Cargo check-in, waiting for flight", mode="stationary", start=p("rno"), end=p("rno"),
             duration=60, custodian="Airline cargo, RNO"),
        dict(name="Commercial flight RNO to LAX", mode="flight", start=p("rno"), end=p("lax"),
             duration=85, custodian="Airline cargo hold, flight 1186"),
        dict(name="Cargo recovery at LAX", mode="stationary", start=p("lax"), end=p("lax"),
             duration=30, custodian="Airline cargo, LAX"),
        dict(name="Courier to Cedars-Sinai", mode="ground", start=p("lax"), end=p("cedars"),
             duration=40, custodian="Courier M. Reyes, Basin Courier LA"),
    ]
