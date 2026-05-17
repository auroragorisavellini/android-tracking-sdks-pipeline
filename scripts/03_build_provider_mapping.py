#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
03_build_provider_mapping.py

Build a canonical SDK-to-provider mapping table from Exodus Privacy tracker rules.

This public-release version removes:
- Google Colab / Google Drive dependencies
- hard-coded local paths

Example:
    python code/03_build_provider_mapping.py \
        --exodus-rules data_raw/exodus_trackers_code_rules.csv \
        --outdir outputs/provider_mapping

Outputs:
    sdk_company_lookup_v4_style.csv
    sdk_provider_mapping_final.csv
    sdk_company_lookup_FINAL_CANONICAL.csv
    unknown_sdks.csv
    provider_counts.csv
"""

import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests
from bs4 import BeautifulSoup
from tqdm import tqdm


# ==================================================
# Original-style helper functions
# ==================================================

def normalize_sdk_name(name):
    if pd.isna(name):
        return None

    name = str(name).lower()
    name = re.sub(r"\(.*?\)", "", name)
    name = re.sub(r"[^a-z0-9\s]", " ", name)
    name = re.sub(r"\s+", " ", name)

    return name.strip()


def normalize_company_name_canonical(name):
    if pd.isna(name):
        return None

    name = str(name).lower()
    name = re.sub(r"\(.*?\)", "", name)
    name = name.replace("&", "and")
    name = re.sub(r"[^a-z0-9\s]", " ", name)
    name = re.sub(
        r"\b(inc|incorporated|ltd|limited|llc|corp|corporation|co|company|gmbh|ag|plc|srl|spa|sarl|sas|bv|pte)\b",
        "",
        name,
    )
    name = re.sub(r"\s+", " ", name)

    return name.strip()


def company_from_domain(url):
    """
    extract the first component of the website domain.

    This is kept only as diagnostic metadata and is NOT used as final provider
    assignment.
    """
    if not url:
        return None

    domain = urlparse(url).netloc.replace("www.", "")
    if not domain:
        return None

    return domain.split(".")[0].capitalize()


def get_tracker_website(exodus_url, timeout=10):
    """
    visit the Exodus tracker page and return the explicit Website/Pagina web link.
    """
    try:
        r = requests.get(
            exodus_url,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0"}
        )
        if r.status_code != 200:
            return None

        soup = BeautifulSoup(r.text, "html.parser")

        for a in soup.find_all("a", href=True):
            text = a.get_text(" ", strip=True).lower()
            href = a["href"].strip()

            if "http" in href and "exodus" not in href:
                if (
                    "pagina web" in text
                    or "website" in text
                    or "tracker web page" in text
                    or "web page" in text
                ):
                    return href

        return None

    except Exception:
        return None


LEGAL_REGEX = re.compile(
    r"""(
        [A-Z][A-Za-z0-9&.,\-\s]+?\s
        (?:
            GmbH|AG|
            Inc\.?|LLC|Corp\.?|Corporation|
            Ltd\.?|PLC|
            S\.r\.l\.|S\.p\.A\.|S\.L\.|
            B\.V\.|
            SARL|SAS|
            AB|AS|
            SA|
            Pte\sLtd
        )
    )""",
    re.VERBOSE,
)

LEGAL_PATHS = [
    "/impressum",
    "/imprint",
    "/legal",
    "/about",
    "/company",
    "/privacy",
]


def extract_from_meta(soup):
    """
    use selected meta fields as company/name candidates.
    """
    for meta in soup.find_all("meta"):
        key = meta.get("property") or meta.get("name")
        val = meta.get("content")

        if key and val:
            if key.lower() in ["og:site_name", "og:publisher", "author", "publisher"]:
                return val.strip()

    return None


def extract_from_jsonld(soup):
    """
    parse JSON-LD and return the first field whose name contains "name".
    """
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            if not script.string:
                continue

            data = pd.json_normalize(pd.read_json(script.string))

            for col in data.columns:
                if "name" in col.lower():
                    return str(data[col].iloc[0])

        except Exception:
            continue

    return None


def get_legal_page(base_url, timeout=10):
    """
    try common legal/about/privacy paths and return the first valid HTML page.
    """
    for path in LEGAL_PATHS:
        try:
            r = requests.get(base_url.rstrip("/") + path, timeout=timeout)

            if r.status_code == 200 and len(r.text) > 500:
                return r.text

        except Exception:
            continue

    return None


def get_exodus_ownership(exodus_url, timeout=10):
    """
    extract structured Exodus Ownership field.
    """
    try:
        r = requests.get(exodus_url, timeout=timeout)
        if r.status_code != 200:
            return None

        soup = BeautifulSoup(r.text, "html.parser")

        for dt in soup.find_all("dt"):
            if dt.get_text(strip=True).lower() == "ownership":
                dd = dt.find_next_sibling("dd")
                if dd:
                    return dd.get_text(strip=True)

        return None

    except Exception:
        return None


def extract_company_info(website, exodus_url, timeout=10):
    """
    extract declared company from footer, meta, JSON-LD, legal pages, or
    Exodus ownership fallback.
    """
    result = {
        "declared_company": None,
        "company_from_domain": company_from_domain(website),

        # Big Tech
        "mentions_google": False,
        "mentions_meta": False,
        "mentions_amazon": False,
        "mentions_microsoft": False,

        # SDK platforms
        "mentions_unity": False,
        "mentions_applovin": False,
        "mentions_ironsource": False,
        "mentions_digital_turbine": False,
        "mentions_inmobi": False,

        # Standards
        "mentions_iab": False,

        "footer_text": None,
        "company_source_auto": None,
    }

    try:
        r = requests.get(
            website,
            timeout=timeout,
            headers={"User-Agent": "Mozilla/5.0"},
        )

        if r.status_code != 200:
            return result

        soup = BeautifulSoup(r.text, "html.parser")

        # 1. Footer
        footer = soup.find("footer")
        if footer:
            footer_text = footer.get_text(" ", strip=True)
            result["footer_text"] = footer_text[:1000]

            m = LEGAL_REGEX.search(footer_text)
            if m:
                result["declared_company"] = m.group(1).strip()
                result["company_source_auto"] = "footer"

            text = footer_text.lower()
            result["mentions_google"] = "google" in text
            result["mentions_meta"] = "meta" in text or "facebook" in text
            result["mentions_amazon"] = "amazon" in text or "aws" in text
            result["mentions_microsoft"] = "microsoft" in text

            result["mentions_unity"] = "unity" in text
            result["mentions_applovin"] = "applovin" in text
            result["mentions_ironsource"] = "ironsource" in text
            result["mentions_digital_turbine"] = "digital turbine" in text
            result["mentions_inmobi"] = "inmobi" in text

            result["mentions_iab"] = (
                "iab" in text or "interactive advertising bureau" in text
            )

        # 2. Meta
        if not result["declared_company"]:
            meta_company = extract_from_meta(soup)
            if meta_company:
                result["declared_company"] = meta_company
                result["company_source_auto"] = "meta"

        # 3. JSON-LD
        if not result["declared_company"]:
            jsonld_company = extract_from_jsonld(soup)
            if jsonld_company:
                result["declared_company"] = jsonld_company
                result["company_source_auto"] = "jsonld"

        # 4. Legal pages
        if not result["declared_company"]:
            legal_html = get_legal_page(website)
            if legal_html:
                m = LEGAL_REGEX.search(legal_html)
                if m:
                    result["declared_company"] = m.group(1).strip()
                    result["company_source_auto"] = "legal_page"

        # 5. Exodus fallback
        if not result["declared_company"]:
            exodus_owner = get_exodus_ownership(exodus_url)
            if exodus_owner:
                result["declared_company"] = exodus_owner
                result["company_source_auto"] = "exodus_ownership"

        return result

    except Exception:
        return result


# ==================================================
# Manual SDK-to-provider mapping
# ==================================================

MANUAL_COMPANY_MAP = {
    # Analytics / Data
    "teemo": "Near Intelligence",
    "xiti": "AT Internet",
    "atinternet": "AT Internet",
    "mixpanel": "Mixpanel, Inc.",
    "appmetrica": "Yandex",
    "gameanalytics": "GameAnalytics",
    "amplitude": "Amplitude, Inc.",
    "countly": "Countly Ltd",
    "kochava": "Kochava",
    "webtrends": "Webtrends",
    "new relic": "New Relic, Inc.",
    "dynatrace": "Dynatrace",
    "singular": "Singular",
    "matomo piwik": "Matomo",
    "sensoro": "Sensoro Co Ltd",
    "sensors analytics": "Sensors Network Technology (Beijing) Co., Ltd.",
    "talkingdata": "Beijing Tendcloud Tianxia Technology Co., Ltd.",
    "thinkingdata analytics": "ThinkingData Information Technology (Shanghai) Co., Ltd.",
    "thinkingdata": "ThinkingData Information Technology (Shanghai) Co., Ltd.",
    "appanalytics": "AppAnalytics Yazılım AŞ",
    "app analytics": "AppAnalytics Yazılım AŞ",
    "appbrain": "AppBrain",
    "apptentive": "Alchemer LLC",
    "apptimize": "Airship",
    "appsee": "ServiceNow",
    "instabug": "Luciq",
    "taplytics": "Taplytics",
    "bugsnag": "SmartBear Software",
    "bugfender": "Bugfender",
    "uxcam": "UXCam",
    "raygun": "Raygun Ltd.",
    "pyze": "Pyze, Inc.",
    "airship": "Airship",
    "urbanairship": "Airship",
    "localytics": "Upland Software",
    "wootric": "InMoment, Inc.",
    "foresee": "Verint Systems Inc.",
    "blueconic": "BlueConic Inc",
    "clevertap": "CleverTap, Inc.",
    "leanplum": "CleverTap, Inc.",
    "braze": "Braze, Inc.",
    "braze formerly appboy": "Braze, Inc.",
    "mobfox": "Matomy Media Group",
    "onesignal": "OneSignal",
    "appdynamics": "Cisco Systems Inc",
    "conviva": "Conviva",
    "batch": "Batch",
    "pubmatic": "PubMatic",
    "pusher": "Pusher",
    "radius networks": "Radius Networks",
    "kissmetrics": "Kissmetrics",
    "webtrekk": "Mapp Digital",
    "nielsen": "Nielsen",
    "tenjin": "Tenjin",
    "tapstream": "Tapstream",
    "repro": "Repro Inc",
    "split": "Harness Feature Management & Experimentation",
    "exponea": "Bloomreach Engagement",
    "userexperior": "DevRev",
    "retency": "Retency",

    # Fraud / Risk
    "ipqualityscore": "IPQualityScore LLC",

    # Debugging / Monitoring
    "bugsee": "Bugsee Inc",
    "rollbar": "Rollbar, Inc.",
    "loggly": "SolarWinds",

    # Big Tech
    "google tag manager": "Google LLC",
    "firebase analytics": "Google LLC",
    "google admob": "Google LLC",
    "alooma": "Google LLC",
    "anvato a google company": "Google LLC",
    "accountkit": "Meta Platforms, Inc.",
    "crowdtangle": "Meta Platforms, Inc.",
    "amazon advertisement": "Amazon Inc",
    "amazon mobile associates": "Amazon Inc",
    "amazon analytics amazon insights": "Amazon Inc",
    "amazon analytics amazon insights": "Amazon Inc",
    "sizmek": "Amazon Inc",
    "microsoft visual studio app center crashes": "Microsoft Corporation",
    "microsoft visual studio app center analytics": "Microsoft Corporation",
    "app center crashes": "Microsoft Corporation",
    "app center analytics": "Microsoft Corporation",
    "hockeyapp": "Microsoft Corporation",

    # Adobe / Oracle / SAP
    "omniture": "Adobe Inc.",
    "marketo": "Adobe Inc.",
    "demdex": "Adobe Inc.",
    "adobe experience cloud": "Adobe Inc.",
    "auditude": "Adobe Inc.",
    "bluekai": "Oracle Corporation",
    "moat": "Oracle Corporation",
    "sap cdc": "SAP SE",
    "gigya": "SAP SE",
    "emarsys": "SAP SE",
    "emarsys predict": "SAP SE",
    "exacttarget": "Salesforce",

    # AppLovin
    "applovin": "AppLovin",
    "adjust": "AppLovin",
    "adjust unbotify": "AppLovin",
    "mopub": "AppLovin",
    "twitter mopub": "AppLovin",

    # Unity / Gaming Ads
    "unity3d ads": "Unity Technologies",
    "unity ads": "Unity Technologies",
    "unity": "Unity Technologies",
    "ironsource": "Unity Technologies",
    "tapjoy": "Unity Technologies",
    "deltadna": "Unity Technologies",
    "tapdaq": "Unity Technologies",
    "chartboost": "LoopMe",

    # Digital Turbine
    "fyber": "Digital Turbine Inc",
    "fyber sponsorpay": "Digital Turbine Inc",
    "sponsorpay": "Digital Turbine Inc",
    "heyzap": "Digital Turbine Inc",
    "inneractive": "Digital Turbine Inc",

    # Chinese ecosystem
    "baidu mobile ads": "Baidu Inc",
    "baidu mobile stat": "Baidu Inc",
    "baidu navigation": "Baidu Inc",
    "baidu location": "Baidu Inc",
    "baidu map": "Baidu Inc",
    "baidu maps": "Baidu Inc",
    "baidu appx": "Baidu Inc",
    "duapps": "Baidu Inc",
    "tencent map lbs": "Tencent Holdings Ltd.",
    "tencent mobwin": "Tencent Holdings Ltd.",
    "tencent mta": "Tencent Holdings Ltd.",
    "tencent stats": "Tencent Holdings Ltd.",
    "tencent weiyun": "Tencent Holdings Ltd.",
    "wechat location": "Tencent Holdings Ltd.",
    "bugly": "Tencent Holdings Ltd.",
    "umeng analytics": "Alibaba Group",
    "umeng feedback": "Alibaba Group",
    "alimama": "Alibaba Group",
    "ads mogo": "Alibaba Group",
    "autonavi amap": "Alibaba Group",
    "jpush": "Aurora Mobile",
    "jiguang": "Aurora Mobile",
    "aurora mobile": "Aurora Mobile",
    "pangle": "ByteDance Ltd.",
    "mintegral": "Mobvista Group",
    "mobvista": "Mobvista Group",
    "nativex": "Mobvista Group",

    # Other AdTech
    "x mode": "Outlogic",
    "x mode social": "Outlogic",
    "add apt tr": "AddApptr GmbH",
    "admitad": "Mitgo Group",
    "yoc vis x": "YOC AG",
    "adcolony": "Digital Turbine Inc",
    "inmobi": "InMobi",
    "aerserv": "InMobi",
    "appnext": "Appnext",
    "appodeal": "Appodeal Inc",
    "appodeal stack": "Appodeal Inc",
    "startapp": "Start.io",
    "criteo": "Criteo S.A.",
    "loopme": "LoopMe Ltd",
    "ogury presage": "Ogury Ltd.",
    "presage": "Ogury Ltd.",
    "outbrain": "Outbrain Inc",
    "ligatus": "Outbrain Inc",
    "teads": "Outbrain Inc",
    "widespace": "Azerion Group",
    "madvertise": "Azerion Group",
    "verve": "Verve Group",
    "verve group": "Verve Group",
    "roxymity": "Verve Group",
    "roximity": "Verve Group",
    "nexage": "Verizon Communications Inc.",
    "jumptap": "Verizon Communications Inc.",
    "millennial media": "Verizon Communications Inc.",
    "conversant": "Epsilon",
    "admarvel": "Opera Software AS",
    "rubicon project": "Magnite, Inc.",
    "supersonic ads": "Supersonic",
    "adtiming": "AdTiming Technology Company Limited",
    "ad x": "Neptune Company",
    "adadapted": "AdAdapted",
    "adbuddiz": "AdBuddiz",
    "adfalcon": "Noqoush Mobile Media Group",
    "adfit daum": "AXZ Corp.",
    "adfit": "AXZ Corp.",
    "adtrial": "Playdigious SAS",
    "adcenix": "Adcenix",
    "adform": "Adform",
    "adincube": "Ogury Ltd.",
    "admost": "Admost",
    "adot": "Veepee Group",
    "shallwead": "Korea Center",
    "hyprmx": "Jun Group",
    "kiip": "NinthDecimal",
    "mobpower": "Mobpower",
    "cloudmobi": "CloudMobi Technology",
    "cheetah ads": "Cheetah Mobile Inc",
    "tnk factory": "TNK Factory",
    "youappi": "Affle",
    "persona ly": "OP Vision Ltd",
    "personaly": "OP Vision Ltd",
    "gemius heatmap": "Gemius",
    "gemius": "Gemius",
    "nend": "FAN Communications Inc",
    "kidoz": "Kidoz Inc",
    "flurry": "Yahoo Inc",
    "flymob": "Al Adtech Holding Limited",
    "revmob": "RevMob Ltd.",
    "cauly": "FSN Co., Ltd.",
    "calldorado": "CallDorado GmbH",
    "pingstart": "PingStart Inc.",
    "enhance": "MobileFuse Inc.",
    "mdotm": "MDOTM Ltd",
    "zapr": "Zapr Media Labs",
    "adpopcorn": "adPOPcorn Co., Ltd.",
    "maio by i mobile": "i-mobile Co., Ltd.",
    "actv8me": "Actv8me, Inc.",
    "oneaudience": "OneAudience LLC",
    "button": "Button, Inc.",
    "adpie": "GOM Factory Co., Ltd.",
    "gom factory adpie": "GOM Factory Co., Ltd.",
    "fluzo": "FLUZO Technologies, S.L.",
    "blesh": "Blesh Ltd.",
    "fineboost": "Shenzhen Yifan Space-Time Technology Co., Ltd.",
    "rjfun": "RjFun Technologies",
    "buzzad benefit": "Buzzvil",
    "virgo mobile": "Virgo Group",
    "offertoro": "OfferToro",
    "pincrux": "Pincrux Inc.",
    "yoadx": "Yoadx Ltd.",
    "veloxity": "Veloxity Inc.",
    "smart": "Equativ",
    "glispa connect": "Glispa GmbH",
    "s4m": "S4M",
    "integral ad science": "Integral Ad Science",
    "freewheel": "Comcast Corporation",
    "instreamatic adman": "Instreamatic",
    "moodmedia": "Mood Media",
    "yinzcam sobek": "YinzCam",
    "placer": "Placer.ai",

    # Social / Media
    "snapchat login kit": "Snap Inc.",
    "snap ad kit": "Snap Inc.",
    "giphy analytics": "Shutterstock, Inc.",
    "mail ru": "VK Group",
    "mytarget": "VK Group",

    # Developer tools
    "appcelerator": "Axway",
    "appcelerator analytics": "Axway",
    "titanium sdk": "Axway",
    "splunk mint": "Cisco Systems Inc",
    "bugsense": "Splunk",
    "sentry": "Functional Software, Inc.",
    "bitly": "Bitly, Inc.",
    "jumio": "Jumio Corporation",
    "mapbox": "Mapbox Inc",
    "ibm digital analytics": "IBM",

    # Miscellaneous
    "foursquare": "Foursquare Labs Inc",
    "factual": "Foursquare Labs Inc",
    "placed": "Foursquare Labs Inc",
    "didomi": "Didomi",
    "hypertrack": "HyperTrack, Inc.",
    "glympse": "Glympse Inc",
    "herow": "Herow",
    "mediba": "mediba Inc.",
    "geniee": "Geniee Inc.",
    "openback": "X Corp.",
    "cooladata": "Medallia, Inc.",
    "sense360": "Medallia, Inc.",
    "scoreloop": "BlackBerry",
    "openlocate": "SafeGraph",
    "swirl": "Swirl Networks",
    "zendrive": "Zendrive, Inc.",
    "gpshopper": "Synchrony Financial",
    "open source": "Open Source",
    "opentelemetry opencensus opentracing": "Open Source",
    "acra": "Open Source",
    "metrics": "Open Source",
    "prebid mobile": "Prebid.org",
    "solar2d corona": "Corona Labs Inc",
    "mozilla telemetry": "Mozilla Foundation",
    "altbeacon": "Radius Networks",
    "areametrics": "Point Inside",
    "axonix": "Ericsson Emodo",
    "appvador": "Supership",
    "anysdk": "AnyMind Group",
    "ooyala": "Dalet",
    "in loco": "In Loco",
    "inloco": "In Loco",
    "sync2ad": "SYNC",
    "pushspring": "T-Mobile US, Inc.",
    "beaconsinspace": "Fysical Labs",
    "beaconsinspace fysical": "Fysical Labs",
    "fysical": "Fysical Labs",
    "altamob": "Beijing Chuju Technology Co., Ltd.",
    "alta mob": "Beijing Chuju Technology Co., Ltd.",
    "esri arcgis": "Esri",
    "signalframe": "PwC",
    "predicio": "TechFinUK LTD",
    "predicio sdk": "TechFinUK LTD",
    "lenddo": "LenddoEFL",
    "lenddo sdk": "LenddoEFL",
    "shenzhen hexun huagu": "Shenzhen Hexun Huagu Information Technology Co., Ltd.",
    "huq sourcekit": "Huq Industries",
    "cedexis radar": "Citrix",
    "lotadata": "LotaData",
    "backelite": "Capgemini",
    "synerise": "Synerise S.A.",
    "uber analytics": "Uber Technologies, Inc.",
    "signal360": "Signal360, Inc.",
    "telequid": "Telequid",
    "carnival": "Carnival Corporation",
    "yueying crash sdk": "Guangzhou Dongjing Computer Technology Co., Ltd.",
}


# Additional manually validated provider mappings
MANUAL_COMPANY_MAP.update({
    "singlespot": "Singlespot SAS",
    "fiksu": "ClickDealer",
    "apteligent by vmware": "VMware",
    "appmonet": "Verve Group",
    "branch": "Branch Metrics",
    "vungle": "Liftoff",
    "dov e": "DOV-E Voice Technologies Ltd.",
    "pubnative": "Verve Group",
    "infonline": "saas.group",
    "fidzup": "Fidzup",
    "iqzone": "IQzone, Inc.",
    "upsight": "Arctic Wolf",
    "apsalar": "Singular",
    "lotame": "Lotame",
    "followanalytics": "Bryj Technologies, Inc.",
    "moengage": "MoEngage Inc.",
    "adcash": "Adcash",
    "amobee": "Nexxen",
    "yume": "RhythmOne",
    "oztam": "OzTAM",
    "abtasty": "AB Tasty",
    "acrcloud": "ACRCloud",
    "aarki": "Skillz Inc.",
    "akamai map": "Akamai Technologies",
    "airpush": "Airpush Inc.",
    "carto (formerly nutiteq)": "CARTO",
    "jiguang aurora mobile jpush": "Aurora Mobile",
    "pokkt": "AnyMind Group",
    "adjoe": "Applike Group",
    "appsgeyser": "AppsGeyser",
    "bidmachine": "BidMachine Inc",
    "indooratlas": "IndoorAtlas",
    "cifrasoft": "Cifrasoft",
    "mopinion": "Netigate",
    "plexure": "Plexure",
    "solar2d (corona)": "Corona Labs Inc",
    "ad(x)": "CloudX",
    "adgatemedia": "Prodege LLC",
    "adgem": "AdGem",
    "coulus coelib": "Measurement Systems",
    "weborama": "Weborama",
    "locuslabs": "Acuity Brands Inc",
    "colocator": "Crowd Connected",
    "shopkick": "Trax",
    "alphonso": "LG Ads Solutions",
    "smaato": "Verve Group",
    "facebook ads": "Meta Platforms, Inc.",
})

MANUAL_COMPANY_MAP = {
    normalize_sdk_name(k): v
    for k, v in MANUAL_COMPANY_MAP.items()
    if v is not None
}


SCRAPING_CORRECTION_MAP = {
    "unity3d ads": "Unity Technologies",
    "unity ads": "Unity Technologies",
    "rubicon project": "Magnite, Inc.",
    "amplitude": "Amplitude, Inc.",
    "applause": "Applause App Quality, Inc.",
    "adtiming": "AdTiming Technology Company Limited",
    "mobvista": "Mobvista Group",
    "braze formerly appboy": "Braze, Inc.",
    "criteo": "Criteo S.A.",
    "clevertap": "CleverTap, Inc.",
    "startapp": "Start.io",
    "tapjoy": "Unity Technologies",
    "mintegral": "Mobvista Group",
    "deltadna": "Unity Technologies",
    "leanplum": "CleverTap, Inc.",
    "parsel y": "Automattic Inc.",
    "jumio": "Jumio Corporation",
    "unacast pure": "Unacast, Inc.",
    "snapchat login kit": "Snap Inc.",
    "snap ad kit": "Snap Inc.",
    "google admob": "Google LLC",
    "loopme": "LoopMe Ltd",
    "superawesome": "Epic Games",
    "vdopia": "Chocolate Platform",
    "sentry": "Functional Software, Inc.",
    "bitly": "Bitly, Inc.",
    "pyze": "Pyze, Inc.",
    "raygun": "Raygun Ltd.",
    "sk planet tad": "SK Planet Co., Ltd.",
    "foresee": "Verint Systems Inc.",
    "wootric": "InMoment, Inc.",
    "sensors analytics": "Sensors Network Technology (Beijing) Co., Ltd.",
    "jumptap": "Verizon Communications Inc.",
    "blueconic": "BlueConic Inc",
    "braze": "Braze, Inc.",
    "mapbox": "Mapbox Inc",
    "inmarket": "InMarket",
    "duapps": "Baidu Inc",
    "bugly": "Tencent Holdings Ltd.",
    "tapdaq": "Unity Technologies",
    "adbrix": "IGAWorks",
    "uxcam": "UXCam",
    "amoad": "CyberAgent, Inc.",
    "glympse": "Glympse Inc",
    "herow": "Herow",
    "mediba": "mediba Inc.",
    "geniee": "Geniee Inc.",
    "autonavi amap": "Alibaba Group",
}

SCRAPING_CORRECTION_MAP = {
    normalize_sdk_name(k): v
    for k, v in SCRAPING_CORRECTION_MAP.items()
    if v is not None
}


CANONICAL_PROVIDER_MAP = {
    "google llc": "Google Inc",
    "google": "Google Inc",
    "google marketing platform": "Google Inc",
    "google for developers": "Google Inc",
    "firebase": "Google Inc",
    "firebase analytics": "Google Inc",
    "meta": "Meta Platforms, Inc",
    "meta platforms inc": "Meta Platforms, Inc",
    "meta for developers": "Meta Platforms, Inc",
    "facebook inc": "Meta Platforms, Inc",
    "crowdtangle": "Meta Platforms, Inc",
    "accountkit": "Meta Platforms, Inc",
    "amazon": "Amazon Inc",
    "amazon inc": "Amazon Inc",
    "amazon com inc": "Amazon Inc",
    "amazon web services inc": "Amazon Inc",
    "amazon appstore developer portal": "Amazon Inc",
    "baidu": "Baidu Inc",
    "baidu inc": "Baidu Inc",
    "du apps studio baidu ecosystem": "Baidu Inc",
    "tencent": "Tencent Holdings Ltd.",
    "tencent holdings ltd": "Tencent Holdings Ltd.",
    "alibaba": "Alibaba Group",
    "alibaba group": "Alibaba Group",
    "unity": "Unity Technologies",
    "unity technologies": "Unity Technologies",
    "deltadna": "Unity Technologies",
    "tapjoy": "Unity Technologies",
    "adobe": "Adobe Inc",
    "adobe inc": "Adobe Inc",
    "omniture": "Adobe Inc",
    "marketo": "Adobe Inc",
    "demdex": "Adobe Inc",
    "verizon communications": "Verizon Communications Inc.",
    "verizon communications inc": "Verizon Communications Inc.",
    "digital turbine": "Digital Turbine Inc",
    "digital turbine inc": "Digital Turbine Inc",
    "adjust gmbh applovin": "AppLovin",
    "applovin": "AppLovin",
    "mobvista": "Mobvista Group",
    "mobvista group": "Mobvista Group",
    "mobvista group mintegral": "Mobvista Group",
    "mobvista mintegral group": "Mobvista Group",
    "mintegral": "Mobvista Group",
    "foursquare": "Foursquare Labs Inc",
    "foursquare labs inc": "Foursquare Labs Inc",
    "verve": "Verve Group",
    "verve group": "Verve Group",
    "verve group europe": "Verve Group",
    "verve group europe gmbh": "Verve Group",
    "loopme": "LoopMe Ltd",
    "loopme ltd": "LoopMe Ltd",
    "supership": "Supership Inc",
    "supership inc": "Supership Inc",
    "medallia": "Medallia, Inc",
    "medallia inc": "Medallia, Inc",
    "open source coda hale": "Open Source",
    "open source": "Open Source",
    "radius networks inc": "Radius Networks",
    "radius networks": "Radius Networks",
    "yahoo": "Yahoo Inc",
    "yahoo inc": "Yahoo Inc",
    "oracle": "Oracle Corporation",
    "oracle corporation": "Oracle Corporation",
    "sap": "SAP SE",
    "sap se": "SAP SE",
    "microsoft": "Microsoft Corporation",
    "microsoft corporation": "Microsoft Corporation",
    "cisco systems": "Cisco Systems Inc",
    "cisco systems inc": "Cisco Systems Inc",
    "comcast": "Comcast Corporation",
    "comcast corporation": "Comcast Corporation",
    "matomy media group": "Matomy Media Group",
    "mapp digital": "Mapp Digital",
    "epsilon": "Epsilon",
}

CANONICAL_PROVIDER_MAP = {
    normalize_company_name_canonical(k): v
    for k, v in CANONICAL_PROVIDER_MAP.items()
}


# ==================================================
# Pipeline functions
# ==================================================

def load_exodus_rules(exodus_path):
    exodus_df = pd.read_csv(exodus_path)

    required = ["name", "slug", "url", "types"]
    missing = [c for c in required if c not in exodus_df.columns]

    if missing:
        raise ValueError(f"Missing required columns in Exodus rules file: {missing}")

    exodus_df = exodus_df[required].drop_duplicates()

    print("Number of SDK rules:", len(exodus_df))
    return exodus_df


def scrape_provider_candidates(exodus_df, sleep_seconds=0.0):
    rows = []

    for _, row in tqdm(exodus_df.iterrows(), total=len(exodus_df)):
        sdk_name = row["name"]
        sdk_type = row["types"]
        exodus_url = row["url"]

        tracker_website = get_tracker_website(exodus_url)

        company_info = {
            "declared_company": None,
            "company_from_domain": None,
            "mentions_google": False,
            "mentions_meta": False,
            "mentions_amazon": False,
            "mentions_microsoft": False,
            "mentions_unity": False,
            "mentions_applovin": False,
            "mentions_ironsource": False,
            "mentions_digital_turbine": False,
            "mentions_inmobi": False,
            "mentions_iab": False,
            "footer_text": None,
            "company_source_auto": None,
        }

        if tracker_website:
            company_info = extract_company_info(tracker_website, exodus_url)

        rows.append({
            "sdk_name": sdk_name,
            "sdk_type": sdk_type,
            "slug": row["slug"],
            "exodus_url": exodus_url,
            "tracker_website": tracker_website,
            **company_info,
            "source": "Website → Exodus fallback",
        })

        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    sdk_company_df = pd.DataFrame(rows)

    sdk_company_df = sdk_company_df[
        ~sdk_company_df["sdk_name"].apply(normalize_sdk_name).isin(
            ["statistiche", "statistics", "display"]
        )
    ].copy()

    coverage_pct = sdk_company_df["declared_company"].notna().mean() * 100
    print(f"Declared company coverage: {coverage_pct:.1f}%")

    return sdk_company_df


def apply_provider_mapping(sdk_company_df):
    sdk_company_df = sdk_company_df.copy()

    sdk_company_df["sdk_name_norm"] = (
        sdk_company_df["sdk_name"].apply(normalize_sdk_name)
    )

    sdk_company_df["provider_manual"] = (
        sdk_company_df["sdk_name_norm"].map(MANUAL_COMPANY_MAP)
    )

    sdk_company_df["provider_scraping_correction"] = (
        sdk_company_df["sdk_name_norm"].map(SCRAPING_CORRECTION_MAP)
    )

    # Final provider assignment.
    sdk_company_df["provider_raw"] = (
        sdk_company_df["provider_manual"]
        .combine_first(sdk_company_df["provider_scraping_correction"])
        .combine_first(sdk_company_df["declared_company"])
    )

    sdk_company_df["provider_source"] = "unknown"

    sdk_company_df.loc[
        sdk_company_df["declared_company"].notna(),
        "provider_source",
    ] = "scraped"

    sdk_company_df.loc[
        sdk_company_df["provider_scraping_correction"].notna(),
        "provider_source",
    ] = "scraping_corrected"

    sdk_company_df.loc[
        sdk_company_df["provider_manual"].notna(),
        "provider_source",
    ] = "manual"

    sdk_company_df.loc[
        sdk_company_df["provider_raw"].isna(),
        "provider_source",
    ] = "unknown"

    sdk_company_df["provider_raw_norm"] = (
        sdk_company_df["provider_raw"].apply(normalize_company_name_canonical)
    )

    sdk_company_df["company_canonical"] = (
        sdk_company_df["provider_raw_norm"]
        .map(CANONICAL_PROVIDER_MAP)
        .combine_first(sdk_company_df["provider_raw"])
    )

    return sdk_company_df


def write_outputs(sdk_company_df, outdir):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    out_v4_style = outdir / "sdk_company_lookup_v4_style.csv"
    out_final = outdir / "sdk_provider_mapping_final.csv"
    out_unknown = outdir / "unknown_sdks.csv"
    out_provider_counts = outdir / "provider_counts.csv"
    out_canonical = outdir / "sdk_company_lookup_FINAL_CANONICAL.csv"

    sdk_company_df.to_csv(out_v4_style, index=False)
    sdk_company_df.to_csv(out_final, index=False)
    sdk_company_df.to_csv(out_canonical, index=False)

    coverage = sdk_company_df["company_canonical"].notna().mean() * 100

    print(f"Final provider coverage: {coverage:.2f}%")
    print()
    print("Provider source distribution:")
    print(sdk_company_df["provider_source"].value_counts(dropna=False))
    print()

    unknown_cols = [
        c for c in [
            "sdk_name",
            "sdk_type",
            "exodus_url",
            "tracker_website",
            "company_from_domain",
        ]
        if c in sdk_company_df.columns
    ]

    unknown_df = sdk_company_df.loc[
        sdk_company_df["company_canonical"].isna(),
        unknown_cols,
    ]

    unknown_df.to_csv(out_unknown, index=False)

    provider_counts = (
        sdk_company_df["company_canonical"]
        .dropna()
        .value_counts()
        .reset_index()
    )
    provider_counts.columns = ["company_canonical", "count"]
    provider_counts.to_csv(out_provider_counts, index=False)

    print("Unmapped SDKs:")
    print(unknown_df)
    print()
    print(f"Saved original-style lookup to: {out_v4_style}")
    print(f"Saved provider mapping to: {out_final}")
    print(f"Saved canonical provider mapping to: {out_canonical}")
    print(f"Saved unknown SDKs to: {out_unknown}")
    print(f"Saved provider counts to: {out_provider_counts}")


def main():
    parser = argparse.ArgumentParser(
        description="Build canonical SDK-provider mapping from Exodus tracker rules."
    )

    parser.add_argument(
        "--exodus-rules",
        default="data_raw/exodus_trackers_code_rules.csv",
        help="Path to Exodus tracker rules CSV.",
    )

    parser.add_argument(
        "--outdir",
        default="outputs/provider_mapping",
        help="Output directory for provider mapping files.",
    )

    parser.add_argument(
        "--sleep-seconds",
        type=float,
        default=0.0,
        help="Optional pause between provider page requests.",
    )

    args = parser.parse_args()

    exodus_df = load_exodus_rules(args.exodus_rules)
    sdk_company_df = scrape_provider_candidates(
        exodus_df=exodus_df,
        sleep_seconds=args.sleep_seconds,
    )
    sdk_company_df = apply_provider_mapping(sdk_company_df)
    write_outputs(sdk_company_df, args.outdir)


if __name__ == "__main__":
    main()
