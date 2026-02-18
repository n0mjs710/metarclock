#!/usr/bin/env python

# Standard library modules
import time
import json
import requests

def get_metar(_url):
    _metar = 'Placeholder'

    plain = {'accept': 'text/plain'}
    html = {'accept': 'text/html'}
    xml = {'accept': 'application/xml'}
    json = {'accept': 'application/json'}
    response = requests.get(_url, timeout=3)
    response.close()
    print(response.content)
    return response


url = 'https://aviationweather.gov/api/data/metar?ids=KLWC&hours=0&format=json'

while True:
    metar = get_metar(url)
    time.sleep(5)
    print()
