class HermesError(Exception):
    pass


class OutOfStockHermesError(HermesError):
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


class HttpStatusHermesError(HermesError):
    def __init__(self, status_code: int, url: str) -> None:
        self.status_code = status_code
        self.url = url
        super().__init__(
            f"Site {status_code} dondurdu; bu kontrol atlandi, sonraki turda tekrar denenecek."
        )
