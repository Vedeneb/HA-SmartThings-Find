import logging
import json
import pytz
import qrcode
import base64
import aiohttp
import asyncio
import random
import string
import re
import html
from io import BytesIO
from datetime import datetime, timedelta
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry

from .const import DOMAIN, BATTERY_LEVELS, CONF_ACTIVE_MODE_SMARTTAGS, CONF_ACTIVE_MODE_OTHERS

_LOGGER = logging.getLogger(__name__)

URL_PRE_SIGNIN = 'https://account.samsung.com/accounts/v1/FMM2/signInGate?state={state}&redirect_uri=https:%2F%2Fsmartthingsfind.samsung.com%2Flogin.do&response_type=code&client_id=ntly6zvfpn&scope=iot.client&locale=de_DE&acr_values=urn:samsungaccount:acr:basic&goBackURL=https:%2F%2Fsmartthingsfind.samsung.com%2Flogin'
URL_QR_CODE_SIGNIN = 'https://account.samsung.com/accounts/v1/FMM2/signInWithQrCode'
URL_SIGNIN_XHR = 'https://account.samsung.com/accounts/v1/FMM2/signInXhr'
URL_QR_POLL = 'https://account.samsung.com/accounts/v1/FMM2/signInWithQrCodeProc'
URL_SIGNIN_SUCCESS = 'https://account.samsung.com{next_url}'
URL_GET_CSRF = "https://smartthingsfind.samsung.com/chkLogin.do"
URL_DEVICE_LIST = "https://smartthingsfind.samsung.com/device/getDeviceList.do"
URL_REQUEST_LOC_UPDATE = "https://smartthingsfind.samsung.com/dm/addOperation.do"
URL_SET_LAST_DEVICE = "https://smartthingsfind.samsung.com/device/setLastSelect.do"


async def do_login_stage_one(hass: HomeAssistant) -> tuple:
    """
    Perform the first stage of the login process.

    This function performs the initial login steps for the SmartThings Find service.
    It generates a random state string, sends a pre-login request, and retrieves
    the QR code URL from the response.

    Args:
        hass (HomeAssistant): Home Assistant instance.

    Returns:
        tuple: A tuple containing the session and QR code URL if successful, None otherwise.
    """
    session = async_get_clientsession(hass)
    session.cookie_jar.clear()

    # Generating the state parameter
    state = ''.join(random.choices(string.ascii_letters + string.digits, k=16))

    try:
        # Load the initial login page which already sets some cookies. 'state'
        # is a randomly generated string. 'client_id' seems to be static for
        # SmartThings find.
        async with session.get(URL_PRE_SIGNIN.format(state=state)) as res:
            if res.status != 200:
                _LOGGER.error(
                    f"Pre-login request failed with status {res.status}")
                return None
            _LOGGER.debug(f"Step 1: Pre-Login: Status Code: {res.status}")

        # Load the "Login with QR-Code"-page
        async with session.get(URL_QR_CODE_SIGNIN) as res:
            if res.status != 200:
                _LOGGER.error(
                    f"QR code URL request failed with status {res.status}, Response: {text[:250]}...")
                return None
            text = await res.text()
            _LOGGER.debug(f"Step 2: QR-Code URL: Status Code: {res.status}")

        # Search the URL which is embedded in the QR Code. It looks like this:
        # https://signin.samsung.com/key/abcdefgh
        match = re.search(r"https://signin\.samsung\.com/key/[^'\"]+", text)
        if not match:
            _LOGGER.error("QR code URL not found in the response")
            return None

        qr_url = match.group(0)
        _LOGGER.info(f"Extracted QR code URL: {qr_url}")

        return session, qr_url
    except Exception as e:
        _LOGGER.error(
            f"An error occurred during the login process (stage 1): {e}", exc_info=True)
        return None


