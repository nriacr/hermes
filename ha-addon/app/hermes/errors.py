class HermesError(Exception):
    pass


class OutOfStockHermesError(HermesError):
    def __init__(self, message: str, product_title: str = "", product_url: str = "") -> None:
        self.product_title = product_title
        self.product_url = product_url
        super().__init__(message)


class HttpStatusHermesError(HermesError):
    def __init__(self, status_code: int, url: str) -> None:
        self.status_code = status_code
        self.url = url
        super().__init__(
            f"Site {status_code} dondurdu; bu kontrol atlandi, sonraki turda tekrar denenecek."
        )
