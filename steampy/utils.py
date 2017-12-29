import copy
import struct
import urllib.parse as urlparse
import re
from typing import List

import requests
from bs4 import BeautifulSoup, Tag

from steampy.exceptions import TooManyRequests, SteamServerError
from steampy.models import GameOptions


def text_between(text: str, begin: str, end: str) -> str:
    start = text.index(begin) + len(begin)
    end = text.index(end, start)
    return text[start:end]


def texts_between(text: str, begin: str, end: str):
    stop = 0
    while True:
        try:
            start = text.index(begin, stop) + len(begin)
            stop = text.index(end, start)
            yield text[start:stop]
        except:
            raise StopIteration


def account_id_to_steam_id(account_id: str) -> str:
    if int(account_id) > 76561197960265728:
        return account_id
    return str(int(account_id) + 76561197960265728)

    # first_bytes = int(account_id).to_bytes(4, byteorder='big')
    # last_bytes = 0x1100001.to_bytes(4, byteorder='big')
    # return str(struct.unpack('>Q', last_bytes + first_bytes)[0])


def steam_id_to_account_id(steam_id: str) -> str:
    if int(steam_id) < 76561197960265728:
        return steam_id
    return str(int(steam_id) - 76561197960265728)
    # return str(struct.unpack('>L', int(steam_id).to_bytes(8, byteorder='big')[4:])[0])


def price_to_float(price: str) -> float:
    return float(price[1:].split()[0])


def merge_items_with_descriptions_from_inventory(inventory_response: dict, game: GameOptions) -> dict:
    inventory = inventory_response['rgInventory']
    descriptions = inventory_response['rgDescriptions']
    return merge_items(inventory.values(), descriptions, context_id=game.context_id)


def merge_items_with_descriptions_from_offers(offers_response: dict) -> dict:
    descriptions = {get_description_key(offer): offer for offer in offers_response['response'].get('descriptions', [])}
    received_offers = offers_response['response'].get('trade_offers_received', [])
    sent_offers = offers_response['response'].get('trade_offers_sent', [])
    offers_response['response']['trade_offers_received'] = list(
        map(lambda offer: merge_items_with_descriptions_from_offer(offer, descriptions), received_offers))
    offers_response['response']['trade_offers_sent'] = list(
        map(lambda offer: merge_items_with_descriptions_from_offer(offer, descriptions), sent_offers))
    return offers_response


def merge_items_with_descriptions_from_offer(offer: dict, descriptions: dict) -> dict:
    merged_items_to_give = merge_items(offer.get('items_to_give', []), descriptions)
    merged_items_to_receive = merge_items(offer.get('items_to_receive', []), descriptions)
    offer['items_to_give'] = merged_items_to_give
    offer['items_to_receive'] = merged_items_to_receive
    return offer


def merge_items(items: List[dict], descriptions: dict, **kwargs) -> dict:
    merged_items = {}
    for item in items:
        description_key = get_description_key(item)
        description = copy.copy(descriptions[description_key])
        item_id = item.get('id') or item['assetid']
        description['contextid'] = item.get('contextid') or kwargs['context_id']
        description['id'] = item_id
        description['amount'] = item['amount']
        merged_items[item_id] = description
    return merged_items


def get_description_key(item: dict) -> str:
    return item['classid'] + '_' + item['instanceid']


def get_token_from_trade_offer_url(trade_offer_url: str) -> str:
    params = urlparse.urlparse(trade_offer_url).query
    return urlparse.parse_qs(params)["token"][0]


def get_partner_from_trade_offer_url(trade_offer_url: str) -> str:
    params = urlparse.urlparse(trade_offer_url).query
    return urlparse.parse_qs(params)["partner"][0]


def handle_steam_response(response: requests.Response):
    if response.status_code == 429:
        raise TooManyRequests("Steam responded with a 429 http code. Too many requests")
    elif response.status_code != 200:
        raise SteamServerError("Steam responded with a %s http code" % response.status_code)


def extract_json(response: requests.Response) -> dict:
    try:
        return response.json()
    except ValueError as e:
        raise SteamServerError("Invalid Json") from e