async def do_login_stage_two(session: aiohttp.ClientSession) -> str:
    """
    Perform the second stage of the login process.

    This function continues the login process by fetching the
    CSRF token, polling the server for login status, and ultimately
    retrieving the JSESSIONID required for SmartThings Find.

    Args:
        session (aiohttp.ClientSession): The current session with cookies set from login stage one.

    Returns:
        str: The JSESSIONID if successful, None otherwise.
    """
    try:
        # Here you would generate and display the QR code. This is environment-specific.
        # qr = qrcode.QRCode()
        # qr.add_data(extracted_url)
        # qr.print_ascii()  # Or any other method to display the QR code to the user.

        # Fetch the _csrf token. This needs to be sent with each QR-Poll-Request
        async with session.get(URL_SIGNIN_XHR) as res:
            if res.status != 200:
                _LOGGER.error(
                    f"XHR login request failed with status {res.status}")
                return None
            json_res = await res.json()
            _LOGGER.debug(
                f"Step 3: XHR Login: Status Code: {res.status}, Response: {json_res}")

        csrf_token = json_res.get('_csrf', {}).get('token')
        if not csrf_token:
            _LOGGER.error("CSRF token not found in the response")
            return None

        # Define a timeout. We don't want to poll forever.
        end_time = datetime.now() + timedelta(seconds=120)
        next_url = None

        # We check, whether the QR-Code was scanned and the login was successful.
        # This request returns either:
        #       {"rtnCd":"POLLING"} <-- Not yet logged in. Login still pending.
        #   OR
        #       {"rtnCd":"SUCCESS","nextURL":"/accounts/v1/FMM2/signInComplete"}
        # So we fetch this URL every few seconds, until we receive "SUCCESS", which
        # indicates the user has successfully scanned the Code and approved the login.
        # This is exactly the same way, the website does it.
        # In case the user never scans the QR-Code, we run in the timeout defined above.
        while datetime.now() < end_time:
            try:
                await asyncio.sleep(2)
                async with session.post(URL_QR_POLL, json={}, headers={'X-Csrf-Token': csrf_token}) as res:
                    if res.status != 200:
                        _LOGGER.error(
                            f"QR check request failed with status {res.status}")
                        continue
                    js = await res.json()
                    _LOGGER.debug(
                        f"Step 4: QR CHECK: Status Code: {res.status}, Response: {js}")

                if js.get('rtnCd') == "SUCCESS":
                    next_url = js.get('nextURL')
                    break
            except aiohttp.ClientError as e:
                _LOGGER.error(f"QR Poll request failed: {e}")
                return None

        if not next_url:
            _LOGGER.error("QR Code not scanned within 2 mins")
            return None

        # Fetch the 'next_url' we received from the previous request. On success, this sets
        # the initial JSESSIONID-cookie. We're not done yet, since this cookie is not valid
        # for SmartThings Find (which uses it's own JSESSIONID).
        async with session.get(URL_SIGNIN_SUCCESS.format(next_url=next_url)) as res:
            if res.status != 200:
                _LOGGER.error(
                    f"Login success URL request failed with status {res.status}")
                return None
            text = await res.text()
            _LOGGER.debug(f"Step 5: Login success: Status Code: {res.status}")

        # The response contains another redirect URL which we need to extract from the
        # received HTML/JS-content. This URL looks something like this:
        # https://smartthingsfind.samsung.com/login.do?auth_server_url=eu-auth2.samsungosp.com
        #    &code=[...]&code_expires_in=300&state=[state we generated above]
        #    &api_server_url=eu-auth2.samsungosp.com
        match = re.search(
            r'window\.location\.href\s*=\s*[\'"]([^\'"]+)[\'"]', text)
        if not match:
            _LOGGER.error(
                "Redirect URL not found in the login success response")
            return None

        redirect_url = match.group(1)
        _LOGGER.debug(f"Found Redirect URL: {redirect_url}")

        # Fetch the received redirect_url. This response finally contains our JSESSIONID,
        # which is what actually authenticates us for SmartThings Find.
        async with session.get(redirect_url) as res:
            if res.status != 200:
                _LOGGER.error(
                    f"Redirect URL request failed with status {res.status}")
                return None
            _LOGGER.debug(
                f"Step 6: Follow redirect URL: Status Code: {res.status}")

        jsessionid = session.cookie_jar.filter_cookies(
            'https://smartthingsfind.samsung.com').get('JSESSIONID')
        if not jsessionid:
            _LOGGER.error("JSESSIONID not found in cookies")
            return None

        _LOGGER.debug(f"JSESSIONID: {jsessionid.value[:20]}...")
        return jsessionid.value

    except Exception as e:
        _LOGGER.error(
            f"An error occurred during the login process (stage 2): {e}", exc_info=True)
        return None


