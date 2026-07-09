class HermesError(Exception):
    pass


class OutOfStockHermesError(HermesError):
    pass


class HttpStatusHermesError(HermesError):
    def __init__(self, status_code: int, url: str) -> None:
        self.status_code = status_code
        self.url = url
        super().__init__(
            f"Site {status_code} dondurdu; bu kontrol atlandi, sonraki turda tekrar denenecek."
        )
