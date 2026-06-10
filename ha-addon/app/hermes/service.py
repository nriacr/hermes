import os
import random
import time
from datetime import timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Dict, List

import requests

from .config_loader import load_config
from .constants import (
    AMAZON_SEARCH_ERROR_NOTIFICATION_HOUR,
    AMAZON_SEARCH_HTTP_COOLDOWN_SECONDS,
    NOTIFY_REPEAT_SECONDS,
    SITE_AMAZON,
    SITE_HEPSIBURADA,
    STATE_PATH,
    SUMMARY_PATH,
)
from .errors import HermesError
from .http_client import cleaned_html, fetch_hepsiburada_page, fetch_with_retries
from .logging_utils import log
from .models import HermesConfig, PriceSummaryRow, ProductRule, SearchResultItem
from .notifier import send_pushover
from .providers.registry import extract_offer
from .search_amazon import (
    dedupe_results,
    extract_result_candidates,
    filter_matching_results,
    title_matches_any_keyword,
)
from .storage import load_json, save_json
from .utils import (
    format_local_datetime,
    format_signed_tl,
    format_tl,
    local_now,
    log_cell,
    normalize_item_key,
    normalize_key,
    parse_iso_datetime,
    site_label,
    utc_now,
)


def sorted_summary_rows(rows: List[PriceSummaryRow]) -> List[PriceSummaryRow]:
    return sorted(rows, key=lambda row: (row.seller.casefold(), abs(row.difference), row.price))


def _state_decimal(value: Any):
    if value is None:
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def price_bounds(state_entry: Dict[str, Any], current_price: Decimal):
    min_price = _state_decimal(state_entry.get("min_price"))
    max_price = _state_decimal(state_entry.get("max_price"))
    if min_price is None or current_price < min_price:
        min_price = current_price
    if max_price is None or current_price > max_price:
        max_price = current_price
    return min_price, max_price


def product_check_due(product: ProductRule, state_entry: Dict[str, Any], global_interval_minutes: int) -> bool:
    interval_minutes = product.check_interval_minutes or global_interval_minutes
    last_checked = parse_iso_datetime(state_entry.get("last_checked_at"))
    if not last_checked:
        return True
    elapsed_seconds = (local_now().astimezone(timezone.utc) - last_checked).total_seconds()
    return elapsed_seconds >= interval_minutes * 60


def summary_row_from_state(product: ProductRule, state_entry: Dict[str, Any], seller: str):
    price = _state_decimal(state_entry.get("last_price"))
    if price is None:
        return None
    min_price = _state_decimal(state_entry.get("min_price")) or price
    max_price = _state_decimal(state_entry.get("max_price")) or price
    return PriceSummaryRow(
        seller=seller,
        product_title=str(state_entry.get("title") or product.name or product.url),
        product_url=str(state_entry.get("url") or state_entry.get("configured_url") or product.url),
        price=price,
        target_price=product.target_price,
        min_price=min_price,
        max_price=max_price,
    )


def save_price_summary(rows: List[PriceSummaryRow]) -> None:
    sorted_rows = sorted_summary_rows(rows)
    payload = {
        "checked_at": format_local_datetime(local_now()),
        "row_count": len(sorted_rows),
        "rows": [
            {
                "no": idx,
                "seller": row.seller,
                "product_title": row.product_title,
                "product_url": row.product_url,
                "price": format_tl(row.price),
                "target": format_tl(row.target_price),
                "difference": format_signed_tl(row.difference),
                "min_price": format_tl(row.min_price),
                "max_price": format_tl(row.max_price),
                "price_range": f"{format_tl(row.min_price)} / {format_tl(row.max_price)}",
                "is_target_hit": row.price <= row.target_price,
            }
            for idx, row in enumerate(sorted_rows, start=1)
        ],
    }
    save_json(SUMMARY_PATH, payload)