async def fetch_csrf(hass: HomeAssistant, session: aiohttp.ClientSession, entry_id: str):
    """
    Retrieves the _csrf-Token which needs to be sent with each following request.

    This function retrieves the CSRF token required for further requests to the SmartThings Find service.
    The JSESSIONID must already be present as a cookie in the session at this point.

    Args:
        hass (HomeAssistant): Home Assistant instance.
        session (aiohttp.ClientSession): The current session.

    Raises:
        ConfigEntryAuthFailed: If the CSRF token is not found or if the authentication fails.
    """
    err_msg = ""
    async with session.get(URL_GET_CSRF) as response:
        if response.status == 200:
            csrf_token = response.headers.get("_csrf")
            if csrf_token:
                hass.data[DOMAIN][entry_id]["_csrf"] = csrf_token
                _LOGGER.info("Successfully fetched new CSRF Token")
                return
            else:
                err_msg = f"CSRF token not found in response headers. Status Code: {response.status}, Response: '{await response.text()}'"
                _LOGGER.error(err_msg)
        else:
            err_msg = f"Failed to authenticate with SmartThings Find: [{response.status}]: {await response.text()}"
            _LOGGER.error(err_msg)

        _LOGGER.debug(f"Headers: {response.headers}")

    raise ConfigEntryAuthFailed(err_msg)


async def get_devices(hass: HomeAssistant, session: aiohttp.ClientSession, entry_id: str) -> list:
    """
    Sends a request to the SmartThings Find API to retrieve a list of devices associated with the user's account.

    Args:
        hass (HomeAssistant): Home Assistant instance.
        session (aiohttp.ClientSession): The current session.

    Returns:
        list: A list of devices if successful, empty list otherwise.
    """
    url = f"{URL_DEVICE_LIST}?_csrf={hass.data[DOMAIN][entry_id]['_csrf']}"
    async with session.post(url, headers={'Accept': 'application/json'}, data={}) as response:
        if response.status != 200:
            _LOGGER.error(f"Failed to retrieve devices [{response.status}]: {await response.text()}")
            if response.status == 404:
                _LOGGER.warn(
                    f"Received 404 while trying to fetch devices -> Triggering reauth")
                raise ConfigEntryAuthFailed(
                    "Request to get device list failed: 404")
            return []
        response_json = await response.json()
        devices_data = response_json["deviceList"]
        devices = []
        for device in devices_data:
            # Double unescaping required. Example:
            # "Benedev&amp;#39;s S22" first becomes "Benedev&#39;s S22" and then "Benedev's S22"
            device['modelName'] = html.unescape(
                html.unescape(device['modelName']))
            identifier = (DOMAIN, device['dvceID'])
            ha_dev = device_registry.async_get(
                hass).async_get_device({identifier})
            if ha_dev and ha_dev.disabled:
                _LOGGER.debug(
                    f"Ignoring disabled device: '{device['modelName']}' (disabled by {ha_dev.disabled_by})")
                continue
            ha_dev_info = DeviceInfo(
                identifiers={identifier},
                manufacturer="Samsung",
                name=device['modelName'],
                model=device['modelID'],
                configuration_url="https://smartthingsfind.samsung.com/"
            )
            devices += [{"data": device, "ha_dev_info": ha_dev_info}]
            _LOGGER.debug(f"Adding device: {device['modelName']}")
        return devices


