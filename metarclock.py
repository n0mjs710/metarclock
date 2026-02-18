#!/usr/bin/env python

# Non standard library modules add to requirements file.
import serial
from netifaces import ifaddresses, AF_INET, AF_INET6
from tzlocal import get_localzone

# Standard library modules
import os
import sys
import subprocess
import logging
import logging.handlers
import time
import json
#from urllib.request import Request, build_opener, install_opener
#from urllib.error import HTTPError, URLError
import httpx
from datetime import datetime, timedelta
from dateutil import parser, tz
from zoneinfo import ZoneInfo
from configparser import ConfigParser

# Time constants
zones = {'ut': ZoneInfo('UTC'),'et': ZoneInfo('America/New_York'),'ct':ZoneInfo('America/Chicago'),'mt':ZoneInfo('America/Denver'),'pt':ZoneInfo('America/Los_Angeles'),'lt':get_localzone()}

friendlyTime = '%I:%M %p'
friendlyTimeZ = '%I:%M %p %Z'
friendlyDate = '%A %B %d, %Y'
friendlyTz = '%Z'

### VARIABLE INITIALIZATION
metar_id    = 0
lastOnline  = False
online      = False
warn        = ''     # string, multi-line 15 characters x 5 lines
dim         = False
lastdim     = '10'
lastbright  = '100'
ipaddr = 'offline'

### OTHER CONSTANTS
spinner = ['|','/','--','\\\\']
# Nextion Commands
EndCom   = b'\xff\xff\xff'
green    = 2016
blue     = 31
black    = 0
brown    = 48192
yellow   = 65504
gray     = 33840
white    = 65535
red      = 63488

### BUILD URL OPENER OBJECT THAT SENDS REQUIRED HEADERS ON EVERY REQUEST
UA = "MetarClock/1.0 (https://github.com/n0mjs710/metarclock; [email protected])"

HEADER = {
    "User-Agent": UA,  # swap for a Safari UA if needed
    "Accept": "application/json,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://aviationweather.gov/",
    "Origin": "https://aviationweather.gov",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
    "Connection": "keep-alive",
}


### Helper functions
# Create a datetime object from METAR date/time string
def mkDatetime (_dtimestring):
    _dtimestring = parser.isoparse(_dtimestring)
    #_dtimestring = _dtimestring.astimezone(tz.gettz("US/Central"))
    return _dtimestring #datetime.strptime(_dtimestring, '%Y-%m-%d %H:%M:%S').replace(tzinfo=zones['ut'])

# Make datetime object "aware" from a supplied timezone
def mkLocalTime (_dtimestring, _tz):
    return _dtimestring.astimezone(_tz)

# return a friendly string from a datetime object with a supplied format 
def friendlyT (_datetime, _format):
    return _datetime.strftime(_format)

# convert and round temp from C to F
def ctof(_ctemp):
    return round((_ctemp*9.0/5.0)+32)

# convert and round speed from KT to MPH
def ktom(_kts):
    _kts = int(0 if _kts is None else _kts)
    return round(_kts * 1.15078)



# get the metar from the weather service and make a rudimentary test to see if the data is ok
def get_metar(_url):
    global metar_id
    try:
        with httpx.Client(http2=True, headers=HEADER, timeout=10) as client:
            r = client.get(_url)
        if r.status_code == 200:
            data = r.json()
            return data[0] if isinstance(data, list) and data else "METAR Data Bad"
        logger.warning("[METARClock] AWC HTTP %s", r.status_code)
        metar_id = 0
        return "URL Unreachable" if r.status_code == 403 else f"HTTP {r.status_code}"
    except Exception as e:
        logger.warning("[METARClock] AWC fetch error: %s", e)
        metar_id = 0
        return "URL Unreachable"

# NEW CODE HERE
def nextion_recover():
    logger.warning('{} Attempting Nextion resync...'.format(logPFX))
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    ser.write(b'\xff\xff\xff\xff\xff')   # flush any partial command
    time.sleep(0.05)
    ser.reset_input_buffer()
    # Escalate to soft reset if needed — uncomment if flush alone isn't enough:
    # ser.write(b'rest\xff\xff\xff')
    # time.sleep(1.5)
    # ser.reset_input_buffer()
    logger.warning('{} Nextion resync complete'.format(logPFX))


