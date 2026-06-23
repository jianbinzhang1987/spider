import os

# Base Directories
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SESSIONS_DIR = os.path.join(BASE_DIR, "sessions")

# Browser Configuration
HEADLESS = False  # Set to False by default to allow manual solving of captchas/sliders
TIMEOUT = 30000   # 30 seconds default timeout in ms
PAGE_DELAY_MIN = 1.0  # seconds
PAGE_DELAY_MAX = 3.0  # seconds

# Target Websites and Credentials
CREDENTIALS = {
    # International Sites
    "digikey": {
        "name": "Digi-Key",
        "url": "https://www.digikey.cn",  # Using .cn for better local speed, redirects or behaves similar
        "login_url": "https://www.digikey.cn/zh/login",
        "username": "285521944@qq.com",
        "password": "Lxf861211-"
    },
    "mouser": {
        "name": "Mouser",
        "url": "https://www.mouser.cn",
        "login_url": "https://www.mouser.cn/MyAccount/Login",
        "username": "大浪淘金754",
        "password": "Lxf861211-"
    },
    "element14": {
        "name": "element14",
        "url": "https://cn.element14.com",
        "login_url": "https://cn.element14.com/webapp/wcs/stores/servlet/LogonForm",
        "username": "285521944@qq.com",
        "password": "Lxf861211-"
    },
    
    # Domestic Sites
    "szlcsc": {
        "name": "立创商城",
        "url": "https://www.szlcsc.com",
        "login_url": "https://www.szlcsc.com/login.html",
        "username": "15165098292",
        "password": "Lxf861211-"
    },
    "ickey": {
        "name": "云汉芯城",
        "url": "https://www.ickey.cn",
        "login_url": "https://www.ickey.cn/login.html",
        "username": "15165098292",
        "password": "Lxf861211-"
    },
    "hqew": {
        "name": "华强电子网",
        "url": "https://www.hqew.com",
        "login_url": "https://www.hqew.com/login.html",
        "username": "15165098292",
        "password": "Lxf861211-"
    },
    "allchips": {
        "name": "硬之城",
        "url": "https://www.allchips.com",
        "login_url": "https://www.allchips.com/login",
        "username": "15165098292",
        "password": "Lxf861211-"
    },
    "ichunt": {
        "name": "猎芯网",
        "url": "https://www.ichunt.com",
        "login_url": "https://www.ichunt.com/login.html",
        "username": "15165098292",
        "password": "Lxf861211-"
    },
    "ic_net": {
        "name": "创新在线",
        "url": "https://member.ic.net.cn",
        "login_url": "https://member.ic.net.cn/login.php",
        "username": "15165098292",
        "password": "Lxf861211_"
    },
    "oneyac": {
        "name": "唯样商城",
        "url": "https://www.oneyac.com",
        "login_url": "https://www.oneyac.com/login.html",
        "username": "15165098292",
        "password": "Lxf861211-"
    },
    "icgoo": {
        "name": "创新icgoo",
        "url": "https://www.icgoo.net",
        "login_url": "https://www.icgoo.net/login.html",
        "username": "15165098292",
        "password": "Lxf861211_"
    },
    "iceasy": {
        "name": "iceasy",
        "url": "https://www.iceasy.com",
        "login_url": "https://www.iceasy.com/login.html",
        "username": "15165098292",
        "password": "Lxf861211_"
    },
    "icdeal": {
        "name": "百能芯城",
        "url": "https://www.icdeal.com",
        "login_url": "https://www.icdeal.com/login.html",
        "username": "15165098292",
        "password": "Lxf861211"
    },
    "iczoom": {
        "name": "拍明芯城",
        "url": "https://www.iczoom.com",
        "login_url": "https://www.iczoom.com/login.html",
        "username": "15165098292",
        "password": "Lxf861211_"
    },
    "vipmro": {
        "name": "京东工业汇",
        "url": "https://www.vipmro.com",
        "login_url": "https://www.vipmro.com/login.html",
        "username": "15165098292",
        "password": "Lxf861211-"
    },
    "cmalls": {
        "name": "小猫商城",
        "url": "https://www.cmalls.net",
        "login_url": "https://www.cmalls.net/login.html",
        "username": "15165098292",
        "password": "Lxf861211-"
    },
    "ic_stk": {
        "name": "艾汐芯城",
        "url": "https://www.ic-stk.cn",
        "login_url": "https://www.ic-stk.cn/login.html",
        "username": "15165098292",
        "password": "Lxf861211-"
    },
    "wlxmall": {
        "name": "万联芯城",
        "url": "https://www.wlxmall.com",
        "login_url": "https://www.wlxmall.com/login.html",
        "username": "15165098292",
        "password": "Lxf861211-"
    },
    "hqchip": {
        "name": "华秋商城",
        "url": "https://www.hqchip.com",
        "login_url": "https://www.hqchip.com/login.html",
        "username": "15165098292",
        "password": "Lxf861211-"
    }
}
