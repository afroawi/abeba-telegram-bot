import httpx


class BackendError(Exception):
    def __init__(self, status, message):
        self.status = status
        super().__init__(message)


class Backend:
    def __init__(self, base_url, service_token):
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/", timeout=httpx.Timeout(150, connect=10)
        )
        self.service_token = service_token

    async def close(self):
        await self.client.aclose()

    async def request(self, method, path, token=None, data=None, service=False):
        headers = (
            {"X-Abeba-Service-Token": self.service_token}
            if service
            else {"Authorization": f"Bearer {token}"}
        )
        response = await self.client.request(method, path, headers=headers, json=data)
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.is_error:
            detail = (
                payload.get("error", {}).get("message")
                or payload.get("detail")
                or "Abeba is unavailable. Please try again."
            )
            raise BackendError(response.status_code, detail)
        return payload

    async def session(self, telegram_user_id):
        return await self.request(
            "POST",
            "channels/auth/telegram-service/",
            data={"telegram_user_id": telegram_user_id},
            service=True,
        )
