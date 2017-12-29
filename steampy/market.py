import json

import re
from bs4 import BeautifulSoup, Tag
from steampy.confirmation import ConfirmationExecutor

from .exceptions import SteamServerError, ApiException
from .utils import handle_steam_response, extract_json, text_between
from .constants import COMMUNITY_URL
from .models import GameOptions, Currency
from .session import SteamSession, login_required


class SteamMarket:
    def __init__(self, steam_session: SteamSession):
        self.steam_session = steam_session

    def fetch_price(self, market_hash_name: str, game: GameOptions, currency: str = Currency.USD) -> dict:
        url = COMMUNITY_URL + '/market/priceoverview/'
        params = {'currency': currency, 'appid': game.app_id, 'market_hash_name': market_hash_name}
        response = self.steam_session.get(url, params=params)
        handle_steam_response(response)
        response_json = extract_json(response)
        return response_json

    @login_required
    def get_my_market_listings(self, fetch_all_sell_listings=True) -> dict:
        url = COMMUNITY_URL + "/market"
        response = self.steam_session.get(url, cookies={"ActListPageSize": "50"})
        handle_steam_response(response)

        try:
            listings = self._get_listings_from_html(response.text)
            sell_listing_count = len(
                [a for a in listings.get("sell_listings", {}).values() if not a["need_confirmation"]])
            if fetch_all_sell_listings and self._need_to_fetch_more_sell_listings(response.text):
                more_sell_listings = self._get_sell_listings_from_endpoint(sell_listing_count)
                listings["sell_listings"].update(more_sell_listings)

        except Exception as e:
            raise SteamServerError() from e

        return listings

    @login_required
    def get_market_history(self, count=30, start=0) -> dict:
        url = COMMUNITY_URL + "/market/myhistory/render/?query=&start=%s&count=%s" % (start, count)
        response = self.steam_session.get(url)
        handle_steam_response(response)
        response_json = extract_json(response)
        html = response_json.get("result_html")

        if response_json.get("success") is False or response_json.get("total_count") is None or \
                        '<div class="market_listing_table_message">There was an error' in html:
            raise SteamServerError("Invalid response")

        try:
            dictionary_listings = self._get_market_history_from_json(response_json)
        except Exception as e:
            raise SteamServerError("Invalid response") from e

        return dictionary_listings

    @login_required
    def create_sell_order(self, asset_id: str, game: GameOptions, money_to_receive: str) -> dict:
        data = {
            "assetid": asset_id,
            "sessionid": self._get_session_id(),
            "contextid": game.context_id,
            "appid": game.app_id,
            "amount": 1,
            "price": money_to_receive
        }
        headers = {'Referer': "%s/profiles/%s/inventory" % (COMMUNITY_URL, self.steam_session.steam_id)}
        response = self.steam_session.post(COMMUNITY_URL + "/market/sellitem/", data, headers=headers)
        handle_steam_response(response)
        response_json = extract_json(response)
        if response_json.get("needs_mobile_confirmation"):
            return self._confirm_sell_listing(asset_id)
        return response_json

    @login_required
    def create_buy_order(self, market_name: str, price_single_item: int, quantity: int, game: GameOptions,
                         currency: Currency = Currency.USD) -> dict:
        data = {
            "sessionid": self._get_session_id(),
            "currency": currency.value,
            "appid": game.app_id,
            "market_hash_name": market_name,
            "price_total": price_single_item * quantity,
            "quantity": quantity
        }
        headers = {'Referer': "%s/market/listings/%s/%s" % (COMMUNITY_URL, game.app_id, market_name)}
        response = self.steam_session.post(COMMUNITY_URL + "/market/createbuyorder/", data, headers=headers)
        handle_steam_response(response)
        response_json = extract_json(response)

        if response_json.get("success") != 1:
            raise ApiException("There was a problem creating the order. Are you using the right currency? success: %s"
                               % response_json.get("success"))
        return response_json

    @login_required
    def cancel_sell_order(self, sell_listing_id: str):
        """Steam return nothing from this call"""
        data = {"sessionid": self._get_session_id()}
        headers = {'Referer': COMMUNITY_URL + "/market/"}
        url = "%s/market/removelisting/%s" % (COMMUNITY_URL, sell_listing_id)
        response = self.steam_session.post(url, data=data, headers=headers)
        handle_steam_response(response)

    @login_required
    def cancel_buy_order(self, buy_order_id) -> dict:
        data = {"sessionid": self._get_session_id(), "buy_orderid": buy_order_id}
        headers = {"Referer": COMMUNITY_URL + "/market"}
        response = self.steam_session.post(COMMUNITY_URL + "/market/cancelbuyorder/", data, headers=headers)
        handle_steam_response(response)
        response_json = extract_json(response)

        if response_json.get("success") != 1:
            raise ApiException("There was a problem canceling the order. success: %s" % response_json.get("success"))
        return response_json

    def _confirm_sell_listing(self, asset_id: str) -> dict:
        con_executor = ConfirmationExecutor(self.steam_session.steam_guard['identity_secret'],
                                            self.steam_session.steam_guard['steamid'],
                                            self.steam_session)
        try:
            return con_executor.confirm_sell_listing(asset_id)
        except Exception as e:
            raise SteamServerError("[CONFIRM_SELL_LISTING_ERROR]") from e

    def _get_session_id(self) -> str:
        return self.steam_session.cookies.get_dict()['sessionid']

    def _get_listings_from_html(self, html: str) -> dict:
        listings = self._extract_listing_from_html(html)
        assets_descriptions = json.loads(text_between(html, "var g_rgAssets = ", ";\r\n"))
        listing_id_to_assets_address = self._get_listing_id_to_assets_address_from_html(html)
        listings = self._merge_listings_with_descriptions(listings, listing_id_to_assets_address, assets_descriptions)
        return listings

    def _extract_listing_from_html(self, html: str) -> dict:
        doc = BeautifulSoup(html, "html.parser")
        listings_nodes = doc.select("div[id=myListings]")[0].select("div.market_home_listing_table")
        sell_listings_dict = {}
        buy_orders_dict = {}
        for node in listings_nodes:
            if "My sell listings" in node.text:
                sell_listings_dict = self._get_sell_listings_from_node(node)
            elif "My listings awaiting confirmation" in node.text:
                sell_listings_awaiting_conf = self._get_sell_listings_from_node(node)
                for listing in sell_listings_awaiting_conf.values():
                    listing["need_confirmation"] = True
                sell_listings_dict.update(sell_listings_awaiting_conf)
            elif "My buy orders" in node.text:
                buy_orders_dict = self._get_buy_orders_from_node(node)
        return {"buy_orders": buy_orders_dict, "sell_listings": sell_listings_dict}

    @staticmethod
    def _get_buy_orders_from_node(node: Tag) -> dict:
        buy_orders_raw = node.findAll("div", {"id": re.compile('mybuyorder_\\d+')})
        buy_orders_dict = {}
        for order in buy_orders_raw:
            qnt_price_raw = order.select("span[class=market_listing_price]")[0].text.split("@")
            order = {
                "order_id": order.attrs["id"].replace("mybuyorder_", ""),
                "quantity": int(qnt_price_raw[0].strip()),
                "price": qnt_price_raw[1].strip(),
                "item_name": order.a.text
            }
            buy_orders_dict[order["order_id"]] = order
        return buy_orders_dict

    @staticmethod
    def _get_sell_listings_from_node(node: Tag) -> dict:
        sell_listings_raw = node.findAll("div", {"id": re.compile('mylisting_\d+')})
        sell_listings_dict = {}
        for listing_raw in sell_listings_raw:
            spans = listing_raw.select("span[title]")
            listing = {
                "listing_id": listing_raw.attrs["id"].replace("mylisting_", ""),
                "buyer_pay": spans[0].text.strip(),
                "you_receive": spans[1].text.strip()[1:-1],
                "created_on": listing_raw.findAll("div", {"class": "market_listing_listed_date"})[0].text.strip(),
                "need_confirmation": False
            }
            sell_listings_dict[listing["listing_id"]] = listing
        return sell_listings_dict

    @staticmethod
    def _get_listing_id_to_assets_address_from_html(html: str) -> dict:
        listing_id_to_assets_address = {}
        r = "CreateItemHoverFromContainer\( [\w]+, 'mylisting_([\d]+)_[\w]+', ([\d]+), '([\d]+)', '([\d]+)', [\d]+ \);"
        for match in re.findall(r, html):
            listing_id_to_assets_address[match[0]] = [str(match[1]), match[2], match[3]]
        return listing_id_to_assets_address

    @staticmethod
    def _merge_listings_with_descriptions(listings: dict, ids_to_assets_address: dict, descriptions: dict) -> dict:
        for listing_id, listing in listings.get("sell_listings").items():
            asset_address = ids_to_assets_address[listing_id]
            description = descriptions[asset_address[0]][asset_address[1]][asset_address[2]]
            listing["description"] = description
        return listings

    @staticmethod
    def _need_to_fetch_more_sell_listings(html: str) -> bool:
        if '<span id="tabContentsMyActiveMarketListings_end">' in html:
            n_showing = int(text_between(html, '<span id="tabContentsMyActiveMarketListings_end">', '</span>'))
            n_total = int(text_between(html, '<span id="tabContentsMyActiveMarketListings_total">', '</span>'))
            return n_total > n_showing
        return False

    def _get_sell_listings_from_endpoint(self, start: int) -> dict:
        params = {"query": "", "start": start, "count": -1}
        url = COMMUNITY_URL + "/market/mylistings/render/"
        response = self.steam_session.get(url, params=params)
        handle_steam_response(response)
        response_json = extract_json(response)
        document = BeautifulSoup(response_json.get("results_html"), "html.parser")

        id_to_assets_address = self._get_listing_id_to_assets_address_from_html(response_json.get("hovers"))
        listings = self._get_sell_listings_from_node(document)
        listings = self._merge_listings_with_descriptions(listings, id_to_assets_address, response_json.get("assets"))
        return {"sell_listings": listings}

    @staticmethod
    def _get_market_history_from_json(response_json: dict) -> dict:
        html = response_json.get("result_html")
        r = "[\w]+\( [\w]+, '(history_row_[\d_]+_name)', ([\d]+), '([\d]+)', '([\d]+)', [\d]+ \);"
        transaction_to_data = {}
        for match in re.findall(r, html):
            transaction_id = match[0]
            appid = match[1]
            contextid = match[2]
            itemid = match[3]
            transaction_to_data[transaction_id] = (appid, contextid, itemid)

        assets_dictionary = {}
        for appid in response_json.get("assets", {}):
            for contextid in assets_dictionary[appid]:
                for itemid, value in assets_dictionary[appid][contextid].items():
                    assets_dictionary[(appid, contextid, itemid)] = value

        listings = []
        soup = BeautifulSoup(html, "html.parser")
        divs = soup.select('div[class="market_listing_row market_recent_listing_row"]')
        for div in divs:
            listed_dates = div.find_all("div", class_="market_listing_listed_date")
            listedon_raw = listed_dates[1].text.strip()
            actedon_raw = listed_dates[0].text.strip()
            actedwith_div = div.find_all("div", class_="market_listing_whoactedwith")[0]
            price_raw = div.find_all("span", class_="market_listing_price")[0].text.strip()
            item_span = div.find_all("span", class_="market_listing_item_name")[0]
            item_name = item_span.text.strip()
            listing_id = div.get("id").replace("history_row_", "")

            listing = {
                "item_name": item_name,
                "listed_on": listedon_raw,
                "acted_on": actedon_raw,
                "price": price_raw,
                "listing_id": listing_id,
            }

            if "Listing created" in actedwith_div.text:
                listing["action"] = 1
            elif "Listing canceled" in actedwith_div.text:
                listing["action"] = 2
            elif "Listing expired" in actedwith_div.text:
                listing["action"] = 5
            else:
                url = actedwith_div.span.span.a.get("href")
                image = actedwith_div.span.span.a.img.get("src")
                name = actedwith_div.div.text.strip()
                listing["user"] = {"profile_url": url, "image_url": image}
                sign = div.find_all("div", class_="market_listing_gainorloss")[0].text.strip()
                if sign == "+":
                    listing["user"]["name"] = name.replace("Seller:", "").strip()
                    listing["action"] = 3
                else:
                    listing["user"]["name"] = name.replace("Buyer:", "").strip()
                    listing["action"] = 4
                key = transaction_to_data.get(item_span.get("id"), "")
                if key in assets_dictionary:
                    listing["description"] = assets_dictionary[key]
            listings.append(listing)

        data_dictionary = {
            "total_count": response_json.get("total_count"),
            "pagesize": response_json.get("pagesize"),
            "listings": listings,
            "start": response_json.get("start")
        }
        return data_dictionary