def serialReceive():
    received = None  # safe default — fixes the unbound variable bug

    if not ser.in_waiting:
        return None

    try:
        raw = ser.read_until(expected=EndCom)
        received = raw[:-3].decode('utf-8')
    except UnicodeDecodeError:
        logger.error('{} Non-UTF8 data from Nextion, triggering resync: {}'.format(logPFX, repr(raw)))
        nextion_recover()
        return None

    # \x1a is Nextion's "invalid command" error byte — stream is out of sync
    if '\x1a' in received:
        logger.warning('{} Nextion returned 0x1A error byte, triggering resync'.format(logPFX))
        nextion_recover()
        return None

    if ser.in_waiting:
        errSerial = ser.read(ser.in_waiting)
        logger.error('{} Valid received: {}, but unexpected serial data waiting: {}'.format(logPFX, repr(received), repr(errSerial)))
        ser.reset_input_buffer()

    return received
# END NEW CODE

''' REPLACED CODE FOLLOWS
# Receive data from the Nextion
def serialReceive():
    if ser.in_waiting:
        received = ser.read_until(expected=EndCom)[:-3].decode('utf-8')
        if ser.in_waiting:
            errSerial = ser.read(ser.in_waiting)
            logger.error('{} Vaid received: {}, but unexpected serial data waiting: {}'.format(logPFX, repr(received), repr(errSerial)))
            ser.reset_input_buffer()
    return received if received else None
'''
# Send a string to the Nextion, making it a bytes object in utf-8, and sending the EndCom
def nextionWrite(_string):
    _val = bytes(_string, 'utf-8')
    _val += EndCom
    ser.write(_val)

# Convert all type None items in METAR to 0. Only used for workaround/development
def dictClean(dirty):
    clean = {}
    for key, value in dirty:
        if value is None:
            value = 0
        clean[key] = value
    return clean

# Execute a system command -- used ONLY for nmcli (changing WiFi networks)
def execute(command):
    try:
        output = subprocess.check_output(command, shell=True, stderr=subprocess.STDOUT, universal_newlines=True)
        return output
    except subprocess.CalledProcessError as e:
        logger.error('{} Command execution failed with error code {}: {}'.format(logPFX, e.returncode, e.output))
        return None

# Check for active network connection
def checkOnline():
    global ipaddr, online, lastOnline
    online = AF_INET in ifaddresses(netInterface)
    if online != lastOnline:
        if online:
            nextionWrite('data.nowifi.aph=0')       # turn off no wifi icon
            nextionWrite('data.wifi.aph=127')       # turn on wifi icon
            ipaddr = ifaddresses(netInterface)[2][0]['addr']
        else:
            nextionWrite('data.wifi.aph=0')
            nextionWrite('data.nowifi.aph=127')
            ipaddr = 'Offline'
        lastOnline = online
        nextionWrite('settings.ipaddr.txt=\"{}\"'.format(ipaddr))
        logger.info('{} WiFi interface change detected, IP Address: {}'.format(logPFX, ipaddr))
    return online

# Write the configuration file
def writeConfig():
    try:
        with open(cfgFile, mode='w') as configFile:
            config.write(configFile)
        logger.info('{} Successful configuration file write during user configuration'.format(logPFX))
    except Exception as e:
        logger.error('{} Could not write configuration file: {}'.format(logPFX, e))

