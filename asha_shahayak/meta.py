import httpx


class MetaWhatsApp:
    def __init__(self, access_token: str, phone_number_id: str, graph_version: str) -> None:
        if not access_token or not phone_number_id:
            raise ValueError("Meta WhatsApp credentials are not configured")
        self.access_token = access_token
        self.phone_number_id = phone_number_id
        self.graph_version = graph_version
        self.base = f"https://graph.facebook.com/{graph_version}/{phone_number_id}"

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}

    async def send_text(self, to: str, text: str) -> None:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self.base}/messages",
                headers=self._headers,
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "text",
                    "text": {"body": text},
                },
            )
        response.raise_for_status()

    async def media_bytes(self, media_id: str) -> bytes:
        async with httpx.AsyncClient(timeout=30) as client:
            media = await client.get(
                f"https://graph.facebook.com/{self.graph_version}/{media_id}",
                headers=self._headers,
            )
            media.raise_for_status()
            url = media.json()["url"]
            audio = await client.get(url, headers=self._headers)
            audio.raise_for_status()
            return audio.content

    async def upload_media(self, audio: bytes, mime: str = "audio/ogg") -> str:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{self.base}/media",
                headers=self._headers,
                data={"messaging_product": "whatsapp", "type": mime},
                files={"file": ("reply.ogg", audio, mime)},
            )
        response.raise_for_status()
        return response.json()["id"]

    async def send_audio(self, to: str, media_id: str) -> None:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"{self.base}/messages",
                headers=self._headers,
                json={
                    "messaging_product": "whatsapp",
                    "to": to,
                    "type": "audio",
                    "audio": {"id": media_id},
                },
            )
        response.raise_for_status()