def log_price_summary(rows: List[PriceSummaryRow]) -> None:
    if not rows:
        log("Özet: eslesen=0")
        return
    no_width = 3
    seller_width = 12
    product_width = 40
    price_width = 10
    header = (
        f"{'No':>{no_width}} | "
        f"{log_cell('Satıcı', seller_width)} | "
        f"{log_cell('Ürün Adı', product_width)} | "
        f"{'Fiyat':>{price_width}} | "
        f"{'Hedef':>{price_width}} | "
        f"{'Fark':>{price_width}}"
    )
    separator = (
        f"{'-' * no_width}-+-"
        f"{'-' * seller_width}-+-"
        f"{'-' * product_width}-+-"
        f"{'-' * price_width}-+-"
        f"{'-' * price_width}-+-"
        f"{'-' * price_width}"
    )
    sorted_rows = sorted_summary_rows(rows)
    log(f"Özet: eslesen={len(sorted_rows)}")
    log(header)
    log(separator)
    for idx, row in enumerate(sorted_rows, start=1):
        log(
            f"{idx:>{no_width}} | "
            f"{log_cell(row.seller, seller_width)} | "
            f"{log_cell(row.product_title, product_width)} | "
            f"{format_tl(row.price):>{price_width}} | "
            f"{format_tl(row.target_price):>{price_width}} | "
            f"{format_signed_tl(row.difference):>{price_width}}"
        )


def publish_price_summary(rows: List[PriceSummaryRow]) -> None:
    save_price_summary(rows)
    log_price_summary(rows)


def should_alert(
    state_entry: Dict[str, Any],
    current_price: Decimal,
    target_price: Decimal,
    repeat_after_24h: bool,
) -> bool:
    if current_price > target_price:
        return False
    last_alerted_price = state_entry.get("last_alerted_price")
    if last_alerted_price is None:
        return True
    try:
        if current_price < Decimal(str(last_alerted_price)):
            return True
    except InvalidOperation:
        return True
    if repeat_after_24h:
        last_alerted_at = parse_iso_datetime(state_entry.get("last_alerted_at"))
        if not last_alerted_at:
            return False
        elapsed = (local_now().astimezone(timezone.utc) - last_alerted_at).total_seconds()
        return elapsed >= NOTIFY_REPEAT_SECONDS
    return not state_entry.get("was_below_target", False)


def update_state_entry(
    state_entry: Dict[str, Any],
    current_price: Decimal,
    target_price: Decimal,
    alert_sent: bool,
) -> Dict[str, Any]:
    min_price, max_price = price_bounds(state_entry, current_price)
    updated = dict(state_entry)
    updated["last_price"] = str(current_price)
    updated["min_price"] = str(min_price)
    updated["max_price"] = str(max_price)
    updated["last_checked_at"] = utc_now()
    updated["was_below_target"] = current_price <= target_price
    if alert_sent:
        updated["last_alerted_price"] = str(current_price)
        updated["last_alerted_at"] = utc_now()
    return updated


def cooldown_remaining_seconds(search_state: Dict[str, Any]) -> int:
    status = search_state.get("last_error_status")
    if status not in {429, 503}:
        return 0
    last_checked = parse_iso_datetime(search_state.get("last_checked_at"))
    if not last_checked:
        return 0
    elapsed = (local_now().astimezone(timezone.utc) - last_checked).total_seconds()
    remaining = AMAZON_SEARCH_HTTP_COOLDOWN_SECONDS - int(elapsed)
    return max(0, remaining)


def should_send_search_error_notification(state_entry: Dict[str, Any]) -> bool:
    now = local_now()
    if now.hour != AMAZON_SEARCH_ERROR_NOTIFICATION_HOUR:
        return False
    last_notified = parse_iso_datetime(state_entry.get("last_error_notified_at"))
    if not last_notified:
        return True
    return last_notified.astimezone().date() < now.date()


def wait_before_request(label: str, config: HermesConfig) -> None:
    delay = random.randint(config.request_delay_min_seconds, config.request_delay_max_seconds)
    log(f"{label} istegi oncesi {delay} saniye bekleniyor.")
    if delay > 0:
        time.sleep(delay)