### STUFF DONE ONCE ON STARTUP
def startup():
    global lastOnline, online, lastdim, lastbright, ipaddr, dim

    # Set the nextion for startup
    nextionWrite('page splash')
   
    # Check for active network connection
    # This is a one-off for the startup routine
    online = AF_INET in ifaddresses(netInterface)
    if not online:
        for i in range(72):
            nextionWrite('splash.ipaddr.txt=\"{}\"'.format(spinner[i%4]))
            time.sleep(.1)
            online = AF_INET in ifaddresses(netInterface)
            if online:
                break
    
    # Handle whether we're using MPH or KT for this clock
    if eval(config['system']['mph']) == True:
        nextionWrite('settings.spdunit.txt=\"MPH\"')
        nextionWrite('data.mph.aph=127')       # turn on MPH
        nextionWrite('data.kt.aph=0')          # turn off KT
    else:
        nextionWrite('settings.spdunit.txt=\"KT\"')
        nextionWrite('data.kt.aph=127')        # turn on KT
        nextionWrite('data.mph.aph=0')         # turn off MPH

    # Handle WiFi icon on splash, data and settings pages
    checkOnline()
    nextionWrite('splash.ipaddr.txt=\"{}\"'.format(ipaddr))
    time.sleep(2)          # Ensures we see the IP address on the display briefly before starting the main loop

    # Set all settings page items to the initial value from the saved configuration file
    nextionWrite('settings.ipaddr.txt=\"{}\"'.format(ipaddr))
    nextionWrite('settings.station.txt=\"{}\"'.format(config['awos']['station']))
    nextionWrite('settings.ssid.txt=\"{}\"'.format(config['wifi']['ssid']))
    nextionWrite('settings.password.txt="{}\"'.format(config['wifi']['password']))
    nextionWrite('settings.dim_on.txt=\"{}:{:02d}\"'.format(config['display']['dimhr'],int(config['display']['dimmin'])))
    nextionWrite('settings.brt_on.txt=\"{}:{:02d}\"'.format(config['display']['brthr'],int(config['display']['brtmin']))) 
    for key in zones.keys():
        if key == config['system']['tz']:
            nextionWrite('settings.{}.val=1'.format(key))
        else:
            nextionWrite('settings.{}.val=0'.format(key))
    
    # This is really complicated b/c time "rols over" at midnight. I fix this by determining
    # the span of time within a day, and whether the clock should be bright or dim during
    # that span. This allows me to set the opposite condition outside of that span without
    # having to calculate what happens with rollover at mightnight. This is almost exactly
    # duplicated in the housekeeping routing, except that there, we care about knowing what
    # value is already set (so we don't keep resetting the display), but this is an initial
    # setting, so we leave out the test to determine what state the display is already in.
    currentDTime = datetime.now().astimezone(get_localzone())                   # Getting datetime.now will produce an unaware time in the local TZ -- make it aware
    currentDTime = currentDTime.astimezone(zones[config['system']['tz']])       # Convert it into the timezone we're asking it to display.

    nowTime  = currentDTime.replace(second=0, microsecond=0)
    brightTime = nowTime.replace(hour=int(config['display']['brthr']),  minute=int(config['display']['brtmin']))
    dimTime    = nowTime.replace(hour=int(config['display']['dimhr']),  minute=int(config['display']['dimmin']))

    if dimTime > brightTime:                                                    # *** The "bright" time is during the day because we go dim later than we go bright
        if nowTime >= brightTime and nowTime < dimTime:                         #       We are within the window to be bright
            nextionWrite('dim={}'.format(int(config['display']['brtval'])))           #       Change to bright
            dim = False                                                         #       Flag that we're now bright
            logger.info('{} Display initialized to BRIGHT: {} (datime bright)'.format(logPFX, nowTime))
        if not (nowTime >= brightTime and nowTime < dimTime):                   #       We are outside the window to be bright
            nextionWrite('dim={}'.format(int(config['display']['dimval'])))                               #       Change to dim
            dim = True                                                          #       Flag that we're now dim 
            logger.info('{} Display initialized to DIM: {} (datime bright)'.format(logPFX, nowTime))
    if dimTime < brightTime:                                                    # *** The "dim" time is during the day because we go bright later than we go dim
        if nowTime >= dimTime and nowTime < brightTime :                        #       We are within the window to be dim
            nextionWrite('dim={}'.format(int(config['display']['dimval'])))                               #       change to dim
            dim = True                                                          #       Flag that we're now dim
            logger.info('{} Display initialized to DIM: {} (daytime dim)'.format(logPFX, nowTime))
        if not (nowTime >= dimTime and nowTime < brightTime):                   #       We are outside the window to be dim
            nextionWrite('dim={}'.format(int(config['display']['brtval'])))                               #       change to bright
            dim = False                                                         #       Flag that we're now bright
            logger.info('{} Display initialized to BRIGHT: {} (daytime dim)'.format(logPFX, nowTime))

    # Setup last display values from config file
    lastbright = config['display']['brtval']
    lastdim = config['display']['dimval']
    
    # Move to the data page before beginning to loop
    nextionWrite('page data')



