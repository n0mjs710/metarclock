#!/usr/bin/env python

# Standard library modules
import os
import sys
import time
import json
from urllib.request import Request, urlopen

def get_metar(_url):
    _metar = 'Placeholder'
    req = Request(_url)
    req.add_header('accept', 'application/json')
    print(req.header_items())
    print(req.get_full_url())
    print()

    try:
        with urlopen(_url, timeout=2) as response:
            print('RESPONSE: {}'.format(response))
            print('INFO: {}'.format(response.headers))
            _metar_json = response.read()
            print('JSON: {}'.format(_metar_json))
            try:
                _metar = json.loads(_metar_json)[0]
            except Exception as e:
                print('METAR JSON Error: {}. Received:  {}'.format(e, _metar))
    except Exception as e:
        print('COULDN\'T GET METAR AT ALL: {} ::: {}'.format(e, _url))
    print('METAR Download Successful: {}'.format(_metar))
    return _metar



url = 'https://aviationweather.gov/api/data/metar?ids=KLWC&hours=0&format=json'

while True:
    metar = get_metar(url)
    time.sleep(5)
    print()