async def get_device_location(hass: HomeAssistant, session: aiohttp.ClientSession, dev_data: dict, entry_id: str) -> dict:
    """
    Sends requests to update the device's location and retrieves the current location data for the specified device.

    Args:
        hass (HomeAssistant): Home Assistant instance.
        session (aiohttp.ClientSession): The current session.
        dev_data (dict): The device information obtained from get_devices.

    Returns:
        dict: The device location data.
    """
    dev_id = dev_data['dvceID']
    dev_name = dev_data['modelName']

    set_last_payload = {
        "dvceId": dev_id,
        "removeDevice": []
    }

    update_payload = {
        "dvceId": dev_id,
        "operation": "CHECK_CONNECTION_WITH_LOCATION",
        "usrId": dev_data['usrId']
    }

    csrf_token = hass.data[DOMAIN][entry_id]["_csrf"]

    try:
        active = (
            (dev_data['deviceTypeCode'] == 'TAG' and hass.data[DOMAIN][entry_id][CONF_ACTIVE_MODE_SMARTTAGS]) or
            (dev_data['deviceTypeCode'] != 'TAG' and hass.data[DOMAIN]
             [entry_id][CONF_ACTIVE_MODE_OTHERS])
        )

        if active:
            _LOGGER.debug("Active mode; requesting location update now")
            async with session.post(f"{URL_REQUEST_LOC_UPDATE}?_csrf={csrf_token}", json=update_payload) as response:
                # _LOGGER.debug(f"[{dev_name}] Update request response ({response.status}): {await response.text()}")
                pass
        else:
            _LOGGER.debug("Passive mode; not requesting location update")

        async with session.post(f"{URL_SET_LAST_DEVICE}?_csrf={csrf_token}", json=set_last_payload, headers={'Accept': 'application/json'}) as response:
            _LOGGER.debug(
                f"[{dev_name}] Location response ({response.status})")
            if response.status == 200:
                data = await response.json()
                res = {
                    "dev_name": dev_name,
                    "dev_id": dev_id,
                    "update_success": True,
                    "location_found": False,
                    "used_op": None,
                    "used_loc": None,
                    "ops": []
                }
                used_loc = None
                if 'operation' in data and len(data['operation']) > 0:
                    res['ops'] = data['operation']

                    used_op = None
                    used_loc = {
                        "latitude": None,
                        "longitude": None,
                        "gps_accuracy": None,
                        "gps_date": None
                    }
                    # Find and extract the latest location from the response. Often the response
                    # contains multiple locations (especially for non-SmartTag devices such as phones).
                    # We go through all of them and find the "most usable" one. Sometimes locations
                    # are encrypted (usually OFFLINE_LOC), we ignore these. They could probably also
                    # be encrypted; there is a special getEncToken-Endpoint which returns some sort of
                    # key. Since the only encrypted locations I encountered were even older than the
                    # non encrypted ones, I didn't try anything to encrypt them yet.
                    for op in data['operation']:
                        if op['oprnType'] in ['LOCATION', 'LASTLOC', 'OFFLINE_LOC']:
                            if 'latitude' in op:
                                utcDate = None

                                if 'extra' in op and 'gpsUtcDt' in op['extra']:
                                    utcDate = parse_stf_date(
                                        op['extra']['gpsUtcDt'])
                                else:
                                    _LOGGER.error(
                                        f"[{dev_name}] No UTC date found for operation '{op['oprnType']}', this should not happen! OP: {json.dumps(op)}")
                                    continue

                                if used_loc['gps_date'] and used_loc['gps_date'] >= utcDate:
                                    _LOGGER.debug(
                                        f"[{dev_name}] Ignoring location older than the previous ({op['oprnType']})")
                                    continue

                                locFound = False
                                if 'latitude' in op:
                                    used_loc['latitude'] = float(
                                        op['latitude'])
                                    locFound = True
                                if 'longitude' in op:
                                    used_loc['longitude'] = float(
                                        op['longitude'])
                                    locFound = True

                                if not locFound:
                                    _LOGGER.warn(
                                        f"[{dev_name}] Found no coordinates in operation '{op['oprnType']}'")
                                else:
                                    res['location_found'] = True

                                used_loc['gps_accuracy'] = calc_gps_accuracy(
                                    op.get('horizontalUncertainty'), op.get('verticalUncertainty'))
                                used_loc['gps_date'] = utcDate
                                used_op = op

                            elif 'encLocation' in op:
                                loc = op['encLocation']
                                if 'encrypted' in loc and loc['encrypted']:
                                    _LOGGER.info(
                                        f"[{dev_name}] Ignoring encrypted location ({op['oprnType']})")
                                    continue
                                elif 'gpsUtcDt' not in loc:
                                    _LOGGER.info(
                                        f"[{dev_name}] Ignoring location with missing date ({op['oprnType']})")
                                    continue
                                else:
                                    utcDate = parse_stf_date(loc['gpsUtcDt'])
                                    if used_loc['gps_date'] and used_loc['gps_date'] >= utcDate:
                                        _LOGGER.debug(
                                            f"[{dev_name}] Ignoring location older than the previous ({op['oprnType']})")
                                        continue
                                    else:
                                        locFound = False
                                        if 'latitude' in loc:
                                            used_loc['latitude'] = float(
                                                loc['latitude'])
                                            locFound = True
                                        if 'longitude' in loc:
                                            used_loc['longitude'] = float(
                                                loc['longitude'])
                                            locFound = True
                                        else:
                                            res['location_found'] = True

                                        if not locFound:
                                            _LOGGER.warn(
                                                f"[{dev_name}] Found no coordinates in operation '{op['oprnType']}'")

                                        used_loc['gps_accuracy'] = calc_gps_accuracy(
                                            loc.get('horizontalUncertainty'), loc.get('verticalUncertainty'))
                                        used_loc['gps_date'] = utcDate
                                        used_op = op
                                    continue

                    if used_op:
                        res['used_op'] = used_op
                        res['used_loc'] = used_loc
                    else:
                        _LOGGER.warn(
                            f"[{dev_name}] No useable location-operation found")

                    _LOGGER.debug(
                        f"    --> {dev_name} used operation: {'NONE' if not used_op else used_op['oprnType']}")

                else:
                    _LOGGER.warn(
                        f"[{dev_name}] No operation found in response; marking update failed")
                    res['update_success'] = False
                return res
            else:
                _LOGGER.error(
                    f"[{dev_name}] Failed to fetch device data ({response.status})")
                res_text = await response.text()
                _LOGGER.debug(f"[{dev_name}] Full response: '{res_text}'")

                # Our session is not valid anymore. Refreshing the CSRF Token ist not
                # enough at this point. Instead we have to ask the user to  go through
                # the whole auth flow again
                if res_text == 'Logout' or response.status == 401:
                    raise ConfigEntryAuthFailed(
                        f"Session not valid anymore, received status_code of {response.status} with response '{res_text}'")

    except ConfigEntryAuthFailed as e:
        raise
    except Exception as e:
        _LOGGER.error(
            f"[{dev_name}] Exception occurred while fetching location data for tag '{dev_name}': {e}", exc_info=True)

    return None


