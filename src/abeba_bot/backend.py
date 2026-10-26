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

    async def audio(self, token, message_id, part=1):
        """Fetch authenticated MP3 bytes, never a model-supplied URL."""
        async with self.client.stream(
            'POST', 'channels/chat/audio/',
            headers={'Authorization': f'Bearer {token}'},
            json={'message_id': message_id, 'part': part},
        ) as response:
            if response.is_error:
                await response.aread()
                try:
                    payload = response.json()
                    detail = payload.get('error', {}).get('message') or payload.get('detail')
                except (ValueError, AttributeError):
                    detail = None
                raise BackendError(response.status_code, detail or 'Audio is unavailable right now. Please try again later.')
            if response.headers.get('content-type', '').split(';')[0] != 'audio/mpeg':
                raise BackendError(502, 'Abeba returned an invalid audio response.')
            try:
                parts = int(response.headers['X-Abeba-Audio-Parts'])
                returned_part = int(response.headers['X-Abeba-Audio-Part'])
                if returned_part != part or not 1 <= part <= parts <= 100:
                    raise ValueError
            except (KeyError, ValueError):
                raise BackendError(502, 'Abeba returned invalid audio parts.') from None
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > 20 * 1024 * 1024:
                    raise BackendError(502, 'This audio part is too large to send.')
                chunks.append(chunk)
            if not size:
                raise BackendError(502, 'Abeba returned an empty audio response.')
            return b''.join(chunks), parts
