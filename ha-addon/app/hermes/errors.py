from typing import Optional


class HermesError(Exception):
    pass


class PriceUnavailableHermesError(HermesError):
    """A recognized product has no readable price; stock is not inferred."""


class OutOfStockHermesError(HermesError):
    """The site positively reported that the product or size is not available."""

    def __init__(self, message: str, product_title: str = "", product_url: str = "") -> None:
        self.product_title = product_title
        self.product_url = product_url
        super().__init__(message)


class EmptySearchResultsHermesError(HermesError):
    """A valid search page returned no matching, purchasable products.

    This is an expected marketplace state, not an operational error. Keeping it
    separate from parser and access failures prevents normal out-of-stock
    searches from polluting the dashboard error list or triggering alerts.
    """

    def __init__(self, message: str, no_results_notice: bool = False):
        self.no_results_notice = no_results_notice
        super().__init__(message)


class HttpStatusHermesError(HermesError):
    def __init__(self, status_code: int, url: str) -> None:
        self.status_code = status_code
        self.url = url
        super().__init__(
            f"Site {status_code} döndürdü; bu kontrol atlandı, sonraki turda tekrar denenecek."
        )


class BotProtectionHermesError(HermesError):
    """A site answered with a verification/challenge page instead of content."""

    def __init__(self, message: str, challenge_reason: str = "", http_status: Optional[int] = None,
                 hold_seconds: Optional[float] = None) -> None:
        self.challenge_reason = challenge_reason
        self.http_status = http_status
        # Set when nothing was sent because the site still holds back after an earlier block.
        self.hold_seconds = hold_seconds
        super().__init__(message)


def error_status(exc: BaseException) -> Optional[int]:
    """HTTP status of an error, also for `requests.HTTPError` wrappers."""
    status_code = getattr(exc, "status_code", None)
    if isinstance(status_code, int):
        return status_code
    response_status = getattr(getattr(exc, "response", None), "status_code", None)
    return response_status if isinstance(response_status, int) else None