### HOUSEKEEPING LOOP (TIME, NETWORK and SUCH)
def housekeepingUpdate():
    global lastOnline, online, ipaddr, dim
    
    # Check for active network connection
    checkOnline()

    # Time gets updated even if the METAR isn't new
    currentDTime = datetime.now().astimezone(get_localzone())                   # Getting datetime.now will produce an unaware time in the local TZ -- make it aware
    currentDTime = currentDTime.astimezone(zones[config['system']['tz']])       # Convert it into the timezone we're asking it to display.
    currentTime = '{} {}'.format(friendlyT(currentDTime, friendlyDate), friendlyT(currentDTime, friendlyTimeZ))
    #logger.debug('{} Loop Run @ {}'.format(logPFX, currentTime))
    nextionWrite('data.dtime.txt=\"{}\"'.format(currentTime))
    
    # Time of day to dim the display
    try:
        nowTime  = currentDTime.replace(second=0, microsecond=0)
        brightTime = nowTime.replace(hour=int(config['display']['brthr']),  minute=int(config['display']['brtmin']))
        dimTime    = nowTime.replace(hour=int(config['display']['dimhr']),  minute=int(config['display']['dimmin']))

        # This is really complicated b/c time "rols over" at midnight. I fix this by determining
        # the span of time within a day, and whether the clock should be bright or dim during
        # that span. This allows me to set the opposite condition outside of that span without
        # having to calculate what happens with rollover at mightnight. 
        if dimTime > brightTime:                                                    # *** The "bright" time is during the day because we go dim later than we go bright
            if nowTime >= brightTime and nowTime < dimTime and dim == True:         #       We are within the window to be bright, and the display is currently dim
                nextionWrite('dim={}'.format(int(config['display']['brtval'])))     #       Change to bright
                dim = False                                                         #       Flag that we're now bright
                logger.info('{} Display changed to BRIGHT: {} (datime bright)'.format(logPFX, nowTime))
            if not (nowTime >= brightTime and nowTime < dimTime) and dim == False:  #       We are outside the window to be bright and the diplay is bright
                nextionWrite('dim={}'.format(int(config['display']['dimval'])))                               #       Change to dim
                dim = True                                                          #       Flag that we're now dim 
                logger.info('{} Display changed to DIM: {} (datime bright)'.format(logPFX, nowTime))
        if dimTime < brightTime:                                                    # *** The "dim" time is during the day because we go bright later than we go dim
            if nowTime >= dimTime and nowTime < brightTime and dim == False:        #       We are within the window to be dim, and the display is currently bright
                nextionWrite('dim={}'.format(int(config['display']['dimval'])))                               #       change to dim
                dim = True                                                          #       Flag that we're now dim
                logger.info('{} Display changed to DIM: {} (daytime dim)'.format(logPFX, nowTime))
            if not (nowTime >= dimTime and nowTime < brightTime) and dim == True:   #       We are outside the window to be dim and the display is dim
                nextionWrite('dim={}'.format(int(config['display']['brtval'])))                               #       change to bright
                dim = False                                                         #       Flag that we're now bright
                logger.info('{} Display changed to BRIGHT: {} (daytime dim)'.format(logPFX, nowTime))
    except Exception as e:
        nextionWrite('dim={}'.format(int(config['display']['brtval'])))
        logger.error('{} Error Processing BRIGHT/DIM ({}) housekeeping: {}'.format(logPFX, dim, e))

    if lastbright != config['display']['brtval'] and not dim:
        nextionWrite('dim={}'.format(int(config['display']['brtval'])))
    if lastdim != config['display']['dimval'] and dim:
        nextionWrite('dim={}'.format(int(config['display']['dimval'])))