def calc_gps_accuracy(hu: float, vu: float) -> float:
    """
    Calculate the GPS accuracy using the Pythagorean theorem.
    Returns the combined GPS accuracy based on the horizontal
    and vertical uncertainties provided by the API

    Args:
        hu (float): Horizontal uncertainty.
        vu (float): Vertical uncertainty.

    Returns:
        float: Calculated GPS accuracy.
    """
    try:
        return round((float(hu)**2 + float(vu)**2) ** 0.5, 1)
    except ValueError:
        return None


def get_sub_location(ops: list, subDeviceName: str) -> tuple:
    """
    Extracts sub-location data for devices that contain multiple
    sub-locations (e.g., left and right earbuds).

    Args:
        ops (list): List of operations from the API.
        subDeviceName (str): Name of the sub-device.

    Returns:
        tuple: The operation and sub-location data.
    """
    if not ops or not subDeviceName or len(ops) < 1:
        return {}, {}
    for op in ops:
        if subDeviceName in op.get('encLocation', {}):
            loc = op['encLocation'][subDeviceName]
            sub_loc = {
                "latitude": float(loc['latitude']),
                "longitude": float(loc['longitude']),
                "gps_accuracy": calc_gps_accuracy(loc.get('horizontalUncertainty'), loc.get('verticalUncertainty')),
                "gps_date": parse_stf_date(loc['gpsUtcDt'])
            }
            return op, sub_loc
    return {}, {}


def parse_stf_date(datestr: str) -> datetime:
    """
    Parses a date string in the format "%Y%m%d%H%M%S" to a datetime object.
    This is the format, the SmartThings Find API uses.

    Args:
        datestr (str): The date string in the format "%Y%m%d%H%M%S".

    Returns:
        datetime: A datetime object representing the input date string.
    """
    return datetime.strptime(datestr, "%Y%m%d%H%M%S").replace(tzinfo=pytz.UTC)


def get_battery_level(dev_name: str, ops: list) -> int:
    """
    Try to extract the device battery level from the received operation

    Args:
        dev_name (str): The name of the device.
        ops (list): List of operations from the API.

    Returns:
        int: The battery level if found, None otherwise.
    """
    for op in ops:
        if op['oprnType'] == 'CHECK_CONNECTION' and 'battery' in op:
            batt_raw = op['battery']
            batt = BATTERY_LEVELS.get(batt_raw, None)
            if batt is None:
                try:
                    batt = int(batt_raw)
                except ValueError:
                    _LOGGER.warn(
                        f"[{dev_name}]: Received invalid battery level: {batt_raw}")
            return batt
    return None


def gen_qr_code_base64(data: str) -> str:
    """
    Generates a QR code from the provided data and returns it as a base64-encoded string.
    Used to show a login QR code during authentication flow

    Args:
        data (str): The data to encode in the QR code.

    Returns:
        str: The base64-encoded string representation of the QR code.
    """
    qr = qrcode.QRCode()
    qr.add_data(data)
    img = qr.make_image(fill_color="black", back_color="white")
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("utf-8")