def update_error_notification_state(state_entry: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(state_entry)
    updated["last_error_notified_at"] = utc_now()
    return updated


def reset_missing_alerts(
    updated_items_state: Dict[str, Any], seen_item_keys: set, page_name: str, target_name: str
) -> None:
    missing_reset_count = 0
    for missing_item_key, missing_item_state in list(updated_items_state.items()):
        if missing_item_key in seen_item_keys or not isinstance(missing_item_state, dict):
            continue
        if (
            missing_item_state.get("last_alerted_price") is not None
            or missing_item_state.get("last_alerted_at") is not None
        ):
            changed = dict(missing_item_state)
            changed.pop("last_alerted_price", None)
            changed.pop("last_alerted_at", None)
            changed["last_missing_at"] = utc_now()
            updated_items_state[missing_item_key] = changed
            missing_reset_count += 1
    if missing_reset_count:
        log(
            f"Amazon aramada kaybolan urunler tekrar bildirim icin hazirlandi: "
            f"{page_name} / {target_name} | adet={missing_reset_count}"
        )


def _fetch_product_offer(session: requests.Session, site: str, url: str, timeout: int):
    if site == SITE_HEPSIBURADA:
        response = fetch_hepsiburada_page(session, url, timeout)
    else:
        response = fetch_with_retries(session, url, timeout)
    html = cleaned_html(response)
    lowered = html.lower()
    if "captcha" in lowered and "robot" in lowered:
        raise HermesError(f"{site_label(site)} bot korumasi nedeniyle captcha sayfasi dondu.")
    return extract_offer(site, html)


def _fetch_amazon_detail_result(session: requests.Session, candidate, config: HermesConfig) -> SearchResultItem:
    wait_before_request("Amazon detay", config)
    response = fetch_with_retries(session, candidate.url, config.request_timeout_seconds)
    html = cleaned_html(response)
    if "captcha" in html.lower() and "robot" in html.lower():
        raise HermesError("Amazon bot korumasi nedeniyle captcha sayfasi dondu.")
    offer = extract_offer(SITE_AMAZON, html)
    title = offer.title or candidate.title
    url = offer.url or candidate.url
    log(
        "Amazon arama fiyatı ürün detayından tamamlandı: "
        f"{log_cell(title, 60)} | fiyat={offer.price} TL"
    )
    return SearchResultItem(title=title, url=url, price=offer.price)


def _fetch_amazon_search_results(
    session: requests.Session,
    search_url: str,
    config: HermesConfig,
    max_items_to_scan: int,
    target_keywords: List[str],
):
    wait_before_request("Arama", config)
    response = fetch_with_retries(session, search_url, config.request_timeout_seconds)
    html = cleaned_html(response)
    if "captcha" in html.lower() and "robot" in html.lower():
        raise HermesError("Amazon bot korumasi nedeniyle captcha sayfasi dondu.")

    candidates = extract_result_candidates(html, max_items_to_scan)
    results: List[SearchResultItem] = []
    skipped_detail_count = 0
    for candidate in candidates:
        if candidate.price is not None:
            results.append(SearchResultItem(title=candidate.title, url=candidate.url, price=candidate.price))
            continue
        if not title_matches_any_keyword(candidate.title, target_keywords):
            skipped_detail_count += 1
            continue
        try:
            results.append(_fetch_amazon_detail_result(session, candidate, config))
        except Exception as exc:  # noqa: BLE001
            log(f"Amazon arama detay fiyatı okunamadı: {log_cell(candidate.title, 60)} | {exc}")

    if skipped_detail_count:
        log(f"Amazon detay fiyatı atlandı: eslesmeyen_urun={skipped_detail_count}")
    if not results:
        raise HermesError("Amazon arama sonuçlarında veya ürün detaylarında okunabilir fiyat bulunamadı.")
    return results


def check_once(config: HermesConfig) -> None:
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    session = requests.Session()
    summary_rows: List[PriceSummaryRow] = []

    for product in config.products:
        product_key = normalize_item_key("product", product.site, product.url)
        state_entry = state.get(product_key, {})
        seller = site_label(product.site)
        if not product_check_due(product, state_entry, config.interval_minutes):
            cached_row = summary_row_from_state(product, state_entry, seller)
            if cached_row:
                summary_rows.append(cached_row)
            continue
        try:
            wait_before_request(seller, config)
            offer = _fetch_product_offer(session, product.site, product.url, config.request_timeout_seconds)
            display_name = product.name or offer.title or product.url
            matched_url = offer.url or product.url
            min_price, max_price = price_bounds(state_entry, offer.price)
            summary_rows.append(
                PriceSummaryRow(
                    seller=seller,
                    product_title=offer.title or display_name,
                    product_url=matched_url,
                    price=offer.price,
                    target_price=product.target_price,
                    min_price=min_price,
                    max_price=max_price,
                )
            )
            log(
                f"Kontrol edildi: {seller} | {display_name} | fiyat={offer.price} TL | "
                f"hedef={product.target_price} TL"
            )

            alert_sent = False
            if should_alert(state_entry, offer.price, product.target_price, product.notify_once_in_24h):
                seller_note = f" ({offer.seller})" if offer.seller and product.site == SITE_HEPSIBURADA else ""
                message = (
                    f"Site: {seller}\n"
                    f"{display_name}\n"
                    f"Guncel fiyat: {offer.price} TL{seller_note}\n"
                    f"Hedef fiyat: {product.target_price} TL"
                )
                send_pushover(
                    session,
                    config.pushover_user_key,
                    config.pushover_api_token,
                    f"{seller} fiyat alarmi",
                    message,
                    matched_url,
                    config.request_timeout_seconds,
                )
                alert_sent = True
                log(f"Bildirim gonderildi: {seller} | {display_name}")
            elif offer.price <= product.target_price and product.notify_once_in_24h:
                log(
                    f"Bildirim atlandi, 24 saat dolmadi veya fiyat daha dusuk degil: "
                    f"{seller} | {matched_url}"
                )

            state[product_key] = update_state_entry(
                state_entry,
                offer.price,
                product.target_price,
                alert_sent,
            )
            state[product_key]["title"] = offer.title
            state[product_key]["url"] = matched_url
            state[product_key]["configured_url"] = product.url
            state[product_key]["site"] = product.site
            state[product_key]["last_error"] = None
            state[product_key]["last_error_status"] = None
        except Exception as exc:  # noqa: BLE001
            log(f"Hata: {seller} | {product.url} | {exc}")
            failed = dict(state_entry)
            failed["site"] = product.site
            failed["last_error"] = str(exc)
            failed["last_error_status"] = getattr(exc, "status_code", None)
            failed["last_checked_at"] = utc_now()
            state[product_key] = failed

    for page in config.amazon_search_pages:
        if not page.targets:
            log(f"Amazon arama atlandi: {page.name} | Bu arama sayfasina hedef urun eklenmemis.")
            continue
        page_key = normalize_item_key("amazon_search", page.name, *page.search_urls)
        page_state = state.get(page_key, {})
        remaining = cooldown_remaining_seconds(page_state)
        if remaining > 0:
            minutes = max(1, round(remaining / 60))
            log(
                f"Amazon arama gecici olarak atlandi: {page.name} | "
                f"Amazon korumasi sonrasi {minutes} dk sonra yeniden denenecek."
            )
            skipped = dict(page_state)
            skipped["last_skipped_at"] = utc_now()
            state[page_key] = skipped
            continue
        try:
            all_results = []
            failed_urls = []
            target_keywords = [target.product_name for target in page.targets]
            for idx, search_url in enumerate(page.search_urls, start=1):
                try:
                    url_results = _fetch_amazon_search_results(
                        session,
                        search_url,
                        config,
                        page.max_items_to_scan,
                        target_keywords,
                    )
                    all_results.extend(url_results)
                    log(
                        f"Arama linki kontrol edildi: {page.name} | "
                        f"link={idx}/{len(page.search_urls)} | okunan_urun={len(url_results)}"
                    )
                except Exception as exc:  # noqa: BLE001
                    failed_urls.append(f"{search_url} | {exc}")
                    log(f"Arama linki hatasi: {page.name} | link={idx}/{len(page.search_urls)} | {exc}")

            if not all_results:
                raise HermesError("Amazon arama sayfasindaki linklerin hicbirinde okunabilir urun bulunamadi.")

            results = dedupe_results(all_results)
            log(
                f"Arama sayfasi kontrol edildi: {page.name} | okunan_urun={len(results)} | "
                f"link_sayisi={len(page.search_urls)} | hedef_sayisi={len(page.targets)}"
            )

            targets_state = dict(page_state.get("targets", {}))
            for target in page.targets:
                target_key = normalize_key(target.name)
                target_state = targets_state.get(target_key, {})
                items_state = target_state.get("items", {})
                updated_items_state = dict(items_state)
                matches = filter_matching_results(results, target.product_name)
                seen_item_keys = set()

                log(
                    f"Arama hedefi kontrol edildi: {page.name} / {target.name} | "
                    f"eslesen_urun={len(matches)} | hedef={target.target_price} TL"
                )

                for match in matches:
                    item_key = normalize_key(match.url)
                    seen_item_keys.add(item_key)
                    item_state = dict(items_state.get(item_key, {}))
                    min_price, max_price = price_bounds(item_state, match.price)
                    summary_rows.append(
                        PriceSummaryRow(
                            seller="Amazon",
                            product_title=match.title,
                            product_url=match.url,
                            price=match.price,
                            target_price=target.target_price,
                            min_price=min_price,
                            max_price=max_price,
                        )
                    )
                    alert_sent = False

                    if should_alert(
                        item_state,
                        match.price,
                        target.target_price,
                        target.notify_once_in_24h,
                    ):
                        message = (
                            f"Amazon arama: {page.name}\n"
                            f"Hedef: {target.name}\n"
                            f"Eslesen urun: {match.title}\n"
                            f"Guncel fiyat: {match.price} TL\n"
                            f"Hedef fiyat: {target.target_price} TL"
                        )
                        send_pushover(
                            session,
                            config.pushover_user_key,
                            config.pushover_api_token,
                            "Amazon arama alarmi",
                            message,
                            match.url,
                            config.request_timeout_seconds,
                        )
                        alert_sent = True
                        log(f"Arama bildirimi gonderildi: {target.name} | {match.title}")
                    elif match.price <= target.target_price and target.notify_once_in_24h:
                        log(
                            "Arama bildirimi atlandi, 24 saat dolmadi veya fiyat daha dusuk degil: "
                            f"{match.title} | fiyat={match.price} TL"
                        )

                    updated_items_state[item_key] = update_state_entry(
                        item_state,
                        match.price,
                        target.target_price,
                        alert_sent,
                    )
                    updated_items_state[item_key]["title"] = match.title
                    updated_items_state[item_key]["url"] = match.url
                    updated_items_state[item_key]["last_error"] = None

                if not failed_urls:
                    reset_missing_alerts(updated_items_state, seen_item_keys, page.name, target.name)

                targets_state[target_key] = {
                    "items": updated_items_state,
                    "last_match_count": len(matches),
                    "last_checked_at": utc_now(),
                }

            state[page_key] = {
                "targets": targets_state,
                "last_checked_at": utc_now(),
                "last_error": None if not failed_urls else "; ".join(failed_urls)[:900],
                "last_error_status": None,
                "last_error_notified_at": page_state.get("last_error_notified_at"),
            }
        except Exception as exc:  # noqa: BLE001
            error_message = str(exc)
            error_status = getattr(exc, "status_code", None)
            log(f"Hata: {' | '.join(page.search_urls)} | {error_message}")
            updated_page_state = dict(page_state)
            if should_send_search_error_notification(updated_page_state):
                try:
                    target_names = ", ".join(target.name for target in page.targets)
                    message = (
                        f"Amazon arama: {page.name}\n"
                        f"Hedefler: {target_names}\n"
                        f"Hata: {error_message}\n"
                        "Kontrol etmen gerekebilir: link gecersiz olabilir, Amazon korumasi olabilir veya sayfa yapisi degismis olabilir."
                    )
                    send_pushover(
                        session,
                        config.pushover_user_key,
                        config.pushover_api_token,
                        "Amazon arama hatasi",
                        message[:900],
                        page.search_urls[0],
                        config.request_timeout_seconds,
                    )
                    updated_page_state = update_error_notification_state(updated_page_state)
                    log(f"Amazon arama hata bildirimi gonderildi: {page.name}")
                except Exception as notify_exc:  # noqa: BLE001
                    log(f"Amazon arama hata bildirimi gonderilemedi: {page.name} | {notify_exc}")

            updated_page_state["last_error"] = error_message
            updated_page_state["last_error_status"] = error_status
            updated_page_state["last_checked_at"] = utc_now()
            state[page_key] = updated_page_state

    if config.products or config.amazon_search_pages:
        publish_price_summary(summary_rows)
    save_json(STATE_PATH, state)


def run_service() -> int:
    try:
        config = load_config()
    except Exception as exc:  # noqa: BLE001
        log(f"Baslatma hatasi: {exc}")
        return 1

    run_once = os.getenv("RUN_ONCE", "").strip() == "1"
    if run_once:
        check_once(config)
        return 0

    log(f"Servis basladi. Kontrol araligi: {config.interval_minutes} dakika")
    while True:
        check_once(config)
        next_check = local_now() + timedelta(minutes=config.interval_minutes)
        log(f"Sonraki kontrol: {format_local_datetime(next_check)}")
        time.sleep(config.interval_minutes * 60)