### METAR UPDATE LOOP
def METARupdate():
    logger.debug('{} METARupdate Loop Started: {}'.format(logPFX, time.time()))
    global metar_id, metarDTime, lastOnline, online, dim
    metar = 'uninitialized METAR'

    currentDTime = datetime.now().astimezone(get_localzone())                   # Getting datetime.now will produce an unaware time in the local TZ -- make it aware
    currentDTime = currentDTime.astimezone(zones[config['system']['tz']])       # Convert it into the timezone we're asking it to display.

    url   = config['system']['url'].format(config['awos']['station'])
    
    if online == True:
        metar = get_metar(url)
        if type(metar) is dict:
            #if metar_id != metar['icaoId']:
            #    metar_id = metar['icaoId']
            #if metar_id == metar['icaoId']:
            logger.info('{} New METAR Received with ID {}'.format(logPFX, metar['icaoId']))
            nextionWrite('data.stat.pco={}'.format(white))

            # Metar Time Conversion
            metarRTime = mkDatetime(metar['reportTime'])
            metarDTime = mkLocalTime(metarRTime, zones[config['system']['tz']])
            metarTime = '{} {}'.format(friendlyT(metarDTime, friendlyDate), friendlyT(metarDTime, friendlyTimeZ))

            # Metar Time
            nextionWrite('data.mtime.pco={}'.format(green))              # indicate recent data data with green font
            nextionWrite('data.mtime.txt=\"{}\"'.format(metarTime))      # display time

            # Wind Direction
            if 'wdir' not in metar: metar['wdir'] = None
            nextionWrite('data.dir_g.val={}'.format(0 if metar['wdir'] == None else metar['wdir']))         # display guage
            nextionWrite('data.dir.txt=\"{}\"'.format('NA' if metar['wdir'] == None else metar['wdir']))    # display digital

            # Wind Speed
            if 'wspd' not in metar: metar['wspd'] = None
            spd = 0 if metar['wspd'] == None else metar['wspd']                 # set None type to 0
            spd = ktom(spd) if eval(config['system']['mph']) == True else spd   # convert to MPH if configured -- eval turn text into boolean
            nextionWrite('data.spd_g.val={}'.format((spd * 9)%360))             # Gauge requires scaling * 9 to display
            nextionWrite('data.spd.txt=\"{}\"'.format(spd))

            # Wind gusts
            # 2025-09-03 API change doesn't return the key if there is no value
            if 'wgst' not in metar:
                spd = 0
                metar['wgst'] = None
            else:
                spd = ktom(metar['wgst'])
            spd = ktom(spd) if eval(config['system']['mph']) == True else spd   # convert to MPH if configured -- eval turn text into boolean
            nextionWrite('data.gust_g.val={}'.format((spd * 9)%360))            # gauge requires scaling * 9 to display
            nextionWrite('data.gust.txt=\"{}\"'.format(spd))

            # Temperature
            ftmp = 'NA' if metar['temp'] == None else ctof(metar['temp'])            # convert to F and round
            nextionWrite('data.temp_g.val={}'.format(0 if ftmp == 'NA' else ftmp*3)) # Gauge requires scaling * 3 to display
            nextionWrite('data.temp.txt=\"{}\"'.format(ftmp))

            # Dewpoint
            ftmp = 'NA' if metar['dewp'] == None else ctof(metar['dewp'])            # convert to F and round
            nextionWrite('data.dewp_g.val={}'.format(0 if ftmp == 'NA' else ftmp*3)) # Gauge requires scaling * 3 to display
            nextionWrite('data.dewp.txt=\"{}\"'.format(ftmp))

            # WXString
            if 'wxString' not in metar: metar['wxString'] = 'NA'
            nextionWrite('data.prcp.txt=\"{}\"'.format('' if metar['wxString'] == None else metar['wxString']))

            # Visiblity
            nextionWrite('data.vis.txt=\"{}\"'.format('' if metar['visib'] == None else str(metar['visib']) + ' mi'))

            # Altimeter
            alt = 'NA' if 'altim' not in metar else metar['altim']/33.864
            nextionWrite('data.alt.txt=\"{}\"'.format(alt))

            # Sky Condition
            val = ''
            if 'clouds' in metar:
                for entry in metar['clouds']:
                    val += '{} {}, '.format(entry['base'], entry['cover'])
                val = 'data.sky.txt=\"{}{}'.format(val[:-2], '\"')
            else:
                metar['clouds'] = ''
            nextionWrite(val)

            # Warning/Alerts
            nextionWrite('data.warn.txt=\"{}\"'.format(warn))

            # Write station name in white b/c METAR is good.
            nextionWrite('data.stat.txt=\"{}\"'.format(config['awos']['station']))
            nextionWrite('data.stat.pco=65535')

            # Log the METAR information
            logger.debug('{} New Metar Processed at {}'.format(logPFX, metarTime))
            logger.debug('    Station: {}'.format(metar['icaoId']))
            logger.debug('    Wind Direction: {}'.format(metar['wdir']))
            logger.debug('    Wind Speed: {}'.format(metar['wspd']))
            logger.debug('    Wind Gusts: {}'.format(metar['wgst']))
            logger.debug('    Temperature: {}C, {}F'.format(metar['temp'], round((metar['temp']*9/5)+32)) if metar['temp'] != None else None)
            logger.debug('    Ceiling: {}'.format(metar['clouds']))
        else:
            nextionWrite('data.stat.pco={}'.format(red))
            nextionWrite('data.stat.txt=\"{}\"'.format(config['awos']['station']))
            nextionWrite('data.warn.txt=\"{}\"'.format(metar))
            logger.error('{} FAILED TO PARSE METAR: {}'.format(logPFX, metar))
            metar_id = 0
    else:
        logger.error('{} Network OFFLINE, cannot load metar'.format(logPFX))
        metar_id = 0

    # Change METAR time color based on METAR age
    try:
        if currentDTime > metarDTime + timedelta(hours=1):
            nextionWrite('data.mtime.pco={}'.format(red))
    except Exception as e:
        nextionWrite('data.mtime.pco={}'.format(red))
        logger.error('{} Missing METAR Time Processing METAR aging {} Exception: {}'.format(logPFX, metar, e))
 
 ### 

### CONFIG UPDATE ON DEMAND
def CFGupdate(_cmdStr):
    if type(_cmdStr) == None:
        logger.info('CFGuptate called with type None argument, returning...')
        return
    global metar_id, online, lastOnline
    cmd = _cmdStr[:3]
    arg = _cmdStr[3:]
    if cmd == 'STA':
        config.set('awos', 'station', arg.upper())
        nextionWrite('settings.station.txt=\"{}\"'.format(config['awos']['station']))
        logger.info('{} New station selected: {}'.format(logPFX, config['awos']['station']))
        writeConfig()
        metar_id = 0
    
    elif cmd == 'DIM':
        hr = int(arg.split(':')[0])
        if hr > 24:
            logger.warning('{} Invalid DIM hour selected: {}; reverting to previous hour value: {}'.format(logPFX, hr, config['display']['dimhr']))
            hr = config['display']['dimhr']
        mn = int(arg.split(':')[1])
        if mn > 59:
            logger.warning('{} Invalid DIM minute selected: {}; reverting to previous minute value: {}'.format(logPFX, hr, config['display']['dimmin']))
            mm = config['display']['dimmin']
        config.set('display', 'dimhr', str(hr))
        config.set('display', 'dimmin', str(mn))
        nextionWrite('settings.dim_on.txt=\"{}:{:02d}\"'.format(hr, mn))        
        logger.info('{} New Display Dim time selected: {}:{:02d}'.format(logPFX, hr, mn))
        writeConfig()

    elif cmd == 'BRT':
        hr = int(arg.split(':')[0])
        if hr > 24:
            logger.warning('{} Invalid BRIGHT hour selected: {}; reverting to previous hour value: {}'.format(logPFX, hr, config['display']['brthr']))
            hr = config['display']['brthr']
        mn = int(arg.split(':')[1])
        if mn > 59:
            logger.warning('{} Invalid BRIGHT minute selected: {}; reverting to previous minute value: {}'.format(logPFX, hr, config['display']['brtmin']))
            mm = config['display']['brtmin']
        config.set('display', 'brthr', str(hr))
        config.set('display', 'brtmin', str(mn))
        nextionWrite('settings.brt_on.txt=\"{}:{:02d}\"'.format(hr, mn))        
        logger.info('{} New Display Bright time selected: {}:{:02d}'.format(logPFX, hr, mn))
        writeConfig()

    elif cmd == 'DMV':
        config.set('display', 'dimval', arg)
        writeConfig()
        logger.info('{} New display DIM value selected: {}'.format(logPFX, arg))

    elif cmd == 'BRV':
        config.set('display', 'brtval', arg)
        writeConfig()
        logger.info('{} New display BRIGHT value selected: {}'.format(logPFX, arg))

    elif cmd == 'SPU':
        if config['system']['mph'] == 'True':
            config.set('system', 'mph', 'False')
            nextionWrite('settings.spdunit.txt=\"KT\"')
            nextionWrite('data.kt.aph=127')        # turn on KT
            nextionWrite('data.mph.aph=0')         # turn off MPH     
        else:
            config.set('system', 'mph', 'True')
            nextionWrite('settings.spdunit.txt=\"MPH\"')
            nextionWrite('data.mph.aph=127')       # turn on MPH
            nextionWrite('data.kt.aph=0')          # turn off KT
        writeConfig()
        logger.info('{} New SPEED UNIT selected: {}'.format(logPFX, config['system']['mph']))
        metar_id = 0
 
    elif cmd == 'TZD':
        config.set('system', 'tz', arg)
        writeConfig()
        logger.info('{} New Timezone selected: {}'.format(logPFX, arg))
        metar_id = 0
    
    elif cmd == 'WFI':
        tempSSID = config['wifi']['ssid']
        tempPassword = config['wifi']['password']
        logger.info('{} New WiFi network selected'.format(logPFX))
        credentials = arg.split(':password:')
  
        config.set('wifi','ssid', credentials[0])
        config.set('wifi','password', credentials[1])
        nextionWrite('settings.ssid.txt=\"{}\"'.format(config['wifi']['ssid']))
        nextionWrite('settings.password.txt=\"{}\"'.format(config['wifi']['password']))
        logger.info('{} New WiFi credentials selected. SSID: {} Password: {}'.format(logPFX, config['wifi']['ssid'],config['wifi']['password']))

        response = execute('sudo /usr/bin/nmcli dev wifi connect "{}" password "{}"'.format(config['wifi']['ssid'], config['wifi']['password']))
        logger.info('{} EXEC: {}'.format(logPFX,response))     
        if config['wifi']['ssid'] != tempSSID:
            response = execute('sudo /usr/bin/nmcli c delete "{}"'.format(tempSSID))
            logger.info('{} EXEC: {}'.format(logPFX,response))  
        writeConfig()
        lastOnline = False
        checkOnline()
        # Ensure we get a new METAR
        metar_id = 0
    
    else:
        logger.error('{} Unexpected (valid) string from Nextion: {}'.format(logPFX, repr(_cmdStr)))
    


if __name__ == '__main__':
    '''
    IN ORDER FOR THIS PROGRAM TO CONFIGURE THE NETWORK, THE USER THAT IT RUNS AS
    MUST HAVE SUDO ACCESS AND BE ABLE TO RUN NMCLI WITHOUT A PASSWORD. I ADDED THE
    FOLLOWING TWO LINES WITH VISUDO:

    # Added 2023-07-09 CTB
    metar ALL = NOPASSWD: /usr/bin/nmcli
    '''
    
    #**** PROGRAM VERSION ****#
    __version__ = "2.3b"

    #**** YOU WILL NEED TO CHANGE THESE THINGS ****#
    # Things that are PLATFORM AND INSTALLATION SPECIFIC (ie SBC and/or OS and user)
    serialDevice = '/dev/ttyS1'
    netInterface = 'wlan0'
    cfgFile = '/home/metar/config.ini'

    #**** SOME THINGS HERE COULD CHANGE -- LIKE THE LOG LEVEL AND IF YOU ****#
    #****    WANT CONSOLE LOGGING, WHICH CAN BE USEFUL FOR DEBUGGING     ****#
    logPFX = '[METARClock V{}]'.format(__version__)
    consoleLog = True
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    syslogHandler = logging.handlers.SysLogHandler(address='/dev/log')
    logger.addHandler(syslogHandler)
    if consoleLog == True:
        consoleHandler = logging.StreamHandler(sys.stdout)
        logger.addHandler(consoleHandler)
    logger.info('{}: Starting up'.format(logPFX))

    # Read external configuration file
    config = ConfigParser()
    config.read(cfgFile)

    ser = serial.Serial(
      port=serialDevice,
      baudrate = 115200,
      parity = serial.PARITY_NONE,
      stopbits = serial.STOPBITS_ONE,
      bytesize = serial.EIGHTBITS,
      timeout = 2 # timeout in reception in seconds
    )

    # Configure serial port and other startup stuff
    startup()

    #**** THESE ARE THE MAIN LOOPING FUNCTIONS. THE PROGRAM STAYS ****#
    #****     FOREVER ONCE THE CONFIGURAITON AND SETUP IS DONE    ****#
    lastUpdate = 0
    lastMETAR = 0
    while True:
        now = time.time()
        if lastUpdate + 5 <= now:
            housekeepingUpdate()
            lastUpdate = now
        if lastMETAR + 300 <= now: # or metar_id == 0:
            METARupdate()             
            lastMETAR = now
        if ser.in_waiting:
            CFGupdate(serialReceive())
            housekeepingUpdate()
            lastUpdate = now
            METARupdate()
            lastMETAR = now
        time.sleep(.5)     # Wait to run the loop again.
